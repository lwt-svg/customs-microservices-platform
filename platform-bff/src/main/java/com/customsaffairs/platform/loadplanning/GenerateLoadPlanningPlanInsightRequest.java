package com.customsaffairs.platform.loadplanning;

import jakarta.validation.constraints.Size;

/**
 * 请求生成一份候选装载方案的辅助解读。
 *
 * <p>{@code planCode} 可以不传：Python 会按运行中已选中的方案，或当前默认候选方案进行解读。浏览器传值时，
 * 它也只能选择该运行已经生成的方案，不能借此提交新的坐标、评分或审批结果。
 */
public record GenerateLoadPlanningPlanInsightRequest(@Size(max = 64) String planCode) {}
