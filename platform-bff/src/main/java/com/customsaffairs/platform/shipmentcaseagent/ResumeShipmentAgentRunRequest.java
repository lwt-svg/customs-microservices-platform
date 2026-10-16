package com.customsaffairs.platform.shipmentcaseagent;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.util.List;

/**
 * 对 Shipment Agent 当前人工中断作出结构化决定。
 *
 * <p>{@code expectedRunVersion} 防止两个页面同时处理同一待办；{@code interruptType} 防止
 * 浏览器拿旧弹窗去回答新的中断。可选字段只在对应动作需要时使用，具体业务组合仍由 Python
 * 工作流统一校验，Java BFF 不复制一份容易漂移的状态机。</p>
 */
public record ResumeShipmentAgentRunRequest(
        @Positive(message = "expectedRunVersion 必须大于 0。")
        int expectedRunVersion,

        @NotNull(message = "interruptType 不能为空。")
        ShipmentAgentInterruptType interruptType,

        @NotNull(message = "action 不能为空。")
        ShipmentAgentAction action,

        String comment,
        String selectedResourceNumber,
        List<String> requiredCapabilities,

        @Size(max = 500, message = "dataFacts 一次不能超过 500 条。")
        List<@NotNull @Valid ShipmentDataFactRequest> dataFacts) {
}
