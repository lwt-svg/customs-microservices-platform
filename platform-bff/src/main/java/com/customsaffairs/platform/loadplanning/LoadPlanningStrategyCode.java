package com.customsaffairs.platform.loadplanning;

/**
 * 智能装托一次运行可要求 Python 生成的候选策略。
 *
 * <p>枚举让浏览器在进入业务服务前就只能提交双方约定的稳定值，避免把任意字符串继续 转发给 Python。几何算法和每种策略的具体权重仍完全属于 Python 服务。
 */
public enum LoadPlanningStrategyCode {
  STABLE,
  BALANCED,
  UTILIZATION
}
