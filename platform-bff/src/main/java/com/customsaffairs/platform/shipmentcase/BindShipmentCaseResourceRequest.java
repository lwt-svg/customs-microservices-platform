package com.customsaffairs.platform.shipmentcase;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/**
 * 把一个已有业务资源绑定到案件时提交的命令。
 *
 * <p>{@code expectedRevision} 是页面读取案件时看到的版本号。Python 会把它与当前 revision
 * 比较，从而阻止旧页面覆盖较新的案件状态。</p>
 */
public record BindShipmentCaseResourceRequest(
        @NotNull ShipmentResourceType resourceType,
        @NotBlank
        @Size(max = 64)
        @Pattern(
                regexp = "^[A-Za-z0-9][A-Za-z0-9_-]*$",
                message = "资源编号只能包含字母、数字、下划线和短横线")
        String resourceNumber,
        @NotNull @Positive Integer expectedRevision) {
}
