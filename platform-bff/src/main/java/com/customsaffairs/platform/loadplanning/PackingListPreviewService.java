package com.customsaffairs.platform.loadplanning;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.util.List;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Packing List 预览专用代理。
 *
 * <p>Java 只执行大小保护、Nacos 服务发现、multipart 转发和字段命名适配。PDF 字节仅在
 * 当前请求的内存中短暂存在，不会写入磁盘；真正的坐标解析仍由 Python 完成。</p>
 */
@Service
public class PackingListPreviewService {

    static final long MAX_PDF_BYTES = 5L * 1024 * 1024;

    private static final String SERVICE_ID = "load-planning-service";

    private final DiscoveryClient discoveryClient;
    private final RestClient restClient;

    public PackingListPreviewService(
            DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClientBuilder.build();
    }

    /**
     * 上传 PDF 并取得只读预览。
     *
     * <p>本方法不会创建装载任务，也不会把候选自动标记为已核验。</p>
     */
    public PackingListPreviewResponse preview(MultipartFile file) {
        validateLocalFile(file);

        URI uri = buildUri("api", "v1", "packing-list-imports", "preview");
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        try {
            body.add("file", namedResource(file));
            RemotePackingListPreview response = restClient.post()
                    .uri(uri)
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(body)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleRemoteError)
                    .body(RemotePackingListPreview.class);
            if (response == null) {
                throw badGateway("Python 装载规划服务返回了空的 Packing List 预览。");
            }
            return response.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (IOException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST, "无法读取待识别的 PDF 文件。", exception);
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY,
                    "调用 Python Packing List 预览接口失败。",
                    exception);
        }
    }

    private static void validateLocalFile(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请选择非空 PDF 文件。");
        }
        if (file.getSize() > MAX_PDF_BYTES) {
            throw new ResponseStatusException(
                    HttpStatus.PAYLOAD_TOO_LARGE, "PDF 不能超过 5 MiB。");
        }
    }

    private ByteArrayResource namedResource(MultipartFile file) throws IOException {
        String originalFilename = file.getOriginalFilename();
        String safeFilename = originalFilename == null || originalFilename.isBlank()
                ? "packing-list.pdf"
                : originalFilename;
        byte[] bytes = file.getBytes();
        return new ByteArrayResource(bytes) {
            @Override
            public String getFilename() {
                return safeFilename;
            }
        };
    }

    private URI buildUri(String... pathSegments) {
        ServiceInstance instance = discoveryClient.getInstances(SERVICE_ID).stream()
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 load-planning-service 实例。"));
        UriComponentsBuilder builder = UriComponentsBuilder.fromUri(instance.getUri());
        for (String pathSegment : pathSegments) {
            builder.pathSegment(pathSegment);
        }
        return builder.build().encode().toUri();
    }

    /** 将 Python 的解析错误固定为前端可依赖的 HTTP 状态和中文原因。 */
    private void handleRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            ClientHttpResponse response) throws IOException {
        HttpStatus targetStatus = switch (response.getStatusCode().value()) {
            case 413 -> HttpStatus.PAYLOAD_TOO_LARGE;
            case 415 -> HttpStatus.UNSUPPORTED_MEDIA_TYPE;
            case 422 -> HttpStatus.UNPROCESSABLE_ENTITY;
            default -> HttpStatus.BAD_GATEWAY;
        };
        String reason = switch (targetStatus) {
            case PAYLOAD_TOO_LARGE -> "PDF 不能超过 5 MiB。";
            case UNSUPPORTED_MEDIA_TYPE -> "只接受有效的 PDF 文件。";
            case UNPROCESSABLE_ENTITY ->
                    "PDF 无法识别：文件可能已加密、损坏、空白、没有文字层或没有可识别的装载表格；"
                            + "当前版本不支持 OCR。";
            default -> "Python 装载规划服务暂时无法预览 Packing List。";
        };
        throw new ResponseStatusException(targetStatus, reason);
    }

    private static ResponseStatusException badGateway(String reason) {
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason);
    }

    /** Python snake_case 响应模型；转换后才暴露给控制器。 */
    private record RemotePackingListPreview(
            @JsonProperty("parser_version") String parserVersion,
            @JsonProperty("declared_total_pallets") Integer declaredTotalPallets,
            @JsonProperty("declared_total_cbm") BigDecimal declaredTotalCbm,
            @JsonProperty("cargo_candidates") List<RemoteCargoCandidate> cargoCandidates,
            @JsonProperty("historical_pallet_patterns")
            List<RemoteHistoricalPalletPattern> historicalPalletPatterns,
            List<String> checks) {

        private PackingListPreviewResponse toPlatformResponse() {
            return new PackingListPreviewResponse(
                    parserVersion,
                    declaredTotalPallets,
                    declaredTotalCbm,
                    cargoCandidates == null
                            ? List.of()
                            : cargoCandidates.stream()
                                    .map(RemoteCargoCandidate::toPlatformResponse)
                                    .toList(),
                    historicalPalletPatterns == null
                            ? List.of()
                            : historicalPalletPatterns.stream()
                                    .map(RemoteHistoricalPalletPattern::toPlatformResponse)
                                    .toList(),
                    checks == null ? List.of() : List.copyOf(checks));
        }
    }

    private record RemoteCargoCandidate(
            @JsonProperty("cargo_key") String cargoKey,
            @JsonProperty("item_code") String itemCode,
            @JsonProperty("item_codes") List<String> itemCodes,
            String description,
            @JsonProperty("carton_quantity") int cartonQuantity,
            @JsonProperty("units_per_carton") int unitsPerCarton,
            @JsonProperty("carton_net_weight_kg") BigDecimal cartonNetWeightKg,
            @JsonProperty("carton_gross_weight_kg") BigDecimal cartonGrossWeightKg,
            @JsonProperty("length_mm") BigDecimal lengthMm,
            @JsonProperty("width_mm") BigDecimal widthMm,
            @JsonProperty("height_mm") BigDecimal heightMm,
            @JsonProperty("source_locator") String sourceLocator,
            BigDecimal confidence) {

        private PackingListCargoCandidateResponse toPlatformResponse() {
            return new PackingListCargoCandidateResponse(
                    cargoKey,
                    itemCode,
                    itemCodes == null ? List.of() : List.copyOf(itemCodes),
                    description,
                    cartonQuantity,
                    unitsPerCarton,
                    cartonNetWeightKg,
                    cartonGrossWeightKg,
                    lengthMm,
                    widthMm,
                    heightMm,
                    sourceLocator,
                    confidence);
        }
    }

    private record RemoteHistoricalPalletPattern(
            @JsonProperty("cargo_key") String cargoKey,
            @JsonProperty("cartons_per_layer") int cartonsPerLayer,
            @JsonProperty("pattern_carton_count") int patternCartonCount,
            @JsonProperty("pallet_quantity") Integer palletQuantity,
            @JsonProperty("pallet_tare_weight_kg") BigDecimal palletTareWeightKg,
            @JsonProperty("loaded_length_mm") BigDecimal loadedLengthMm,
            @JsonProperty("loaded_width_mm") BigDecimal loadedWidthMm,
            @JsonProperty("loaded_height_mm") BigDecimal loadedHeightMm,
            BigDecimal cbm,
            @JsonProperty("source_locator") String sourceLocator) {

        private HistoricalPalletPatternResponse toPlatformResponse() {
            return new HistoricalPalletPatternResponse(
                    cargoKey,
                    cartonsPerLayer,
                    patternCartonCount,
                    palletQuantity,
                    palletTareWeightKg,
                    loadedLengthMm,
                    loadedWidthMm,
                    loadedHeightMm,
                    cbm,
                    sourceLocator);
        }
    }
}
