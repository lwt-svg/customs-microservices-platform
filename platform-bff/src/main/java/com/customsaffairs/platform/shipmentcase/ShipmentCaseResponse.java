package com.customsaffairs.platform.shipmentcase;

import java.util.List;

/**
 * Java BFF 返回给 Vue 的完整发运案件快照。
 *
 * <p>字段使用 camelCase。案件事实、revision、资源绑定和事件时间线全部来自 Python；
 * Java 不创建 JPA Entity，也不保存一份容易与 Python 不一致的副本。</p>
 */
public record ShipmentCaseResponse(
        String id,
        String caseNumber,
        String caseName,
        String responsibleOrganizationCode,
        ShipmentTransportScenario transportScenario,
        ShipmentCaseStatus status,
        int revision,
        String description,
        String createdAt,
        String updatedAt,
        List<ShipmentCaseResourceBindingResponse> resourceBindings,
        List<ShipmentCaseEventResponse> events) {
}
