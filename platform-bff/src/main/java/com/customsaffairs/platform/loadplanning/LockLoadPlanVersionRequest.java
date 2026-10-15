package com.customsaffairs.platform.loadplanning;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

/**
 * 把一条已经批准的试算运行发布成正式装载方案版本。
 *
 * <p>请求里故意没有 {@code planCode}、审批人或锁定人。方案只能来自运行中已经批准的
 * {@code selectedPlanCode}，操作者身份只能由服务器上下文提供，浏览器不能在 JSON
 * 中替换这些审计事实。
 */
public record LockLoadPlanVersionRequest(
    @NotNull @Min(1) Integer expectedRunVersion,
    @NotNull @Min(0) Integer expectedCurrentVersionNumber,
    @NotBlank @Size(max = 2000) String lockReason) {}
