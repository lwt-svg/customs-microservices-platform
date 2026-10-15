package com.customsaffairs.platform;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;

/**
 * Java BFF 的程序入口。
 *
 * <p>{@code @SpringBootApplication} 是 Spring Boot 的核心注解。它告诉框架：</p>
 * <ol>
 *   <li>从当前包及其子包扫描带有 Spring 注解的类；</li>
 *   <li>根据 pom.xml 中的依赖自动配置 Web 服务器和 JSON 转换；</li>
 *   <li>把本类作为 Java 程序启动点。</li>
 * </ol>
 *
 * <p>{@code @EnableDiscoveryClient} 开启服务注册和发现：应用正式启动时会依据
 * application.yml 中的本地配置登记到 Nacos。测试 profile 会显式关闭此功能，
 * 从而避免单元测试依赖 Nacos 是否启动。</p>
 *
 * <p>业务模块通过独立的实体、Service 和 Controller 加入，启动类只负责应用入口和基础设施开关，
 * 不把用户、组织、权限或菜单业务逻辑写进这里。</p>
 */
@SpringBootApplication
@EnableDiscoveryClient
public class PlatformBffApplication {

    /**
     * Java 标准入口方法。执行 {@code mvn spring-boot:run} 时，Maven 最终会从这里启动程序。
     *
     * @param args 启动参数；第一阶段没有自定义参数，但保留它符合 Java 标准签名。
     */
    public static void main(String[] args) {
        SpringApplication.run(PlatformBffApplication.class, args);
    }
}
