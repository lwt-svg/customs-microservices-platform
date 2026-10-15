package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;
import java.util.List;

/** 一份可比较的候选装托方案。 */
public record LoadPlanningAgentPlanResponse(
    String planId,
    String planCode,
    String strategyCode,
    String strategyName,
    String algorithmVersion,
    String scoreVersion,
    Boolean feasible,
    BigDecimal score,
    String summary,
    LoadPlanningAgentMetricsResponse metrics,
    List<String> warnings,
    List<LoadPlanningAgentPalletResponse> pallets) {

  /** 兼容未返回算法版本的旧测试响应。 */
  public LoadPlanningAgentPlanResponse(
      String planId,
      String planCode,
      String strategyCode,
      String strategyName,
      Boolean feasible,
      BigDecimal score,
      String summary,
      LoadPlanningAgentMetricsResponse metrics,
      List<String> warnings,
      List<LoadPlanningAgentPalletResponse> pallets) {
    this(
        planId,
        planCode,
        strategyCode,
        strategyName,
        null,
        null,
        feasible,
        score,
        summary,
        metrics,
        warnings,
        pallets);
  }
}
