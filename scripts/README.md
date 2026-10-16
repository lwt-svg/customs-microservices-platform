# 本地一键启动、监督与真实链路验收

这些脚本只管理个人学习仓库的本地进程。它们会把日志放在 `runtime/logs/`，把自己启动的 PID 放在 `runtime/pids/`；两者都不会进入 Git。

先准备一次本地配置：

```bash
cp -n .env.example .env.local
```

请按自己的本机 MySQL/Nacos 配置编辑 `.env.local`。不要把它提交到 Git，也不要把真实企业地址、账号或 Token 写入这里。

已有旧版 `.env.local` 时不要覆盖。脚本会在内存中根据现有本机 MySQL 配置补出模拟承运人
和智能装载服务所需的数据库 URL、回调 URL 和本地 DEMO Token，不会改写原文件；仍可参考
`.env.example` 手工补齐这些字段，方便理解配置来源。

## 最省事的日常启动方式

日常学习 LINK 七步和智能装载时，推荐只运行这一条：

```bash
./scripts/run-local.sh core
```

想先零费用跑通“解读按钮 → Java BFF → Python → 规则结果”，使用：

```bash
./scripts/run-local.sh core --with-rules
```

第 23.9 章要试验本机 Codex 方案解读时：

```bash
# 首次使用时完成一次 Codex CLI 登录
codex login

./scripts/run-local.sh core --with-codex
```

这个参数只覆盖本次启动进程，不修改 `.env.local`。普通 `core` 仍默认关闭模型调用。
Codex CLI 未登录、超时或返回格式不正确时，Python 会返回本地规则说明并标记
`degraded=true`，不会让规划、审批或发布失败。

使用自己的 OpenAI API Key 时，只把 Key 写入未提交的 `.env.local`：

```dotenv
OPENAI_API_KEY=你的真实Key
```

然后运行：

```bash
./scripts/run-local.sh core --with-openai
```

Key 只会注入智能装载 Python，不会传给 LINK、Java BFF 或 Vite。每次显式运行
`--with-openai` 都会重启“由这些脚本管理”的 8200 服务，以重新读取刚修改的 Key 和模型；
若 8200 是手工启动的外部进程，脚本会提示但不会停止它。

终端会一直保持在前台，既能用于普通 Terminal，也适合 Codex PTY。启动完成后可以直接打开
5173；学习结束时在这个终端按 `Ctrl+C`。

`Ctrl+C` 只停止“本次 `run-local.sh` 实际新建”的应用进程。启动前已经存在并被复用的服务
不会被停止，MySQL 和 Nacos 也会保留。这样不会因为一次学习会话结束而误杀另一个终端或
另一个项目正在使用的进程。

它会启动或复用 Docker 中的 MySQL、Nacos，以及：

```text
LINK Python      http://127.0.0.1:8000/docs
智能装载 Python   http://127.0.0.1:8200/docs
Java BFF         http://127.0.0.1:8080/actuator/health
平台网站          http://127.0.0.1:5173
```

core 模式会在业务 VALIDATE 时保存一条 `PENDING` 可靠投递任务，但不会要求 8100 在线，
也不会立即发送。这样七步主线可以独立学习。

学习“异步承运人、回调、重试”扩展时，改为：

```bash
./scripts/run-local.sh full
```

它在核心环境基础上，再启动：

```text
模拟承运人 API  http://127.0.0.1:8100/docs
模拟承运人门户  http://127.0.0.1:5174
```

full 模式会开启 LINK 的立即投递和后台重试。若 LINK 是一键脚本以 core 模式启动的，脚本会
只重启自己管理的 LINK 进程来切换模式；用户手工启动的外部进程不会被停止。

## 后台启动方式

如果不希望当前终端保持在前台，也可以继续使用：

```bash
./scripts/start-core.sh
./scripts/start-full.sh
./scripts/start-core.sh --with-rules
./scripts/start-full.sh --with-rules
./scripts/start-core.sh --with-codex
./scripts/start-full.sh --with-codex
./scripts/start-core.sh --with-openai
./scripts/start-full.sh --with-openai
```

这两个脚本启动完成后会退出，服务继续在后台运行。结束时再执行：

```bash
./scripts/stop-local.sh
```

不带参数的 `stop-local.sh` 会处理仓库 PID 目录中登记的全部应用进程；前台监督内部使用
`--manifest` 精确清理本次会话新建的 PID。`--manifest` 是内部安全机制，日常无需手工调用。

## 日志不会再一直累加

应用仍把“当前这一次进程”的日志写在熟悉的位置：

```text
runtime/logs/link-booking-service.log
runtime/logs/load-planning-service.log
runtime/logs/platform-bff.log
runtime/logs/platform-web.log
runtime/logs/link-carrier-simulator.log
runtime/logs/carrier-simulator-web.log
```

当脚本确认某个应用确实需要启动新进程时，会先把它原来的非空日志归档到：

```text
runtime/logs/archive/<服务名>/
```

归档优先使用 gzip 压缩，当前 `.log` 随后清空，默认每个服务只保留最近 10 份。已经健康运行
并被脚本复用的进程不会触发轮转，也不会清空它正在写的日志。`compose.log`、
`schema-setup.log` 和三个 Python 服务各自的 Alembic 日志也按相同规则归档，避免一次性命令
在每次启动时无限追加。

轮转前会确认 current log 是 `runtime/logs/` 的直接普通文件，并拒绝符号链接、`..` 路径
逃逸和指向外部目录的归档作用域。两个 `start-core.sh` / `start-full.sh` 同时运行时，还会
通过 `runtime/locks/local-lifecycle.lock/` 原子串行化整段启动流程，避免两个终端同时完成
“检查、轮转、启动”。`run-local.sh` 直接复用其调用的 start 脚本锁，不会重复加锁。

确实需要保留更多本地历史时，可只对当前命令设置 1 到 100 之间的数量，例如：

```bash
LOCAL_LOG_ARCHIVE_KEEP=20 ./scripts/run-local.sh core
```

这个变量只控制归档份数，不会删除当前日志、数据库或 `runtime/` 目录。请不要把
`runtime/logs/archive/` 当作长期审计存储；它只是本地学习环境的有界故障排查记录。

这里限制的是“多次本地启动留下的历史日志”。某个应用在单次进程持续运行期间，current
`.log` 仍会继续追加；这是为了不移动或截断正在被进程写入的文件。个人学习环境在正常
停止、下次启动时就会自动归档。若以后改成 7×24 小时长期运行，应分别使用 Java Logback、
Python logging 或日志平台提供的按大小/按日期滚动策略，而不是让 Shell 在运行中强制清空。

## “启动完成”现在检查什么

只检查 8000、8080、8200 是否监听，并不能证明业务链路可用。`start-core.sh`、
`start-full.sh` 和 `run-local.sh` 在输出“已就绪”之前都会通过 Java BFF 请求：

```text
GET /api/v1/platform/load-planning/tasks
GET /api/v1/platform/link-booking/applications
GET /api/v1/platform/shipment-cases
```

这三次只读探测实际覆盖：

```text
Java BFF :8080
→ 从 Nacos 查找 LINK 与 Agent Runtime
→ Python :8000 / :8200 查询业务列表
→ 两个独立 MySQL schema 返回数据
→ Python → Java BFF 返回统一 JSON
```

因此它比 `/actuator/health` 或端口检查更接近用户真正会使用的核心链路。只想执行相同
的只读检查：

```bash
python3 scripts/shipment-case-e2e.py --probe-only --wait-seconds 30
```

## 智能装载真实 E2E

服务已启动后，可执行：

```bash
python3 scripts/load-planning-e2e.py
```

脚本只访问 `127.0.0.1:8080`，不会绕过 Java 直连 Python、Nacos 或 MySQL。它使用
`SYNTHETIC_DEMO` 构造数据验证两条相互独立的真实业务链路。第一条依次验证：

```text
创建装载任务
→ 使用 Idempotency-Key 创建运行
→ 同一个 key 重放并确认 runId 不变
→ active runs 恢复
→ 驳回旧运行
→ 使用新的固定 Idempotency-Key 创建 parentRunId 关联的新运行并重放
→ 批准新方案
→ 从完整历史恢复 REJECTED 与 APPROVED
→ 将批准方案锁定为不可变 LOCKED V1，并重放完全相同的锁定请求
→ 通过 list/current/detail 复核三份快照、SHA-256、算法版本和审计事件
→ active runs 清空，旧运行仍为 REJECTED，来源运行仍为 APPROVED
```

第二条专门验证人工补充约束后的恢复闭环：

```text
创建缺少安全约束的独立任务
→ 启动运行并暂停在 AWAITING_INPUT
→ 用 GET 恢复同一个 runId、运行版本和三个结构化问题
→ 提交托盘选择、是否可堆叠和最大堆叠层数
→ 同一个 runId 进入 READY_FOR_REVIEW
→ 批准可行方案
→ 锁定为该任务自己的 LOCKED V1
→ 重新读取正式版本和 APPROVED 运行，并确认 active runs 已清空
```

创建运行或重规划遇到“请求可能成功、但响应没有回来”的网络情况时，重试会复用该次操作
原来的 `Idempotency-Key`。正式锁定不伪造 HTTP 幂等键，而是重放完全相同的来源运行、
运行版本、当前正式版本号和锁定原因，由服务端业务唯一约束返回同一个版本。任务创建和审批
没有请求级幂等键；补充约束也不依赖自动重发。它们的响应未知时会先 GET 恢复服务端权威
状态，不会盲目重复写入。输出只包含构造任务编号、步骤和状态，不打印 `.env.local`、
请求体或响应体。构造任务会保留在本地数据库中，便于之后从 5173 页面复盘。

## Shipment Case 真实 E2E

`core` 服务启动后，可执行：

```bash
python3 scripts/shipment-case-e2e.py
```

脚本只访问 `http://127.0.0.1:8080/api/v1/platform/*`，不会绕过 Java BFF 直连
Python、Nacos 或 MySQL。每次运行使用新的合成编号，依次验证：

```text
创建装载任务与 LINK 草稿
→ 创建 Shipment Case
→ 使用相同 Idempotency-Key 重放创建，确认 id/revision 不变
→ 绑定装载任务与 LINK 申请
→ 使用旧 revision 得到 409，且案件与事件没有变化
→ DRAFT → ACTIVE
→ 使用相同 Idempotency-Key 重放 ACTIVE，确认没有重复推进
→ ACTIVE → COMPLETED
→ GET 复核两个绑定、绑定时状态快照、连续 revision 和无重复事件 sequence
```

装载任务和 LINK 草稿创建接口当前没有请求级幂等键。如果这两个前置 POST 的网络响应未知，
脚本只会按本次唯一业务编号执行 GET 恢复，不会盲目重复创建。Shipment Case 的写接口具有
幂等键；其网络响应未知时，脚本会复用该命令原来的 `Idempotency-Key` 重试，不会生成新
key。输出不会打印请求体、响应全文或 `.env.local`。合成资源会保留在本地数据库中，便于
从发运案件中心查看最终时间线。

只验证脚本自己的两类安全策略、不访问网络：

```bash
python3 scripts/shipment-case-e2e.py --self-test
```

只读取三条平台链路确认服务就绪、不创建测试数据：

```bash
python3 scripts/shipment-case-e2e.py --probe-only --wait-seconds 30
```

## Shipment 总 Agent 真实 E2E

这条验收需要 LINK 真正投递到本机模拟承运人，因此先使用 `full`，不能使用只保存
`PENDING` 投递任务的 `core`：

```bash
./scripts/run-local.sh full
```

服务就绪后，在另一个终端执行：

```bash
python3 scripts/shipment-agent-e2e.py
```

脚本创建一组全新的 `SYNTHETIC_DEMO` / `DEMO-` 合成数据，并验证：

```text
Java BFF 创建装载任务、LINK 草稿和 SEA_FCL Shipment Case
→ 绑定两个资源并启动 Shipment 总 Agent
→ 人工批准案件激活
→ 总 Agent 幂等启动装载子 Agent
→ 批准可行装载方案并发布不可变 LOCKED V1
→ 8200 内置 Worker 自动发现 LOCKED V1，再人工批准 LINK 提交
→ 总 Agent 进入 WAITING_EXTERNAL，不占线程等待承运人
→ 本机模拟承运人开始处理并确认
→ CONFIRMED 回调写回 LINK
→ Worker 自动发现 LINK 终态，再人工批准结案
→ 复核案件、Agent Center、运行历史、active=null 和完整 Trace
```

E2E 在两个自动等待阶段只做有上限的 GET 轮询，并断言等待时存在 `nextWakeAt`、进入人工
审批点后该字段已经清空。自动唤醒 Worker 随 8200 FastAPI 进程启动，正常租期内同一运行
只允许一个实例领取；失租接管时，租约 token 会拒绝旧实例的迟到投影，业务版本和下游
幂等再保护实际副作用。无变化检查不会增加运行版本、Trace 或重复通知，暂时性故障会指数
退避。因此不需要新增 Redis、Celery、Kafka、端口或启动命令。显式 `reconcile` API 仍保留
为页面“立即检查（故障兜底）”，不再是正常 E2E 的必经步骤。

业务资源、Shipment Case、装载子 Agent 与 Shipment 总 Agent 都只通过
`http://127.0.0.1:8080/api/v1/platform/*` 访问。只有模拟“承运人员工点击处理与确认”
时，脚本才访问固定的 `http://127.0.0.1:8100/api/v1/portal/*`；它不会调用真实承运人，
也不会从 `.env.local` 读取或拼接外部地址。

只检查脚本自身的合成数据、本机 URL 边界和幂等重试，不访问网络：

```bash
python3 scripts/shipment-agent-e2e.py --self-test
```

只读检查完整链路是否适合运行这条 E2E，不创建测试数据：

```bash
python3 scripts/shipment-agent-e2e.py --probe-only --wait-seconds 30
```

## 脚本自身检查

不启动服务也可以先检查脚本：

```bash
./scripts/check-local-scripts.sh
```

它会执行 Bash 语法检查、Python 编译检查、三个 E2E 的离线安全重试自检、日志轮转自检、
三种智能解读模式/API Key 隔离自检和帮助入口检查。
日志自检覆盖归档内容、最旧删除与最新保留、空日志也按降低后的上限收敛、无关文件不删除、
非法保留数回退、current/archive 符号链接与路径逃逸拒绝、复用现有进程不轮转，以及两个
并发启动流程必须串行。本机安装了 ShellCheck 时还会自动执行 ShellCheck。

## 复用与停止边界

如果 8000、8200、8080 或 5173 是以前手工打开的旧进程，脚本会为安全起见复用而不重启
它们。复用 Python 8000/8200 前还会确认对应数据库已经位于当前 Alembic head；检查失败时
不会把基础健康接口误报成完整启动成功。想让一键脚本完整接管时，只需第一次手工关闭原来的
四个终端，再运行 `./scripts/run-local.sh core`；此后即可统一管理。

查看服务状态：

```bash
./scripts/status-local.sh
```

停止由脚本启动的应用进程：

```bash
./scripts/stop-local.sh
```

`stop-local.sh` 不会停止 MySQL/Nacos，也不会停止不是这些脚本启动的同端口服务。每次启动前，脚本会先检查端口；如果发现健康的现有服务，会复用而不把它登记为自己的 PID。若端口被其他未知进程占用，脚本会停止并提示你自行处理，避免误杀。
