# 本地模拟承运人门户

这是第 22 章的独立 Vue 3 门户，专门演示“承运人接收 LINK 订舱请求、人工处理、确认/拒绝、向 LINK 回调”的完整闭环。

它只连接本地构造服务 `http://127.0.0.1:8100`，不含登录，也不会连接真实承运人或真实业务账号。

## 启动

日常学习从仓库根目录启动完整异步环境：

```bash
./scripts/run-local.sh full
```

该命令会同时启动或复用模拟承运人服务和本门户。浏览器打开
`http://127.0.0.1:5174`。Vite 会把浏览器的 `/api` 请求转发到本地 `8100` 端口，所以
开发时不需要处理跨域。

只有单独调试本门户时，才在确认 8100 已健康后进入本目录执行：

```bash
npm install
npm run dev
```

常用检查：

```bash
npm run type-check
npm run build
```

## 建议阅读顺序

1. `src/api/carrier.ts`：先认识 API 路径、后端 snake_case 字段如何映射成页面使用的 camelCase。
2. `src/App.vue`：再阅读列表查询、详情选择、开始处理、确认/拒绝和回调重试的事件函数。
3. `src/style.css`：最后看状态颜色、时间线和移动端布局。

## 页面操作链路

```text
模拟订舱列表
  → 选择一条请求
  → RECEIVED 状态下“开始处理”
  → PROCESSING 状态下“确认订舱”或“拒绝订舱”
  → 独立模拟服务创建回调投递记录
  → 在“回调投递”中查看状态；RETRY_WAIT 或 DEAD 可“重新投递”
```

门户使用的后端 API：

```text
GET  /api/v1/portal/bookings?status=&keyword=
GET  /api/v1/portal/bookings/{id}
POST /api/v1/portal/bookings/{id}/process
POST /api/v1/portal/bookings/{id}/decision
GET  /api/v1/portal/callback-deliveries
POST /api/v1/portal/callback-deliveries/{id}/retry
```

`decision` 请求中，确认传 `{"decision":"CONFIRMED"}`；拒绝传 `{"decision":"REJECTED","rejection_reason_code":"DEMO-舱位不足"}`。构造订舱参考号由服务端统一生成，避免门户和服务端各自生成不同的编号规则。回调状态中，`RETRY_WAIT` 表示等待自动重试，`DEAD` 表示自动重试已耗尽；这两种状态都可以由人工点击“重新投递”。
