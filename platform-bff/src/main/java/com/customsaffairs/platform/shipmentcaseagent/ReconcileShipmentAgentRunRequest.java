package com.customsaffairs.platform.shipmentcaseagent;

import jakarta.validation.constraints.Positive;

/**
 * 要求 Agent 重新读取外部资源状态的命令。
 *
 * <p>它不是后台死循环轮询。页面或后续租约 Worker 使用同一接口发出一次幂等“重新核对”，
 * Python 从 checkpoint 恢复、检查 LINK/装载子流程后再次持久化状态。</p>
 */
public record ReconcileShipmentAgentRunRequest(
        @Positive(message = "expectedRunVersion 必须大于 0。")
        int expectedRunVersion) {
}
