package com.customsaffairs.platform.shipmentcaseagent;

import jakarta.validation.constraints.Positive;

/**
 * 从一份发运案件启动总编排 Agent 的平台请求。
 *
 * <p>{@code expectedCaseRevision} 是浏览器最后看到的案件版本。Python 会用它做乐观并发
 * 校验，避免用户基于旧页面启动一条已经不符合最新资源绑定关系的运行。</p>
 */
public record StartShipmentAgentRunRequest(
        @Positive(message = "expectedCaseRevision 必须大于 0。")
        int expectedCaseRevision) {
}
