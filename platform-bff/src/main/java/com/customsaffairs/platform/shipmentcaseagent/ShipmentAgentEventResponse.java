package com.customsaffairs.platform.shipmentcaseagent;

import java.util.Map;

/**
 * Shipment Agent 的一条追加式运行事件。
 *
 * <p>事件记录可审计的节点变化、工具结果和人工等待，不暴露模型思维链。资源字段为空表示该
 * 事件只属于工作流本身；非空时可以跳转到具体装载任务、装载版本或 LINK 申请。</p>
 */
public record ShipmentAgentEventResponse(
        long id,
        int sequence,
        String eventType,
        String stage,
        String title,
        String detail,
        Map<String, Object> data,
        String resourceType,
        String resourceNumber,
        String actorSubject,
        String actorDisplayName,
        String actorSource,
        String requestId,
        String occurredAt) {
}
