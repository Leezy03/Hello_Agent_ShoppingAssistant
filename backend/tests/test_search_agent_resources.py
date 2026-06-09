import asyncio
import time
from types import SimpleNamespace

import pytest

import app.agents.shopping_advisor_agent as shopping_agent_module
from app.agents.shopping_advisor_agent import (
    AgentRunMetrics,
    LangGraphAgent,
    SearchPipelineTimeoutError,
)


class FakeAssistantMessage:
    content = "检索完成"


class FakeCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=FakeAssistantMessage())]
        )


class FakeAsyncOpenAI:
    exited = False

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions())

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        type(self).exited = True


class DelayedSearchTool:
    def __init__(self, delay=0.1):
        self.delay = delay
        self.payloads = []

    async def ainvoke(self, payload):
        self.payloads.append(payload)
        await asyncio.sleep(self.delay)
        return f"result for {payload['query']}"


class EmptySearchTool:
    async def ainvoke(self, payload):
        raise RuntimeError("No web results found")


def test_synthesis_closes_async_openai_client(monkeypatch):
    FakeAsyncOpenAI.exited = False
    monkeypatch.setattr(shopping_agent_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(
        shopping_agent_module,
        "get_llm_config",
        lambda: {
            "api_key": "test-key",
            "base_url": "https://example.com/v1",
            "timeout": 1,
            "model": "test-model",
        },
    )
    agent = LangGraphAgent("test", object(), "system prompt", uses_search=True)
    metrics = AgentRunMetrics()

    result = asyncio.run(
        agent._synthesize_search_results(
            "test query",
            [{"query": "test query", "content": "search result"}],
            metrics,
        )
    )

    assert result == "检索完成"
    assert metrics.model_duration_ms is not None
    assert FakeAsyncOpenAI.exited is True


def test_search_queries_run_concurrently():
    agent = LangGraphAgent("test", object(), "system prompt", uses_search=True)
    search_tool = DelayedSearchTool(delay=0.15)
    metrics = AgentRunMetrics()

    started_at = time.perf_counter()
    results = asyncio.run(
        agent._run_concurrent_searches(
            search_tool,
            ["query 1", "query 2", "query 3"],
            metrics,
        )
    )
    elapsed = time.perf_counter() - started_at

    assert elapsed < 0.35
    assert len(results) == 3
    assert search_tool.payloads == [
        {"query": "query 1"},
        {"query": "query 2"},
        {"query": "query 3"},
    ]
    assert all(call.status == "success" for call in metrics.search_calls)
    assert all(call.duration_ms is not None for call in metrics.search_calls)


def test_empty_searches_are_recorded_and_metrics_are_appended():
    agent = LangGraphAgent("test", object(), "system prompt", uses_search=True)
    metrics = AgentRunMetrics()

    first_results = asyncio.run(
        agent._run_concurrent_searches(
            EmptySearchTool(),
            ["query 1", "query 2"],
            metrics,
        )
    )
    second_results = asyncio.run(
        agent._run_concurrent_searches(
            EmptySearchTool(),
            ["fallback query"],
            metrics,
        )
    )

    assert first_results == []
    assert second_results == []
    assert metrics.tool_call_count == 3
    assert [call.status for call in metrics.search_calls] == [
        "empty",
        "empty",
        "empty",
    ]
    assert all(call.error_type == "NoResults" for call in metrics.search_calls)


def test_wait_for_cancels_search_pipeline():
    agent = LangGraphAgent("test", object(), "system prompt", uses_search=True)
    cancelled = False

    async def slow_pipeline(query, metrics):
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    agent._arun_search_agent = slow_pipeline

    with pytest.raises(SearchPipelineTimeoutError):
        agent.run_with_timeout("test query", timeout_seconds=0.01)

    assert cancelled is True


def test_wait_for_maps_mcp_cleanup_exception_group_to_timeout():
    agent = LangGraphAgent("test", object(), "system prompt", uses_search=True)

    async def cleanup_failure_after_cancel(query, metrics):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            raise ExceptionGroup(
                "mcp cleanup failed",
                [RuntimeError("broken stdio resource")],
            ) from exc

    agent._arun_search_agent = cleanup_failure_after_cancel

    with pytest.raises(SearchPipelineTimeoutError):
        agent.run_with_timeout("test query", timeout_seconds=0.01)


def test_search_plan_is_bounded_per_agent_type():
    query = """请搜索候选产品:
- 产品A（品牌: A；型号: A1；入选原因: 测试）
- 产品B（品牌: B；型号: B1；入选原因: 测试）
- 产品C（品牌: C；型号: C1；入选原因: 测试）
"""

    review_agent = LangGraphAgent(
        "review",
        object(),
        "prompt",
        uses_search=True,
        search_kind="review",
    )
    price_agent = LangGraphAgent(
        "price",
        object(),
        "prompt",
        uses_search=True,
        search_kind="price",
    )
    risk_agent = LangGraphAgent(
        "risk",
        object(),
        "prompt",
        uses_search=True,
        search_kind="risk",
    )

    assert len(review_agent._plan_search_queries(query)) == 6
    assert len(price_agent._plan_search_queries(query)) == 3
    assert len(risk_agent._plan_search_queries(query)) == 6


def test_search_plan_uses_compact_unquoted_product_names():
    query = """请搜索候选产品:
- 联想 拯救者R7000 游戏笔记本电脑（品牌: 联想；型号: R7000；入选原因: 测试）
- 戴尔 G5 游戏笔记本电脑（品牌: 戴尔；型号: G5；入选原因: 测试）
"""
    agent = LangGraphAgent(
        "review",
        object(),
        "prompt",
        uses_search=True,
        search_kind="review",
    )

    planned = agent._plan_search_queries(query)
    fallback = agent._plan_fallback_search_queries(query, planned)

    assert planned == [
        "联想 拯救者R7000 评测 使用体验",
        "联想 拯救者R7000 缺点 用户评价",
        "戴尔 G5 评测 使用体验",
        "戴尔 G5 缺点 用户评价",
    ]
    assert fallback == ["联想 拯救者R7000", "戴尔 G5"]
    assert all('"' not in search_query for search_query in planned)


def test_empty_evidence_response_is_valid_json():
    agent = LangGraphAgent(
        "risk",
        object(),
        "prompt",
        uses_search=True,
        search_kind="risk",
    )

    payload = shopping_agent_module.json.loads(agent._empty_evidence_response())

    assert payload["evidence"] == []
    assert "不得据此作确定性结论" in payload["coverage_notes"][0]
