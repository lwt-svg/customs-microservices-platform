package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;
import java.util.List;

/** 候选方案中的一块实际托盘及其全部箱体摆放结果。 */
public record LoadPlanningAgentPalletResponse(
    Integer palletIndex,
    String palletCode,
    BigDecimal loadedHeightMm,
    BigDecimal payloadWeightKg,
    BigDecimal grossWeightKg,
    Integer boxCount,
    Integer layerCount,
    Integer capacityBoxCount,
    BigDecimal layerAreaUtilization,
    BigDecimal payloadWeightUtilization,
    BigDecimal heightUtilization,
    BigDecimal balanceOffset,
    String patternType,
    Integer boxesPerFullLayer,
    List<LoadPlanningAgentPlacementResponse> placements) {}
