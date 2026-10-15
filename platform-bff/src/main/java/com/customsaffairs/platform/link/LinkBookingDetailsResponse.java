package com.customsaffairs.platform.link;

import java.math.BigDecimal;
import java.util.List;

/**
 * 装箱单 PDF 中识别出的订舱业务明细。
 *
 * <p>这些字段来自 PDF，而起运地、目的地、运输方式、箱型和日期仍由业务人员补充。保留
 * 完整明细可以让人工复核页、模拟 LINK 七步表单和最终 Recap 使用同一份数据。</p>
 */
public record LinkBookingDetailsResponse(
        String invoiceNumber,
        String documentDate,
        String ciqNumber,
        String shipper,
        String consignee,
        String notifyParty,
        String marks,
        BigDecimal totalPallets,
        BigDecimal totalCartons,
        BigDecimal totalQuantity,
        BigDecimal totalCbm,
        BigDecimal totalNetWeightKg,
        BigDecimal totalGrossWeightKg,
        List<LinkBookingItemResponse> items,
        List<LinkBookingPackageResponse> packages,
        List<String> checks) {
}
