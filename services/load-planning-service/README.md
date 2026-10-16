# Load Planning Service

智能装载与 Shipment Operations Agent Runtime。第 23.4～23.7 节已经形成可恢复、可审批、
可正式版本化的确定性装托闭环；第 23.8 节又把现有写入口接入无 LLM 的 LangGraph 编排和
本地 SQLite Checkpointer；第 23.9 节增加了可关闭、只读、失败可降级的方案解读 Provider。
Shipment Agent V3 进一步在同一个 8200 FastAPI 进程内加入自动唤醒、数据库租约和站内
业务通知。它不会给出“运输绝对安全”结论。

## 现在支持什么

- 创建一份装载任务，同时提交多条箱规和多个候选托盘规格；
- 通过 `RULES_V3` 坐标解析预览 Packing List，不保存原 PDF；
- 查询任务列表摘要和单个任务详情；
- 尺寸统一使用 `mm`，重量统一使用 `kg`；
- 严格拒绝 0 或负数尺寸、重量；
- 保存 `source_type/source_reference/source_locator/confidence/verified`；
- 构造示例使用 `SYNTHETIC_DEMO`，且不能标成已核实正式数据；
- 未知堆码层数、顶部承重、托盘承重、最大高度保持 `null`；
- 状态化运行保存输入快照哈希、人工答案、事件轨迹和三种候选方案；
- 箱体保持竖直，只允许 XY 平面旋转；执行边界、碰撞、层数、高度、载重和托盘库存约束；
- `STABLE/BALANCED/UTILIZATION` 分别表达低重心、折中和利用率优先；
- 方案必须由人工批准或驳回，大模型不参与坐标与评分生成；
- LangGraph 负责缺约束、方案评审、重规划和正式发布四类人工中断；
- Graph `thread_id` 直接使用可从业务历史恢复的 `run_id`；
- SQLite checkpoint 只保存流程游标和业务引用，不复制完整方案或 XYZ；
- Graph 关闭后自动回到原有 Service 路径，HTTP 请求和响应契约不变；
- `APPROVED` 候选可以锁定为不可变 `LOCKED V1/V2`；
- `plan-insights` 可以使用本地规则、Codex CLI 或 OpenAI Responses 解释已有确定性结果，
  但不能修改坐标、审批或发布；
- Shipment Case 总 LangGraph 可编排数据就绪、智能装载、LINK 订舱和结案审批；
- FastAPI lifespan 内置轻量 Worker，自动观察装载正式版本和 LINK 终态，不增加启动命令；
- Worker 用条件更新领取数据库租约，正常租期内同一运行只由一个实例取得调度权；
- 失租接管遵循至少一次恢复语义，迟到投影由 token 拒绝，业务副作用再由版本与幂等保护；
- 权威状态未变化时只安排下一次检查，不增加 `run_version`、Trace 或重复通知；
- 暂时性故障按指数退避重试，业务通知按类型去重，并在问题消失后转为 `RESOLVED`；
- `next_wake_at`、`wake_reason` 与通知通过运行详情返回，前端可以解释何时再检查、谁需处理；
- 使用独立 MySQL schema：`load_planning_service`；
- 可选注册到本机 Nacos：`load-planning-service@127.0.0.1:8200`；
- pytest 使用内存 SQLite，不要求本机 MySQL 启动。

## 本地准备

需要 Python 3.12。在当前目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

不要覆盖仓库已有 `.env.local`。如需本机 MySQL，在你自己的 `.env.local` 追加：

```dotenv
LOAD_PLANNING_DATABASE_URL=mysql+asyncmy://lab_user:你的本机密码@127.0.0.1:3306/load_planning_service
```

LangGraph 默认开启，不要求修改旧 `.env.local`。如需显式配置：

```dotenv
LOAD_PLANNING_LANGGRAPH_ENABLED=true
LOAD_PLANNING_LANGGRAPH_CHECKPOINT_PATH=../../runtime/checkpoints/load-planning-langgraph.sqlite3
```

SQLite Checkpointer 跟随当前 Python 进程打开，不是一个需要另行启动的服务，也不需要模型 API
Key。仓库一键脚本会把路径指向 Git 忽略的 `runtime/checkpoints/`，不会覆盖 `.env.local`。

Shipment Agent 自动唤醒默认开启，并跟随同一个 FastAPI lifespan 启停。所有配置都有安全
默认值，旧 `.env.local` 不需要补字段，也不需要启动第二个 Python 程序。需要做故障实验时
可以参考 `.env.example` 临时覆盖扫描间隔、租约、批量大小、等待间隔和退避上限；正常学习
直接保留默认值即可。

自动检查只读取装载和 LINK 的权威状态：它不会自动批准方案、自动提交 LINK、自动重试
`DEAD` 投递或绕过任何人工审批。后台等待结束后会进入下一个人工节点并清除
`next_wake_at`；`POST /shipment-agent-runs/{run_id}/reconcile` 仍作为管理员“立即检查”
故障兜底保留，但正常链路不需要调用。

LLM 解读默认可以关闭，也可以使用不联网的规则模式。需要学习 23.9 时，只在你自己的
`.env.local` 追加相应配置，不要覆盖原文件，更不要把真实 Key 写入 `.env.example`：

```dotenv
LOAD_PLANNING_PLAN_INSIGHTS_ENABLED=true
LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER=rule_based
```

切换到 `codex_cli` 时需要本机 Codex 已登录；切换到 `openai_responses` 时还需要服务端
OpenAI API Key。外部 Provider 失败会回退规则说明，装载算法和正式版本链不受影响。

先在本机 MySQL 创建独立 schema 和账号授权，再执行：

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8200 --reload
```

打开 Swagger：

```text
http://127.0.0.1:8200/docs
```

默认不注册 Nacos；单独学习本服务时无需启动 Nacos。需要让 Java BFF 服务发现时，再追加：

```dotenv
LOAD_PLANNING_NACOS_DISCOVERY_ENABLED=true
LOAD_PLANNING_NACOS_REGISTER_PORT=8200
```

Nacos 地址、分组、用户名和密码沿用仓库本地环境变量。服务名默认固定为
`load-planning-service`，不会误用 LINK 的 `NACOS_REGISTER_PORT=8000`。

## 手工调用

仓库内的请求示例只包含构造数据：

```bash
curl -i \
  -H 'Content-Type: application/json' \
  --data @examples/create-load-planning-task.json \
  http://127.0.0.1:8200/api/v1/load-planning-tasks

curl -s http://127.0.0.1:8200/api/v1/load-planning-tasks

curl -s http://127.0.0.1:8200/api/v1/load-planning-tasks/LP-DEMO-0001
```

Packing List 预览：

```bash
curl -s \
  -F 'file=@/你的路径/demo-packing-list.pdf;type=application/pdf' \
  http://127.0.0.1:8200/api/v1/packing-list-imports/preview
```

预览只接受不超过 5 MiB、带文字层的 PDF。文件在内存中解析，不写入数据库和上传目录。

## 实测状态化智能体

先用上面的任务接口创建任务。然后启动运行；策略不填时默认生成三套：

```bash
curl -s \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: local-demo-create-001' \
  --data '{"selected_pallet_code":null,"strategy_codes":["STABLE","BALANCED","UTILIZATION"]}' \
  http://127.0.0.1:8200/api/v1/load-planning/tasks/LP-DEMO-0001/agent-runs
```

如果请求在网络层超时，可以使用**同一个** `Idempotency-Key` 重试；服务端会返回原来的
`run_id`，不会重复创建运行。若同一个 key 换了托盘或策略，服务端会返回 `409`，此时应
换一个新 key。旧脚本不带这个 header 仍然兼容，但无法获得创建请求级别的幂等保护。

如果响应是 `AWAITING_INPUT`，按 `questions[].key/value_type` 组织答案并恢复同一个 `run_id`：

```bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{"answers":[
    {"key":"selected_pallet_code","value":"PALLET-1200X1000"},
    {"key":"cargo.ITEM-001.stackable","value":false},
    {"key":"cargo.ITEM-002.max_stack_layers","value":4}
  ]}' \
  http://127.0.0.1:8200/api/v1/load-planning/agent-runs/替换成run_id/resume
```

进入 `READY_FOR_REVIEW` 后批准一个可行方案，或用 `REJECT` 和非空 `comment` 驳回：

```bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{"decision":"APPROVE","plan_code":"BALANCED-001","comment":"仓库复核通过"}' \
  http://127.0.0.1:8200/api/v1/load-planning/agent-runs/替换成run_id/decision
```

任意时刻可用 `GET /api/v1/load-planning/agent-runs/{run_id}` 恢复完整状态。

这些公开 URL 没有因为 LangGraph 改名。`create/resume/decision/replan/lock` 在 Python 内部经过
`LoadPlanningWorkflowOrchestrator`；GET、list、history、current 和 detail 仍直接读取 MySQL
业务事实。Java BFF 和 Vue 不需要认识 `thread_id` 或 checkpoint。

正式发布一条已批准运行：

```bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "expected_run_version":3,
    "expected_current_version_number":0,
    "lock_reason":"仓库与负责人复核后正式发布"
  }' \
  http://127.0.0.1:8200/api/v1/load-planning/agent-runs/替换成run_id/plan-versions
```

同一来源运行和同一锁定请求重放会返回原版本，不会创建第二个 V1。

## 验证

```bash
pytest -q
ruff check .
ruff format --check .
```

只验证 LangGraph：

```bash
pytest -q tests/test_load_planning_workflow.py
```

个人学习笔记保存在本地并手工同步到语雀，不纳入 Git。服务端代码阅读建议从
`app/api/`、`app/services/`、`app/workflows/`、`app/repositories/` 和对应 `tests/`
按调用链逐层进入。
