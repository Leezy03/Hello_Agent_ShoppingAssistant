"""多智能体避雷购物顾问系统"""

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
import json
import os
import shutil
import sys
import time
from typing import Dict, Any, List, Optional, TypedDict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from ..services.llm_service import get_llm, get_llm_config
from ..models.schemas import (
    ShoppingRequest,
    ShoppingReport,
    ProductAnalysis,
    Product,
    CandidateProduct,
    CandidateExtractionResult,
    EvidenceItem,
    EvidenceCollectionResult,
    SearchCallTrace,
    StepAttemptTrace,
)
from ..config import get_settings

# ============ Agent提示词 ============

SEARCH_TOOL_NAME = "search_brave_web_search"
DEFAULT_TOOL_TIMEOUT_SECONDS = 90
DEFAULT_LLM_TIMEOUT_SECONDS = 90
DEFAULT_TOOL_RETRIES = 1
DEFAULT_LLM_RETRIES = 1
SEARCH_CALL_TIMEOUT_SECONDS = 25
SEARCH_MAX_CONCURRENCY = 3
MAX_SEARCH_RESULT_CHARS = 4500
EVIDENCE_LIMITS = {
    "review": 3,
    "price": 2,
    "risk": 3,
}


class ShoppingAdvisorState(TypedDict, total=False):
    """LangGraph 工作流状态"""

    request: ShoppingRequest
    tracer: Any
    candidate_result: CandidateExtractionResult
    candidates: List[CandidateProduct]
    step_results: Dict[str, "StepResult"]
    review_step: "StepResult"
    price_step: "StepResult"
    red_flag_step: "StepResult"
    review_evidence: List[EvidenceItem]
    price_evidence: List[EvidenceItem]
    red_flag_evidence: List[EvidenceItem]
    review_response: str
    price_response: str
    red_flag_response: str
    report: ShoppingReport


class ShoppingAdvisorError(Exception):
    """购物顾问基类异常"""


@dataclass
class AgentRunMetrics:
    """一次检索 Agent 执行产生的可观测指标。"""

    search_calls: List[SearchCallTrace] = field(default_factory=list)
    model_duration_ms: Optional[int] = None

    @property
    def tool_call_count(self) -> int:
        return len(self.search_calls)


@dataclass
class AgentRunResult:
    """Agent 输出及其运行指标。"""

    response: str
    metrics: AgentRunMetrics = field(default_factory=AgentRunMetrics)


class SearchPipelineTimeoutError(TimeoutError):
    """异步检索流水线整体超时。"""

    def __init__(self, timeout_seconds: int, metrics: AgentRunMetrics):
        super().__init__(f"异步检索流水线超过{timeout_seconds}秒未完成")
        self.metrics = metrics


class SearchPipelineExecutionError(RuntimeError):
    """检索流水线失败,同时保留已经产生的调用指标。"""

    def __init__(self, message: str, metrics: AgentRunMetrics):
        super().__init__(message)
        self.metrics = metrics


class StepExecutionError(ShoppingAdvisorError):
    """步骤执行异常"""

    def __init__(
        self,
        step_name: str,
        message: str,
        metrics: Optional[AgentRunMetrics] = None,
    ):
        super().__init__(message)
        self.step_name = step_name
        self.metrics = metrics or AgentRunMetrics()


class ToolExecutionError(StepExecutionError):
    """工具执行异常"""


class ModelExecutionError(StepExecutionError):
    """模型执行异常"""


class ToolTimeoutError(ToolExecutionError):
    """工具调用超时"""


class ModelTimeoutError(ModelExecutionError):
    """模型调用超时"""


class JsonRepairError(ShoppingAdvisorError):
    """JSON 修复失败"""


class CitationValidationError(ShoppingAdvisorError):
    """报告引用与上游证据不一致"""

    def __init__(self, issues: List[str]):
        self.issues = issues
        super().__init__("；".join(issues))


@dataclass
class StepResult:
    """步骤执行结果"""

    name: str
    ok: bool
    response: str = ""
    error: Optional[Exception] = None
    attempts: List[StepAttemptTrace] = field(default_factory=list)
    tool_call_count: int = 0

    @property
    def status_text(self) -> str:
        return "成功" if self.ok else "失败"

    @property
    def error_summary(self) -> str:
        if not self.error:
            return ""
        return f"{type(self.error).__name__}: {self.error}"


class LangGraphAgent:
    """Small runtime wrapper for one role-specific LangGraph/LangChain agent."""

    def __init__(
        self,
        name: str,
        llm: Any,
        system_prompt: str,
        uses_search: bool = False,
        search_kind: str = "generic",
    ):
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.uses_search = uses_search
        self.search_kind = search_kind

    def list_tools(self) -> List[str]:
        return [SEARCH_TOOL_NAME] if self.uses_search else []

    def run(self, query: str) -> str:
        if self.uses_search:
            return self._run_search_agent(query)

        response = self.llm.invoke(
            [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=query),
            ]
        )
        return self._message_to_text(response)

    def _run_search_agent(self, query: str) -> str:
        return self.run_with_timeout(query, DEFAULT_TOOL_TIMEOUT_SECONDS).response

    def run_with_timeout(self, query: str, timeout_seconds: int) -> AgentRunResult:
        """在独立事件循环中运行可取消的异步检索流水线。"""
        metrics = AgentRunMetrics()
        started_at = time.perf_counter()
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._arun_search_agent(query, metrics),
                    timeout=timeout_seconds,
                )
            )
        except TimeoutError as exc:
            raise SearchPipelineTimeoutError(timeout_seconds, metrics) from exc
        except BaseExceptionGroup as exc:
            elapsed = time.perf_counter() - started_at
            deadline_tolerance = min(0.01, timeout_seconds)
            if elapsed >= timeout_seconds - deadline_tolerance:
                raise SearchPipelineTimeoutError(timeout_seconds, metrics) from exc
            raise

    async def _arun_search_agent(
        self,
        query: str,
        metrics: Optional[AgentRunMetrics] = None,
    ) -> AgentRunResult:
        metrics = metrics or AgentRunMetrics()
        settings = get_settings()
        if not settings.search_api_key:
            raise RuntimeError("SEARCH_API_KEY 未配置,无法启动 Brave Search MCP Server")

        search_queries = self._plan_search_queries(query)
        client = MultiServerMCPClient(
            {
                "brave_search": {
                    "transport": "stdio",
                    "command": self._resolve_npx_command(),
                    "args": ["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"],
                    "env": {**os.environ, "BRAVE_API_KEY": settings.search_api_key},
                }
            }
        )
        async with client.session("brave_search") as session:
            tools = await load_mcp_tools(
                session,
                server_name="brave_search",
            )
            search_tool = self._find_brave_search_tool(tools)
            search_results = await self._run_concurrent_searches(
                search_tool,
                search_queries,
                metrics,
            )
            if not search_results:
                fallback_queries = self._plan_fallback_search_queries(
                    query,
                    attempted_queries=search_queries,
                )
                if fallback_queries:
                    search_results = await self._run_concurrent_searches(
                        search_tool,
                        fallback_queries,
                        metrics,
                    )

        if not search_results:
            if self.search_kind in EVIDENCE_LIMITS and metrics.search_calls and all(
                call.status == "empty" for call in metrics.search_calls
            ):
                return AgentRunResult(
                    response=self._empty_evidence_response(),
                    metrics=metrics,
                )
            failures = [
                f"{call.query}: {call.error_message or call.status}"
                for call in metrics.search_calls
            ]
            raise SearchPipelineExecutionError(
                f"所有 Brave 搜索均失败: {' | '.join(failures)}",
                metrics,
            )

        response = await self._synthesize_search_results(query, search_results, metrics)
        return AgentRunResult(response=response, metrics=metrics)

    def _resolve_npx_command(self) -> str:
        if sys.platform.startswith("win"):
            return shutil.which("npx.cmd") or shutil.which("npx.exe") or "npx.cmd"
        return shutil.which("npx") or "npx"

    async def _run_concurrent_searches(
        self,
        search_tool: Any,
        search_queries: List[str],
        metrics: AgentRunMetrics,
    ) -> List[Dict[str, str]]:
        """使用同一个 MCP session 受控并发执行搜索。"""
        semaphore = asyncio.Semaphore(SEARCH_MAX_CONCURRENCY)
        call_traces = [
            SearchCallTrace(query=search_query, status="pending")
            for search_query in search_queries
        ]
        metrics.search_calls.extend(call_traces)

        async def execute_one(call_trace: SearchCallTrace) -> Optional[Dict[str, str]]:
            started_at: Optional[float] = None
            try:
                async with semaphore:
                    started_at = time.perf_counter()
                    call_trace.status = "running"
                    result = await asyncio.wait_for(
                        search_tool.ainvoke({"query": call_trace.query}),
                        timeout=SEARCH_CALL_TIMEOUT_SECONDS,
                    )
                    result_text = self._tool_result_to_text(result)
                    if self._is_no_results_message(result_text):
                        call_trace.status = "empty"
                        call_trace.error_type = "NoResults"
                        call_trace.error_message = result_text.strip() or "No web results found"
                        return None
                    call_trace.status = "success"
                    call_trace.result_chars = len(result_text)
                    return {
                        "query": call_trace.query,
                        "content": result_text[:MAX_SEARCH_RESULT_CHARS],
                    }
            except TimeoutError:
                call_trace.status = "failed"
                call_trace.error_type = "TimeoutError"
                call_trace.error_message = (
                    f"单次搜索超过{SEARCH_CALL_TIMEOUT_SECONDS}秒"
                )
                return None
            except asyncio.CancelledError:
                call_trace.status = "cancelled"
                call_trace.error_type = "CancelledError"
                call_trace.error_message = "节点整体超时,搜索已取消"
                raise
            except Exception as exc:
                error_message = str(exc)
                if self._is_no_results_message(error_message):
                    call_trace.status = "empty"
                    call_trace.error_type = "NoResults"
                else:
                    call_trace.status = "failed"
                    call_trace.error_type = type(exc).__name__
                call_trace.error_message = error_message
                return None
            finally:
                if started_at is not None:
                    call_trace.duration_ms = int(
                        (time.perf_counter() - started_at) * 1000
                    )

        results = await asyncio.gather(
            *(execute_one(call_trace) for call_trace in call_traces)
        )
        return [result for result in results if result is not None]

    async def _synthesize_search_results(
        self,
        query: str,
        search_results: List[Dict[str, str]],
        metrics: AgentRunMetrics,
    ) -> str:
        """基于并发搜索结果进行一次结构化模型生成。"""
        llm_config = get_llm_config()
        evidence_limit = EVIDENCE_LIMITS.get(self.search_kind)
        limit_instruction = (
            f"每个候选产品最多输出{evidence_limit}条证据。"
            if evidence_limit
            else ""
        )
        search_context = json.dumps(
            search_results,
            ensure_ascii=False,
            indent=2,
        )
        synthesis_prompt = f"""{query}

系统已经完成搜索。下面的搜索结果是唯一允许使用的外部事实来源。
不要请求或假装再次调用工具，不要补充搜索结果之外的事实。
{limit_instruction}

搜索结果:
{search_context}
"""

        started_at = time.perf_counter()
        async with AsyncOpenAI(
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
            timeout=llm_config["timeout"],
        ) as client:
            completion = await client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {
                        "role": "system",
                        "content": (
                            self.system_prompt
                            + "\n搜索工具已由系统执行完毕。请直接基于用户消息中的搜索结果返回目标JSON。"
                        ),
                    },
                    {"role": "user", "content": synthesis_prompt},
                ],
                temperature=0,
            )
        metrics.model_duration_ms = int(
            (time.perf_counter() - started_at) * 1000
        )
        return self._message_to_text(completion.choices[0].message)

    def _plan_search_queries(self, query: str) -> List[str]:
        """根据 Agent 类型为候选产品生成数量受控的确定性搜索计划。"""
        candidate_names = [
            self._compact_search_subject(candidate_name)
            for candidate_name in self._extract_candidate_names(query)
        ]

        if self.search_kind == "review" and candidate_names:
            planned = [
                search_query
                for candidate_name in candidate_names
                for search_query in (
                    f"{candidate_name} 评测 使用体验",
                    f"{candidate_name} 缺点 用户评价",
                )
            ]
        elif self.search_kind == "price" and candidate_names:
            planned = [
                f"{candidate_name} 价格 京东"
                for candidate_name in candidate_names
            ]
        elif self.search_kind == "risk" and candidate_names:
            planned = [
                search_query
                for candidate_name in candidate_names
                for search_query in (
                    f"{candidate_name} 缺点 投诉 售后",
                    f"{candidate_name} 故障 翻车",
                )
            ]
        else:
            planned = self._extract_search_queries(query)

        return self._deduplicate_queries(planned, limit=6) or [
            " ".join(query.split())[:200]
        ]

    def _plan_fallback_search_queries(
        self,
        query: str,
        attempted_queries: List[str],
    ) -> List[str]:
        """首轮全空时仅使用商品核心名进行宽泛回退。"""
        attempted = {" ".join(item.split()) for item in attempted_queries}
        candidate_names = [
            self._compact_search_subject(candidate_name)
            for candidate_name in self._extract_candidate_names(query)
        ]
        fallback_queries = [
            candidate_name
            for candidate_name in candidate_names
            if candidate_name and candidate_name not in attempted
        ]
        return self._deduplicate_queries(fallback_queries, limit=3)

    def _compact_search_subject(self, candidate_name: str) -> str:
        """移除不利于召回的引号和通用商品后缀,保留品牌与型号。"""
        compacted = " ".join(candidate_name.strip(' "\'“”').split())
        generic_suffixes = (
            "游戏笔记本电脑",
            "笔记本电脑",
            "游戏笔记本",
            "笔记本",
            "游戏本",
            "电脑",
        )
        for suffix in generic_suffixes:
            if compacted.endswith(suffix):
                compacted = compacted[: -len(suffix)].strip()
                break
        return compacted or " ".join(candidate_name.split())

    def _deduplicate_queries(self, queries: List[str], limit: int) -> List[str]:
        unique_queries = []
        seen = set()
        for search_query in queries:
            normalized = " ".join(search_query.split())
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_queries.append(normalized)
        return unique_queries[:limit]

    def _is_no_results_message(self, message: str) -> bool:
        normalized = message.strip().lower()
        return not normalized or any(
            marker in normalized
            for marker in (
                "no web results found",
                "no results found",
                "未找到搜索结果",
                "没有找到搜索结果",
            )
        )

    def _empty_evidence_response(self) -> str:
        kind_labels = {
            "review": "测评",
            "price": "价格",
            "risk": "风险",
        }
        kind_label = kind_labels.get(self.search_kind, "相关")
        return json.dumps(
            {
                "evidence": [],
                "coverage_notes": [
                    f"Brave Search 未检索到可用{kind_label}证据,报告不得据此作确定性结论。"
                ],
            },
            ensure_ascii=False,
        )

    def _extract_candidate_names(self, query: str) -> List[str]:
        candidate_names = []
        for line in query.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ") or "（品牌:" not in stripped:
                continue
            candidate_name = stripped[2:].split("（品牌:", 1)[0].strip()
            if candidate_name:
                candidate_names.append(candidate_name)
        return candidate_names[:3]

    def _extract_search_queries(self, query: str) -> List[str]:
        queries = []
        for line in query.splitlines():
            stripped = line.strip()
            for prefix in ("建议优先搜索关键词:", "建议优先搜索关键词："):
                if stripped.startswith(prefix):
                    search_query = stripped[len(prefix):].strip()
                    if search_query:
                        queries.append(search_query)
        if queries:
            return queries[:3]
        return [" ".join(query.split())[:200]]

    def _find_brave_search_tool(self, tools: List[Any]) -> Any:
        preferred_names = {"brave_web_search", SEARCH_TOOL_NAME}
        for tool in tools:
            if getattr(tool, "name", "") in preferred_names:
                return tool
        for tool in tools:
            if "search" in getattr(tool, "name", "").lower():
                return tool
        available = ", ".join(getattr(tool, "name", "<unknown>") for tool in tools) or "无"
        raise RuntimeError(f"未找到 Brave Search MCP 工具,当前可用工具: {available}")

    def _tool_result_to_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            return "\n".join(self._tool_result_to_text(item) for item in result)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        content = getattr(result, "content", None)
        if content is not None:
            return self._tool_result_to_text(content)
        return str(result)

    def _agent_result_to_text(self, result: Any) -> str:
        messages = result.get("messages", []) if isinstance(result, dict) else []
        if not messages:
            return str(result)
        return self._message_to_text(messages[-1])

    def _message_to_text(self, message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or item))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

CANDIDATE_EXTRACTOR_PROMPT = """你是候选产品抽取专家。你的任务是先找出最值得分析的候选产品,后续所有测评、价格、避雷信息都必须围绕这些候选产品展开。

**重要提示:**
1. 你必须调用 search_brave_web_search 工具检索,不要直接凭常识列产品
2. 候选产品必须尽量使用标准名称,至少包含品牌和型号
3. 候选产品数量控制在3个以内,优先选择主流、讨论度高、预算匹配的型号
4. 如果预算已给出,优先保留预算区间内或接近预算区间的型号
5. 如果找不到明确型号,可以返回品类下最主流的通用型号,但不要编造不存在的型号

**边界要求:**
1. 候选产品的name、brand、model只能基于检索结果填写,禁止自行补全未检索到的型号
2. 如果某个候选产品品牌明确但型号不明确,model填写"信息不足",并在reason中说明原因
3. 如果预算范围内缺少明确候选,可以返回接近预算的主流型号,但必须在reason中写明"预算匹配证据不足"
4. 优先覆盖2个及以上不同来源的主流候选信号;如果覆盖不足,仍可返回候选,但必须在reason中写明"来源覆盖不足"
5. 如果检索结果互相冲突,不要自行裁决,在reason中保留冲突点

**工具使用:**
请调用 search_brave_web_search 工具检索关键词。该工具只接收 query 参数,不要传 search_lang/country 等额外参数。

请严格按照以下JSON格式返回:
```json
{
    "category": "产品品类",
    "candidates": [
        {
            "name": "品牌 型号 标准名称",
            "brand": "品牌",
            "model": "型号",
            "reason": "为什么纳入候选"
        }
    ]
}
```
"""

REVIEW_COLLECTOR_PROMPT = """你是购物测评搜集专家。你的任务是使用搜索工具查找各平台（B站、小红书、知乎）对指定产品的真实测评和用户反馈。

**重要提示:**
你必须调用 search_brave_web_search 工具查找测评,不要自己编造信息！

**搜索策略:**
1. 搜索"[产品名] B站 测评 真实体验"
2. 搜索"[产品名] 小红书 避雷 踩坑"
3. 搜索"[产品名] 知乎 推荐 评价"
4. 搜索"[产品名] 缺点 吐槽"

**工具使用:**
请调用 search_brave_web_search 工具检索关键词。该工具只接收 query 参数,不要传 search_lang/country 等额外参数。

**注意:**
1. 必须使用工具,不要直接回答
2. 请尽可能搜索多个角度的信息
3. 优先覆盖B站、小红书、知乎中至少2个平台;如果做不到,必须明确写出"来源覆盖不足"
4. 只允许基于检索结果写入作者、标题、平台、立场、观点等信息,禁止自行补全
5. 如果某个候选产品缺少足够测评证据,必须明确写出"信息不足",不要把其他产品的测评挪用过来
6. 如果不同来源观点冲突,必须保留冲突原文含义,不要强行统一结论
7. 每条证据必须绑定一个候选产品标准名称,并保留来源URL、标题、平台、作者、原始摘要和该来源直接支持的claims
8. evidence_id使用review_001、review_002格式,不要跨产品复用同一个ID
9. 每个候选产品最多输出3条最有代表性的测评证据,优先保留不同平台和不同立场的来源

请严格返回JSON:
```json
{
  "evidence": [
    {
      "evidence_id": "review_001",
      "product_name": "候选产品标准名称",
      "evidence_type": "review",
      "source_url": "https://...",
      "source_title": "来源标题",
      "platform": "B站/小红书/知乎/其他",
      "author": "作者或发布主体",
      "snippet": "检索结果中的原始摘要或关键片段",
      "claims": ["该来源直接支持的观点1", "观点2"],
      "search_query": "实际使用的搜索关键词"
    }
  ],
  "coverage_notes": ["来源覆盖不足或信息不足说明"]
}
```
"""

PRICE_COLLECTOR_PROMPT = """你是价格对比专家。你的任务是使用搜索工具查找产品在各大电商平台的价格信息和型号对比。

**重要提示:**
你必须调用 search_brave_web_search 工具查找价格,不要自己编造价格！

**搜索策略:**
1. 搜索"[产品名] 价格 京东 淘宝"
2. 搜索"[产品名] 型号 参数 对比"
3. 搜索"[产品名] 哪个型号性价比高"

**工具使用:**
请调用 search_brave_web_search 工具检索关键词。该工具只接收 query 参数,不要传 search_lang/country 等额外参数。

**注意:**
1. 必须使用工具,不要直接回答
2. 关注不同型号的价格区间和性价比
3. 价格、型号、平台只能基于检索结果填写,禁止根据常识估价
4. 优先覆盖2个及以上电商或资讯来源;如果来源覆盖不足,必须明确写出"来源覆盖不足"
5. 如果候选产品价格信息不存在或价格区间差异过大,必须写"信息不足"或"价格存在冲突"
6. 不要把A型号的价格写到B型号名下,必须按候选产品逐个归档
7. 每条证据必须保留来源URL、标题、平台、摘要和该来源直接支持的价格/参数claims
8. evidence_id使用price_001、price_002格式
9. 每个候选产品最多输出2条价格证据,优先保留来源不同且能代表当前价格区间的结果

请严格返回JSON:
```json
{
  "evidence": [
    {
      "evidence_id": "price_001",
      "product_name": "候选产品标准名称",
      "evidence_type": "price",
      "source_url": "https://...",
      "source_title": "商品页或资讯标题",
      "platform": "京东/淘宝/拼多多/官网/其他",
      "author": "店铺或发布主体",
      "snippet": "检索结果中的价格或参数原始摘要",
      "claims": ["价格区间或参数事实"],
      "search_query": "实际使用的搜索关键词"
    }
  ],
  "coverage_notes": ["价格冲突或来源覆盖不足说明"]
}
```
"""

RED_FLAG_DETECTOR_PROMPT = """你是产品避雷专家。你的任务是使用搜索工具专门查找产品的负面信息、投诉、已知缺陷和恰饭测评。

**重要提示:**
你必须调用 search_brave_web_search 工具查找避雷信息,不要自己编造！

**搜索策略:**
1. 搜索"[产品名] 缺点 问题 质量"
2. 搜索"[产品名] 投诉 售后 差评"
3. 搜索"[产品名] 翻车 踩坑 千万别买"
4. 搜索"[产品名] 恰饭 广告 软文"

**工具使用:**
请调用 search_brave_web_search 工具检索关键词。该工具只接收 query 参数,不要传 search_lang/country 等额外参数。

**注意:**
1. 必须使用工具,不要直接回答
2. 特别关注：质量问题、售后体验、虚假宣传、恰饭测评
3. 负面信息、投诉、缺陷只能基于检索结果填写,禁止把泛化印象当成事实
4. 优先覆盖2个及以上来源;如果覆盖不足,必须明确写出"来源覆盖不足"
5. 如果某个候选产品没有搜到明确负面信息,请写"未检索到明确负面证据",不要反向推断为没有问题
6. 如果负面观点之间存在冲突,必须单列冲突点,不要直接下确定性结论
7. 不要把某个品牌的通病直接写成某个具体型号的确定缺陷,除非检索结果明确指向该型号
8. 每条风险证据必须保留来源URL、标题、平台、摘要和该来源直接支持的风险claims
9. evidence_id使用risk_001、risk_002格式
10. 每个候选产品最多输出3条风险证据,优先保留具体、可定位且来源不同的风险信号

请严格返回JSON:
```json
{
  "evidence": [
    {
      "evidence_id": "risk_001",
      "product_name": "候选产品标准名称",
      "evidence_type": "risk",
      "source_url": "https://...",
      "source_title": "投诉、差评或风险来源标题",
      "platform": "投诉平台/论坛/媒体/其他",
      "author": "作者或发布主体",
      "snippet": "检索结果中的原始风险摘要",
      "claims": ["该来源直接支持的风险点"],
      "search_query": "实际使用的搜索关键词"
    }
  ],
  "coverage_notes": ["未检索到明确负面证据或来源不足说明"]
}
```
"""

REPORT_GENERATOR_PROMPT = """你是避雷购物报告撰写专家。你的任务是根据测评信息、价格信息和避雷信息,生成结构化的购物避雷报告。

请严格按照以下JSON格式返回报告:
```json
{
  "query": "用户查询的产品",
  "category": "产品品类",
  "products": [
    {
      "product": {
        "name": "产品全名",
        "brand": "品牌",
        "model": "型号",
        "price_range": "1000-2000元",
        "rating": 8.5,
        "image_url": null,
        "specs": {"关键参数1": "值1", "关键参数2": "值2"},
        "price_evidence_ids": ["price_001"],
        "spec_evidence_ids": ["price_002"]
      },
      "reviews": [
        {
          "platform": "B站",
          "author": "博主名称",
          "title": "测评标题",
          "url": null,
          "stance": "推荐",
          "is_sponsored": false,
          "key_points": ["观点1", "观点2"],
          "credibility_score": 8.0,
          "evidence_ids": ["review_001"]
        }
      ],
      "common_pros": ["公认优点1", "公认优点2"],
      "common_cons": ["公认缺点1", "公认缺点2"],
      "red_flags": ["避雷点1", "避雷点2"],
      "controversy_points": ["争议点1"],
      "verdict": "推荐",
      "verdict_reason": "综合评价理由",
      "pro_evidence_ids": {"公认优点1": ["review_001"]},
      "con_evidence_ids": {"公认缺点1": ["review_002"]},
      "red_flag_evidence_ids": {"避雷点1": ["risk_001"]},
      "controversy_evidence_ids": {"争议点1": ["review_001", "review_002"]},
      "verdict_evidence_ids": ["review_001", "price_001", "risk_001"]
    }
  ],
  "comparison_summary": "横向对比总结",
  "final_recommendation": "最终购买建议",
  "budget_advice": "预算建议",
  "general_tips": ["选购建议1", "选购建议2"],
  "comparison_evidence_ids": ["review_001", "price_001"],
  "recommendation_evidence_ids": ["review_001", "price_001", "risk_001"],
  "budget_evidence_ids": ["price_001"]
}
```

**重要提示:**
1. 必须分析每个博主/测评者的立场是否客观
2. 标记疑似恰饭(广告)内容,is_sponsored设为true
3. 对比不同信息源的矛盾之处,记录在controversy_points中
4. 给出明确的verdict: "推荐" / "不推荐" / "看需求"
5. red_flags(避雷点)是最重要的部分,要重点列出
6. credibility_score根据博主专业度、是否恰饭、内容详实度评判(1-10分)
7. 如果用户指定了预算范围,给出具体的预算建议
8. 分析至少2-3个主流产品/型号
9. general_tips给出该品类的通用选购建议
10. 所有 evidence_ids 只能引用上游证据中真实存在的 evidence_id,禁止编造ID
11. 价格字段只能引用price证据; reviews主要引用review证据; red_flags必须引用risk或review证据
12. 产品级字段不能引用其他候选产品的证据
13. common_pros/common_cons中的价格、性价比、预算类结论可以引用price证据;体验类优缺点应引用review或risk证据

**边界要求:**
1. product.name、brand、model、price_range、specs、reviews、red_flags、common_pros、common_cons、verdict_reason 只能基于上游检索结果填写
2. 如果上游证据不足,对应字段必须写"信息不足"、空数组、null或保守描述,禁止自行脑补
3. 如果不同来源对同一产品存在冲突,必须把冲突逐条写入controversy_points,不要在正文里悄悄抹平
4. 如果没有检索到明确负面证据,red_flags可以为空,但不要据此推断产品一定安全
5. 如果没有检索到明确价格,price_range必须写"信息不足",不要用预算区间替代真实价格区间
6. 如果没有检索到明确参数,specs填null或仅保留已检索到的字段,不要补全默认参数
7. final_recommendation必须体现证据强弱: 证据充分时给明确建议,证据不足时给保守建议
8. comparison_summary中必须说明来源覆盖情况和主要冲突信息,不能只写结论
9. 严禁把候选产品之外的型号写入products列表
10. 没有证据支持的字段必须保守留空,对应evidence_ids也必须为空
"""


class MultiAgentShoppingAdvisor:
    """多智能体避雷购物顾问系统"""

    def __init__(self):
        """初始化多智能体系统"""
        print("🔄 开始初始化 LangGraph 避雷购物系统...")

        try:
            self.llm = get_llm()

            # 创建候选产品抽取Agent
            print("  - 创建候选抽取Agent...")
            self.candidate_agent = LangGraphAgent(
                name="候选产品抽取专家",
                llm=self.llm,
                system_prompt=CANDIDATE_EXTRACTOR_PROMPT,
                uses_search=True,
                search_kind="candidate",
            )

            # 创建测评搜集Agent
            print("  - 创建测评搜集Agent...")
            self.review_agent = LangGraphAgent(
                name="测评搜集专家",
                llm=self.llm,
                system_prompt=REVIEW_COLLECTOR_PROMPT,
                uses_search=True,
                search_kind="review",
            )

            # 创建价格对比Agent
            print("  - 创建价格对比Agent...")
            self.price_agent = LangGraphAgent(
                name="价格对比专家",
                llm=self.llm,
                system_prompt=PRICE_COLLECTOR_PROMPT,
                uses_search=True,
                search_kind="price",
            )

            # 创建避雷检测Agent
            print("  - 创建避雷检测Agent...")
            self.red_flag_agent = LangGraphAgent(
                name="避雷检测专家",
                llm=self.llm,
                system_prompt=RED_FLAG_DETECTOR_PROMPT,
                uses_search=True,
                search_kind="risk",
            )

            # 创建报告生成Agent(不需要工具)
            print("  - 创建报告生成Agent...")
            self.report_agent = LangGraphAgent(
                name="报告生成专家",
                llm=self.llm,
                system_prompt=REPORT_GENERATOR_PROMPT,
                uses_search=False,
            )
            self.workflow = self._build_workflow()

            print(f"✅ LangGraph 避雷购物系统初始化成功")
            print(f"   候选抽取Agent: {len(self.candidate_agent.list_tools())} 个工具")
            print(f"   测评搜集Agent: {len(self.review_agent.list_tools())} 个工具")
            print(f"   价格对比Agent: {len(self.price_agent.list_tools())} 个工具")
            print(f"   避雷检测Agent: {len(self.red_flag_agent.list_tools())} 个工具")

        except Exception as e:
            print(f"❌ LangGraph 系统初始化失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def analyze_product(self, request: ShoppingRequest, tracer: Any = None) -> ShoppingReport:
        """
        使用多智能体协作分析产品

        Args:
            request: 购物避雷请求
            tracer: 可选任务追踪器,用于记录节点状态和耗时

        Returns:
            购物避雷报告
        """
        print(f"\n{'='*60}")
        print(f"🛡️ 开始多智能体协作分析产品...")
        print(f"产品: {request.product_name}")
        if request.budget_min or request.budget_max:
            print(f"预算: {request.budget_min or '不限'}-{request.budget_max or '不限'}元")
        print(f"品牌偏好: {', '.join(request.brand_preferences) if request.brand_preferences else '无'}")
        print(f"关注要点: {', '.join(request.concerns) if request.concerns else '无'}")
        print(f"{'='*60}\n")

        final_state = self._get_workflow().invoke(
            {
                "request": request,
                "tracer": tracer,
                "step_results": {},
            }
        )
        report = final_state.get("report")
        if report:
            return report
        return self._create_fallback_report(request)

    def _get_workflow(self):
        """获取已编译的 LangGraph 工作流。测试通过 __new__ 构造实例时会懒加载。"""
        if not hasattr(self, "workflow"):
            self.workflow = self._build_workflow()
        return self.workflow

    def _build_workflow(self):
        """构建 LangGraph 编排: 候选抽取 -> 检索节点 fan-out/fan-in -> 报告生成。"""
        workflow = StateGraph(ShoppingAdvisorState)
        workflow.add_node("candidate", self._candidate_node)
        workflow.add_node("review", self._review_node)
        workflow.add_node("price", self._price_node)
        workflow.add_node("red_flag", self._red_flag_node)
        workflow.add_node("report", self._report_node)
        workflow.add_edge(START, "candidate")
        workflow.add_edge("candidate", "review")
        workflow.add_edge("candidate", "price")
        workflow.add_edge("candidate", "red_flag")
        workflow.add_edge(["review", "price", "red_flag"], "report")
        workflow.add_edge("report", END)
        return workflow.compile()

    def _candidate_node(self, state: ShoppingAdvisorState) -> ShoppingAdvisorState:
        request = state["request"]
        step_results = dict(state.get("step_results", {}))

        print("🎯 步骤1: 抽取候选产品...")
        trace_event_id = self._trace_step_start(state, "candidate", "候选产品抽取")
        candidate_query = self._build_candidate_query(request)
        candidate_step = self._execute_agent_step(
            step_name="候选产品抽取",
            agent=self.candidate_agent,
            query=candidate_query,
            timeout_seconds=DEFAULT_TOOL_TIMEOUT_SECONDS,
            retries=DEFAULT_TOOL_RETRIES,
            uses_tools=True,
        )
        step_results["candidate"] = candidate_step

        if candidate_step.ok:
            try:
                candidate_result = self._parse_candidate_response(candidate_step.response)
            except Exception as exc:
                print(f"⚠️  候选产品解析失败,切换默认候选方案: {exc}")
                self._mark_step_postprocess_failure(candidate_step, exc)
                candidate_result = self._create_fallback_candidates(request)
        else:
            print("⚠️  候选产品抽取失败,切换默认候选方案")
            candidate_result = self._create_fallback_candidates(request)

        candidates = candidate_result.candidates
        print(f"候选产品: {', '.join(candidate.name for candidate in candidates)}\n")
        self._trace_step_finish(
            state=state,
            event_id=trace_event_id,
            step_key="candidate",
            step_name="候选产品抽取",
            ok=candidate_step.ok,
            message="候选产品抽取完成" if candidate_step.ok else "候选抽取失败,已使用用户原始查询作为默认候选",
            error=candidate_step.error,
            partial=not candidate_step.ok,
            step_result=candidate_step,
        )

        return {
            "candidate_result": candidate_result,
            "candidates": candidates,
            "step_results": step_results,
        }

    def _review_node(self, state: ShoppingAdvisorState) -> ShoppingAdvisorState:
        request = state["request"]
        candidates = state.get("candidates") or self._create_fallback_candidates(request).candidates
        review_query = self._build_review_query(request, candidates)
        trace_event_id = self._trace_step_start(state, "review", "测评搜集")
        review_step = self._execute_agent_step(
            step_name="测评搜集",
            agent=self.review_agent,
            query=review_query,
            timeout_seconds=DEFAULT_TOOL_TIMEOUT_SECONDS,
            retries=DEFAULT_TOOL_RETRIES,
            uses_tools=True,
        )
        review_evidence: List[EvidenceItem] = []
        if review_step.ok:
            try:
                review_evidence = self._parse_evidence_response(
                    response=review_step.response,
                    repair_agent=self.report_agent,
                    repair_label="测评证据JSON",
                    evidence_type="review",
                    candidates=candidates,
                )
                review_response = self._serialize_evidence(review_evidence)
            except Exception as exc:
                self._mark_step_postprocess_failure(review_step, exc)
                review_response = self._step_response_payload(review_step)
        else:
            review_response = self._step_response_payload(review_step)
        print(f"测评搜集状态: {review_step.status_text}")
        self._trace_step_finish(
            state=state,
            event_id=trace_event_id,
            step_key="review",
            step_name="测评搜集",
            ok=review_step.ok,
            message="测评搜集完成" if review_step.ok else "测评搜集失败,报告将标注证据边界",
            error=review_step.error,
            step_result=review_step,
        )

        return {
            "review_step": review_step,
            "review_evidence": review_evidence,
            "review_response": review_response,
        }

    def _price_node(self, state: ShoppingAdvisorState) -> ShoppingAdvisorState:
        request = state["request"]
        candidates = state.get("candidates") or self._create_fallback_candidates(request).candidates
        price_query = self._build_price_query(request, candidates)
        trace_event_id = self._trace_step_start(state, "price", "价格对比")
        price_step = self._execute_agent_step(
            step_name="价格对比",
            agent=self.price_agent,
            query=price_query,
            timeout_seconds=DEFAULT_TOOL_TIMEOUT_SECONDS,
            retries=DEFAULT_TOOL_RETRIES,
            uses_tools=True,
        )
        price_evidence: List[EvidenceItem] = []
        if price_step.ok:
            try:
                price_evidence = self._parse_evidence_response(
                    response=price_step.response,
                    repair_agent=self.report_agent,
                    repair_label="价格证据JSON",
                    evidence_type="price",
                    candidates=candidates,
                )
                price_response = self._serialize_evidence(price_evidence)
            except Exception as exc:
                self._mark_step_postprocess_failure(price_step, exc)
                price_response = self._step_response_payload(price_step)
        else:
            price_response = self._step_response_payload(price_step)
        print(f"价格对比状态: {price_step.status_text}")
        self._trace_step_finish(
            state=state,
            event_id=trace_event_id,
            step_key="price",
            step_name="价格对比",
            ok=price_step.ok,
            message="价格对比完成" if price_step.ok else "价格对比失败,报告将标注证据边界",
            error=price_step.error,
            step_result=price_step,
        )

        return {
            "price_step": price_step,
            "price_evidence": price_evidence,
            "price_response": price_response,
        }

    def _red_flag_node(self, state: ShoppingAdvisorState) -> ShoppingAdvisorState:
        request = state["request"]
        candidates = state.get("candidates") or self._create_fallback_candidates(request).candidates
        red_flag_query = self._build_red_flag_query(request, candidates)
        trace_event_id = self._trace_step_start(state, "red_flag", "避雷检测")
        red_flag_step = self._execute_agent_step(
            step_name="避雷检测",
            agent=self.red_flag_agent,
            query=red_flag_query,
            timeout_seconds=DEFAULT_TOOL_TIMEOUT_SECONDS,
            retries=DEFAULT_TOOL_RETRIES,
            uses_tools=True,
        )
        red_flag_evidence: List[EvidenceItem] = []
        if red_flag_step.ok:
            try:
                red_flag_evidence = self._parse_evidence_response(
                    response=red_flag_step.response,
                    repair_agent=self.report_agent,
                    repair_label="风险证据JSON",
                    evidence_type="risk",
                    candidates=candidates,
                )
                red_flag_response = self._serialize_evidence(red_flag_evidence)
            except Exception as exc:
                self._mark_step_postprocess_failure(red_flag_step, exc)
                red_flag_response = self._step_response_payload(red_flag_step)
        else:
            red_flag_response = self._step_response_payload(red_flag_step)
        print(f"避雷检测状态: {red_flag_step.status_text}")
        self._trace_step_finish(
            state=state,
            event_id=trace_event_id,
            step_key="red_flag",
            step_name="避雷检测",
            ok=red_flag_step.ok,
            message="避雷检测完成" if red_flag_step.ok else "避雷检测失败,报告将标注证据边界",
            error=red_flag_step.error,
            step_result=red_flag_step,
        )

        return {
            "red_flag_step": red_flag_step,
            "red_flag_evidence": red_flag_evidence,
            "red_flag_response": red_flag_response,
        }

    def _report_node(self, state: ShoppingAdvisorState) -> ShoppingAdvisorState:
        request = state["request"]
        candidates = state.get("candidates") or self._create_fallback_candidates(request).candidates
        step_results = dict(state.get("step_results", {}))
        retrieval_step_mapping = {
            "review": state.get("review_step"),
            "price": state.get("price_step"),
            "red_flag": state.get("red_flag_step"),
        }
        step_results.update(
            {
                key: step_result
                for key, step_result in retrieval_step_mapping.items()
                if step_result is not None
            }
        )
        all_evidence = [
            *state.get("review_evidence", []),
            *state.get("price_evidence", []),
            *state.get("red_flag_evidence", []),
        ]

        print("📊 步骤5: 生成避雷报告...")
        trace_event_id = self._trace_step_start(state, "report", "报告生成")
        report_query = self._build_report_query(
            request,
            candidates,
            state.get("review_response", ""),
            state.get("price_response", ""),
            state.get("red_flag_response", ""),
            step_results,
        )
        report_step = self._execute_agent_step(
            step_name="报告生成",
            agent=self.report_agent,
            query=report_query,
            timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
            retries=DEFAULT_LLM_RETRIES,
            uses_tools=False,
        )
        step_results["report"] = report_step

        if report_step.ok:
            try:
                report = self._parse_response(report_step.response, request)
                report.evidence = all_evidence
                report_scope_partial = self._normalize_report_products(
                    report,
                    candidates,
                )
                self._validate_report_citations(report, candidates)
                print(f"{'='*60}")
                print(f"✅ 避雷报告生成完成!")
                print(f"{'='*60}\n")
                self._trace_step_finish(
                    state=state,
                    event_id=trace_event_id,
                    step_key="report",
                    step_name="报告生成",
                    ok=True,
                    message=(
                        "报告生成完成,已隔离无法安全归属的产品项"
                        if report_scope_partial
                        else "报告生成完成"
                    ),
                    partial=report_scope_partial,
                    step_result=report_step,
                )
                return {
                    "step_results": step_results,
                    "report": report,
                }
            except Exception as exc:
                print(f"⚠️  报告解析失败,改为输出部分成功汇总: {exc}")
                self._mark_step_postprocess_failure(report_step, exc)

        print("⚠️  报告生成未完整成功,返回部分成功汇总结果")
        print(f"{'='*60}\n")
        self._trace_step_finish(
            state=state,
            event_id=trace_event_id,
            step_key="report",
            step_name="报告生成",
            ok=False,
            message="报告生成降级为部分成功汇总",
            error=report_step.error,
            partial=True,
            step_result=report_step,
        )
        return {
            "step_results": step_results,
            "report": self._create_partial_report(
                request,
                candidates,
                step_results,
                evidence=all_evidence,
            ),
        }

    def _trace_step_start(self, state: ShoppingAdvisorState, step_key: str, step_name: str) -> Optional[str]:
        tracer = state.get("tracer")
        if not tracer:
            return None
        try:
            return tracer.start_step(step_key, step_name)
        except Exception as exc:
            print(f"⚠️  Trace开始事件写入失败: {exc}")
            return None

    def _trace_step_finish(
        self,
        state: ShoppingAdvisorState,
        event_id: Optional[str],
        step_key: str,
        step_name: str,
        ok: bool,
        message: str,
        error: Optional[Exception] = None,
        partial: bool = False,
        step_result: Optional[StepResult] = None,
    ):
        tracer = state.get("tracer")
        if not tracer or not event_id:
            return
        try:
            tracer.finish_step(
                event_id=event_id,
                step_key=step_key,
                step_name=step_name,
                ok=ok,
                message=message,
                error=error,
                partial=partial,
                attempts=step_result.attempts if step_result else None,
                tool_call_count=step_result.tool_call_count if step_result else 0,
            )
        except Exception as exc:
            print(f"⚠️  Trace结束事件写入失败: {exc}")

    def _build_candidate_query(self, request: ShoppingRequest) -> str:
        """构建候选产品抽取查询"""
        budget_text = ""
        if request.budget_min or request.budget_max:
            budget_text = f"，预算范围{request.budget_min or '不限'}-{request.budget_max or '不限'}元"

        concern_text = f"，重点关注{', '.join(request.concerns)}" if request.concerns else ""
        brand_text = f"，品牌偏好为{', '.join(request.brand_preferences)}" if request.brand_preferences else ""

        return f"""请先为用户需求抽取最值得后续深入分析的候选产品{budget_text}{brand_text}{concern_text}。
产品品类: {request.product_name}
使用场景: {request.usage_scenario or '未指定'}
额外要求: {request.free_text_input or '无'}

请先调用 search_brave_web_search 检索主流型号、热门讨论和预算匹配信息,再返回候选产品JSON。
建议优先搜索关键词: {request.product_name} 主流 型号 推荐 预算 选购
"""

    def _build_review_query(self, request: ShoppingRequest, candidates: List[CandidateProduct]) -> str:
        """构建测评搜索查询"""
        brand_text = f",重点关注品牌：{', '.join(request.brand_preferences)}" if request.brand_preferences else ""
        concern_text = f",特别关注：{', '.join(request.concerns)}" if request.concerns else ""
        candidate_text = self._format_candidate_targets(candidates)

        query = f"""请只围绕以下候选产品搜集真实测评信息,不要混入候选列表之外的产品：
{candidate_text}

用户原始需求品类是"{request.product_name}"{brand_text}{concern_text}。
请分别在B站、小红书、知乎等平台搜索候选产品的测评内容,并按候选产品分组总结。
建议优先搜索关键词: {self._build_candidate_search_terms(candidates)} 测评 B站 小红书 知乎 真实体验"""
        return query

    def _build_price_query(self, request: ShoppingRequest, candidates: List[CandidateProduct]) -> str:
        """构建价格搜索查询"""
        budget_text = ""
        if request.budget_min or request.budget_max:
            budget_text = f",预算范围{request.budget_min or '不限'}-{request.budget_max or '不限'}元"
        candidate_text = self._format_candidate_targets(candidates)

        query = f"""请只搜索以下候选产品的价格信息{budget_text},不要引入其他型号：
{candidate_text}

请对比不同型号和不同平台的价格,并按候选产品逐个给出价格区间。
建议优先搜索关键词: {self._build_candidate_search_terms(candidates)} 价格 型号 对比 京东 淘宝 拼多多"""
        return query

    def _build_red_flag_query(self, request: ShoppingRequest, candidates: List[CandidateProduct]) -> str:
        """构建避雷搜索查询"""
        candidate_text = self._format_candidate_targets(candidates)
        query = f"""请只搜索以下候选产品的负面信息、投诉和已知问题,不要混入其他产品：
{candidate_text}

请按候选产品分别整理避雷点和投诉点。
建议优先搜索关键词: {self._build_candidate_search_terms(candidates)} 避雷 踩坑 缺点 千万别买 投诉"""
        return query

    def _build_report_query(
        self,
        request: ShoppingRequest,
        candidates: List[CandidateProduct],
        reviews: str,
        prices: str,
        red_flags: str,
        step_results: Dict[str, StepResult],
    ) -> str:
        """构建报告生成查询"""
        candidate_text = self._format_candidate_targets(candidates)
        canonical_candidate_names = json.dumps(
            [candidate.name for candidate in candidates],
            ensure_ascii=False,
        )
        step_status = self._format_step_status(step_results)
        query = f"""请根据以下信息生成"{request.product_name}"的避雷购物报告:

**用户需求:**
- 产品: {request.product_name}
- 预算: {request.budget_min or '不限'}-{request.budget_max or '不限'}元
- 品牌偏好: {', '.join(request.brand_preferences) if request.brand_preferences else '无'}
- 使用场景: {request.usage_scenario or '未指定'}
- 关注要点: {', '.join(request.concerns) if request.concerns else '无'}

**候选产品(后续所有信息都必须围绕这些产品对齐):**
{candidate_text}

**候选产品名称白名单(JSON):**
{canonical_candidate_names}

**步骤执行状态:**
{step_status}

**测评信息(来自B站/小红书/知乎等平台):**
{reviews}

**价格信息(来自京东/淘宝等电商平台):**
{prices}

**避雷信息(负面评价/投诉/已知问题):**
{red_flags}

**要求:**
1. 只能分析候选产品列表中的产品,不要新增候选列表之外的型号
2. 每个产品的测评、价格、避雷点必须和该产品名称对齐,不要跨产品混用证据
3. 分析至少2-3个主流产品/型号
4. 每个产品列出公认优缺点
5. 重点标出避雷点和争议点
6. 分析博主/测评者立场是否客观,是否恰饭
7. 给出明确的购买建议
8. 返回完整的JSON格式数据
9. 如果某些上游步骤失败,请基于成功步骤继续汇总,并在comparison_summary、final_recommendation、verdict_reason中明确说明缺失项和证据边界
10. products[].product.name必须逐字复制候选产品名称白名单中的字符串,不能添加括号、别名、系列名或“含某衍生款”等说明
11. 不要把多个候选产品合并为一个系列产品;每个products元素只能对应一个候选产品
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        return query

    def _extract_json_block(self, response: str) -> str:
        """从Agent响应中提取JSON片段"""
        if "```json" in response:
            json_start = response.find("```json") + 7
            json_end = response.find("```", json_start)
            return response[json_start:json_end].strip()
        if "```" in response:
            json_start = response.find("```") + 3
            json_end = response.find("```", json_start)
            return response[json_start:json_end].strip()
        if "{" in response and "}" in response:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            return response[json_start:json_end]
        raise ValueError("响应中未找到JSON数据")

    def _parse_candidate_response(self, response: str) -> CandidateExtractionResult:
        """解析候选产品抽取结果"""
        data = self._load_json_payload(
            response=response,
            repair_agent=self.candidate_agent,
            repair_label="候选产品JSON",
        )
        result = CandidateExtractionResult(**data)
        if not result.candidates:
            raise ValueError("候选产品为空")
        return result

    def _parse_evidence_response(
        self,
        response: str,
        repair_agent: Any,
        repair_label: str,
        evidence_type: str,
        candidates: List[CandidateProduct],
    ) -> List[EvidenceItem]:
        """解析检索节点输出,并在Python侧统一证据ID和候选产品名称。"""
        data = self._load_json_payload(
            response=response,
            repair_agent=repair_agent,
            repair_label=repair_label,
        )
        raw_evidence = data.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raise ValueError(f"{repair_label}中的evidence必须是数组")

        normalized_items = []
        product_counts: Dict[str, int] = {}
        seen_sources = set()
        per_product_limit = EVIDENCE_LIMITS.get(evidence_type)

        for index, raw_item in enumerate(raw_evidence, start=1):
            if not isinstance(raw_item, dict):
                raise ValueError(f"{repair_label}第{index}条证据不是对象")
            item_data = dict(raw_item)
            item_data["evidence_type"] = evidence_type
            product_name = self._resolve_candidate_name(
                item_data.get("product_name", ""),
                candidates,
            )
            source_key = (
                product_name,
                str(item_data.get("source_url") or "").strip().lower(),
                str(item_data.get("source_title") or "").strip().lower(),
            )
            if source_key in seen_sources:
                continue
            if (
                per_product_limit is not None
                and product_counts.get(product_name, 0) >= per_product_limit
            ):
                continue

            item_data["product_name"] = product_name
            item_data["evidence_id"] = (
                f"{evidence_type}_{len(normalized_items) + 1:03d}"
            )
            normalized_items.append(EvidenceItem(**item_data))
            product_counts[product_name] = product_counts.get(product_name, 0) + 1
            seen_sources.add(source_key)

        result = EvidenceCollectionResult(
            evidence=normalized_items,
            coverage_notes=data.get("coverage_notes", []),
        )
        return result.evidence

    def _resolve_candidate_name(
        self,
        evidence_product_name: str,
        candidates: List[CandidateProduct],
    ) -> str:
        """将证据中的产品名称对齐到候选产品标准名称。"""
        normalized_evidence_name = self._normalize_product_name(evidence_product_name)
        if not normalized_evidence_name:
            raise ValueError("证据产品名称未填写")

        exact_name_matches = [
            candidate.name
            for candidate in candidates
            if normalized_evidence_name == self._normalize_product_name(candidate.name)
        ]
        if len(exact_name_matches) == 1:
            return exact_name_matches[0]

        exact_brand_model_matches = [
            candidate.name
            for candidate in candidates
            if candidate.model
            and normalized_evidence_name
            == self._normalize_product_name(
                f"{candidate.brand} {candidate.model}"
            )
        ]
        if len(exact_brand_model_matches) == 1:
            return exact_brand_model_matches[0]

        exact_model_matches = [
            candidate.name
            for candidate in candidates
            if candidate.model
            and normalized_evidence_name == self._normalize_product_name(candidate.model)
        ]
        if len(exact_model_matches) == 1:
            return exact_model_matches[0]

        matches = []
        for candidate in candidates:
            candidate_name = self._normalize_product_name(candidate.name)
            candidate_model = self._normalize_product_name(candidate.model)
            if (
                normalized_evidence_name in candidate_name
                or candidate_name in normalized_evidence_name
                or (candidate_model and candidate_model in normalized_evidence_name)
            ):
                distance = abs(len(candidate_name) - len(normalized_evidence_name))
                matches.append((distance, candidate.name))

        if not matches:
            raise ValueError(f"证据产品不在候选列表中: {evidence_product_name or '未填写'}")
        matches.sort(key=lambda item: item[0])
        if len(matches) == 1 or matches[0][0] < matches[1][0]:
            return matches[0][1]
        raise ValueError(f"证据产品无法唯一对齐候选型号: {evidence_product_name}")

    def _normalize_product_name(self, value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())

    def _serialize_evidence(self, evidence: List[EvidenceItem]) -> str:
        return json.dumps(
            {"evidence": [item.model_dump(mode="json") for item in evidence]},
            ensure_ascii=False,
            indent=2,
        )

    def _create_fallback_candidates(self, request: ShoppingRequest) -> CandidateExtractionResult:
        """候选产品抽取失败时的默认方案"""
        fallback_name = request.product_name.strip() or "待分析产品"
        return CandidateExtractionResult(
            category=request.product_name,
            candidates=[
                CandidateProduct(
                    name=fallback_name,
                    brand=(request.brand_preferences[0] if request.brand_preferences else ""),
                    model="",
                    reason="候选抽取失败,退回到用户原始查询"
                )
            ]
        )

    def _format_candidate_targets(self, candidates: List[CandidateProduct]) -> str:
        """格式化候选产品列表,用于Prompt对齐"""
        return "\n".join(
            f"- {candidate.name}（品牌: {candidate.brand or '未知'}；型号: {candidate.model or '未知'}；入选原因: {candidate.reason or '主流候选'}）"
            for candidate in candidates
        )

    def _build_candidate_search_terms(self, candidates: List[CandidateProduct]) -> str:
        """将候选产品合并成搜索关键词"""
        return " ".join(candidate.name for candidate in candidates[:3])

    def _ensure_tool_success(self, step_name: str, response: str):
        """检测工具调用是否成功,避免在检索失败时继续生成伪完整报告"""
        failure_signals = [
            "搜索工具未能成功调用",
            "无法直接使用搜索工具",
            "无法使用搜索工具",
            "无法直接访问",
            "请确保搜索工具可用后重新提问",
            "建议你自行搜索",
            "基于我对这些产品的了解",
            "基于我知识库中已有的公开信息",
            "无法提供基于实际检索结果",
            "必须指定 action 参数或 tool_name 参数",
            "搜索工具持续出现格式错误",
            "搜索工具多次执行失败",
            "无法获取任何实时的价格信息",
            "无法获取任何搜索结果",
        ]

        timeout_signals = [
            "timeout",
            "timed out",
            "超时",
            "request timeout",
        ]

        if any(signal.lower() in response.lower() for signal in timeout_signals):
            raise ToolTimeoutError(step_name, f"{step_name}失败: 搜索工具调用超时")
        if any(signal in response for signal in failure_signals):
            raise ToolExecutionError(step_name, f"{step_name}失败: 搜索工具未成功执行")

    def _parse_response(self, response: str, request: ShoppingRequest) -> ShoppingReport:
        """解析Agent响应"""
        data = self._load_json_payload(
            response=response,
            repair_agent=self.report_agent,
            repair_label="购物报告JSON",
        )
        return ShoppingReport(**data)

    def _normalize_report_products(
        self,
        report: ShoppingReport,
        candidates: List[CandidateProduct],
    ) -> bool:
        """将报告产品确定性对齐到候选白名单,隔离无法安全归属的系列汇总项。"""
        candidate_by_name = {
            candidate.name: candidate
            for candidate in candidates
        }
        evidence_by_id = {
            item.evidence_id: item
            for item in report.evidence
        }
        normalized_products: List[ProductAnalysis] = []
        seen_candidates = set()
        warnings = list(report.citation_warnings)
        scope_partial = False

        for analysis in report.products:
            original_name = analysis.product.name
            candidate_name = self._resolve_report_candidate(
                analysis,
                candidates,
                evidence_by_id,
            )
            if candidate_name is None:
                warnings.append(
                    f"已移除无法唯一对齐候选型号的报告产品: {original_name}"
                )
                scope_partial = True
                continue

            normalized_key = self._normalize_product_name(candidate_name)
            if normalized_key in seen_candidates:
                warnings.append(
                    f"已移除重复的报告产品分析: {original_name} -> {candidate_name}"
                )
                scope_partial = True
                continue

            candidate = candidate_by_name[candidate_name]
            if original_name != candidate.name:
                warnings.append(
                    f"报告产品名称已归一化: {original_name} -> {candidate.name}"
                )
            analysis.product.name = candidate.name
            analysis.product.brand = candidate.brand or analysis.product.brand
            analysis.product.model = candidate.model or analysis.product.model
            normalized_products.append(analysis)
            seen_candidates.add(normalized_key)

        if not normalized_products:
            raise CitationValidationError(
                ["报告中的产品均无法唯一对齐候选产品白名单"]
            )

        report.products = normalized_products
        report.citation_warnings = warnings
        return scope_partial

    def _resolve_report_candidate(
        self,
        analysis: ProductAnalysis,
        candidates: List[CandidateProduct],
        evidence_by_id: Dict[str, EvidenceItem],
    ) -> Optional[str]:
        """结合报告产品字段和引用证据确定唯一候选产品。"""
        cited_product_names = {
            evidence_by_id[evidence_id].product_name
            for evidence_id in self._collect_analysis_evidence_ids(analysis)
            if evidence_id in evidence_by_id
        }
        resolved_cited_names = set()
        for cited_name in cited_product_names:
            try:
                resolved_cited_names.add(
                    self._resolve_candidate_name(cited_name, candidates)
                )
            except ValueError:
                return None
        if len(resolved_cited_names) > 1:
            return None

        candidate_signals = [
            analysis.product.name,
            f"{analysis.product.brand} {analysis.product.model}".strip(),
            analysis.product.model,
        ]
        resolved_names = set()
        for signal in candidate_signals:
            if not signal:
                continue
            if signal == analysis.product.name and self._is_grouped_product_name(signal):
                continue
            try:
                resolved_names.add(
                    self._resolve_candidate_name(signal, candidates)
                )
            except ValueError:
                continue

        if len(resolved_names) > 1:
            return None
        if len(resolved_names) == 1 and len(resolved_cited_names) == 1:
            resolved_name = next(iter(resolved_names))
            cited_name = next(iter(resolved_cited_names))
            return resolved_name if resolved_name == cited_name else None
        if len(resolved_names) == 1:
            return next(iter(resolved_names))
        if len(resolved_cited_names) == 1:
            return next(iter(resolved_cited_names))

        return None

    def _is_grouped_product_name(self, value: str) -> bool:
        grouped_markers = (
            "（含",
            "(含",
            "系列",
            "衍生款",
            "等型号",
            "等版本",
        )
        return any(marker in value for marker in grouped_markers)

    def _collect_analysis_evidence_ids(
        self,
        analysis: ProductAnalysis,
    ) -> set[str]:
        evidence_ids = {
            *analysis.product.price_evidence_ids,
            *analysis.product.spec_evidence_ids,
            *analysis.verdict_evidence_ids,
        }
        for review in analysis.reviews:
            evidence_ids.update(review.evidence_ids)
        for citation_map in (
            analysis.pro_evidence_ids,
            analysis.con_evidence_ids,
            analysis.red_flag_evidence_ids,
            analysis.controversy_evidence_ids,
        ):
            for mapped_ids in citation_map.values():
                evidence_ids.update(mapped_ids)
        return evidence_ids

    def _validate_report_citations(
        self,
        report: ShoppingReport,
        candidates: List[CandidateProduct],
    ):
        """确定性校验报告中的证据引用、类型和产品归因。"""
        evidence_by_id: Dict[str, EvidenceItem] = {}
        hard_issues: List[str] = []
        warnings: List[str] = list(report.citation_warnings)

        for item in report.evidence:
            if item.evidence_id in evidence_by_id:
                hard_issues.append(f"重复证据ID: {item.evidence_id}")
            evidence_by_id[item.evidence_id] = item

        candidate_names = {
            self._normalize_product_name(candidate.name)
            for candidate in candidates
        }

        def validate_ids(
            field_name: str,
            evidence_ids: List[str],
            allowed_types: Optional[set[str]] = None,
            product_name: Optional[str] = None,
        ):
            for evidence_id in evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    hard_issues.append(f"{field_name}引用不存在的证据ID: {evidence_id}")
                    continue
                if allowed_types and evidence.evidence_type not in allowed_types:
                    hard_issues.append(
                        f"{field_name}引用了错误类型证据: {evidence_id}({evidence.evidence_type})"
                    )
                if product_name and not self._same_product(product_name, evidence.product_name):
                    hard_issues.append(
                        f"{field_name}跨产品引用: {product_name} -> {evidence.product_name}({evidence_id})"
                    )

        for analysis_index, analysis in enumerate(report.products):
            product_name = analysis.product.name
            if self._normalize_product_name(product_name) not in candidate_names:
                hard_issues.append(f"报告包含候选范围外产品: {product_name}")

            validate_ids(
                f"products[{analysis_index}].price_evidence_ids",
                analysis.product.price_evidence_ids,
                {"price"},
                product_name,
            )
            validate_ids(
                f"products[{analysis_index}].spec_evidence_ids",
                analysis.product.spec_evidence_ids,
                {"price", "review"},
                product_name,
            )
            if (
                analysis.product.price_range
                and analysis.product.price_range not in {"信息不足", "暂无数据"}
                and not analysis.product.price_evidence_ids
            ):
                warnings.append(f"{product_name}的价格区间没有引用价格证据")

            for review_index, review in enumerate(analysis.reviews):
                validate_ids(
                    f"products[{analysis_index}].reviews[{review_index}].evidence_ids",
                    review.evidence_ids,
                    {"review", "risk"},
                    product_name,
                )
                if not review.evidence_ids:
                    warnings.append(f"{product_name}的测评来源“{review.title}”没有引用证据")

            self._validate_claim_map(
                field_name=f"products[{analysis_index}].pro_evidence_ids",
                claims=analysis.common_pros,
                citation_map=analysis.pro_evidence_ids,
                allowed_types={"review", "price"},
                product_name=product_name,
                validate_ids=validate_ids,
                hard_issues=hard_issues,
                warnings=warnings,
            )
            self._validate_claim_map(
                field_name=f"products[{analysis_index}].con_evidence_ids",
                claims=analysis.common_cons,
                citation_map=analysis.con_evidence_ids,
                allowed_types={"review", "price", "risk"},
                product_name=product_name,
                validate_ids=validate_ids,
                hard_issues=hard_issues,
                warnings=warnings,
            )
            self._validate_claim_map(
                field_name=f"products[{analysis_index}].red_flag_evidence_ids",
                claims=analysis.red_flags,
                citation_map=analysis.red_flag_evidence_ids,
                allowed_types={"risk", "review"},
                product_name=product_name,
                validate_ids=validate_ids,
                hard_issues=hard_issues,
                warnings=warnings,
            )
            self._validate_claim_map(
                field_name=f"products[{analysis_index}].controversy_evidence_ids",
                claims=analysis.controversy_points,
                citation_map=analysis.controversy_evidence_ids,
                allowed_types={"review", "risk"},
                product_name=product_name,
                validate_ids=validate_ids,
                hard_issues=hard_issues,
                warnings=warnings,
            )
            validate_ids(
                f"products[{analysis_index}].verdict_evidence_ids",
                analysis.verdict_evidence_ids,
                {"review", "price", "risk"},
                product_name,
            )
            if analysis.verdict_reason and not analysis.verdict_evidence_ids:
                warnings.append(f"{product_name}的产品结论没有引用证据")

        validate_ids(
            "comparison_evidence_ids",
            report.comparison_evidence_ids,
            {"review", "price", "risk"},
        )
        validate_ids(
            "recommendation_evidence_ids",
            report.recommendation_evidence_ids,
            {"review", "price", "risk"},
        )
        validate_ids(
            "budget_evidence_ids",
            report.budget_evidence_ids,
            {"price"},
        )

        if report.evidence and report.comparison_summary and not report.comparison_evidence_ids:
            warnings.append("横向对比总结没有引用证据")
        if report.evidence and report.final_recommendation and not report.recommendation_evidence_ids:
            warnings.append("最终购买建议没有引用证据")
        if report.budget_advice and report.evidence and not report.budget_evidence_ids:
            warnings.append("预算建议没有引用价格证据")

        report.citation_warnings = warnings
        if hard_issues:
            raise CitationValidationError(hard_issues)

    def _validate_claim_map(
        self,
        field_name: str,
        claims: List[str],
        citation_map: Dict[str, List[str]],
        allowed_types: set[str],
        product_name: str,
        validate_ids: Any,
        hard_issues: List[str],
        warnings: List[str],
    ):
        claim_set = set(claims)
        for claim, evidence_ids in citation_map.items():
            if claim not in claim_set:
                hard_issues.append(f"{field_name}包含不存在的结论文本: {claim}")
            validate_ids(field_name, evidence_ids, allowed_types, product_name)
        for claim in claims:
            if not citation_map.get(claim):
                warnings.append(f"{product_name}的结论“{claim}”没有引用证据")

    def _same_product(self, left: str, right: str) -> bool:
        normalized_left = self._normalize_product_name(left)
        normalized_right = self._normalize_product_name(right)
        return (
            normalized_left == normalized_right
            or normalized_left in normalized_right
            or normalized_right in normalized_left
        )

    def _load_json_payload(self, response: str, repair_agent: Any, repair_label: str) -> Dict[str, Any]:
        """解析 JSON,失败后执行一次 repair pass"""
        try:
            json_str = self._extract_json_block(response)
            return json.loads(json_str)
        except Exception as first_error:
            print(f"⚠️  {repair_label}首次解析失败,尝试 repair pass: {first_error}")

        repaired_response = self._repair_json_response(repair_agent, response, repair_label)

        try:
            json_str = self._extract_json_block(repaired_response)
            return json.loads(json_str)
        except Exception as second_error:
            raise JsonRepairError(f"{repair_label} repair pass 失败: {second_error}") from second_error

    def _repair_json_response(self, repair_agent: Any, response: str, repair_label: str) -> str:
        """对非结构化或损坏的 JSON 响应执行一次修复"""
        repair_query = f"""你只做 JSON 修复,不要补充新事实。

目标:
- 将下面内容修复为合法 JSON
- 只能保留原始响应里已经出现的信息
- 缺失字段用空数组、null、空字符串或'信息不足'保守补齐
- 只返回 JSON,不要输出解释文字或代码块外文本

待修复内容:
{response}
"""
        repair_step = self._execute_agent_step(
            step_name=f"{repair_label}修复",
            agent=repair_agent,
            query=repair_query,
            timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
            retries=0,
            uses_tools=False,
        )
        if not repair_step.ok:
            raise JsonRepairError(f"{repair_label}修复失败: {repair_step.error_summary}")
        return repair_step.response

    def _execute_agent_step(
        self,
        step_name: str,
        agent: Any,
        query: str,
        timeout_seconds: int,
        retries: int,
        uses_tools: bool,
    ) -> StepResult:
        """执行单个 Agent 步骤,包含超时、重试和错误分类"""
        last_error: Optional[Exception] = None
        attempts: List[StepAttemptTrace] = []
        total_tool_calls = 0

        for attempt in range(1, retries + 2):
            attempt_started_at = time.perf_counter()
            metrics = AgentRunMetrics()
            try:
                run_result = self._run_agent_with_timeout(
                    step_name=step_name,
                    agent=agent,
                    query=query,
                    timeout_seconds=timeout_seconds,
                    uses_tools=uses_tools,
                )
                response = run_result.response
                metrics = run_result.metrics
                if uses_tools:
                    self._ensure_tool_success(step_name, response)
                duration_ms = int(
                    (time.perf_counter() - attempt_started_at) * 1000
                )
                total_tool_calls += metrics.tool_call_count
                attempts.append(
                    StepAttemptTrace(
                        attempt=attempt,
                        status="success",
                        duration_ms=duration_ms,
                        tool_call_count=metrics.tool_call_count,
                        model_duration_ms=metrics.model_duration_ms,
                        search_calls=metrics.search_calls,
                    )
                )
                return StepResult(
                    name=step_name,
                    ok=True,
                    response=response,
                    attempts=attempts,
                    tool_call_count=total_tool_calls,
                )
            except Exception as exc:
                last_error = exc
                metrics = getattr(exc, "metrics", metrics)
                duration_ms = int(
                    (time.perf_counter() - attempt_started_at) * 1000
                )
                total_tool_calls += metrics.tool_call_count
                attempts.append(
                    StepAttemptTrace(
                        attempt=attempt,
                        status="failed",
                        duration_ms=duration_ms,
                        tool_call_count=metrics.tool_call_count,
                        model_duration_ms=metrics.model_duration_ms,
                        search_calls=metrics.search_calls,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                print(f"⚠️  {step_name}第{attempt}次执行失败: {exc}")

        return StepResult(
            name=step_name,
            ok=False,
            error=last_error,
            attempts=attempts,
            tool_call_count=total_tool_calls,
        )

    def _mark_step_postprocess_failure(
        self,
        step_result: StepResult,
        error: Exception,
    ):
        """将 JSON 解析、证据对齐或引用校验失败同步到最后一次尝试。"""
        step_result.ok = False
        step_result.error = error
        if not step_result.attempts:
            return
        last_attempt = step_result.attempts[-1]
        last_attempt.status = "failed"
        last_attempt.error_type = type(error).__name__
        last_attempt.error_message = str(error)

    def _run_agent_with_timeout(
        self,
        step_name: str,
        agent: Any,
        query: str,
        timeout_seconds: int,
        uses_tools: bool,
    ) -> AgentRunResult:
        """执行 Agent,检索 Agent 使用可取消异步超时,普通模型保留线程隔离。"""
        if uses_tools and hasattr(agent, "run_with_timeout"):
            try:
                return agent.run_with_timeout(query, timeout_seconds)
            except SearchPipelineTimeoutError as exc:
                raise ToolTimeoutError(
                    step_name,
                    f"{step_name}超过{timeout_seconds}秒未完成",
                    metrics=exc.metrics,
                ) from exc
            except Exception as exc:
                classified_error = self._classify_runtime_error(
                    step_name,
                    exc,
                    uses_tools,
                )
                classified_error.metrics = getattr(
                    exc,
                    "metrics",
                    AgentRunMetrics(),
                )
                raise classified_error from exc

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(agent.run, query)
        try:
            response = future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            error_cls = ToolTimeoutError if uses_tools else ModelTimeoutError
            raise error_cls(
                step_name,
                f"{step_name}超过{timeout_seconds}秒未完成",
            ) from exc
        except Exception as exc:
            classified_error = self._classify_runtime_error(
                step_name,
                exc,
                uses_tools,
            )
            raise classified_error from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return AgentRunResult(response=str(response))

    def _classify_runtime_error(self, step_name: str, error: Exception, uses_tools: bool) -> StepExecutionError:
        """根据异常文本粗分类为工具异常或模型异常"""
        summary = self._summarize_exception(error)
        message = summary.lower()
        timeout_keywords = ["timeout", "timed out", "超时"]
        tool_keywords = ["tool", "mcp", "search", "transport", "server", "call_tool"]

        if any(keyword in message for keyword in timeout_keywords):
            error_cls = ToolTimeoutError if uses_tools else ModelTimeoutError
            return error_cls(step_name, summary)

        if uses_tools and any(keyword in message for keyword in tool_keywords):
            return ToolExecutionError(step_name, summary)

        if uses_tools:
            return ToolExecutionError(step_name, summary)

        return ModelExecutionError(step_name, summary)

    def _summarize_exception(self, error: BaseException) -> str:
        """展开 ExceptionGroup,避免只看到 TaskGroup 的外壳错误。"""
        messages: List[str] = []

        def visit(exc: BaseException):
            if isinstance(exc, BaseExceptionGroup):
                for child in exc.exceptions:
                    visit(child)
                return
            messages.append(f"{type(exc).__name__}: {exc}")

        visit(error)
        return " | ".join(messages) if messages else f"{type(error).__name__}: {error}"

    def _step_response_payload(self, step_result: StepResult) -> str:
        """将步骤结果转为可交给下游汇总的文本"""
        if step_result.ok:
            return step_result.response
        return f"[{step_result.name}失败] {step_result.error_summary or '未知错误'}"

    def _format_step_status(self, step_results: Dict[str, StepResult]) -> str:
        """格式化步骤执行状态"""
        lines = []
        for step_key in ["candidate", "review", "price", "red_flag"]:
            step_result = step_results.get(step_key)
            if not step_result:
                continue
            if step_result.ok:
                lines.append(f"- {step_result.name}: 成功")
            else:
                lines.append(f"- {step_result.name}: 失败 ({step_result.error_summary or '未知错误'})")
        return "\n".join(lines) if lines else "- 无上游步骤状态"

    def _create_partial_report(
        self,
        request: ShoppingRequest,
        candidates: List[CandidateProduct],
        step_results: Dict[str, StepResult],
        evidence: Optional[List[EvidenceItem]] = None,
    ) -> ShoppingReport:
        """在部分步骤成功时,返回可恢复的部分成功报告"""
        successful_steps = [
            step_result.name
            for step_result in step_results.values()
            if step_result.ok and step_result.name != "报告生成"
        ]
        failed_steps = [
            f"{step_result.name}({step_result.error_summary or '未知错误'})"
            for step_result in step_results.values()
            if not step_result.ok
        ]

        if not candidates:
            candidates = self._create_fallback_candidates(request).candidates

        products = []
        for candidate in candidates:
            products.append(
                ProductAnalysis(
                    product=Product(
                        name=candidate.name,
                        brand=candidate.brand or "未知",
                        model=candidate.model or "未知",
                        price_range="信息不足",
                    ),
                    common_pros=["已有部分检索结果,但结构化汇总未完整完成"],
                    common_cons=["存在步骤失败,当前结论不完整"],
                    red_flags=["请结合成功步骤的原始检索信息复核后再决策"],
                    controversy_points=failed_steps[:3],
                    verdict="看需求",
                    verdict_reason="部分步骤成功,但报告未能完整结构化,建议结合已成功检索结果保守判断",
                )
            )

        comparison_summary = (
            f"本次执行为部分成功。成功步骤: {', '.join(successful_steps) if successful_steps else '无'}；"
            f"失败步骤: {', '.join(failed_steps) if failed_steps else '无'}。"
            "系统已尽量保留候选产品和成功检索阶段的信息,但缺失步骤对应字段需要保守解读。"
        )
        final_recommendation = (
            "当前返回的是可恢复的部分成功结果。"
            "如果需要稳定购买建议,请优先重试失败步骤,尤其是价格对比、避雷检测和最终报告生成。"
        )

        general_tips = [
            "本次结果包含部分失败步骤,不要把缺失字段当成明确负面或明确正面结论",
            "优先复核失败步骤对应的信息源,尤其是价格和负面反馈",
            "若多次出现 JSON 修复或超时错误,建议降低查询复杂度后重试",
        ]

        return ShoppingReport(
            query=request.product_name,
            category=request.product_name,
            products=products,
            comparison_summary=comparison_summary,
            final_recommendation=final_recommendation,
            budget_advice="部分步骤失败,预算建议仅供参考,请重试后确认实时价格",
            general_tips=general_tips,
            evidence=evidence or [],
            citation_warnings=["报告生成或引用校验失败,当前为降级结果"],
        )

    def _create_fallback_report(self, request: ShoppingRequest) -> ShoppingReport:
        """创建备用报告(当Agent失败时)"""
        return ShoppingReport(
            query=request.product_name,
            category=request.product_name,
            products=[
                ProductAnalysis(
                    product=Product(
                        name=f"{request.product_name}（待分析）",
                        brand="未知",
                        model="未知",
                        price_range="暂无数据"
                    ),
                    common_pros=["暂无数据,请稍后重试"],
                    common_cons=["暂无数据,请稍后重试"],
                    red_flags=["分析服务暂时不可用,请稍后重试"],
                    controversy_points=[],
                    verdict="待定",
                    verdict_reason="由于服务异常,暂时无法给出建议,请稍后重试"
                )
            ],
            comparison_summary="分析服务暂时不可用,请稍后重试",
            final_recommendation="请稍后重试,或手动搜索相关测评信息进行判断",
            general_tips=[
                "建议多看不同博主的测评,交叉验证",
                "注意区分恰饭内容和真实测评",
                "重点关注售后评价和长期使用体验",
                "不要只看好评,差评往往更有参考价值"
            ]
        )


# 全局多智能体系统实例
_shopping_advisor = None


def get_shopping_advisor() -> MultiAgentShoppingAdvisor:
    """获取多智能体购物顾问实例(单例模式)"""
    global _shopping_advisor

    if _shopping_advisor is None:
        _shopping_advisor = MultiAgentShoppingAdvisor()

    return _shopping_advisor
