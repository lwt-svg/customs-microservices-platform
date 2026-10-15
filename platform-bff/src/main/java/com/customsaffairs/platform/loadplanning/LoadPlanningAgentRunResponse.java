package com.customsaffairs.platform.loadplanning;

import java.util.List;

/** 平台返回给浏览器的智能体完整运行快照。 */
public record LoadPlanningAgentRunResponse(
    String runId,
    String parentRunId,
    String taskNumber,
    String status,
    String currentStep,
    Integer runVersion,
    String selectedPalletCode,
    String selectedPlanCode,
    String failureCode,
    String failureMessage,
    List<LoadPlanningAgentQuestionResponse> questions,
    List<LoadPlanningAgentEventResponse> events,
    List<LoadPlanningAgentPlanResponse> plans) {

  /** 兼容旧测试和旧调用方；新字段由 Python 运行服务提供。 */
  public LoadPlanningAgentRunResponse(
      String runId,
      String taskNumber,
      String status,
      String currentStep,
      String selectedPalletCode,
      String selectedPlanCode,
      List<LoadPlanningAgentQuestionResponse> questions,
      List<LoadPlanningAgentEventResponse> events,
      List<LoadPlanningAgentPlanResponse> plans) {
    this(
        runId,
        null,
        taskNumber,
        status,
        currentStep,
        1,
        selectedPalletCode,
        selectedPlanCode,
        null,
        null,
        questions,
        events,
        plans);
  }
}
