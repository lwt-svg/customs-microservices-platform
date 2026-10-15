package com.customsaffairs.platform.shipmentcase;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/**
 * 推进发运案件状态时提交的命令。
 *
 * <p>请求只表达“希望变成什么状态”和原因。哪些流转有效、终态如何保护，全部由 Python
 * 领域服务执行；Java 不维护第二份状态转移表。</p>
 */
public record ChangeShipmentCaseStatusRequest(
        @NotNull ShipmentCaseStatus targetStatus,
        @NotNull @Positive Integer expectedRevision,
        @NotBlank @Size(max = 2_000) String reason) {
}
