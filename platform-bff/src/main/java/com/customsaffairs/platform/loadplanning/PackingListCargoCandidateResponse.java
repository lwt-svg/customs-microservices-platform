package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;
import java.util.List;

/**
 * Packing List 中按相同箱规聚合出的货物候选。
 *
 * <p>这里仍然只是文档识别结果，尺寸、重量和数量必须由业务人员复核后才能带入正式装载任务。</p>
 */
public record PackingListCargoCandidateResponse(
        String cargoKey,
        String itemCode,
        List<String> itemCodes,
        String description,
        int cartonQuantity,
        int unitsPerCarton,
        BigDecimal cartonNetWeightKg,
        BigDecimal cartonGrossWeightKg,
        BigDecimal lengthMm,
        BigDecimal widthMm,
        BigDecimal heightMm,
        String sourceLocator,
        BigDecimal confidence) {
}
