package com.customsaffairs.platform.common.api;

/**
 * BFF 自定义健康检查接口返回的最小数据。
 *
 * <p>这里故意不返回数据库地址、Nacos 地址、环境变量或版本构建信息，避免把基础设施细节
 * 暴露给浏览器。容器探针使用 Actuator，Vue 或学习者可使用这个更容易理解的业务接口。</p>
 *
 * @param status 服务当前状态。
 * @param service 服务名称，方便多个服务并存时识别响应来源。
 */
public record HealthData(String status, String service) {
}
