package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;

/** Java BFF 返回给 Vue 的候选托盘规格。 */
public record LoadPlanningPalletSpecResponse(
        long id,
        String palletCode,
        String palletName,
        int quantity,
        BigDecimal lengthMm,
        BigDecimal widthMm,
        BigDecimal heightMm,
        BigDecimal tareWeightKg,
        BigDecimal maxLoadKg,
        BigDecimal maxTotalHeightMm,
        String sourceType,
        String sourceReference,
        String sourceLocator,
        BigDecimal confidence,
        boolean verified) {
}
