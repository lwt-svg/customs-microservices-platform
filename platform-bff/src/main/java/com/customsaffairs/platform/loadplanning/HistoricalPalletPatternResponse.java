package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;

/**
 * Packing List 里记录的一种历史装托模式。
 *
 * <p>{@code loadedHeightMm} 是装货后的历史总高度，不是空托高度，不能直接当作创建任务时的
 * 托盘 {@code heightMm}。</p>
 */
public record HistoricalPalletPatternResponse(
        String cargoKey,
        int cartonsPerLayer,
        int patternCartonCount,
        Integer palletQuantity,
        BigDecimal palletTareWeightKg,
        BigDecimal loadedLengthMm,
        BigDecimal loadedWidthMm,
        BigDecimal loadedHeightMm,
        BigDecimal cbm,
        String sourceLocator) {
}
