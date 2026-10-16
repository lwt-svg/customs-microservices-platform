package com.customsaffairs.platform.shipmentcaseagent;

import java.util.Map;

/** Shipment Agent 持久化站内通知；外部飞书等渠道只能消费该事实。 */
public record ShipmentAgentNotificationResponse(
        String id,
        String notificationType,
        String severity,
        String status,
        String title,
        String message,
        String ownerRole,
        Map<String, Object> actionContext,
        int occurrenceCount,
        String createdAt,
        String lastSeenAt,
        String resolvedAt) {
}
