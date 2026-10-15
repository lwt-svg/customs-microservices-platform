package com.customsaffairs.platform.loadplanning;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * 恢复智能体时对一个澄清问题的回答。
 *
 * <p>{@link JsonNode} 是刻意选择的边界类型：若使用 String，会把 {@code true} 和 {@code 12.5} 错误变成文本；若使用
 * Object，又不便在校验阶段精确限制 JSON 类型。 JsonNode 可以原样保留 boolean、number、string，再由 Jackson 无损发送给 Python。
 */
public record LoadPlanningAgentAnswerRequest(
    @NotBlank @Size(max = 255) String key, @NotNull JsonNode value) {

  /** 智能体问答协议目前只接受标量，不接受 null、数组或对象。 */
  @AssertTrue(message = "答案值只能是 string、number 或 boolean")
  public boolean isScalarValue() {
    return value == null || value.isTextual() || value.isNumber() || value.isBoolean();
  }
}
