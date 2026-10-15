package com.customsaffairs.platform.loadplanning;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/** 浏览器批准或拒绝智能体候选方案时提交的平台请求。 */
public record DecideLoadPlanningAgentRunRequest(
    @NotNull LoadPlanningAgentDecision decision,
    @Size(max = 64) String planCode,
    @Size(max = 2_000) String comment,
    Integer expectedRunVersion) {

  /** 兼容尚未发送乐观锁版本号的旧调用方。 */
  public DecideLoadPlanningAgentRunRequest(
      LoadPlanningAgentDecision decision, String planCode, String comment) {
    this(decision, planCode, comment, null);
  }

  /** 与 Python 审批契约保持一致：批准必须选方案，驳回必须留下审计原因。 */
  @AssertTrue(message = "APPROVE 必须提供 planCode，REJECT 必须提供 comment")
  public boolean isDecisionDetailComplete() {
    if (decision == null) {
      return true;
    }
    return switch (decision) {
      case APPROVE -> hasText(planCode);
      case REJECT -> hasText(comment);
    };
  }

  private static boolean hasText(String value) {
    return value != null && !value.trim().isEmpty();
  }
}
