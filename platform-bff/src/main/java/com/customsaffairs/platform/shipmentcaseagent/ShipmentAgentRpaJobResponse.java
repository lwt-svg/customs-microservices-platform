package com.customsaffairs.platform.shipmentcaseagent;

/**
 * Shipment Coordinator 当前关联的一条 RPA Job 安全摘要。
 *
 * <p>这里只暴露业务页面判断进度所需的字段。原始业务 payload、Worker attempt token、
 * 幂等键和领取凭据始终留在 Python RPA 控制面，不能经过 Java BFF 返回浏览器。</p>
 */
public record ShipmentAgentRpaJobResponse(
        String jobId,
        int sequence,
        String domain,
        String operation,
        String environment,
        String status,
        String currentStep,
        String sideEffectPhase,
        String businessOutcome,
        String externalReference,
        String errorCode,
        String updatedAt) {
}
