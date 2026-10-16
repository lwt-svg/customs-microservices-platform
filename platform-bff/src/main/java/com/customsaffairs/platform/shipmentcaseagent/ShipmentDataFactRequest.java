package com.customsaffairs.platform.shipmentcaseagent;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/**
 * 业务人员或权威系统为一次 Shipment Agent 运行补充的可追溯事实。
 *
 * <p>{@link JsonNode} 用来无损保留 JSON 标量类型。例如库存 {@code 8} 必须继续是数字，
 * 主数据确认 {@code true} 必须继续是布尔值，不能在 Java 代理层被改成字符串。factType
 * 与 value 的业务组合仍由 Python 数据就绪策略统一校验。</p>
 */
public record ShipmentDataFactRequest(
        @NotNull(message = "subjectType 不能为空。")
        ShipmentDataSubjectType subjectType,

        @NotBlank(message = "subjectCode 不能为空。")
        @Size(max = 128, message = "subjectCode 长度不能超过 128。")
        @Pattern(
                regexp = "^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
                message = "subjectCode 格式不正确。")
        String subjectCode,

        @NotNull(message = "factType 不能为空。")
        ShipmentDataFactType factType,

        @NotNull(message = "value 不能为空。")
        JsonNode value,

        @NotNull(message = "sourceType 不能为空。")
        ShipmentDataSourceType sourceType,

        @Size(max = 255, message = "sourceReference 长度不能超过 255。")
        String sourceReference,

        @Size(max = 255, message = "sourceLocator 长度不能超过 255。")
        String sourceLocator,

        @Size(max = 64, message = "observedAt 长度不能超过 64。")
        String observedAt,

        @Size(max = 64, message = "expiresAt 长度不能超过 64。")
        String expiresAt) {

    /** 当前补数协议只允许 string、number 或 boolean，不接受数组和对象。 */
    @AssertTrue(message = "value 只能是 string、number 或 boolean。")
    public boolean isScalarValue() {
        return value == null
                || value.isTextual()
                || value.isNumber()
                || value.isBoolean();
    }
}
