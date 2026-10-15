package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;

/** Java BFF 返回给 Vue 的货物箱规。 */
public record LoadPlanningCargoItemResponse(
        long id,
        String itemCode,
        String skuCode,
        String cargoName,
        int quantity,
        BigDecimal lengthMm,
        BigDecimal widthMm,
        BigDecimal heightMm,
        BigDecimal grossWeightKg,
        Integer maxStackLayers,
        BigDecimal maxTopLoadKg,
        Boolean stackable,
        Boolean keepUpright,
        Boolean fragile,
        String sourceType,
        String sourceReference,
        String sourceLocator,
        BigDecimal confidence,
        boolean verified) {
}
