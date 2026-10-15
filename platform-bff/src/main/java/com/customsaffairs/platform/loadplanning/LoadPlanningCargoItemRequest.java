package com.customsaffairs.platform.loadplanning;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

/** Vue 创建装载任务时提交的一条货物箱规；尺寸统一为 mm，重量统一为 kg。 */
public record LoadPlanningCargoItemRequest(
        @NotBlank @Size(max = 64) String itemCode,
        @Size(max = 128) String skuCode,
        @NotBlank @Size(max = 255) String cargoName,
        @Min(1) @Max(1_000_000) int quantity,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal lengthMm,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal widthMm,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal heightMm,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal grossWeightKg,
        @Min(1) @Max(10_000) Integer maxStackLayers,
        @DecimalMin(value = "0", inclusive = false) BigDecimal maxTopLoadKg,
        Boolean stackable,
        Boolean keepUpright,
        Boolean fragile,
        @NotBlank
        @Pattern(regexp =
                "^(SYNTHETIC_DEMO|MANUAL|ENTERPRISE_HISTORICAL|DOCUMENT_IMPORT)$")
        String sourceType,
        @Size(max = 255) String sourceReference,
        @Size(max = 255) String sourceLocator,
        @DecimalMin("0") @DecimalMax("1") BigDecimal confidence,
        boolean verified) {

    /**
     * 文件导入和企业历史数据必须能追溯到原始位置；手工和脱敏构造数据允许不填来源位置。
     */
    @AssertTrue(message = "文件来源货物必须同时填写来源名称和定位信息")
    public boolean isSourceTraceComplete() {
        if (!"ENTERPRISE_HISTORICAL".equals(sourceType)
                && !"DOCUMENT_IMPORT".equals(sourceType)) {
            return true;
        }
        return hasText(sourceReference) && hasText(sourceLocator);
    }

    @AssertTrue(message = "SYNTHETIC_DEMO 构造数据不能标记为已核实正式数据")
    public boolean isVerificationAllowed() {
        return !"SYNTHETIC_DEMO".equals(sourceType) || !verified;
    }

    private static boolean hasText(String value) {
        return value != null && !value.trim().isEmpty();
    }
}
