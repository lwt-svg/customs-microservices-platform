# 智能装载构造示例数据

## 1. 目录用途

这个目录保存第 23 章使用的完全构造数据：

```text
synthetic-cargo-master.json
synthetic-pallet-benchmark.json
synthetic-container-benchmark.json
```

三个文件全部满足：

```text
data_classification = SYNTHETIC_DEMO
contains_enterprise_data = false
```

其中的组织、编号、SKU、尺寸、重量、数量和约束均为独立构造，不是对企业文件进行简单改名。

## 2. 使用限制

允许用于：

- 学习数据结构；
- 前端页面演示；
- 单元测试和集成测试；
- 托盘与集装箱算法开发；
- 3D 展示开发。

禁止用于：

- 真实仓库装载；
- 生产主数据；
- 运输安全认证；
- 推导真实客户或企业业务；
- 替代包装工程师或经过培训人员的确认。

## 3. 文件说明

### `synthetic-cargo-master.json`

演示货物包装主数据，重点学习：

```text
包装版本
标准单位
旋转限制
堆码状态
未知约束
来源追踪
允许用途
```

### `synthetic-pallet-benchmark.json`

演示箱子装托盘的算法输入，重点检查：

```text
全部箱子是否分配
是否越界
是否碰撞
是否超高
是否超重
是否违反旋转限制
```

### `synthetic-container-benchmark.json`

演示装载单元进入构造容器的算法输入，重点检查：

```text
是否能通过门
是否越过内部边界
是否碰撞
是否违反不可堆叠规则
总载荷是否超限
```

这里的容器尺寸也是构造数据，不是任何真实箱型的官方参数。

截至 2026-10-14，这个文件是项目中唯一已经整理成明确 JSON 契约的集装箱 benchmark。
它是第 23.11 的开发起点，不代表第 23.11 的算法和 3D 已经实现。

还要注意它目前只包含：

```text
容器和门洞构造尺寸
最大载荷构造值
装载单元构造尺寸、重量和方向
约束断言
```

它没有给出一套已经计算好的正式 `placements`。正确开发顺序是：

```text
读取 benchmark
→ 确定性算法生成柜内 XYZ
→ 独立验证 expected_assertions
→ Three.js 展示后端结果
```

不能让前端为了显示 3D 而自己随意拼一套位置。

LINK 订舱中的 `20GP`、`40GP`、`40HQ` 只是柜型标签，也不能替代这里所需的内部尺寸、门洞、
最大载荷和设备来源。真实装柜必须另外建立经过业务确认的设备主数据。

## 4. JSON 格式检查

在项目根目录运行：

```bash
python3 -m json.tool docs/examples/load-planning/synthetic-cargo-master.json >/dev/null
python3 -m json.tool docs/examples/load-planning/synthetic-pallet-benchmark.json >/dev/null
python3 -m json.tool docs/examples/load-planning/synthetic-container-benchmark.json >/dev/null
```

如果本机安装了 `jq`，也可以运行：

```bash
jq empty docs/examples/load-planning/*.json
```

## 5. 后续代码怎样读取

后续测试代码不应该直接把这些字段散落复制到测试方法中。建议：

```text
测试读取 JSON
→ 先验证 data_classification
→ 转成 Pydantic 或 Java DTO
→ 调用算法
→ 逐条执行 expected_assertions
```

生产代码加载数据时必须拒绝把 `SYNTHETIC_DEMO` 自动发布为正式主数据。

## 6. 对应章节

```text
23.0
→ 数据盘点、脱敏、来源和 benchmark 基线

23.4 / 23.5
→ 已实现的箱子装托盘算法与托盘 3D

23.11
→ 下一阶段的装载单元到集装箱算法与集装箱 3D
```

第 23.11 当前是设计章节。只有算法、后端校验、BFF、前端 3D、审批、版本和自动测试都落地
后，才能把它标为已完成。
