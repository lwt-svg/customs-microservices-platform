package com.customsaffairs.platform.link;

import java.math.BigDecimal;
import java.util.List;

/** 按包组聚合的托数、箱数、重量、体积与 PO 分配。 */
public record LinkBookingPackageResponse(
        String description,
        BigDecimal pallets,
        BigDecimal cartons,
        BigDecimal quantity,
        BigDecimal netWeightKg,
        BigDecimal grossWeightKg,
        BigDecimal cbm,
        List<LinkBookingPoAllocationResponse> poAllocations) {
}
