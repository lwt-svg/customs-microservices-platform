package com.customsaffairs.platform.shipmentcaseagent;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * 数据就绪评估发现的一项可执行业务问题。
 *
 * <p>requiredValue 和 actualValue 保留原始 JSON 标量类型，使页面可以正确区分数量、
 * 布尔确认和空值。issueCode、severity 与 ownerRole 暂时保留字符串，避免 BFF 复制
 * Python 策略引擎里会持续演进的完整规则枚举。</p>
 */
public record ShipmentDataReadinessIssueResponse(
        String issueId,
        String issueCode,
        String severity,
        ShipmentDataSubjectType subjectType,
        String subjectCode,
        ShipmentDataFactType factType,
        JsonNode requiredValue,
        JsonNode actualValue,
        String unit,
        String message,
        ShipmentAgentAction resolution,
        String ownerRole) {
}
