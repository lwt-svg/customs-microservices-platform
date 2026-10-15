package com.customsaffairs.platform.loadplanning;

import java.util.List;

/**
 * 返回给平台页面的候选方案辅助解读。
 *
 * <p>该对象只承载说明文字，不是规划结果的一部分。正式审批与发布仍然只依据结构化方案、确定性规则检查和人工确认。
 *
 * @param runId 本次装载规划运行编号。
 * @param planCode 被解读的候选方案编号。
 * @param provider 本次实际使用的解读来源。
 * @param model 来源内部使用的模型或规则版本；页面仅在有值时显示。
 * @param degraded 是否因首选解读服务不可用而降级。
 * @param generatedAt 本次解读生成时间。
 * @param summary 对候选方案的简要业务说明。
 * @param tradeoffs 该方案相对其他目标的主要取舍。
 * @param risks 需要业务人员关注的风险。
 * @param nextChecks 正式审批或现场执行前建议继续核对的事项。
 * @param disclaimer 解读能力的使用边界。
 */
public record LoadPlanningPlanInsightResponse(
    String runId,
    String planCode,
    String provider,
    String model,
    Boolean degraded,
    String generatedAt,
    String summary,
    List<String> tradeoffs,
    List<String> risks,
    List<String> nextChecks,
    String disclaimer) {}
