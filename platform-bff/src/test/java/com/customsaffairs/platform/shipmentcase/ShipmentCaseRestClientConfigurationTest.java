package com.customsaffairs.platform.shipmentcase;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestClient;

/** Shipment Case 专用 RestClient 的超时和模块隔离测试。 */
class ShipmentCaseRestClientConfigurationTest {

    private final ShipmentCaseRestClientConfiguration configuration =
            new ShipmentCaseRestClientConfiguration();

    @Test
    void shouldUseSafeLocalDefaults() {
        new ApplicationContextRunner()
                .withBean(RestClient.Builder.class, RestClient::builder)
                .withUserConfiguration(ShipmentCaseRestClientConfiguration.class)
                .run(context -> {
                    ShipmentCaseClientProperties properties =
                            context.getBean(ShipmentCaseClientProperties.class);
                    assertThat(properties.connectTimeout())
                            .isEqualTo(Duration.ofSeconds(2));
                    assertThat(properties.readTimeout())
                            .isEqualTo(Duration.ofSeconds(15));
                    assertThat(context)
                            .hasBean(
                                    ShipmentCaseRestClientConfiguration
                                            .REST_CLIENT_BEAN_NAME);
                });
    }

    @Test
    void shouldApplyConfiguredTimeoutsWithoutMutatingBuilderUsedByOtherClients() {
        ClientHttpRequestFactory originalFactory = mock(ClientHttpRequestFactory.class);
        RestClient.Builder sharedBuilder =
                RestClient.builder().requestFactory(originalFactory);
        ShipmentCaseClientProperties properties =
                new ShipmentCaseClientProperties(
                        Duration.ofMillis(350), Duration.ofSeconds(4));

        RestClient shipmentCaseClient =
                configuration.shipmentCaseRestClient(sharedBuilder, properties);
        RestClient unrelatedClient = sharedBuilder.build();

        Object shipmentCaseFactory =
                ReflectionTestUtils.getField(shipmentCaseClient, "clientRequestFactory");
        Object unrelatedFactory =
                ReflectionTestUtils.getField(unrelatedClient, "clientRequestFactory");

        assertThat(shipmentCaseFactory)
                .isInstanceOf(SimpleClientHttpRequestFactory.class);
        assertThat(ReflectionTestUtils.getField(shipmentCaseFactory, "connectTimeout"))
                .isEqualTo(350);
        assertThat(ReflectionTestUtils.getField(shipmentCaseFactory, "readTimeout"))
                .isEqualTo(4_000);
        assertThat(unrelatedFactory).isSameAs(originalFactory);
    }

    @Test
    void shouldRejectZeroOrNegativeTimeouts() {
        assertThatThrownBy(() -> new ShipmentCaseClientProperties(
                        Duration.ZERO, Duration.ofSeconds(1)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("connect-timeout");

        assertThatThrownBy(() -> new ShipmentCaseClientProperties(
                        Duration.ofSeconds(1), Duration.ofSeconds(-1)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("read-timeout");
    }
}
