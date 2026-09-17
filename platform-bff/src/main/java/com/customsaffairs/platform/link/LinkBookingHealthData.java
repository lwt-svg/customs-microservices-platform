package com.customsaffairs.platform.link;

/**
 * Python LINK 服务 {@code /health/live} 返回的最小健康状态。
 *
 * <p>这个 record 的字段与 Python 当前健康检查 JSON 对应。它只包含服务运行状态，不包含
 * 订舱申请、客户文件、地址、金额或其他业务数据。</p>
 *
 * @param status Python 服务声明的存活状态。
 * @param service Python 服务名称。
 * @param environment Python 服务运行环境名称。
 */
public record LinkBookingHealthData(String status, String service, String environment) {
}
