# 第 24 章｜Shipment Case 阶段验收

> 验收日期：2026-10-15。
>
> 本章已完成“确定性跨域案件骨架和人工平台闭环”。Vue、Java BFF、Nacos、Python Agent
> Runtime、LINK 只读验证和 MySQL 已经通过真实本机链路连接。
>
> 本章没有完成完整 Shipment Agent。文档理解、RAG、Shipment 总 LangGraph、真实身份与
> 权限、生产运维仍在当前验收范围之外。

## 1. 本章交付范围

用户已经可以在平台完成：

```text
创建 DRAFT 发运案件
-> 绑定已有装载任务
-> 绑定已有 LINK 订舱申请
-> 把 DRAFT 推进到 ACTIVE
-> 把 ACTIVE 推进到 COMPLETED
-> 查看 revision、资源引用和案件事件
```

系统在这条人工流程中提供：

```text
命令级 Idempotency-Key
乐观并发 expected_revision
资源存在性验证
资源唯一归属
最小案件状态机
只追加审计事件
Java 平台错误语义
Vue 409 刷新交互
真实本机 E2E
```

本章的“闭环”指人工可以从平台创建、关联、推进并审计一票案件。系统尚未根据单证和知识库
自主规划整票发运。

## 2. 已完成能力和代码证据

### 2.1 Python Shipment Case 领域后端

主要文件：

```text
services/load-planning-service/
├── app/api/shipment_cases.py
├── app/integrations/shipment_resources.py
├── app/models/shipment_case.py
├── app/repositories/shipment_cases.py
├── app/schemas/shipment_case.py
├── app/services/shipment_cases.py
├── alembic/versions/20261015_01_create_shipment_case_tables.py
├── alembic/versions/20261015_02_add_shipment_case_command_ledger.py
└── tests/
    ├── test_shipment_cases.py
    └── test_shipment_resource_verification.py
```

已验收：

| 能力 | 当前实现 |
| --- | --- |
| 案件持久化 | `shipment_cases` 保存案件主状态和 revision。 |
| 资源引用 | `shipment_case_resource_bindings` 保存稳定编号与绑定时验证快照。 |
| 审计事件 | `shipment_case_events` 按 sequence 只追加。 |
| 命令台账 | `shipment_case_commands` 单独保存 `APPLIED/NO_OP`、key 哈希、请求摘要和结果 revision。 |
| 事件保护 | `APPLIED` 命令以 `RESTRICT` 引用业务事件；误删事件不能释放已使用的 key。 |
| 创建幂等 | 创建事件与 `APPLIED` 命令在同一事务提交。 |
| 修改幂等 | key 哈希全局唯一；同一请求可重放，换案件、动作或 payload 返回 409。 |
| 业务 no-op | 重复绑定同一资源会登记 `NO_OP`，但不推进 revision 或伪造 `RESOURCE_BOUND`。 |
| 乐观并发 | 写请求必须携带 `expected_revision`。 |
| 状态机 | `DRAFT -> ACTIVE -> COMPLETED`，DRAFT/ACTIVE 可取消，终态不可继续推进。 |
| 资源归属 | 同一资源类型和编号只能绑定一个案件。 |
| 装载任务验证 | 通过本地异步数据库查询确认存在。 |
| LINK 验证 | 通过只读 HTTP 详情接口确认存在并记录当时状态。 |
| 事务边界 | LINK 等待发生在案件写事务之外，返回后重新锁行和检查。 |
| 迁移安全 | 02 在任何 DDL 前预检未知事件和历史重复 key；MySQL 已验证 `01 -> 02 -> 01 -> 02`。 |

Python 是 Shipment Case 业务事实的权威所有者。

### 2.2 Java BFF 平台入口

主要文件：

```text
platform-bff/src/main/java/com/customsaffairs/platform/shipmentcase/
├── ShipmentCaseClientProperties.java
└── ShipmentCaseRestClientConfiguration.java
platform-bff/src/test/java/com/customsaffairs/platform/
├── ShipmentCaseControllerTest.java
└── ShipmentCaseServiceTest.java
platform-bff/src/test/java/com/customsaffairs/platform/shipmentcase/
└── ShipmentCaseRestClientConfigurationTest.java
```

已验收五个接口：

```text
GET  /api/v1/platform/shipment-cases
GET  /api/v1/platform/shipment-cases/{caseNumber}
POST /api/v1/platform/shipment-cases
POST /api/v1/platform/shipment-cases/{caseNumber}/resources
POST /api/v1/platform/shipment-cases/{caseNumber}/status
```

Java 已完成：

```text
Jakarta Validation 基础校验
Idempotency-Key 原样透传
camelCase / snake_case 转换
通过 Nacos 查找 load-planning-service
400 / 404 / 409 / 502 / 503 平台错误翻译
不回显 Python 错误正文
成功响应使用 ApiResponse 外壳
Shipment Case 专用 connect / read timeout
连接失败或连接/读取超时稳定返回 503
其他 HTTP 客户端异常保持 502
不向浏览器泄漏底层异常或 Python 正文
专用超时配置不影响其他 RestClient
```

默认连接超时为 2 秒，读取超时为 15 秒。超时不是自动重试或熔断；写命令结果未知时，
调用方仍必须复用原 `Idempotency-Key`。

Java 没有创建 Shipment Case Entity、Repository 或业务表。它不保存 Python 案件副本。

### 2.3 Vue 发运案件中心

主要文件：

```text
platform-web/src/
├── api/platform.ts
├── components/shipment-case/
│   ├── ShipmentCaseCreateDrawer.vue
│   ├── ShipmentCaseDetailDrawer.vue
│   └── shipmentCasePendingCommands.ts
├── views/ShipmentCasesView.vue
├── router/index.ts
├── layouts/AdminLayout.vue
├── views/PortalView.vue
└── style.css
```

已验收：

```text
案件列表、搜索和状态/场景筛选
案件 KPI 和未绑定资源筛选
创建案件抽屉
案件详情工作台
装载任务与 LINK 申请绑定
案件状态推进
revision 和资源引用展示
审计时间线
加载、空状态和错误状态
网络结果未知时复用原幂等键
待确认命令写入同标签页 sessionStorage
关闭抽屉或刷新页面后可恢复原 key 和表单
详情命令只在 revision 未变化时恢复
成功、400、409、用户修改或重置时清除
网络异常和 5xx 时保留
TTL 为 6 小时，最多保留 24 条
409 后刷新权威案件，不自动重放写命令
```

项目不保证跨标签页、关闭浏览器会话后或跨设备恢复。`sessionStorage` 只是前端辅助，
Python 命令台账仍是最终幂等边界。

页面显示的 `resourceStatusAtVerification` 已明确标为“绑定时状态快照”。

### 2.4 本机真实 E2E

脚本：

```text
scripts/shipment-case-e2e.py
```

脚本固定通过 Java 8080 执行：

```text
创建合成装载任务
-> 创建 LINK 合成草稿
-> 创建 Shipment Case
-> 重放创建命令
-> 用同一 revision 并发绑定装载任务和 LINK，一条成功、一条 409
-> 刷新后完成另一资源绑定
-> 用新 key 重复绑定同一资源，验证成功 NO_OP
-> 复用该 NO_OP key 改绑另一资源，必须 409
-> 使用旧 revision 验证 409
-> DRAFT -> ACTIVE
-> 重放 ACTIVE 命令
-> ACTIVE -> COMPLETED
-> GET 复核最终绑定和事件
```

最终断言：

```text
revision 为 R1、R2、R3、R4、R5
两个资源各绑定一次
两个绑定都保存正确验证来源和状态快照
事件 sequence 连续为 1..5
幂等重放和成功 NO_OP 都不增加 revision 或事件
成功 NO_OP 的 key 不能改作另一 payload
同 revision 的并发绑定必须恰好一条成功、一条 409
409 不改变案件事实
最终有 5 条业务事件
最终有 6 条成功命令记录，其中 5 条 APPLIED、1 条 NO_OP
```

E2E 使用合成编号和合成数据，没有调用真实承运人，也没有读取企业真实订单。

本次真实通过的案件是：

```text
SHP-E2E-20261015112738-E488C4
COMPLETED / R5 / 2 个绑定 / 5 条事件 / 6 条命令记录
```

## 3. 自动化验收记录

本章收口时记录的结果：

```text
Python pytest：92 passed
Python Ruff：passed
Java Maven：118 tests passed，BUILD SUCCESS
Vue type-check：passed
Vue build：passed
本地脚本检查：passed
Shipment Case 并发与 NO_OP E2E：passed
```

对应命令：

```bash
cd platform-web
npm run type-check
npm run build

cd ../services/load-planning-service
conda run -n customs-affairs-os-lab python -m pytest
conda run -n customs-affairs-os-lab python -m ruff check .

cd ../../platform-bff
mvn clean test

cd ..
./scripts/check-local-scripts.sh
python3 scripts/shipment-case-e2e.py
git diff --check
```

测试数量会随以后新增测试变化。验收时应同时看是否为零失败，不能只记住数字。

## 4. 用户可重复执行的人工复测清单

页面：

```text
http://127.0.0.1:5173/tradeops/shipment-cases
```

下面的复选框故意保持未勾选，方便你把笔记复制到语雀后，使用自己的本机测试数据亲自操作
并记录结果。第 3 节单独记录了已经通过的自动 E2E；开发阶段也已检查案件列表、创建抽屉
和详情工作台的渲染。当前文档不宣称已经手工走完整套页面流程，也不宣称已经执行过两标签
页 409 操作。

准备一条未绑定装载任务和一条未绑定 LINK 草稿，再完成：

- [ ] 创建唯一编号的 DRAFT 案件，页面显示 `R1`。
- [ ] 绑定装载任务，页面显示 `R2`。
- [ ] 绑定 LINK 申请，页面显示 `R3`。
- [ ] 激活案件并填写原因，页面显示 `ACTIVE / R4`。
- [ ] 完成案件并填写原因，页面显示 `COMPLETED / R5`。
- [ ] 详情中有两个资源卡片，验证来源分别正确。
- [ ] 两个状态字段都写明“绑定时状态快照”。
- [ ] 时间线有五条事件，最新事件在页面上方。
- [ ] 终态案件不再显示普通绑定和状态推进表单。

两标签页并发复测：

- [ ] 两个标签页打开同一个案件。
- [ ] 标签页 A 成功写入后，标签页 B 使用旧 revision 提交。
- [ ] B 收到 409 并刷新到最新案件。
- [ ] B 的失败命令没有新增事件。
- [ ] 页面没有自动替用户重放写操作。

待确认命令恢复复测：

- [ ] 打开创建抽屉，填写唯一案件数据。
- [ ] 在浏览器开发者工具中临时切换为 Offline 后点击创建。
- [ ] 记录 `sessionStorage` 中保存的创建 key。
- [ ] 恢复 Online 并刷新同一标签页，确认表单和原 key 已恢复。
- [ ] 保持字段不变再次提交，确认请求继续使用原 key。
- [ ] 成功响应后，确认对应待确认记录已经清除。

## 5. 数据与身份边界

### 5.1 当前数据是合成学习数据

本章 E2E 创建：

```text
LP-SHIP-E2E-*
DEMO-SHIP-E2E-*
SHP-E2E-*
```

这些记录用于本机链路验收。当前没有足以支持真实企业发运决策的完整订单、箱规、托盘、
集装箱、路线和安全约束数据。

### 5.2 当前身份仍是占位值

Python 当前写入：

```text
responsible_organization_code = DEMO_ORG
actor_subject = SYSTEM_DEMO
actor_display_name = SYSTEM_DEMO
actor_source = SYSTEM_DEMO
```

这些值由服务端写入，浏览器请求体不能把它们转发成自己的声明。它们仍然只是演示占位值，
不能证明用户已经登录，也不能用于真实责任追溯。

Java 已有组织、用户、角色、权限和菜单的数据管理基础，但 Shipment Case 当前没有：

```text
真实 Session
可信当前用户
API 强制 RBAC
案件级查看和操作权限
可信身份传递到 Python
```

### 5.3 资源状态是绑定时快照

案件绑定表保存：

```text
resource_status_at_verification
verified_at
verification_source
```

它回答“绑定时验证到了什么”。装载任务或 LINK 申请后续发生变化时，案件不会自动得到实时
状态。页面不能把这个字段写成“当前状态”。

## 6. 当前还没有完成的能力

### 6.1 文档理解

Shipment Case 目前不会读取 Packing List、Commercial Invoice 或其他单证，也不会生成字段
证据、置信度和跨单证冲突结果。

### 6.2 RAG

当前没有把企业 SOP、CTU Code 摘要、路线规则或历史案件放入可检索知识库。系统也没有给
案件答案附上知识来源。

### 6.3 Shipment 总 LangGraph

第 23 章的装载 Agent 内部已经使用 LangGraph。Shipment Case 本章没有总 Graph，当前状态
推进来自人工操作和确定性 Service。

尚未实现的总编排包括：

```text
根据案件事实选择下一步专业能力
缺资料时暂停并追问
专业任务失败后的分支和恢复
人工审批节点
跨模块完成条件判断
```

### 6.4 真实身份、RBAC 和案件权限

当前用户不能通过登录身份获得或失去某个案件的访问权。关务、供应链和平台管理员之间也
没有完成 API 级权限隔离。

### 6.5 生产运维

当前有本机健康接口、启动脚本和日志，但还没有生产级：

```text
Metrics 和业务指标
分布式 Trace
告警规则和通知
审计检索与留存策略
备份恢复演练
多实例部署
Kubernetes 发布与回滚验收
Ops Copilot / 运维 Agent
```

## 7. 为什么当前还不能称为完整 Shipment Agent

一个完整 Shipment Agent 至少需要读取案件材料、结合知识证据判断缺口、选择工具、处理
失败分支、暂停等待人工，再根据新的事实继续运行。

当前 Shipment Case 已提供这些可靠基础：

```text
稳定案件编号
跨域资源引用
幂等命令
并发保护
状态和事件
平台操作界面
真实跨服务 E2E
```

这些能力让后续 Agent 有可恢复、可审计的业务载体。当前执行者仍是用户，系统负责验证和
保存确定性业务命令。

## 8. 可用于项目说明的准确表述

可以写：

```text
我实现了 Vue + Java BFF + Python Agent Runtime 的跨语言 Shipment Case 平台骨架。
系统通过 Nacos 发现 Python 服务，以幂等键、乐观并发、资源验证和只追加事件串联装载
任务与 LINK 订舱申请，并通过真实本机 MySQL 和 E2E 验证人工案件闭环。
```

当前不要写：

```text
系统已经完成全自动国际发运 Agent。
系统已经接入企业真实知识库和真实订单。
系统已经完成关务与供应链的生产权限隔离。
案件展示的是所有专业资源实时状态。
SYSTEM_DEMO 代表真实登录用户。
```

## 9. 路线调整记录

24.0 编写时，原计划把企业案件工作台和平台 E2E 放到第 27 章。实际开发到基础案件后端后，
如果只保留 Python 与 Java 接口，就无法从用户入口验证案件是否真正连通，也无法证明真实
Nacos、MySQL 和跨服务 DTO 能一起工作。

因此，实际开发顺序调整为：

```text
24.5
  提前完成基础 Vue 发运案件中心和详情工作台

24.8
  提前完成只经过 Java 8080 的真实本机平台 E2E
```

这次调整不需要回改 24.0。24.0 保存的是当时的产品边界和开发计划，24.5、24.8 与本节记录
后续发生的实施变化。按时间顺序保留两份记录，才能看见项目怎样从规划走到实际代码。

第 27 章不再重复建设基础案件页面和基础链路 E2E。更新后的 25～30 章路线如下，这些内容
仍是后续计划，不能按已完成功能验收：

| 章节 | 后续主题 | 目标边界 |
| --- | --- | --- |
| 第 25 章 | 文档理解、字段证据、冲突检查与 RAG | 让案件材料产生可追溯候选字段、冲突和知识依据，人工仍负责确认业务真值。 |
| 第 26 章 | Shipment 总 LangGraph | 编排案件级步骤、缺资料暂停、专业工具调用、人工节点和失败恢复。 |
| 第 27 章 | Agent Eval、回归集与生产准入 | 用固定样本和指标评价正确性、稳定性与退化行为，建立版本准入门槛。 |
| 第 28 章 | 登录、RBAC、案件权限与可信身份 | 把真实用户和组织上下文贯通 Java、Python、案件访问和审计。 |
| 第 29 章 | Metrics、Trace、告警与 Ops Copilot | 建立技术和业务可观测性，再让运维助手基于受控数据协助排查。 |
| 第 30 章 | Docker、Kubernetes、备份恢复与部署验收 | 完成容器部署、多实例、回滚、数据恢复和环境级验收。 |

这条路线把“功能能跑”与“Agent 可以被评价、授权和运维”分开验收。第 24 章负责可靠的案件
载体和人工闭环，后续章节在这个事实边界上增加智能能力与生产保障。

## 10. 验收结论

第 24 章可以按当前范围收口：

```text
确定性跨域案件骨架：完成
人工平台业务闭环：完成
Vue / Java / Python 真实链路：完成
本机合成数据 E2E：完成
```

项目整体仍需继续补齐：

```text
文档理解与证据
RAG
Shipment 总 LangGraph
真实身份、RBAC 和案件权限
生产监控、告警与部署运维
```

这两个结论可以同时成立：第 24 章已经交付了可靠案件底座，完整企业级 Shipment Agent
仍需要后续能力。
