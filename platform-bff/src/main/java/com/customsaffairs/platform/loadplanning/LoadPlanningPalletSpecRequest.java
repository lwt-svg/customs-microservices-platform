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

/** Vue 创建装载任务时提交的一种候选托盘规格。 */
public record LoadPlanningPalletSpecRequest(
        @NotBlank @Size(max = 64) String palletCode,
        @NotBlank @Size(max = 128) String palletName,
        @Min(1) @Max(100_000) int quantity,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal lengthMm,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal widthMm,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal heightMm,
        @NotNull @DecimalMin(value = "0", inclusive = false) BigDecimal tareWeightKg,
        @DecimalMin(value = "0", inclusive = false) BigDecimal maxLoadKg,
        @DecimalMin(value = "0", inclusive = false) BigDecimal maxTotalHeightMm,
        @NotBlank
        @Pattern(regexp =
                "^(SYNTHETIC_DEMO|MANUAL|ENTERPRISE_HISTORICAL|DOCUMENT_IMPORT)$")
        String sourceType,
        @Size(max = 255) String sourceReference,
        @Size(max = 255) String sourceLocator,
        @DecimalMin("0") @DecimalMax("1") BigDecimal confidence,
        boolean verified) {

    @AssertTrue(message = "文件来源托盘必须同时填写来源名称和定位信息")
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
