package com.customsaffairs.platform.discovery;

/**
 * Java BFF 从注册中心查到的一台服务实例的安全视图。
 *
 * <p>它不是 Nacos 原始对象，而是专门提供给 API 响应的简化数据结构。这样可以避免把 Nacos
 * 元数据、认证信息或内部实现细节直接暴露给浏览器。</p>
 *
 * @param serviceId 服务在 Nacos 中登记的稳定名称，例如 link-booking-service。
 * @param host 当前实例可访问的主机地址。
 * @param port 当前实例监听的 HTTP 端口。
 * @param secure 当前实例是否使用 HTTPS；本地学习环境固定为 false。
 */
public record DiscoveredServiceInstance(String serviceId, String host, int port, boolean secure) {
}
