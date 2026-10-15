# 第 24 章｜Shipment Case 代码学习导读

> 本章代码已经形成一条可运行的平台链路：
>
> ```text
> Vue
> -> Java platform-bff
> -> Python Agent Runtime
> -> MySQL / LINK 只读验证接口
> ```
>
> 当前成果是“确定性跨域案件骨架和人工平台闭环”。用户可以创建案件、绑定已有装载任务
> 和 LINK 订舱申请、推进案件状态、处理 revision 冲突并查看事件时间线。
>
> Shipment Case 目前没有总 LangGraph，也没有让 LLM 自主决定下一步。真实登录、RBAC、
> 案件权限、文档理解、RAG 和生产运维仍未接入。

## 1. 这篇导读怎样使用

第 24 章的小节按开发顺序记录了每一步。本文换一种读法，带你沿真实请求穿过三种语言，
再从数据所有权的方向倒着读一遍。

推荐分两轮：

```text
第一轮：Vue -> Java -> Python
目标：跟踪一次“绑定装载任务”的 HTTP 请求

第二轮：Python Model / Service -> Java DTO -> Vue
目标：判断每个字段由谁保存、谁决定、谁只负责展示
```

第一次阅读先不要钻进每一行异常恢复代码。你需要先回答三个问题：

1. 浏览器发出的请求先到哪里？
2. 哪一层决定资源能不能绑定？
3. 最终的 revision、绑定记录和事件由谁写入数据库？

第 24 章当前没有单独的 `24.6`、`24.7` 文件。开发过程中页面闭环完成后，下一份独立笔记
直接记录平台 E2E，因此编号跳到 `24.8`；复制到语雀时不是漏了两篇。

## 2. 学习前准备

项目目录：

```bash
cd /Users/apple/vscodeproject/customs-affairs-os-python-lab
```

启动核心环境：

```bash
./scripts/run-local.sh core
```

页面地址：

```text
http://127.0.0.1:5173/tradeops/shipment-cases
```

这一条学习链路会用到：

| 进程 | 端口 | 本次作用 |
| --- | ---: | --- |
| Vue | 5173 | 页面、表单、命令键和错误交互。 |
| Java BFF | 8080 | 平台入口、DTO 转换、Nacos 查询和错误翻译。 |
| Python Agent Runtime | 8200 | Shipment Case 业务规则、事务和持久化。 |
| LINK Python | 8000 | 绑定 LINK 申请时提供只读详情。 |
| Nacos | 8848 | 让 Java 按服务名找到 Python。 |
| MySQL | 3306 | 保存装载任务和 Shipment Case 业务事实。 |

`core` 已包含当前页面需要的服务。模拟承运人异步扩展不参与本章的案件绑定。

为了手工走“绑定装载任务”，先准备：

```text
一条 DRAFT Shipment Case
一条尚未绑定给其他案件的装载任务
```

不要使用已经完成的 E2E 案件继续绑定。`COMPLETED` 和 `CANCELLED` 都是终态。

## 3. 第一轮：Vue -> Java -> Python 跟踪一次请求

本轮只跟踪一个动作：

```text
在案件详情中输入装载任务编号
-> 点击“验证并绑定”
-> 页面从 R1 变为 R2
-> 时间线增加一条 RESOURCE_BOUND
```

浏览器发送给 Java 的请求形状大致如下：

```http
POST /api/v1/platform/shipment-cases/SHP-LEARN-001/resources
Idempotency-Key: shipment-resource-bind-...
Content-Type: application/json
```

```json
{
  "resourceType": "LOAD_PLANNING_TASK",
  "resourceNumber": "LP-LEARN-001",
  "expectedRevision": 1
}
```

这里有两个不同的并发保护：

```text
Idempotency-Key
  识别“这是不是同一条命令的网络重试”

expectedRevision
  识别“用户操作时看到的案件版本是否仍然是最新版本”
```

它们不能互相替代。

### 3.1 Vue 详情抽屉组装业务命令

文件：

```text
platform-web/src/components/shipment-case/ShipmentCaseDetailDrawer.vue
```

先看 `loadDetail()`：

```ts
detail.value = await getShipmentCase(caseNumber)
```

详情抽屉打开时会重新查询案件。写请求使用的 `expectedRevision` 来自这次详情响应，而不是
父页面列表中可能已经过期的行数据。

然后按顺序读：

```text
validateResource()
-> resourceCommandKey()
-> bindResource()
```

`validateResource()` 只做页面即时校验，例如编号不能为空、格式是否合法。Java 和 Python
仍会再次校验，不能把前端校验当成安全边界。

`resourceCommandKey()` 保存：

```text
fingerprint = JSON.stringify(input)
key = shipment-resource-bind-<UUID>
```

同一份输入在网络结果不明确时再次提交，会复用原 key。用户修改资源编号或 revision 后，
输入指纹改变，页面才生成新 key。

`bindResource()` 负责：

```text
读取 detail.revision
-> 组成 BindShipmentCaseResourceInput
-> 取得本次命令的 Idempotency-Key
-> 调用 bindShipmentCaseResource()
-> 用服务端完整响应替换 detail
```

成功后页面不会自行执行：

```text
revision + 1
resourceBindings.push(...)
events.push(...)
```

这些字段由 Python 决定。Vue 只使用服务端返回的新快照。

如果 Java 返回 409，代码进入：

```text
refreshAfterConflict()
```

页面会清除旧命令键并重新 GET 案件，不会自动重放写请求。用户看完最新 revision 后，再
决定是否提交新命令。

### 3.2 Vue API 层发送 camelCase 请求

文件：

```text
platform-web/src/api/platform.ts
```

先找类型：

```text
ShipmentCase
ShipmentCaseResourceBinding
ShipmentCaseEvent
BindShipmentCaseResourceInput
```

再找函数：

```text
bindShipmentCaseResource()
requestWithOptions()
PlatformApiError
```

`bindShipmentCaseResource()` 把命令键放在 Header：

```ts
headers: {
  'Content-Type': 'application/json',
  'Idempotency-Key': idempotencyKey,
}
```

浏览器只请求相对地址：

```text
/api/v1/platform/shipment-cases/...
```

`platform-web/vite.config.ts` 会在本地开发时把 `/api` 转发到：

```text
http://127.0.0.1:8080
```

所以 Vue 不需要知道 Python 的 8200 端口。

`requestWithOptions()` 把非 2xx 响应转换成带 `status` 的 `PlatformApiError`。详情抽屉正是
靠 `status === 409` 区分并发冲突和普通错误。

### 3.3 Java Controller 接住平台请求

相关文件：

```text
platform-bff/src/main/java/com/customsaffairs/platform/shipmentcase/
├── ShipmentCaseController.java
└── BindShipmentCaseResourceRequest.java
```

入口方法：

```java
ShipmentCaseController.bindResource()
```

Spring 在调用这个方法前会完成：

```text
匹配 URL
-> 把 JSON 反序列化为 BindShipmentCaseResourceRequest
-> 执行 @Valid
-> 读取 Idempotency-Key Header
```

`BindShipmentCaseResourceRequest` 是 Java `record`。可以把它理解为一个不可变的请求数据
对象，作用接近 Pydantic 请求模型。

它检查：

```text
resourceType 必填
resourceNumber 格式和长度
expectedRevision 必须大于 0
```

Controller 不查数据库，也不判断资源是否存在。它只调用：

```java
shipmentCaseService.bindResource(caseNumber, request, idempotencyKey)
```

### 3.4 Java Service 找到 Python 并转换字段

先按下面顺序看三个文件：

```text
platform-bff/src/main/java/com/customsaffairs/platform/shipmentcase/
├── ShipmentCaseClientProperties.java
├── ShipmentCaseRestClientConfiguration.java
└── ShipmentCaseService.java
```

`ShipmentCaseClientProperties` 接收连接和读取超时；`ShipmentCaseRestClientConfiguration`
用克隆后的 Builder 创建 Shipment Case 专用客户端；`ShipmentCaseService` 才负责服务
发现、字段转换和请求转发。默认值是：

```text
connect timeout = 2s
read timeout = 15s
```

超时不等于自动重试，也不等于熔断。写命令若没有拿到确定 HTTP 结果，调用方仍只能携带
相同 `Idempotency-Key` 重试，不能让 Java 重新生成 key 或无条件重放。

再读 Service 的调用链：

```text
bindResource()
-> RemoteBindResourceRequest.from()
-> buildUri()
-> post()
-> handleRemoteError()
-> RemoteShipmentCase.toPlatformResponse()
```

`RemoteBindResourceRequest` 使用 `@JsonProperty` 把 Java 字段转换成 Python 请求字段：

```text
resourceType      -> resource_type
resourceNumber    -> resource_number
expectedRevision  -> expected_revision
```

`buildUri()` 调用：

```java
discoveryClient.getInstances("load-planning-service")
```

Java 从 Nacos 取得实例 URI，再拼出：

```text
/api/v1/shipment-cases/{case_number}/resources
```

Nacos 只返回服务地址。真正的 HTTP 请求仍由 Java `RestClient` 发出。

`post()` 原样转发 `Idempotency-Key`。它不会 trim、替换或重新生成命令键，否则 Python
无法把重试识别成同一条命令。

`handleRemoteError()` 当前执行这些稳定映射：

| Python 状态 | Java 平台状态 |
| ---: | ---: |
| 404 | 404 |
| 409 | 409 |
| 400 / 422 | 400 |
| 502 | 502 |
| 503 | 503 |
| 其他错误 | 502 |

Java 不把 Python 错误正文回显给浏览器。资源不存在目前由 Python 返回 422，再被 Java
归一为平台 400。

### 3.5 Python API 组装依赖并翻译异常

文件：

```text
services/load-planning-service/app/api/shipment_cases.py
```

入口函数：

```python
bind_shipment_case_resource()
```

依赖组装顺序：

```text
get_session_factory()
-> get_shipment_resource_verifier()
-> get_shipment_case_service()
-> bind_shipment_case_resource()
```

`get_shipment_resource_verifier()` 注册两个验证器：

```text
LoadPlanningTaskResourceVerifier
LinkBookingApplicationResourceVerifier
```

API 层不写 SQL。它调用 `ShipmentCaseService.bind_resource()`，再由 `_raise_http()` 把领域
异常变成 HTTP 状态。

请求模型在：

```text
services/load-planning-service/app/schemas/shipment_case.py
```

对应类：

```python
ShipmentCaseResourceBind
```

Python 请求使用 snake_case，并配置 `extra="forbid"`。组织、actor 和幂等键都不在请求
正文中。

### 3.6 Python Service 执行三阶段绑定

文件：

```text
services/load-planning-service/app/services/shipment_cases.py
```

核心方法：

```python
ShipmentCaseService.bind_resource()
```

这是本轮最值得精读的方法。它把绑定拆成三个阶段。

阶段 A 使用短锁行事务：

```text
案件是否存在
-> 同一幂等命令是否已经成功
-> 资源是否已经绑定
-> 已属于本案件时写 NO_OP 命令台账
-> 案件是否仍允许绑定资源
```

因此阶段 A 不是纯读取。它可能提交一条 `NO_OP`，但会在进入阶段 B 之前结束事务。LINK
等待期间仍然没有案件事务或案件行锁。

找到相同幂等键和相同请求摘要时，Service 直接返回已有结果。这个检查早于 revision 校验，
因此第一次命令成功后，网络重试即使仍携带旧 revision，也能得到原结果。

阶段 B 关闭案件事务后验证资源：

```python
verified = await self.resource_verifier.verify(...)
```

装载任务通过本地数据库只读查询验证。LINK 申请通过独立 HTTP 请求验证。远程等待期间不
持有案件写事务，也不锁住案件行。

阶段 C 新建写事务并重新检查：

```text
SELECT 案件 FOR UPDATE
-> 再查命令台账
-> 再查资源归属
-> 并发期间已由本案件绑定时写 NO_OP
-> 再查终态限制
-> 校验 expectedRevision
-> revision + 1
-> 新增资源绑定
-> 追加 RESOURCE_BOUND 事件
-> 写 APPLIED 命令台账
-> flush
-> 事务提交
```

阶段 A 与阶段 C 都检查同一批关键条件，因为阶段 B 等待期间，其他请求可能已经修改案件。

### 3.7 资源验证器读取源系统

文件：

```text
services/load-planning-service/app/integrations/shipment_resources.py
```

绑定装载任务时进入：

```python
LoadPlanningTaskResourceVerifier.verify()
```

它按 `task_number` 查询 `LoadPlanningTask`，并返回：

```python
VerifiedShipmentResource(
    resource_type=...,
    resource_number=...,
    resource_status=...,
    verification_source="LOCAL_LOAD_PLANNING_DATABASE",
    verified_at=...,
)
```

绑定 LINK 申请时进入：

```python
LinkBookingApplicationResourceVerifier.verify()
```

它调用 LINK 的只读详情接口，设置短超时，并把 404、超时、5xx 和协议错误分开处理。

`VerifiedShipmentResource.resource_status` 只表示验证当时看到的状态。案件绑定成功后，源
系统状态可能继续变化，绑定表不会自动同步。

### 3.8 Repository 和 Model 写入权威事实

相关文件：

```text
services/load-planning-service/app/
├── repositories/shipment_cases.py
└── models/shipment_case.py
```

`ShipmentCaseRepository` 负责 SQLAlchemy 查询和加载策略。重点看：

```text
get_by_number(..., for_update=True)
get_command_by_idempotency_key_hash()
get_binding_by_resource()
flush()
```

Model 定义四张表：

```text
ShipmentCase
ShipmentCaseResourceBinding
ShipmentCaseEvent
ShipmentCaseCommand
```

绑定成功后，数据库中的变化是：

```text
shipment_cases.revision: 1 -> 2
shipment_case_resource_bindings: 新增 1 行
shipment_case_events: 新增 sequence=2 的 RESOURCE_BOUND
shipment_case_commands: 新增 result_kind=APPLIED
```

如果资源原本已经属于本案件，成功 no-op 只新增 `ShipmentCaseCommand(result_kind=NO_OP)`：

```text
revision 不变
binding 数量不变
RESOURCE_BOUND 数量不变
该 Idempotency-Key 仍被永久占用
```

唯一约束：

```text
UNIQUE(resource_type, resource_number)
```

保证同一个装载任务或 LINK 申请不能同时属于两个案件。

### 3.9 响应怎样返回页面

Python 用 `ShipmentCaseRead` 生成 snake_case JSON。Java 的
`RemoteShipmentCase.toPlatformResponse()` 把它转换成 `ShipmentCaseResponse`，Controller
再包入统一的 `ApiResponse`。

Vue 最终拿到 camelCase：

```json
{
  "caseNumber": "SHP-LEARN-001",
  "revision": 2,
  "resourceBindings": [
    {
      "resourceType": "LOAD_PLANNING_TASK",
      "resourceNumber": "LP-LEARN-001",
      "resourceStatusAtVerification": "DRAFT",
      "verificationSource": "LOCAL_LOAD_PLANNING_DATABASE"
    }
  ],
  "events": [
    {
      "sequence": 2,
      "eventType": "RESOURCE_BOUND"
    }
  ]
}
```

详情抽屉把 `detail.value` 替换成这份响应，并向父页面发出 `updated`。父页面
`ShipmentCasesView.vue` 再通过 `replaceCase()` 更新列表。

## 4. 第一轮推荐断点

如果你使用 IDE 分别启动 Vue、Java 和 Python，可以按下面顺序打断点。命令行启动也可以
先用浏览器 Network 面板观察请求，再对照代码阅读。

| 顺序 | 文件 | 函数或位置 | 观察内容 |
| ---: | --- | --- | --- |
| 1 | `ShipmentCaseDetailDrawer.vue` | `bindResource()` | `input`、`idempotencyKey`、`detail.revision`。 |
| 2 | `platform.ts` | `requestWithOptions()` 调用 `fetch` 前 | URL、Header 和 camelCase body。 |
| 3 | `ShipmentCaseController.java` | `bindResource()` | Spring 已生成的 request record。 |
| 4 | `ShipmentCaseService.java` | `RemoteBindResourceRequest.from()` | camelCase 怎样变成 snake_case。 |
| 5 | `ShipmentCaseService.java` | `buildUri()` | Nacos 返回的 Python 实例 URI。 |
| 6 | `shipment_cases.py` API | `bind_shipment_case_resource()` | FastAPI 已解析的 payload 和 Header。 |
| 7 | `shipment_cases.py` Service | `bind_resource()` 阶段 A | 当前 revision、命令台账和已有绑定。 |
| 8 | `shipment_resources.py` | `LoadPlanningTaskResourceVerifier.verify()` | 源资源编号、状态和验证来源。 |
| 9 | `shipment_cases.py` Service | 阶段 C 的 `_ensure_revision()` | 锁行后的最新 revision。 |
| 10 | `shipment_cases.py` Service | `_event()` / `_command()` | 业务事件与命令结果怎样分开写。 |
| 11 | `ShipmentCaseService.java` | `RemoteShipmentCase.toPlatformResponse()` | Python 响应到 Java DTO 的转换。 |
| 12 | `ShipmentCaseDetailDrawer.vue` | `detail.value = changed` | 页面收到的 R2 完整快照。 |

一次正常绑定应观察到：

```text
R1
-> 验证装载任务存在
-> 写入绑定和事件
-> R2
```

## 5. 第二轮：从 Python 事实到 Vue 展示

第一轮说明请求怎样流动。第二轮倒着读，判断每一层拥有什么。

### 5.1 Python Model 定义可持久化事实

先打开：

```text
services/load-planning-service/app/models/shipment_case.py
```

按类阅读：

```text
ShipmentCaseStatus
ShipmentTransportScenario
ShipmentResourceType
ShipmentCase
ShipmentCaseResourceBinding
ShipmentCaseEvent
ShipmentCaseCommand
```

这里定义数据库字段、关系和唯一约束。`case_number` 是稳定业务编号，UUID `id` 主要用于
数据库关系。

`resource_status_at_verification` 的准确含义是：

```text
绑定命令执行时，从源系统读取到的状态
```

它不是装载任务或 LINK 申请的实时状态。

### 5.2 Python Service 决定命令是否合法

继续读：

```text
services/load-planning-service/app/services/shipment_cases.py
```

建议顺序：

```text
_ALLOWED_STATUS_TRANSITIONS
-> create()
-> bind_resource()
-> change_status()
-> _ensure_revision()
-> _ensure_case_accepts_resources()
-> _event()
-> _command()
```

Model 说明数据能怎样保存，Service 决定业务命令能否执行。

例如：

```text
DRAFT -> ACTIVE
ACTIVE -> COMPLETED
DRAFT / ACTIVE -> CANCELLED
```

这些转换只在 Python 维护。Java 枚举和 Vue 按钮帮助输入与展示，但不能代替 Python 状态机。

### 5.3 Java DTO 只改变平台接口形状

再打开：

```text
platform-bff/src/main/java/com/customsaffairs/platform/shipmentcase/
├── ShipmentCaseResponse.java
├── ShipmentCaseResourceBindingResponse.java
├── ShipmentCaseEventResponse.java
└── ShipmentCaseService.java
```

`ShipmentCaseResponse` 是返回给 Vue 的 DTO。它不是 JPA Entity，也没有对应 Java
Repository。

Java 保存的相关数据只有平台菜单迁移：

```text
platform-bff/src/main/resources/db/migration/
V9__seed_shipment_case_menu.sql
```

V9 只让页面入口出现在菜单中。Shipment Case 四张业务表由 Python Alembic 管理：

```text
20261015_01：案件、绑定、事件
20261015_02：独立命令台账，并迁移旧幂等字段
```

### 5.4 Vue 类型和组件只保存页面状态

最后读：

```text
platform-web/src/api/platform.ts
platform-web/src/views/ShipmentCasesView.vue
platform-web/src/components/shipment-case/ShipmentCaseCreateDrawer.vue
platform-web/src/components/shipment-case/ShipmentCaseDetailDrawer.vue
platform-web/src/components/shipment-case/shipmentCasePendingCommands.ts
```

Vue 保存的是：

```text
列表当前加载结果
搜索和筛选条件
抽屉是否打开
表单输入
一次网络结果未知时待复用的命令键
```

普通筛选条件、抽屉开关等页面状态可以在刷新后丢失。待确认写命令单独保存在同一标签页的
`sessionStorage` 中：关闭再打开抽屉或刷新页面后可以恢复原 key；详情命令还要求
`expectedRevision` 没有变化。记录最多保留 24 条，TTL 为 6 小时。

项目不保证跨标签页、浏览器会话或设备恢复。案件、revision、资源绑定、事件和最终幂等事实
始终保存在 Python 管理的 MySQL 中，`sessionStorage` 只是浏览器侧的安全重试辅助。

### 5.5 事实所有权表

| 数据或规则 | 权威拥有者 | Java 的作用 | Vue 的作用 |
| --- | --- | --- | --- |
| 案件状态和 revision | Python Shipment Case Service / MySQL | 转换并转发 | 展示服务器结果 |
| 状态转换规则 | Python `ShipmentCaseService` | 枚举约束输入，不复制状态机 | 只显示当前可能按钮 |
| 资源唯一归属 | Python 数据库唯一约束和 Service | 翻译 409 | 刷新后让用户重试 |
| 装载任务当前状态 | 装载模块 | 不保存 | 从案件只能看到绑定时快照 |
| LINK 申请当前状态 | LINK 服务 | 不保存 | 从案件只能看到绑定时快照 |
| 绑定时资源状态 | Python Shipment Case 绑定表 | DTO 转换 | 明确标成“绑定时状态快照” |
| 案件事件 | Python 只追加事件表 | DTO 转换 | 按 sequence 展示时间线 |
| 成功命令与 `NO_OP` | Python 命令台账 | 原样透传 key，不保存副本 | 网络结果未知时复用原 key |
| `DEMO_ORG` | Python 当前演示占位值 | 原样返回 | 显示并注明占位 |
| `SYSTEM_DEMO` | Python 当前演示占位值 | 原样返回 | 只能当演示审计字段 |
| 筛选条件和抽屉状态 | 浏览器 | 无 | Vue 本地状态 |

`DEMO_ORG` 和 `SYSTEM_DEMO` 不能用于真实责任追溯。Java 创建请求 record 没有 actor 或组织
字段，浏览器多传的字段当前不会进入 Service，但系统尚未建立登录用户和可信身份链路。

## 6. Python 学习者怎样读 Java

你不需要先学完整套 Spring 才能读懂本章 Java。先掌握这组对应关系：

| Java | 接近的 Python 概念 | 本章用途 |
| --- | --- | --- |
| `@RestController` | FastAPI `APIRouter` | 声明平台路由。 |
| `record` | Pydantic Model / dataclass | 承载请求和响应字段。 |
| `@Valid` | Pydantic 字段校验 | 在进入 Service 前做基础格式校验。 |
| `ShipmentCaseService` | HTTP adapter / application service | 找 Python、转换字段、翻译错误。 |
| `DiscoveryClient` | 服务注册中心客户端 | 按服务名找 Python。 |
| `RestClient` | `httpx.Client` | 发出 HTTP 请求。 |
| `ResponseStatusException` | `HTTPException` | 返回稳定平台状态。 |

阅读 Java 时先找：

```text
Controller 方法
-> Service 同名方法
-> ShipmentCaseClientProperties
-> ShipmentCaseRestClientConfiguration
-> Remote Request.from()
-> buildUri()
-> RestClient
-> Remote Response.toPlatformResponse()
```

其余 Spring 注解可以在第二遍补学。

## 7. 建议练习

### 练习一：画出一次绑定请求

手工写下这条链：

```text
bindResource()
-> bindShipmentCaseResource()
-> ShipmentCaseController.bindResource()
-> ShipmentCaseService.bindResource()
-> Python bind_shipment_case_resource()
-> ShipmentCaseService.bind_resource()
-> LoadPlanningTaskResourceVerifier.verify()
-> ShipmentCaseRepository.flush()
```

然后在每一段旁边标出：

```text
camelCase 还是 snake_case
有没有数据库事务
谁拥有 Idempotency-Key
谁检查 expectedRevision
```

### 练习二：观察幂等重放

先读测试：

```text
services/load-planning-service/tests/test_shipment_cases.py
test_binding_idempotency_replay_wins_over_stale_revision
```

重点观察：

```text
第一次请求成功后 revision 已变化
第二次仍携带原 expected_revision
同一个 key 和同一个 payload 返回原结果
revision 和事件数量都不再增加
```

### 练习二点一：区分成功 NO_OP 与业务事件

继续读：

```text
test_repeated_binding_is_domain_idempotent_without_new_revision
test_concurrent_service_binding_loser_records_noop_command
```

观察第二个 key 重复绑定同一资源：

```text
HTTP / Service 返回成功
shipment_case_commands 新增 NO_OP
revision 不变
RESOURCE_BOUND 不增加
```

然后用这个已经成功的 key 改绑另一资源，必须得到 409，而且资源验证器不应再被调用。
这两个测试专门防止“no-op 直接返回却不登记 key”的漏洞。

### 练习三：观察旧 revision

先读：

```text
test_stale_revision_cannot_append_a_different_command
```

再用两个浏览器标签页打开同一个案件。A 完成一次绑定后，B 使用旧 revision 提交另一条
命令。预期结果：

```text
Java 返回 409
Vue 重新 GET
案件不会增加事件
页面不会自动重放写请求
```

### 练习四：比较两种资源验证

对比：

```text
LoadPlanningTaskResourceVerifier.verify()
LinkBookingApplicationResourceVerifier.verify()
```

回答：

1. 本地装载任务为什么可以直接查数据库？
2. LINK 为什么通过 HTTP？
3. LINK 超时时，为什么案件 revision 不能改变？
4. 验证等待为什么放在案件写事务外？

对应测试：

```text
services/load-planning-service/tests/
├── test_shipment_cases.py
└── test_shipment_resource_verification.py
```

### 练习五：从 E2E 找跨层证据

文件：

```text
scripts/shipment-case-e2e.py
```

先读：

```text
BffClient.request()
post_idempotent_case_command()
race_resource_bindings()
run_e2e()
```

脚本只访问 Java 8080。`run_e2e()` 最终检查：

```text
revision 连续为 1..5
两个资源绑定都存在
绑定时状态和验证来源正确
五条事件 sequence 连续
幂等重放没有生成重复事件
相同 revision 的两条绑定真实并发时一条成功、一条 409
刷新后完成 loser 绑定
新 key 重复绑定同一资源得到成功 NO_OP
这个 NO_OP key 换 payload 后返回 409
```

## 8. 推荐测试阅读顺序

Python：

```text
test_create_list_and_detail_start_with_one_audited_revision
test_create_is_idempotent_and_rejects_reusing_key_for_new_parameters
test_bind_existing_load_planning_task_through_real_local_verifier
test_repeated_binding_is_domain_idempotent_without_new_revision
test_concurrent_service_binding_loser_records_noop_command
test_binding_idempotency_replay_wins_over_stale_revision
test_stale_revision_cannot_append_a_different_command
test_draft_active_completed_flow_and_terminal_guard
```

Java：

```text
ShipmentCaseControllerTest
├── shouldCreateCaseWith201AndForwardIdempotencyHeader
├── shouldForwardResourceCommandAndItsIdempotencyHeader
└── shouldRejectNonPositiveExpectedRevisionBeforeCallingPython

ShipmentCaseServiceTest
├── shouldCreateCaseWithSnakeCaseAndForwardIdempotencyKeyUnchanged
├── shouldBindResourceWithRevisionAndForwardIdempotencyKey
├── shouldTranslateRemoteErrorsWithoutLeakingPythonBody
├── shouldReturnServiceUnavailableWhenNacosHasNoPythonInstance
└── shouldTranslateTransportTimeoutToStableServiceUnavailableWithoutLeakingCause

ShipmentCaseRestClientConfigurationTest
├── shouldUseSafeLocalDefaults
├── shouldApplyConfiguredTimeoutsWithoutMutatingBuilderUsedByOtherClients
└── shouldRejectZeroOrNegativeTimeouts
```

跨层：

```text
scripts/shipment-case-e2e.py
```

单元测试帮助你理解某一层的规则，E2E 证明这些层通过真实 HTTP、Nacos 和 MySQL 接到了一起。

## 9. 读完后应该能解释什么

你可以用自己的话回答下面的问题，就算读懂了第 24 章的主体代码：

1. Vue 为什么只请求 `/api/v1/platform/...`？
2. Java 为什么有 `ShipmentCaseResponse`，却没有 Shipment Case Entity？
3. Nacos 找到服务后，谁真正发送 HTTP？
4. `Idempotency-Key` 和 `expectedRevision` 分别防什么问题？
5. LINK 验证为什么不能放在案件写事务中等待？
6. 为什么阶段 C 必须重新检查 revision 和资源归属？
7. 为什么绑定表保存的是资源状态快照，而不是实时状态？
8. `DEMO_ORG` 和 `SYSTEM_DEMO` 为什么不能当成真实身份？
9. 为什么当前页面闭环成立，却还不能称为完整 Shipment Agent？

最后一个问题的答案应落在代码事实上：当前 Shipment Case 接受人工命令并执行确定性规则；
它还没有文档理解、RAG、总工作流自主编排和真实权限上下文。
