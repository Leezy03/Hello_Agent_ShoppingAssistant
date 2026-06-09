# AI避雷购物助手

基于 LangGraph + FastAPI + Vue 3 构建的多智能体购物决策系统，用来做“先检索、再比对、再避雷”的辅助分析。

项目当前的核心目标不是给出泛泛推荐，而是围绕用户的预算、品牌偏好和关注点，尽量产出一份更保守、更可落地的购买建议：
- 先抽取候选产品，避免后续证据串台
- 聚合多平台测评、价格和负面反馈
- 输出推荐、不推荐、看需求三类结构化结论
- 在部分步骤失败时仍尽量保留可恢复的结果

## 当前能力

- 5 个 Agent 协同工作：候选产品抽取、测评搜集、价格对比、避雷检测、报告生成
- 执行拓扑为：候选抽取 -> LangGraph fan-out 并行执行测评/价格/避雷检索 -> fan-in 汇总生成报告
- 检索阶段使用 Brave Search MCP Server，通过 LangChain MCP adapters 接入搜索能力
- 报告输出包含：候选产品、测评来源、优缺点、避雷点、争议点、预算建议、最终建议
- 支持可恢复异常处理：步骤级失败、重试、超时分类、JSON repair pass、部分成功汇总
- 支持后台任务状态与节点级 Trace：返回 task_id，记录每个 Agent 节点的状态、耗时和错误信息
- 前端支持结果页展示与导出图片/PDF

## 技术栈

- 后端：Python、FastAPI、Pydantic、LangGraph、LangChain
- Agent 工具：LangChain MCP adapters、Brave Search MCP Server
- 前端：Vue 3、TypeScript、Vite、Ant Design Vue
- 其他前端依赖：Axios、html2canvas、jsPDF

## 项目结构

```text
langgraph-shop-assistant/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   └── shopping_advisor_agent.py
│   │   ├── api/
│   │   │   ├── main.py
│   │   │   └── routes/
│   │   │       └── shopping.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   ├── services/
│   │   │   ├── llm_service.py
│   │   │   └── task_manager.py
│   │   └── config.py
│   ├── requirements.txt
│   ├── requirements-test.txt
│   ├── pytest.ini
│   ├── package.json
│   ├── run.py
│   ├── evals/
│   │   ├── shopping_eval.py
│   │   └── shopping_eval_cases.json
│   ├── tests/
│   │   ├── test_api_routes.py
│   │   ├── test_shopping_advisor_workflow.py
│   │   └── test_shopping_advisor_resilience.py
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── services/
│   │   │   └── api.ts
│   │   ├── types/
│   │   │   └── index.ts
│   │   ├── views/
│   │   │   ├── Home.vue
│   │   │   └── Result.vue
│   │   ├── App.vue
│   │   └── main.ts
│   ├── package.json
│   ├── vite.config.ts
│   └── .env.example
└── README.md
```

## 执行链路

后端核心编排位于 backend/app/agents/shopping_advisor_agent.py。

当前执行顺序如下：

1. 候选产品抽取 Agent
2. LangGraph 从候选节点 fan-out 到测评搜集 Agent、价格对比 Agent、避雷检测 Agent，并行执行三个检索节点
3. 报告生成 Agent 汇总输出 ShoppingReport

并行化后的总耗时通常近似为：

$$
T \approx T_{candidate} + \max(T_{review}, T_{price}, T_{redflag}) + T_{report}
$$

这比早期的全串行执行更适合搜索型场景。

## 异常处理策略

当前实现不是“一步失败整单 fallback”，而是尽量可恢复：

- 每个 Agent run 都有独立的步骤结果与错误分类
- 工具调用和模型调用分别支持超时与重试
- JSON 解析失败后会触发一次 repair pass
- 工具超时与模型超时分别映射为独立错误类型
- 当部分步骤失败时，系统仍会汇总成功步骤并返回部分成功结果

## 快速开始

### 1. 启动后端

Bash：

```powershell
cd backend
pip install -r requirements.txt
copy .env.example .env
python run.py
```

后端默认地址：
- http://localhost:8000
- Swagger 文档：http://localhost:8000/docs
- ReDoc 文档：http://localhost:8000/redoc

### 2. 启动前端

```powershell
cd frontend
npm install
copy .env.example .env
npm run dev
```

前端默认地址：
- http://localhost:5173

## 测试与校验

### 后端测试

后端测试使用 pytest，测试入口位于 backend/tests，当前覆盖：

- API 路由返回成功与异常响应
- 购物分析工作流的成功路径、候选 fallback、检索阶段并行执行
- 重试、工具超时、模型超时、JSON repair pass、部分成功返回等可恢复异常链路
- 后台任务状态查询、节点级 Trace 写入与读取
- 评测集可解析性、报告结构完整度和关键词覆盖评分逻辑

运行方式：

```powershell
cd backend
pip install -r requirements.txt
pip install -r requirements-test.txt
pytest
```

说明：
- 测试用例使用 stub/fake agent，不需要真实 LLM API Key 或 Brave Search API Key
- pytest 配置见 backend/pytest.ini，默认收集 backend/tests 下的 test_*.py

### Agent 评测集

评测集位于 backend/evals/shopping_eval_cases.json，当前包含 10 个购物场景，覆盖手机、洗地机、扫地机器人、空气炸锅、笔记本、降噪耳机、儿童安全座椅、电动牙刷、投影仪、路由器等品类。

评测脚本位于 backend/evals/shopping_eval.py，评估维度包括：

- 报告 schema 是否可解析为 ShoppingReport
- 产品数量是否满足 case 预期
- 品类关键词、用户关注点和风险关键词覆盖情况
- 核心章节是否完整：横向对比、最终建议、优缺点、避雷点、通用建议
- verdict 是否落在推荐/不推荐/看需求/待定等受控结论范围内
- 是否出现“建议自行搜索”“无法访问互联网”等禁用表达

只校验评测集格式：

```powershell
cd backend
python -m evals.shopping_eval --validate-only
```

运行真实 Agent 评测（需要 LLM 与 SEARCH_API_KEY 配置）：

```powershell
cd backend
python -m evals.shopping_eval --live --limit 3 --output evals/results/latest.json
```

说明：evals/results/ 已加入 backend/.gitignore，避免提交本地评测输出。

### 前端校验

前端当前未配置单元测试脚本，可先使用 TypeScript 与 Vite 构建作为基础校验：

```powershell
cd frontend
npm install
npm run build
```

## 环境变量

后端变量见 backend/.env.example：

- LLM_MODEL_ID：模型名称，例如 deepseek-chat、gpt-4o-mini
- LLM_API_KEY：模型 API Key
- LLM_BASE_URL：模型服务地址
- LLM_TIMEOUT：可选，请求超时秒数
- SEARCH_API_KEY：Brave Search API Key
- HOST：后端监听地址
- PORT：后端端口
- APP_DEBUG：是否启用调试模式
- LOG_LEVEL：日志级别
- CORS_ORIGINS：允许跨域的前端地址，多个值用逗号分隔
- TASK_STORE_BACKEND：任务状态存储后端，memory 或 redis
- REDIS_URL：Redis 连接地址，例如 redis://localhost:6379/0
- TASK_STATE_TTL_SECONDS：任务状态过期时间，默认 86400 秒

说明：
- 代码中的配置项名是 search_api_key，但环境变量实际读取的是 SEARCH_API_KEY
- LLM 侧会优先读取 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL_ID，也兼容 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL 形式
- TASK_STORE_BACKEND=memory 适合本地开发；TASK_STORE_BACKEND=redis 适合多 worker 或多实例部署，任务状态、报告和节点 Trace 会写入 Redis 并按 TTL 自动过期

本地 Redis 示例：

```powershell
docker run --name shopping-redis -p 6379:6379 -d redis:7
```

然后在 backend/.env 中设置：

```text
TASK_STORE_BACKEND=redis
REDIS_URL=redis://localhost:6379/0
TASK_STATE_TTL_SECONDS=86400
```

前端变量见 frontend/.env.example：

- VITE_API_BASE_URL：后端 API 地址，默认可设为 http://localhost:8000

## API 端点

- GET /
  - 返回服务基础信息
- GET /health
  - 应用级健康检查
- POST /api/shopping/analyze
  - 输入：ShoppingRequest
  - 输出：ShoppingReportResponse
- POST /api/shopping/tasks
  - 创建后台购物分析任务
  - 输出：task_id
- GET /api/shopping/tasks/{task_id}
  - 查询任务状态、进度、报告结果与节点级 Trace
- GET /api/shopping/tasks/{task_id}/trace
  - 查询每个 LangGraph 节点的开始时间、结束时间、耗时、状态和错误信息
- GET /api/shopping/health
  - 购物分析服务健康检查

## 输出结构

当前报告模型位于 backend/app/models/schemas.py，核心输出包括：

- query：用户查询
- category：产品品类
- products：候选产品分析列表
- comparison_summary：横向对比总结
- final_recommendation：最终购买建议
- budget_advice：预算建议
- general_tips：通用选购建议

每个产品分析项通常包含：

- product：产品名称、品牌、型号、价格区间、评分、规格
- reviews：测评来源列表
- common_pros：公认优点
- common_cons：公认缺点
- red_flags：避雷点
- controversy_points：争议点
- verdict：推荐、不推荐、看需求
- verdict_reason：结论理由

## 已知实现特点

- 候选产品抽取先于所有检索步骤，目的是减少 A 型号测评与 B 型号价格串台的问题
- 三个检索 Agent 通过 LangGraph 原生 fan-out/fan-in 工作流编排，并使用独立的 Brave Search MCP 调用链路
- 报告生成阶段依赖上游检索结果；如果上游部分失败，会显式说明证据边界
- 后台任务状态支持 memory 与 Redis 两种存储；Redis 模式用于多 worker/多实例共享 task_id、进度、报告和节点 Trace

## 安全说明

- 不要提交真实 .env 文件到仓库
- 仅提交 .env.example
- 如果 API Key 曾暴露，请及时在服务商后台轮换
