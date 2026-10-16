package com.customsaffairs.platform.shipmentcaseagent;

/**
 * 数据就绪问题的聚合计数。
 *
 * <p>{@code total} 和 {@code blocking} 对应当前 V2 契约；{@code warning} 是兼容早期
 * 契约的投影。当前策略只产生阻塞问题，因此 warning 通常为 0。</p>
 */
public record ShipmentDataReadinessIssueCountsResponse(
        int total,
        int blocking,
        int warning) {
}
