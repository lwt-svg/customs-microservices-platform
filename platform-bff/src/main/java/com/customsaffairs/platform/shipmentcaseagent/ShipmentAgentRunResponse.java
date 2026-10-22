package com.customsaffairs.platform.shipmentcaseagent;

import java.util.List;

/**
 * Java BFF 返回给 Vue 的 Shipment Coordinator Agent 完整运行快照。
 *
 * <p>状态和阶段保留为字符串，是因为工作流会随着章节继续增加节点；BFF 不应因为 Python
 * 新增一个合法阶段就无法反序列化。真正需要严格防止旧页面误操作的人工中断类型和动作则
 * 使用枚举。所有字段均来自 Python，Java 不另存一份运行副本。</p>
 */
public record ShipmentAgentRunResponse(
        String runId,
        String caseNumber,
        String caseName,
        String transportScenario,
        String caseStatus,
        String workflowVersion,
        String policyVersion,
        String status,
        String currentStage,
        int runVersion,
        int startedCaseRevision,
        int lastObservedCaseRevision,
        List<String> requiredCapabilities,
        boolean scenarioPolicyConfirmed,
        String selectedLoadTaskNumber,
        String selectedLoadRunId,
        String lockedLoadVersionId,
        Integer lockedLoadVersionNumber,
        String selectedBookingApplicationNumber,
        String bookingStatus,
        String carrierSubmissionStatus,
        String bookingReference,
        boolean bookingSubmissionUncertain,
        ShipmentDataReadinessResponse dataReadiness,
        String nextWakeAt,
        String wakeReason,
        List<ShipmentAgentNotificationResponse> notifications,
        String failureCode,
        String failureMessage,
        ShipmentAgentPendingInterruptResponse pendingInterrupt,
        List<ShipmentAgentAction> allowedActions,
        ShipmentAgentChildResponse childAgent,
        List<ShipmentAgentRpaJobResponse> rpaJobs,
        String createdAt,
        String updatedAt,
        String completedAt,
        List<ShipmentAgentEventResponse> events) {
}
