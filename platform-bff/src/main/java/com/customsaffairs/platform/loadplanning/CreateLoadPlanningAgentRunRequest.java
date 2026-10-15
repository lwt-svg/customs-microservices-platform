package com.customsaffairs.platform.loadplanning;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.util.HashSet;
import java.util.List;

/** 浏览器为一个既有装载任务启动状态化智能体时提交的平台请求。 */
public record CreateLoadPlanningAgentRunRequest(
    @Size(max = 64) String selectedPalletCode,
    @NotEmpty @Size(max = 3) List<@NotNull LoadPlanningStrategyCode> strategyCodes) {

  /**
   * 同一种策略重复执行没有额外意义，因此平台边界直接拒绝重复值。
   *
   * <p>这里仅检查传输层约定，不判断托盘是否存在；后者必须由掌握任务数据的 Python 服务返回 422。
   */
  @jakarta.validation.constraints.AssertTrue(message = "候选装托策略不能重复")
  public boolean isStrategyCodeUnique() {
    return strategyCodes == null || new HashSet<>(strategyCodes).size() == strategyCodes.size();
  }
}
