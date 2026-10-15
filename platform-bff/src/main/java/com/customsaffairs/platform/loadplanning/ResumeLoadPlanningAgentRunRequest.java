package com.customsaffairs.platform.loadplanning;

import jakarta.validation.Valid;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;
import java.util.HashSet;
import java.util.List;

/** 浏览器补充澄清答案并恢复同一次状态化运行时提交的平台请求。 */
public record ResumeLoadPlanningAgentRunRequest(
    @NotEmpty @Size(max = 2_000) List<@Valid LoadPlanningAgentAnswerRequest> answers,
    Integer expectedRunVersion) {

  /** 兼容尚未发送乐观锁版本号的旧调用方。 */
  public ResumeLoadPlanningAgentRunRequest(
      List<@Valid LoadPlanningAgentAnswerRequest> answers) {
    this(answers, null);
  }

  /** 同一个问题在一次恢复请求里只能回答一次。 */
  @AssertTrue(message = "同一次恢复请求中的答案 key 不能重复")
  public boolean isAnswerKeyUnique() {
    if (answers == null) {
      return true;
    }
    var keys = new HashSet<String>();
    return answers.stream().map(LoadPlanningAgentAnswerRequest::key).allMatch(keys::add);
  }
}
