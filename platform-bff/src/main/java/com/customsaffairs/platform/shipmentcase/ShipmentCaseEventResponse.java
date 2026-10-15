package com.customsaffairs.platform.shipmentcase;

/**
 * 发运案件时间线中的一条不可变事件。
 *
 * <p>事件既可能描述状态变化，也可能描述资源绑定，因此状态字段和资源字段允许为空。
 * actor 字段只出现在响应中，用于展示 Python 已写入的服务端身份字段；当前值仍是
 * SYSTEM_DEMO 演示占位身份，不是已登录真实用户。任何写请求都没有对应 actor 字段。</p>
 */
public record ShipmentCaseEventResponse(
        long id,
        int sequence,
        String eventType,
        ShipmentCaseStatus previousStatus,
        ShipmentCaseStatus currentStatus,
        ShipmentResourceType resourceType,
        String resourceNumber,
        String actorSubject,
        String actorDisplayName,
        String actorSource,
        String requestId,
        String detail,
        String occurredAt) {
}
