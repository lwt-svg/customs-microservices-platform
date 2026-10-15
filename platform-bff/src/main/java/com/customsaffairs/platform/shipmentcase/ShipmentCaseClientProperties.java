package com.customsaffairs.platform.shipmentcase;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Shipment Case 调用 Python Agent Runtime 时使用的 HTTP 超时。
 *
 * <p>连接超时限制“与 Python 建立 TCP 连接最多等多久”；读取超时限制“连接建立后，
 * 等待 Python 返回数据最多等多久”。二者必须分开配置，因为服务未启动和服务处理过慢
 * 是两类不同的问题。</p>
 */
@ConfigurationProperties(prefix = "platform.shipment-case.client")
public record ShipmentCaseClientProperties(
        @DefaultValue("2s") Duration connectTimeout,
        @DefaultValue("15s") Duration readTimeout) {

    public ShipmentCaseClientProperties {
        requirePositive("connect-timeout", connectTimeout);
        requirePositive("read-timeout", readTimeout);
    }

    private static void requirePositive(String propertyName, Duration value) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException(
                    "platform.shipment-case.client." + propertyName + " 必须大于 0。");
        }
    }
}
