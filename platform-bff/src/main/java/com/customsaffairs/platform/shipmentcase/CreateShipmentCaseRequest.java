package com.customsaffairs.platform.shipmentcase;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/**
 * Vue 创建发运案件时提交的平台请求。
 *
 * <p>请求中故意没有 actor、actorSource 或 responsibleOrganizationCode。Java 不会把
 * 浏览器额外提交的这些字段转发给 Python；当前 Python 写入的是演示占位身份与组织，
 * 真实登录用户上下文仍待后续安全章节接入。Java 只把这里的业务字段转换为 Python
 * 使用的 snake_case。</p>
 *
 * @param caseNumber 稳定案件编号。
 * @param caseName 页面展示的案件名称。
 * @param transportScenario 当前已知的运输场景。
 * @param description 可选业务说明。
 */
public record CreateShipmentCaseRequest(
        @NotBlank
        @Size(max = 64)
        @Pattern(
                regexp = "^[A-Za-z0-9][A-Za-z0-9_-]*$",
                message = "案件编号只能包含字母、数字、下划线和短横线")
        String caseNumber,
        @NotBlank @Size(max = 128) String caseName,
        @NotNull ShipmentTransportScenario transportScenario,
        @Size(max = 2_000) String description) {
}
