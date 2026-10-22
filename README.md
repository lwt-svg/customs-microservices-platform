# 沙丘 OS｜关务与供应链企业业务智能体学习平台

沙丘 OS 的本地企业业务学习平台。当前在同一个平台中放置两个业务域：关务 OS 和供应链
OS。项目使用构造数据和独立沙箱，不连接企业 Nacos、FAT、数据库或任何线上系统。

## 当前技术栈

- Vue 3、TypeScript 与 Vite 后台管理界面；
- Java 21、Spring Boot、Spring Data JPA 与 Flyway 平台 BFF；
- Python、FastAPI、SQLAlchemy 与 Alembic LINK 微服务；
- Python、FastAPI、确定性装托算法与 LangGraph 编排的智能装载服务；
- 独立 Python 模拟承运人服务与 Vue 承运人门户；
- 本地 MySQL 8 与 Nacos；
- Java BFF 通过 Nacos 发现并调用 Python 服务；
- 组织、用户、角色、权限和菜单基础模型。

## 项目结构

```text
customs-microservices-platform/
├── platform-web/                Vue 3 后台管理系统
├── platform-bff/                Java Spring Boot 平台 BFF
├── carrier-simulator-web/       Vue 3 本地模拟承运人门户
├── services/
│   ├── link-booking-service/    Python FastAPI LINK 服务
│   ├── load-planning-service/   Python FastAPI 智能装载任务与算法服务
│   └── link-carrier-simulator/  Python FastAPI 模拟承运人服务
├── scripts/                     macOS/Linux 与 Windows 一键启停脚本
├── requirements-local.txt       一次安装三个 Python 服务与学习工具
├── compose.yaml                 本地 MySQL、Nacos 与可选模拟承运人定义
└── .env.example                 安全的本地配置示例
```

## 产品定位与当前能力

项目定位正在从“关务页面 + 供应链页面”演进为统一的 TradeOps Agent Platform。平台以
`Shipment Case` 作为跨业务案件主线，让文档、订舱、装载、人工确认、状态事件和智能编排
围绕同一个业务对象协作。

```text
Vue 企业工作台
  → Java Platform BFF：身份、权限、菜单、统一 API 和审计入口
    → Python Agent Runtime：Shipment Case 总 LangGraph、装载 Agent 与未来 RAG
    → LINK Python Service：订舱申请与七步业务
    → Carrier Simulator：本地异步承运人闭环
      → MySQL / Nacos
```

当前已经实现：

- LINK 文档导入、人工复核、七步填单和模拟承运人异步闭环；
- LangGraph 装载流程、确定性几何计算、三方案比较、3D 复核、审批和不可变正式版本；
- Shipment Case 案件中心、资源绑定、事件时间线、并发控制和幂等命令台账；
- Shipment Operations Agent 总 LangGraph：缺资料暂停、跨工具恢复、人工审批和完整 Trace；
- V3 自动唤醒：数据库租约协调单实例领取、暂时故障指数退避和可自动恢复的站内业务通知；
- 统一 RPA Job 控制面：LINK、产地证和商检共享幂等、租约、心跳、结果回报与证据模型；
- Playwright 真实浏览器 Worker：自动填写本地 LINK 七步 Sandbox，并支持 Windows
  单一窗口 UKey 人机协同边界；
- Vue → Java BFF → Nacos → Python → MySQL 的真实本机调用链；
- 一键启动、日志轮转、自动测试和端到端验收脚本。

当前仍未完成：

- 文档理解、字段证据、冲突检查和 RAG；
- 真实登录、案件级 RBAC 和可信用户上下文；
- Agent 评测、生产可观测、告警、Ops Copilot、Kubernetes 与恢复演练。

个人学习笔记保存在本地并手工同步到语雀，不属于项目源码，也不会提交到 Git。

## 安全边界

- 只使用构造或脱敏的学习数据；
- `.env.local` 永远不提交 Git；
- Java、LINK Python、智能装载和模拟承运人使用四个独立数据库 schema；
- Vue 只访问 Java BFF，不直接连接 Python 或数据库；
- 承运人门户只访问本地模拟承运人服务；
- LINK 文档导入文件只进入 Git 忽略的 `services/link-booking-service/uploads/`；供应链
  PL 预览在内存中解析、不保存原文件；其他运行文件进入 `runtime/`；
- 不连接企业服务、账号或网络环境。

## 第 12 节：Java Spring Boot BFF 初始化

`platform-bff` 是沙丘 OS 的 Java 平台层起点。第 12 章最初建立的最小能力包括：

- `GET /api/v1/platform/health`：供 Vue 或学习者确认 BFF 已启动；
- `GET /actuator/health`：供本机或容器健康探针使用；
- 统一响应对象 `ApiResponse`；
- 一个不会连接真实 MySQL、Nacos、Python 或企业资源的 MockMvc 测试。

当前仓库已经在这个起点之上继续加入平台数据库、Nacos、关务与供应链代理。这里保留的是
学习顺序，不是在描述整个 BFF 到今天仍“只包含健康检查”。日常启动优先使用仓库根目录的
一键脚本；手工运行 Java 时必须同时准备当前 `application.yml` 所需的本地 MySQL 和 Nacos。

启动和验证：

```bash
cd platform-bff
mvn test
mvn spring-boot:run
curl http://127.0.0.1:8080/api/v1/platform/health
curl http://127.0.0.1:8080/actuator/health
```

预期业务健康检查响应：

```json
{"code":0,"message":"success","data":{"status":"UP","service":"platform-bff"}}
```

## LINK 订舱申请 API（第一条数据库闭环）

服务使用独立 MySQL schema `link_booking_service`，绝不与其他 Python 服务共表或跨库写入。
Java 平台 BFF 使用 `platform_bff_lab`，模拟承运人使用 `link_carrier_simulator`，智能装载
使用 `load_planning_service`。首次启动前，在本机 Docker MySQL 中一次性创建四个学习
schema 并授权给本地学习用户（命令不会把密码写入项目文件）：

```bash
docker compose --env-file .env.local exec -T mysql sh -c \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "
    CREATE DATABASE IF NOT EXISTS platform_bff_lab
      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    GRANT ALL PRIVILEGES ON platform_bff_lab.*
      TO '\''$MYSQL_USER'\''@'\''%'\'';
    CREATE DATABASE IF NOT EXISTS link_booking_service
      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    GRANT ALL PRIVILEGES ON link_booking_service.*
      TO '\''$MYSQL_USER'\''@'\''%'\'';
    CREATE DATABASE IF NOT EXISTS link_carrier_simulator
      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    GRANT ALL PRIVILEGES ON link_carrier_simulator.*
      TO '\''$MYSQL_USER'\''@'\''%'\'';
    CREATE DATABASE IF NOT EXISTS load_planning_service
      CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    GRANT ALL PRIVILEGES ON load_planning_service.*
      TO '\''$MYSQL_USER'\''@'\''%'\'';
    FLUSH PRIVILEGES;
  "'
```

Java BFF 启动时会由 Flyway 自动执行 `V1`–`V12`，不需要手工执行 Java SQL。
确认 `.env.local` 里的 `LINK_DATABASE_URL` 指向 `link_booking_service` 后，再执行 Python
迁移和测试：

```bash
cd services/link-booking-service
conda run -n customs-affairs-os-lab python -m alembic upgrade head
conda run -n customs-affairs-os-lab python -m pytest
conda run -n customs-affairs-os-lab python -m ruff check .
conda run -n customs-affairs-os-lab python -m uvicorn app.main:app --reload --port 8000
```

上面是单服务调试命令。日常学习优先使用后文的 `./scripts/run-local.sh core`，脚本会选择
Python 3.12；不要直接使用 macOS 可能仍为 3.9 的系统 `python3`。

Java 平台菜单基础接口：

```text
GET  /api/v1/platform/menus
GET  /api/v1/platform/menus/tree
POST /api/v1/platform/menus
GET  /api/v1/platform/menus/{menuId}
```

Vue 后台只访问 Java BFF；菜单树也由 Java 组装后返回，前端不直接连接 MySQL
或 Python 服务。

第 21.1～21.5 节保留了 LINK 查询、创建草稿、状态流转、申请详情和操作流水的学习过程。
其中第 21.3 节的同步 `AUTO-LINK-...` 是历史练习阶段；当前代码已由第 22 章异步模拟承运人
闭环替代，不会在提交时同步伪造确认。

```text
GET  /api/v1/platform/link-booking/applications
POST /api/v1/platform/link-booking/applications
GET  /api/v1/platform/link-booking/applications/{applicationNumber}/detail
POST /api/v1/platform/link-booking/applications/{applicationNumber}/status
```

Vue 只访问 Java BFF；Java 通过 Nacos 查找 Python LINK 实例，并把 Python snake_case
请求和响应转换成平台 camelCase JSON。浏览器不需要知道 Python 的地址。

API 均只适用构造或脱敏数据：

- `POST /api/v1/booking-applications`：创建 `DRAFT` 草稿；
- `GET /api/v1/booking-applications` 与 `GET /api/v1/booking-applications/{application_number}`：查询申请；
- `GET /api/v1/booking-applications/{application_number}/detail`：查询当前申请快照和倒序操作流水；
- `POST /api/v1/booking-applications/{application_number}/status`：提交时只完成
  `DRAFT → SUBMITTED`，并在同一事务创建承运人投递任务；
- `POST /api/v1/booking-applications/{application_number}/carrier-submission/retry`：
  人工重试 `DEAD` 或等待重试的投递任务；
- `POST /api/v1/carrier-callbacks/link`：接收模拟承运人的处理中、确认或拒绝回调；
- 申请只有收到承运人终态回调后才进入 `CONFIRMED` 或 `REJECTED`。

第 21.6 节新增 PDF 导入：

```text
POST /api/v1/platform/link-booking/document-imports
GET  /api/v1/platform/link-booking/document-imports/{documentId}
POST /api/v1/platform/link-booking/document-imports/{documentId}/confirm
```

PDF 最大 5 MiB，只支持文本型 PDF，不做 OCR。解析结果只是候选值，必须人工复核后才创建
`DRAFT`；文件随机命名保存在 Git 忽略目录，日志不输出 PDF 原文。

## 第 22 章主线：模拟 LINK 七步

`DRAFT` 申请在平台 `5173` 中完成：

```text
1. 登录 LINK（DEMO 边界提示）
2. Destination
3. Package
4. Purchase Order
5. Participants
6. Attachment
7. Recap：SAVE → 业务 VALIDATE
```

七步执行进度包含登录；页面所说的“六步业务预览”不包含登录，两种计数并不冲突。

`SAVE` 只保存当前浏览器里的演示草稿，不修改后端状态；业务点击 `VALIDATE` 后才调用现有
状态接口，将申请从 `DRAFT` 改为 `SUBMITTED`。页面不会登录真实 LINK，也不会保存真实
账号、客户或地址。

## 第 22 章扩展：独立模拟承运人闭环

模拟承运人默认运行在 `8100`，使用独立 schema `link_carrier_simulator`；独立门户运行在
`5174`。这是用于学习幂等、回调、重试和并发的可选高级扩展，不是日常七步填单的必启服务。
完整状态流转为：

```text
LINK：DRAFT → SUBMITTED ───────────────→ CONFIRMED | REJECTED
投递：PENDING → SENDING → ACCEPTED → PROCESSING → CONFIRMED | REJECTED
承运人：RECEIVED → PROCESSING → CONFIRMED | REJECTED
回调：PENDING → SENDING → DELIVERED
                   └────→ RETRY_WAIT → DEAD → 人工重试
```

请求投递使用稳定的 `Idempotency-Key`；回调使用 `callback_id + payload_hash` 幂等，
并通过本地 `X-Carrier-Callback-Token` 鉴权。所有编号仍为 `DEMO-` 构造值。

## 首次安装

项目不依赖固定的 `/Users/...` 或 `C:\...` 路径，可以克隆到任意目录。`127.0.0.1`
在 macOS、Linux 和 Windows 上都表示当前电脑，不是某一台 Mac 的地址。

共同依赖：

- Git；
- Docker Desktop（Windows 建议启用 WSL 2 backend）或 Docker Engine；
- Python 3.12；推荐使用 Conda 环境 `customs-affairs-os-lab`；
- JDK 21 与 Maven；
- Node.js 20 或更高版本。

MySQL 8.4 和 Nacos 2.5.3 不需要在宿主机分别安装，启动脚本会通过 `compose.yaml`
拉取并启动本地容器。Python、Java、Vue 业务应用默认仍作为宿主机进程运行，方便断点调试。

### Windows：Conda + Docker Desktop

第一次克隆后，在仓库根目录打开 PowerShell：

```powershell
conda create -n customs-affairs-os-lab python=3.12 -y
conda activate customs-affairs-os-lab

# 如果 Windows 执行策略阻止当前脚本，只为当前 PowerShell 进程临时放行。
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup-local.ps1
```

`setup-local.ps1` 会：

1. 检查当前 Conda Python 是否为 3.12；
2. 保留已有 `.env.local`，不存在时才从 `.env.example` 创建；
3. 通过 `requirements-local.txt` 一次安装三个 Python 服务、测试工具和 Playwright；
4. 安装 Playwright Chromium；
5. 对两个 Vue 项目执行 `npm ci`；
6. 预下载 Maven 依赖；
7. 拉取 MySQL 与 Nacos Docker 镜像。

它不会写入企业账号、不会覆盖本机配置，也不会启动真实 LINK 或单一窗口网站。

日常启动：

```powershell
conda activate customs-affairs-os-lab
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\run-local.ps1 core
```

状态和停止：

```powershell
.\scripts\windows\status-local.ps1
.\scripts\windows\stop-local.ps1
```

需要第 22 章可选模拟承运人时：

```powershell
.\scripts\windows\run-local.ps1 full
```

Windows 脚本会自动：

- 启动或复用 Docker MySQL、Nacos；
- 创建四个本地 schema；
- 执行三个 Python 服务各自的 Alembic 迁移；
- 由 Java Flyway 在 BFF 启动时升级平台库；
- 启动 Python、Java、Vue 并等待健康检查成功；
- 把 PID 和日志写入 Git 忽略的 `runtime\`。

> `.env.local`、数据库 volume、上传文件和 `runtime\` 都不会从 Git 下载。换一台电脑首次
> 启动会得到一套新的本地学习环境，这是有意的安全隔离。

LINK Sandbox Worker 会随 `core/full` 自动启动。单一窗口 Worker 不自动启动，因为真实
单一窗口依赖 Windows 交互式桌面、UKey 和人工登录确认。当前学习版只连接本地 Sandbox：

```powershell
# 模拟 UKey 已就绪，领取一票 COO 或 CIQ 任务后退出。
.\scripts\windows\run-single-window-worker.ps1 -SandboxUKeyPresent -Once

# 不带 -Once 时持续轮询；默认显示浏览器，便于人工看清每一步。
.\scripts\windows\run-single-window-worker.ps1 -SandboxUKeyPresent
```

这条命令不会读取真实卡口令，也不会访问真实单一窗口。未来接入正式授权环境时，应新增
独立 Adapter 和版本化 SelectorPack，账号、Cookie、证书与 UKey 口令仍只留在受控申报机。

### macOS / Linux

先创建并激活 Python 3.12 环境，然后一次安装全部 Python 依赖：

```bash
conda create -n customs-affairs-os-lab python=3.12 -y
conda activate customs-affairs-os-lab
python -m pip install -r requirements-local.txt
python -m playwright install chromium
npm --prefix platform-web ci
npm --prefix carrier-simulator-web ci
```

日常启动继续使用下面的 Bash 一键脚本。

## 统一 RPA Sandbox 与真实浏览器闭环

`load-planning-service` 的 8200 端口同时提供两个本地练习页面，不增加新的 Web 服务：

- LINK 七步模拟站：`http://127.0.0.1:8200/api/v1/rpa-sandbox/sites/link`
- 单一窗口模拟站：`http://127.0.0.1:8200/api/v1/rpa-sandbox/sites/single-window`

它们不是对真实网站的复制，不包含企业 URL、账号、Cookie、原始 XPath 或 UKey 口令。
页面只保留“登录/卡就绪、字段填写、保存草稿或暂存”的业务动作形状，而且没有正式提交、
签章或申报按钮。Sandbox 草稿保存在 8200 进程内存中，服务重启后会清空；可靠任务状态、
attempt token、租约、事件和回调收据保存在 `load_planning_service` MySQL schema。

`core/full` 默认自动启动 LINK Worker。需要单独观察浏览器时，可以先在 `.env.local`
设置 `RPA_SANDBOX_AUTO_LINK_WORKER_ENABLED=false`，重新启动 core，再手工执行：

```bash
cd services/load-planning-service
python -m app.rpa.browser_worker --domain LINK --headed --once
```

macOS/Linux 可以用 Sandbox 验证单一窗口流程，但这不等于具备真实 UKey 能力：

```bash
cd services/load-planning-service
python -m app.rpa.browser_worker \
  --domain SINGLE_WINDOW \
  --ukey-present \
  --headed \
  --once
```

服务启动且 Chromium 已安装后，执行三条真实浏览器 E2E：

```bash
python scripts/rpa-sandbox-e2e.py
```

如果 Playwright Chromium 因网络暂时无法下载，也可以显式使用本机已安装的浏览器渠道：

```bash
# macOS / Windows 已安装 Google Chrome
python scripts/rpa-sandbox-e2e.py --browser-channel chrome

# Windows 已安装 Microsoft Edge
python scripts/rpa-sandbox-e2e.py --browser-channel msedge
```

脚本只允许访问 `127.0.0.1/localhost`，使用 `SYNTHETIC_DEMO` 构造数据，经统一 RPA Job API
依次验证 LINK、COO 和 CIQ；它不会绕过控制面直接保存草稿。截图证据进入 Git 忽略的
`runtime/rpa-evidence/`。

关键失败状态：

- `WAITING_HUMAN`：缺少 UKey、登录态或必须由业务人员完成的动作；
- `RETRYABLE_FAILED`：外部写入前的临时浏览器或网络故障，可以安全重试；
- `SITE_VERSION_UNKNOWN`：页面指纹或 SelectorPack 不匹配，必须先适配新页面；
- `RESULT_UNKNOWN`：已经开始保存但结果无法确认，禁止盲目再点，必须人工对账；
- `FAILED / BUSINESS_PAYLOAD_INVALID`：缺少确定性必填字段，先修复源数据再重新派发。

Worker 永远只执行草稿保存/暂存。正式提交、申报和签章必须保留人工确认与企业授权边界。

## 本地启动

首次准备本机配置：

```bash
cp -n .env.example .env.local
```

如果 `.env.local` 已经存在，不要覆盖。不要把其内容发到笔记、终端输出或 Git。

日常学习 PDF、LINK 七步和智能装载，推荐一条前台监督命令：

```bash
./scripts/run-local.sh core
```

Windows 对应命令：

```powershell
.\scripts\windows\run-local.ps1 core
```

第 23.9 章需要试验候选方案智能解读时，先从不联网、无费用的规则模式开始：

```bash
./scripts/run-local.sh core --with-rules
```

想试验本机 Codex CLI 时：

```bash
# 仅第一次使用本机 Codex CLI 时执行
codex login

./scripts/run-local.sh core --with-codex
```

以后使用自己的 OpenAI API Key 时，只在未提交的 `.env.local` 中配置。笔记、代码和 Git
中都不要填写真实值。启动脚本只把 Key 注入智能装载 Python 进程，然后运行：

```bash
./scripts/run-local.sh core --with-openai
```

三个 `--with-*` 参数只影响本次启动，不会改写 `.env.local`。外部模型只接收经过
数据最小化的候选方案数值摘要；
CLI 未登录、超时或返回格式错误时，页面会标记“已降级”并显示本地规则说明，装载计算、
LangGraph、审批和正式发布仍可继续。

OpenAI Provider 的代码、输出合同和 Mock 测试已经完成，但真实 API Key 仍需你在本机做
一次验收。完成本地真实请求前，只能确认接口已经接好，不能声称真实账号调用已经通过。

启动：

```text
MySQL、Nacos、LINK 8000、智能装载 8200、Java 8080、平台 5173
```

这一套服务同时承载关务 OS 和供应链 OS。以后在第 14、15 章补齐部门权限，也仍然使用同一
条启动命令，不会拆成“关务启动一次、供应链再启动一次”。

终端会保持在前台；结束时按 `Ctrl+C`，只清理本次命令新建的应用进程，不会误停启动前
已经存在的外部进程，MySQL 和 Nacos 也会保留。需要后台运行时仍可使用
`./scripts/start-core.sh`。

学习异步承运人扩展：

```bash
./scripts/run-local.sh full
```

额外启动：

```text
模拟承运人 8100、承运人门户 5174
```

后台方式为 `./scripts/start-full.sh`。

其他命令：

```bash
./scripts/status-local.sh
./scripts/stop-local.sh
python3 scripts/load-planning-e2e.py
./scripts/check-local-scripts.sh
```

Windows 对应命令：

```powershell
.\scripts\windows\status-local.ps1
.\scripts\windows\stop-local.ps1
```

脚本把日志和 PID 放进 Git 忽略的 `runtime/`；停止时只处理脚本自己登记的应用进程，不会
按端口盲目杀掉用户原有进程。启动完成前还会通过 8080 请求装载任务列表，真实验证
Java BFF → Nacos → Python → MySQL，而不是只看端口。`load-planning-e2e.py` 同样只访问
8080，并覆盖两条独立链路：一条从方案评审经过驳回、父子重规划、批准到 `LOCKED V1`；
另一条从 `AWAITING_INPUT` 经 GET 恢复、补充约束、批准到 `LOCKED V1`。同时复核创建、
重规划和锁定重放，以及 list/current/detail 冻结快照一致性。

LangGraph 的 SQLite Checkpointer 随智能装载 Python 进程自动打开，保存在 Git 忽略的
`runtime/checkpoints/`，不增加新的端口或手工启动命令。

Shipment Agent V3 的自动唤醒 Worker 也跟随同一个 8200 FastAPI 进程启停，不需要再启动
Celery、Redis、Kafka 或另一条 Python 命令。正常租期内，同一运行只会被一个 Worker
领取；若实例失租后发生接管，迟到投影会被租约 token 拒绝，业务步骤再由版本检查、命令
幂等键和下游状态机共同防止重复副作用。等待状态没有变化时也不会增加业务版本、Trace
或通知。显式 `reconcile` 仍保留为“立即检查（故障兜底）”，不是正常流程的必点步骤。

`core/full` 脚本只用 Docker 启动 MySQL 与 Nacos，其他应用默认使用本机进程。曾经手工构建
模拟承运人镜像时，下载 `python:3.12-slim` 遇到过 Docker Hub `EOF`；这是镜像下载连接中断，
不是业务代码错误，也不阻塞当前本机 `full` 模式。网络恢复后需要容器镜像时再单独重试
`docker compose --env-file .env.local build carrier-simulator`。

浏览器地址：

- Nacos: <http://127.0.0.1:8848/nacos>
- 平台页面: <http://127.0.0.1:5173>
- 模拟承运人门户（仅 full）: <http://127.0.0.1:5174>
- MySQL: `127.0.0.1:3306`（仅供本地学习服务访问）

> `.env.local` 被 Git 忽略。不要填入企业账号、Token、地址或真实数据。

LINK 七步前端主线位于 `platform-web/src/components/link-booking/LinkBookingWizard.vue`；
异步提交、回调幂等和失败重试位于 `services/link-booking-service/app/services/` 与
`services/link-carrier-simulator/app/services/`。
