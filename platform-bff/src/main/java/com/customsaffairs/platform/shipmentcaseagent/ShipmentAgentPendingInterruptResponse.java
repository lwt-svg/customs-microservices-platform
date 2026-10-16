package com.customsaffairs.platform.shipmentcaseagent;

import java.util.List;
import java.util.Map;

/**
 * 展示给业务人员的当前人工待办。
 *
 * <p>{@code details} 只保存构建表单或解释待办所需的结构化业务字段，不包含模型隐藏推理。
 * {@code allowedActions} 由 Python 当前节点给出，前端不应自行猜测哪些按钮可用。</p>
 */
public record ShipmentAgentPendingInterruptResponse(
        ShipmentAgentInterruptType type,
        String title,
        String description,
        List<ShipmentAgentAction> allowedActions,
        Map<String, Object> details) {
}
