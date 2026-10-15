package com.customsaffairs.platform.link;

import java.math.BigDecimal;

/** 一条包组中的 PO 号及其分配数量。 */
public record LinkBookingPoAllocationResponse(String poNumber, BigDecimal quantity) {
}
