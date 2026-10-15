package com.customsaffairs.platform.loadplanning;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.util.HashSet;
import java.util.List;

/** 从已驳回/失败运行创建新运行时提交的可审计重规划请求。 */
public record ReplanLoadPlanningAgentRunRequest(
    @NotBlank @Size(max = 2_000) String reason,
    @Size(max = 64) String selectedPalletCode,
    @Size(max = 3) List<LoadPlanningStrategyCode> strategyCodes) {

  /** 若指定策略，仍然禁止重复；不指定时由 Python 继承父运行策略。 */
  @AssertTrue(message = "重新规划策略不能重复")
  public boolean isStrategyCodeUnique() {
    if (strategyCodes == null) {
      return true;
    }
    return new HashSet<>(strategyCodes).size() == strategyCodes.size();
  }
}
