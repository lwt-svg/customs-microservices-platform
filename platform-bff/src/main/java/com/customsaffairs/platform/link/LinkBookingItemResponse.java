package com.customsaffairs.platform.link;

import java.math.BigDecimal;

/** 装箱单中的一条商品明细。 */
public record LinkBookingItemResponse(
        String itemCode,
        String lotNumber,
        String poNumber,
        String description,
        BigDecimal quantity,
        BigDecimal cartons,
        BigDecimal netWeightKg,
        BigDecimal grossWeightKg) {
}
