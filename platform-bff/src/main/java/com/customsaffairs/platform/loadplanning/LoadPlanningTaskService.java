package com.customsaffairs.platform.loadplanning;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.util.List;
import org.springframework.cloud.client.ServiceInstance;
import org.springframework.cloud.client.discovery.DiscoveryClient;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * 通过 Nacos 发现并调用 Python 装载规划服务。
 *
 * <p>Java 只做平台入口、字段命名适配和稳定错误翻译；装载任务仍由 Python 服务持久化，
 * BFF 不复制第二份任务数据库。</p>
 */
@Service
public class LoadPlanningTaskService {

    private static final String SERVICE_ID = "load-planning-service";
    private static final ParameterizedTypeReference<List<RemoteTaskSummary>> SUMMARY_LIST_TYPE =
            new ParameterizedTypeReference<>() {
            };

    private final DiscoveryClient discoveryClient;
    private final RestClient restClient;

    public LoadPlanningTaskService(
            DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClientBuilder.build();
    }

    /** 查询任务摘要，不在列表接口读取全部箱规。 */
    public List<LoadPlanningTaskSummaryResponse> listTasks() {
        URI uri = buildUri("api", "v1", "load-planning-tasks");
        try {
            List<RemoteTaskSummary> tasks = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleListError)
                    .body(SUMMARY_LIST_TYPE);
            if (tasks == null) {
                throw badGateway("Python 装载规划服务返回了空列表响应。");
            }
            return tasks.stream().map(RemoteTaskSummary::toPlatformResponse).toList();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python 装载任务列表接口失败。", exception);
        }
    }

    /** 创建任务，并把 Vue camelCase 请求显式转换为 Python snake_case。 */
    public LoadPlanningTaskResponse createTask(CreateLoadPlanningTaskRequest request) {
        URI uri = buildUri("api", "v1", "load-planning-tasks");
        RemoteCreateTaskRequest remoteRequest = RemoteCreateTaskRequest.from(request);
        try {
            RemoteTask task = restClient.post()
                    .uri(uri)
                    .body(remoteRequest)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleCreateError)
                    .body(RemoteTask.class);
            if (task == null) {
                throw badGateway("Python 装载规划服务返回了空创建响应。");
            }
            return task.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python 创建装载任务失败。", exception);
        }
    }

    /** 按稳定任务编号读取详情。 */
    public LoadPlanningTaskResponse getTask(String taskNumber) {
        String normalizedTaskNumber = taskNumber == null ? "" : taskNumber.trim();
        if (normalizedTaskNumber.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "装载任务编号不能为空。");
        }

        URI uri = buildUri("api", "v1", "load-planning-tasks", normalizedTaskNumber);
        try {
            RemoteTask task = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleDetailError)
                    .body(RemoteTask.class);
            if (task == null) {
                throw badGateway("Python 装载规划服务返回了空详情响应。");
            }
            return task.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python 装载任务详情接口失败。", exception);
        }
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

    private void handleListError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse ignoredResponse)
            throws IOException {
        throw badGateway("Python 装载规划服务暂时无法查询任务列表。");
    }

    private void handleCreateError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();
        if (statusCode == 409) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT, "装载任务编号已经存在，请更换编号。");
        }
        if (statusCode == 400 || statusCode == 422) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST, "装载任务数据不符合 Python 接口要求。");
        }
        throw badGateway("Python 装载规划服务暂时无法创建任务。");
    }

    private void handleDetailError(
            org.springframework.http.HttpRequest ignoredRequest,
            org.springframework.http.client.ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();
        if (statusCode == 404) {
            throw new ResponseStatusException(
                    HttpStatus.NOT_FOUND, "装载任务不存在或已被移除，请刷新列表。");
        }
        if (statusCode == 400 || statusCode == 422) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "装载任务编号不正确。");
        }
        throw badGateway("Python 装载规划服务暂时无法读取任务详情。");
    }

    private static ResponseStatusException badGateway(String reason) {
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, reason);
    }

    private static String nullableTrim(String value) {
        if (value == null || value.trim().isEmpty()) {
            return null;
        }
        return value.trim();
    }

    /** Java 发往 Python 的任务聚合创建请求。 */
    private record RemoteCreateTaskRequest(
            @JsonProperty("task_number") String taskNumber,
            @JsonProperty("task_name") String taskName,
            String description,
            @JsonProperty("cargo_items") List<RemoteCargoCreate> cargoItems,
            @JsonProperty("pallet_specs") List<RemotePalletCreate> palletSpecs) {

        private static RemoteCreateTaskRequest from(CreateLoadPlanningTaskRequest request) {
            return new RemoteCreateTaskRequest(
                    request.taskNumber().trim(),
                    request.taskName().trim(),
                    nullableTrim(request.description()),
                    request.cargoItems().stream().map(RemoteCargoCreate::from).toList(),
                    request.palletSpecs().stream().map(RemotePalletCreate::from).toList());
        }
    }

    private record RemoteCargoCreate(
            @JsonProperty("item_code") String itemCode,
            @JsonProperty("sku_code") String skuCode,
            @JsonProperty("cargo_name") String cargoName,
            int quantity,
            @JsonProperty("length_mm") BigDecimal lengthMm,
            @JsonProperty("width_mm") BigDecimal widthMm,
            @JsonProperty("height_mm") BigDecimal heightMm,
            @JsonProperty("gross_weight_kg") BigDecimal grossWeightKg,
            @JsonProperty("max_stack_layers") Integer maxStackLayers,
            @JsonProperty("max_top_load_kg") BigDecimal maxTopLoadKg,
            Boolean stackable,
            @JsonProperty("keep_upright") Boolean keepUpright,
            Boolean fragile,
            @JsonProperty("source_type") String sourceType,
            @JsonProperty("source_reference") String sourceReference,
            @JsonProperty("source_locator") String sourceLocator,
            BigDecimal confidence,
            boolean verified) {

        private static RemoteCargoCreate from(LoadPlanningCargoItemRequest item) {
            return new RemoteCargoCreate(
                    item.itemCode().trim(),
                    nullableTrim(item.skuCode()),
                    item.cargoName().trim(),
                    item.quantity(),
                    item.lengthMm(),
                    item.widthMm(),
                    item.heightMm(),
                    item.grossWeightKg(),
                    item.maxStackLayers(),
                    item.maxTopLoadKg(),
                    item.stackable(),
                    item.keepUpright(),
                    item.fragile(),
                    item.sourceType(),
                    nullableTrim(item.sourceReference()),
                    nullableTrim(item.sourceLocator()),
                    item.confidence(),
                    item.verified());
        }
    }

    private record RemotePalletCreate(
            @JsonProperty("pallet_code") String palletCode,
            @JsonProperty("pallet_name") String palletName,
            int quantity,
            @JsonProperty("length_mm") BigDecimal lengthMm,
            @JsonProperty("width_mm") BigDecimal widthMm,
            @JsonProperty("height_mm") BigDecimal heightMm,
            @JsonProperty("tare_weight_kg") BigDecimal tareWeightKg,
            @JsonProperty("max_load_kg") BigDecimal maxLoadKg,
            @JsonProperty("max_total_height_mm") BigDecimal maxTotalHeightMm,
            @JsonProperty("source_type") String sourceType,
            @JsonProperty("source_reference") String sourceReference,
            @JsonProperty("source_locator") String sourceLocator,
            BigDecimal confidence,
            boolean verified) {

        private static RemotePalletCreate from(LoadPlanningPalletSpecRequest pallet) {
            return new RemotePalletCreate(
                    pallet.palletCode().trim(),
                    pallet.palletName().trim(),
                    pallet.quantity(),
                    pallet.lengthMm(),
                    pallet.widthMm(),
                    pallet.heightMm(),
                    pallet.tareWeightKg(),
                    pallet.maxLoadKg(),
                    pallet.maxTotalHeightMm(),
                    pallet.sourceType(),
                    nullableTrim(pallet.sourceReference()),
                    nullableTrim(pallet.sourceLocator()),
                    pallet.confidence(),
                    pallet.verified());
        }
    }

    /** Python 列表响应，snake_case 只停留在 BFF 内部。 */
    private record RemoteTaskSummary(
            long id,
            @JsonProperty("task_number") String taskNumber,
            @JsonProperty("task_name") String taskName,
            String status,
            @JsonProperty("cargo_item_count") int cargoItemCount,
            @JsonProperty("cargo_box_count") int cargoBoxCount,
            @JsonProperty("pallet_spec_count") int palletSpecCount,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt) {

        private LoadPlanningTaskSummaryResponse toPlatformResponse() {
            return new LoadPlanningTaskSummaryResponse(
                    id,
                    taskNumber,
                    taskName,
                    status,
                    cargoItemCount,
                    cargoBoxCount,
                    palletSpecCount,
                    createdAt,
                    updatedAt);
        }
    }

    /** Python 任务详情响应。 */
    private record RemoteTask(
            long id,
            @JsonProperty("task_number") String taskNumber,
            @JsonProperty("task_name") String taskName,
            String description,
            String status,
            RemoteUnits units,
            @JsonProperty("cargo_items") List<RemoteCargoRead> cargoItems,
            @JsonProperty("pallet_specs") List<RemotePalletRead> palletSpecs,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt) {

        private LoadPlanningTaskResponse toPlatformResponse() {
            if (units == null || cargoItems == null || palletSpecs == null) {
                throw badGateway("Python 装载任务详情字段不完整。");
            }
            return new LoadPlanningTaskResponse(
                    id,
                    taskNumber,
                    taskName,
                    description,
                    status,
                    units.toPlatformResponse(),
                    cargoItems.stream().map(RemoteCargoRead::toPlatformResponse).toList(),
                    palletSpecs.stream().map(RemotePalletRead::toPlatformResponse).toList(),
                    createdAt,
                    updatedAt);
        }
    }

    private record RemoteUnits(String dimension, String weight) {
        private LoadPlanningUnitsResponse toPlatformResponse() {
            return new LoadPlanningUnitsResponse(dimension, weight);
        }
    }

    private record RemoteCargoRead(
            long id,
            @JsonProperty("item_code") String itemCode,
            @JsonProperty("sku_code") String skuCode,
            @JsonProperty("cargo_name") String cargoName,
            int quantity,
            @JsonProperty("length_mm") BigDecimal lengthMm,
            @JsonProperty("width_mm") BigDecimal widthMm,
            @JsonProperty("height_mm") BigDecimal heightMm,
            @JsonProperty("gross_weight_kg") BigDecimal grossWeightKg,
            @JsonProperty("max_stack_layers") Integer maxStackLayers,
            @JsonProperty("max_top_load_kg") BigDecimal maxTopLoadKg,
            Boolean stackable,
            @JsonProperty("keep_upright") Boolean keepUpright,
            Boolean fragile,
            @JsonProperty("source_type") String sourceType,
            @JsonProperty("source_reference") String sourceReference,
            @JsonProperty("source_locator") String sourceLocator,
            BigDecimal confidence,
            boolean verified) {

        private LoadPlanningCargoItemResponse toPlatformResponse() {
            return new LoadPlanningCargoItemResponse(
                    id,
                    itemCode,
                    skuCode,
                    cargoName,
                    quantity,
                    lengthMm,
                    widthMm,
                    heightMm,
                    grossWeightKg,
                    maxStackLayers,
                    maxTopLoadKg,
                    stackable,
                    keepUpright,
                    fragile,
                    sourceType,
                    sourceReference,
                    sourceLocator,
                    confidence,
                    verified);
        }
    }

    private record RemotePalletRead(
            long id,
            @JsonProperty("pallet_code") String palletCode,
            @JsonProperty("pallet_name") String palletName,
            int quantity,
            @JsonProperty("length_mm") BigDecimal lengthMm,
            @JsonProperty("width_mm") BigDecimal widthMm,
            @JsonProperty("height_mm") BigDecimal heightMm,
            @JsonProperty("tare_weight_kg") BigDecimal tareWeightKg,
            @JsonProperty("max_load_kg") BigDecimal maxLoadKg,
            @JsonProperty("max_total_height_mm") BigDecimal maxTotalHeightMm,
            @JsonProperty("source_type") String sourceType,
            @JsonProperty("source_reference") String sourceReference,
            @JsonProperty("source_locator") String sourceLocator,
            BigDecimal confidence,
            boolean verified) {

        private LoadPlanningPalletSpecResponse toPlatformResponse() {
            return new LoadPlanningPalletSpecResponse(
                    id,
                    palletCode,
                    palletName,
                    quantity,
                    lengthMm,
                    widthMm,
                    heightMm,
                    tareWeightKg,
                    maxLoadKg,
                    maxTotalHeightMm,
                    sourceType,
                    sourceReference,
                    sourceLocator,
                    confidence,
                    verified);
        }
    }
}
