package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;
import java.util.List;

/** Java BFF 返回给 Vue 的 Packing List 识别预览，字段统一使用 camelCase。 */
public record PackingListPreviewResponse(
        String parserVersion,
        Integer declaredTotalPallets,
        BigDecimal declaredTotalCbm,
        List<PackingListCargoCandidateResponse> cargoCandidates,
        List<HistoricalPalletPatternResponse> historicalPalletPatterns,
        List<String> checks) {
}
