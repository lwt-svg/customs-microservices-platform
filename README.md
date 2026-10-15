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
├── scripts/                     core/full 一键启动、状态与停止脚本
├── docs/                        中文实验笔记
├── compose.yaml                 本地 MySQL、Nacos 与可选模拟承运人定义
└── .env.example                 安全的本地配置示例
```

第一次接手项目，先按下面顺序读基础笔记：

```text
docs/00-代码与笔记联合学习指南.md
docs/00-基础配置.md
docs/00-本地一键启动与停止.md
docs/01-项目边界技术选型与本机环境.md
docs/02-本地Docker-MySQL与Nacos启动.md
docs/03-Git安全配置与本地数据规则.md
docs/04-整体架构与服务通信.md
```

## 系统定位与学习路线

关务 OS 和供应链 OS 不是两个独立网站，也不需要分别启动两套前端。`platform-web` 是统一
的沙丘 OS 前端，`platform-bff` 是统一的 Java 平台入口。用户进入平台后，再从门户选择自己
要处理的业务域：

```text
沙丘 OS
├── 关务 OS：PDF 复核、LINK 七步填单、异步承运人闭环
└── 供应链 OS：装载任务、确定性规划、3D 复核、审批与正式版本
```

当前代码已经有组织、用户、角色、权限、菜单的数据模型，也有两个部门入口，但还没有完成
真实登录、按当前用户过滤门户和菜单、前端路由守卫以及 Java API 的 403 鉴权。因此目前的
两个入口是业务分区，不是已经验收通过的部门权限隔离。权限概念和验收边界统一放在第 11
章；后端实现应继续补进第 14 章，前端实现应继续补进第 15 章，不再新增重复的顶层权限章节。

建议按下面的主线学习：

```text
第 10 章：沙丘 OS 平台全局地图
第 11 章：用户、组织、角色、权限、菜单及当前安全边界
第 12～13 章：Java BFF 与 Nacos 跨服务基础
第 14 章：Java 平台数据模型和 RBAC 基础
第 15 章：Vue 统一门户、菜单和配置页面
第 20～22 章：关务 OS、PDF、LINK 七步和异步承运人
第 23 章：供应链智能装载、LangGraph、3D、审批和可选 LLM
```

第一次学习代码时，先阅读：

```text
docs/00-代码与笔记联合学习指南.md
```

进入 LINK 第一阶段代码前，再阅读：

```text
docs/21章-LINK代码学习导读.md
```

完成 21.6 后，继续阅读：

```text
docs/21.6-PDF解析与人工复核创建订舱草稿.md
docs/21章-LINK第二阶段完成验收.md
docs/22.0-主线七步填单与异步扩展学习路线.md
docs/22.6-模拟LINK七步填报与业务确认.md
docs/00-本地一键启动与停止.md
```

需要继续学习异步幂等、回调和失败重试时，再阅读：

```text
docs/22章-模拟承运人代码学习导读.md
docs/22.1-模拟承运人服务初始化.md
docs/22.2-模拟承运人门户.md
docs/22.3-异步提交与状态流转.md
docs/22.4-回调幂等与失败重试.md
docs/22.5-模拟LINK完整闭环验收.md
```

进入供应链 OS 和智能装载第 23 章前，先阅读总导读：

```text
docs/23章-供应链智能装载代码学习导读.md
```

总导读会再按当前完成状态引导阅读 `23.0`、`23.1`、`23.2`、`23.2.1`、`23.3`、`23.4`、
`23.5`、`23.6`、`23.7`、`23.8` 和 `23.9`。推荐继续阅读：

```text
docs/23.4-状态化装载规划智能体与确定性装托算法.md
docs/23.5-Three.js三维装载复核与人工审批.md
docs/23.6-企业级装载智能体闭环与版本化.md
docs/23.7-正式装载方案锁定与不可变版本.md
docs/23.8-LangGraph工作流编排与持久化恢复.md
docs/23.9-可选LLM方案解读Copilot.md
```

23.4/23.5 已实现“缺约束追问 → 确定性箱子到托盘计算 → 三方案比较 → 3D 复核 → 人工
批准/驳回”的学习闭环。23.6 又补充了运行版本、失败状态、算法/评分版本、服务端
`Idempotency-Key`、父子重规划、任务运行列表端点，以及工作台按任务恢复未完成 `runId`
和从驳回/失败运行重新规划的入口。
23.7 已完成 `APPROVED` 与 `LOCKED` 分层、三份不可变快照、SHA-256 和 V1/V2 正式版本链。
23.8 已把现有写入口接入无 LLM 的 LangGraph StateGraph、人工 interrupt 和本地 SQLite
Checkpointer；Java/Vue URL 与 DTO 保持不变。
23.9 在装载计算完成后增加可选的方案解读。它支持本地规则、本机 Codex CLI 和 OpenAI
Responses API 三种 Provider；无论外部模型是否可用，几何计算、硬约束校验、审批和正式
发布仍由原来的确定性链路负责。

如果你想理解“为什么 LangGraph 只负责编排、为什么 LLM 是可选解释层”，阅读：

```text
docs/23.8-LangGraph工作流编排与持久化恢复.md
docs/23.9-可选LLM方案解读Copilot.md
```

当前已有供应链业务控制塔 KPI、待办队列、运行时间线和版本历史；第 23.10 小节进一步设计
Prometheus、Grafana、OpenTelemetry、趋势和告警，这些生产可观测能力尚未实现。第 23.11
小节是装载单元到集装箱规划与 Three.js 复核的下一阶段设计，目前只有构造 benchmark，
没有集装箱算法、API、数据库和 3D 页面。其他未完成项还包括独立历史运行选择器、真实鉴权
与四眼审批、作业指导书导出、工程级运输安全规则和现场执行反馈。

这些导读给出了 Vue、Java、Nacos、Python 和 MySQL 的阅读顺序、关键方法、调用链、
调试断点、自动测试和手工验收，不需要只按文件名零散阅读。

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

Java BFF 启动时会由 Flyway 自动执行 `V1`–`V8`，不需要手工执行 Java SQL。
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

脚本把日志和 PID 放进 Git 忽略的 `runtime/`；停止时只处理脚本自己登记的应用进程，不会
按端口盲目杀掉用户原有进程。启动完成前还会通过 8080 请求装载任务列表，真实验证
Java BFF → Nacos → Python → MySQL，而不是只看端口。`load-planning-e2e.py` 同样只访问
8080，并覆盖两条独立链路：一条从方案评审经过驳回、父子重规划、批准到 `LOCKED V1`；
另一条从 `AWAITING_INPUT` 经 GET 恢复、补充约束、批准到 `LOCKED V1`。同时复核创建、
重规划和锁定重放，以及 list/current/detail 冻结快照一致性。

LangGraph 的 SQLite Checkpointer 随智能装载 Python 进程自动打开，保存在 Git 忽略的
`runtime/checkpoints/`，不增加新的端口或手工启动命令。

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

七步主线见 `docs/22.6-模拟LINK七步填报与业务确认.md`；异步扩展的完整确认、拒绝和失败
重试见 `docs/22.5-模拟LINK完整闭环验收.md`。
