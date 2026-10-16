package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;

/** 一个箱体在托盘三维坐标系中的位置、尺寸和朝向。 */
public record LoadPlanningAgentPlacementResponse(
    String placementId,
    String itemCode,
    Integer boxIndex,
    Integer layer,
    BigDecimal xMm,
    BigDecimal yMm,
    BigDecimal zMm,
    BigDecimal lengthMm,
    BigDecimal widthMm,
    BigDecimal heightMm,
    Integer rotationDeg,
    BigDecimal grossWeightKg,
    String orientation) {}
