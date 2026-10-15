package com.customsaffairs.platform.shipmentcase;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

/**
 * 只为 Shipment Case 跨服务调用创建 RestClient。
 *
 * <p>这里先 clone Spring Boot 提供的 Builder，再安装本模块自己的请求工厂。这样超时只
 * 作用于 Shipment Case，不会悄悄改变 LINK、装载任务等其他 RestClient 的行为。</p>
 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(ShipmentCaseClientProperties.class)
class ShipmentCaseRestClientConfiguration {

    static final String REST_CLIENT_BEAN_NAME = "shipmentCaseRestClient";

    @Bean(REST_CLIENT_BEAN_NAME)
    RestClient shipmentCaseRestClient(
            RestClient.Builder restClientBuilder,
            ShipmentCaseClientProperties properties) {
        return restClientBuilder.clone()
                .requestFactory(requestFactory(properties))
                .build();
    }

    /**
     * JDK 自带的 HttpURLConnection 请求工厂已经能满足本机学习项目，不需要增加第三方
     * HTTP 客户端依赖。Duration 会由 Spring Boot 支持的 2s、500ms 等配置格式绑定。
     */
    static SimpleClientHttpRequestFactory requestFactory(
            ShipmentCaseClientProperties properties) {
        SimpleClientHttpRequestFactory requestFactory =
                new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(properties.connectTimeout());
        requestFactory.setReadTimeout(properties.readTimeout());
        return requestFactory;
    }
}
