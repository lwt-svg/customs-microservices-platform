package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;

/** 候选方案的总体数量、空间、重量和高度指标。 */
public record LoadPlanningAgentMetricsResponse(
    Integer totalPallets,
    Integer totalBoxes,
    Integer unplacedBoxes,
    BigDecimal volumeUtilization,
    BigDecimal weightUtilization,
    BigDecimal averageLoadedHeightMm,
    BigDecimal maxLoadedHeightMm,
    BigDecimal totalGrossWeightKg) {}
