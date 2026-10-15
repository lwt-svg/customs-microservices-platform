# Shipment Case 合成基准

本目录保存第 24 章的完全合成发运案件。

所有文件必须满足：

```text
data_classification = SYNTHETIC_DEMO
contains_enterprise_data = false
```

允许用途：

- 领域建模；
- Java、Python 和 Vue 自动测试；
- Shipment Orchestrator 路由评测；
- 页面演示；
- 异常和安全测试。

禁止用途：

- 真实订舱；
- 真实报关；
- 真实仓库或运输决策；
- 宣称企业生产准确率；
- 推导真实客户、供应商或企业信息。

当前文件：

```text
synthetic-shipment-cases.json
```

它只提供案件级场景、期望路线和断言。后续章节会逐步增加完全构造的 PDF、Excel、EML
以及字段证据期望文件。

其中 `owner_organization_code` 是未来 Orchestrator 路由评测使用的场景元数据，不是当前
Shipment Case 创建 API 的请求字段。当前服务端仍统一把案件的
`responsible_organization_code` 写成 `DEMO_ORG`。

格式检查：

```bash
python3 -m json.tool \
  docs/examples/shipment-cases/synthetic-shipment-cases.json >/dev/null
```
