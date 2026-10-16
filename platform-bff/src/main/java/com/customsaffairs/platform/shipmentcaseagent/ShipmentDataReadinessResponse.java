package com.customsaffairs.platform.shipmentcaseagent;

import java.util.List;

/**
 * Shipment Agent 最近一次数据就绪评估的只读快照。
 *
 * <p>V2 的权威时间字段是 {@code assessedAt}，直接计数字段是 {@code issueCount} 与
 * {@code blockingIssueCount}。checkedAt 和 issueCounts 保留为兼容投影，旧页面和旧运行
 * 仍可读取；新代码应优先使用 V2 字段。</p>
 */
public record ShipmentDataReadinessResponse(
        String status,
        int revision,
        String policyVersion,
        String sourceMode,
        String snapshotHash,
        String assessedAt,
        String checkedAt,
        String readyAt,
        int issueCount,
        int blockingIssueCount,
        ShipmentDataReadinessIssueCountsResponse issueCounts,
        List<ShipmentDataReadinessIssueResponse> issues) {
}
