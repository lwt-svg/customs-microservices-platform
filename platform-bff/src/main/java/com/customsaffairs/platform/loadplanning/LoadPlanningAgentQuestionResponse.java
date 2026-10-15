package com.customsaffairs.platform.loadplanning;

import java.math.BigDecimal;

/** 智能体暂停时需要业务人员回答的一个结构化问题。 */
public record LoadPlanningAgentQuestionResponse(
    String key,
    String label,
    String valueType,
    String unit,
    String reason,
    Boolean required,
    BigDecimal minValue,
    BigDecimal maxValue) {}
