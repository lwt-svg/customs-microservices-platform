package com.customsaffairs.platform.link;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.IOException;
import java.net.URI;
import java.time.LocalDate;
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
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.util.UriComponentsBuilder;
import org.springframework.web.multipart.MultipartFile;

/**
 * PDF 导入专用代理。
 *
 * <p>它与普通申请代理拆开，避免一个类同时承担 JSON CRUD、multipart 文件转发和解析错误
 * 翻译。文件只在请求内存中短暂存在，Java 不落盘，也不读取 PDF 内容。</p>
 */
@Service
public class LinkBookingDocumentImportService {

    private static final String LINK_BOOKING_SERVICE_ID = "link-booking-service";

    private final DiscoveryClient discoveryClient;
    private final RestClient restClient;

    public LinkBookingDocumentImportService(
            DiscoveryClient discoveryClient, RestClient.Builder restClientBuilder) {
        this.discoveryClient = discoveryClient;
        this.restClient = restClientBuilder.build();
    }

    /** 将浏览器上传的 PDF 原样转发给 Python，由 Python 执行权威格式和大小校验。 */
    public LinkBookingDocumentImportResponse upload(MultipartFile file) {
        if (file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请选择非空 PDF 文件。");
        }

        URI uri = buildLinkUri("api", "v1", "booking-document-imports");
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        try {
            body.add("file", namedResource(file));
            LinkBookingDocumentImportRemoteResponse response = restClient.post()
                    .uri(uri)
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(body)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleDocumentRemoteError)
                    .body(LinkBookingDocumentImportRemoteResponse.class);
            return requireDocumentResponse(response);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (IOException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST, "无法读取待上传的 PDF 文件。", exception);
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK PDF 上传接口失败。", exception);
        }
    }

    /** 查询已经创建的解析会话，供刷新或失败恢复时重新进入人工复核。 */
    public LinkBookingDocumentImportResponse get(long documentId) {
        URI uri = buildLinkUri(
                "api", "v1", "booking-document-imports", Long.toString(documentId));
        try {
            LinkBookingDocumentImportRemoteResponse response = restClient.get()
                    .uri(uri)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleDocumentRemoteError)
                    .body(LinkBookingDocumentImportRemoteResponse.class);
            return requireDocumentResponse(response);
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK PDF 查询接口失败。", exception);
        }
    }

    /**
     * 把人工复核后的完整字段交给 Python。
     *
     * <p>Python 会在单一数据库事务中创建 DRAFT、首条流水并把导入会话标为已确认。</p>
     */
    public LinkBookingApplicationResponse confirm(
            long documentId, ConfirmLinkBookingDocumentImportRequest request) {
        URI uri = buildLinkUri(
                "api", "v1", "booking-document-imports", Long.toString(documentId), "confirm");
        ConfirmDocumentImportRemoteRequest remoteRequest = new ConfirmDocumentImportRemoteRequest(
                request.applicationNumber().trim(),
                request.originPort().trim(),
                request.destinationPort().trim(),
                request.transportMode().trim(),
                request.containerType().trim(),
                request.requestedEtd(),
                LinkBookingDetailsRemote.fromPlatform(request.bookingDetails()));
        try {
            LinkBookingApplicationRemoteResponse response = restClient.post()
                    .uri(uri)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(remoteRequest)
                    .retrieve()
                    .onStatus(HttpStatusCode::isError, this::handleDocumentRemoteError)
                    .body(LinkBookingApplicationRemoteResponse.class);
            if (response == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK PDF 确认响应为空。");
            }
            return response.toPlatformResponse();
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "调用 Python LINK PDF 确认接口失败。", exception);
        }
    }

    private ByteArrayResource namedResource(MultipartFile file) throws IOException {
        String originalFilename = file.getOriginalFilename();
        String safeFilename = originalFilename == null || originalFilename.isBlank()
                ? "demo-booking.pdf"
                : originalFilename;
        byte[] bytes = file.getBytes();
        return new ByteArrayResource(bytes) {
            @Override
            public String getFilename() {
                return safeFilename;
            }
        };
    }

    private LinkBookingDocumentImportResponse requireDocumentResponse(
            LinkBookingDocumentImportRemoteResponse response) {
        if (response == null) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "Python LINK PDF 导入响应为空。");
        }
        return response.toPlatformResponse();
    }

    private URI buildLinkUri(String... pathSegments) {
        ServiceInstance instance = discoveryClient.getInstances(LINK_BOOKING_SERVICE_ID).stream()
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.SERVICE_UNAVAILABLE,
                        "Nacos 中没有可用的 link-booking-service 实例。"));
        UriComponentsBuilder builder = UriComponentsBuilder.fromUri(instance.getUri());
        for (String pathSegment : pathSegments) {
            builder.pathSegment(pathSegment);
        }
        return builder.build().encode().toUri();
    }

    /** 把 Python 的细分解析错误翻译成稳定的平台 HTTP 语义。 */
    private void handleDocumentRemoteError(
            org.springframework.http.HttpRequest ignoredRequest,
            ClientHttpResponse response) throws IOException {
        int statusCode = response.getStatusCode().value();
        HttpStatus targetStatus = switch (statusCode) {
            case 400 -> HttpStatus.BAD_REQUEST;
            case 404 -> HttpStatus.NOT_FOUND;
            case 409 -> HttpStatus.CONFLICT;
            case 413 -> HttpStatus.PAYLOAD_TOO_LARGE;
            case 415 -> HttpStatus.UNSUPPORTED_MEDIA_TYPE;
            case 422 -> HttpStatus.UNPROCESSABLE_ENTITY;
            default -> HttpStatus.BAD_GATEWAY;
        };
        throw new ResponseStatusException(targetStatus, switch (targetStatus) {
            case NOT_FOUND -> "PDF 导入记录不存在。";
            case CONFLICT -> "该 PDF 已确认、申请编号重复或发生并发确认，请刷新后检查。";
            case PAYLOAD_TOO_LARGE -> "PDF 不能超过 5MB。";
            case UNSUPPORTED_MEDIA_TYPE -> "只允许上传 PDF 文件。";
            case UNPROCESSABLE_ENTITY -> "PDF 加密、损坏或没有可提取的文本。";
            case BAD_REQUEST -> "PDF 导入或人工复核字段不正确。";
            default -> "Python LINK 暂时无法处理 PDF 导入。";
        });
    }

    private record ConfirmDocumentImportRemoteRequest(
            @JsonProperty("application_number") String applicationNumber,
            @JsonProperty("origin_port") String originPort,
            @JsonProperty("destination_port") String destinationPort,
            @JsonProperty("transport_mode") String transportMode,
            @JsonProperty("container_type") String containerType,
            @JsonProperty("requested_etd")
            @JsonFormat(shape = JsonFormat.Shape.STRING)
            LocalDate requestedEtd,
            @JsonProperty("booking_details") LinkBookingDetailsRemote bookingDetails) {
    }

    private record CandidateFieldRemoteResponse(
            String value,
            String evidence,
            String confidence) {

        private LinkBookingCandidateFieldResponse toPlatformResponse() {
            return new LinkBookingCandidateFieldResponse(value, evidence, confidence);
        }
    }

    private record CandidateFieldsRemoteResponse(
            @JsonProperty("application_number") CandidateFieldRemoteResponse applicationNumber,
            @JsonProperty("origin_port") CandidateFieldRemoteResponse originPort,
            @JsonProperty("destination_port") CandidateFieldRemoteResponse destinationPort,
            @JsonProperty("transport_mode") CandidateFieldRemoteResponse transportMode,
            @JsonProperty("container_type") CandidateFieldRemoteResponse containerType,
            @JsonProperty("requested_etd") CandidateFieldRemoteResponse requestedEtd,
            @JsonProperty("booking_details") LinkBookingDetailsRemote bookingDetails) {

        private LinkBookingDocumentCandidatesResponse toPlatformResponse() {
            List<CandidateFieldRemoteResponse> fields = java.util.Arrays.asList(
                    applicationNumber,
                    originPort,
                    destinationPort,
                    transportMode,
                    containerType,
                    requestedEtd);
            if (fields.stream().anyMatch(java.util.Objects::isNull)) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK PDF 候选字段不完整。");
            }
            return new LinkBookingDocumentCandidatesResponse(
                    applicationNumber.toPlatformResponse(),
                    originPort.toPlatformResponse(),
                    destinationPort.toPlatformResponse(),
                    transportMode.toPlatformResponse(),
                    containerType.toPlatformResponse(),
                    requestedEtd.toPlatformResponse(),
                    bookingDetails == null ? null : bookingDetails.toPlatformResponse());
        }
    }

    private record LinkBookingDocumentImportRemoteResponse(
            long id,
            String status,
            @JsonProperty("original_filename") String originalFilename,
            @JsonProperty("file_size_bytes") long fileSizeBytes,
            @JsonProperty("parser_version") String parserVersion,
            CandidateFieldsRemoteResponse candidates,
            @JsonProperty("created_application_number") String createdApplicationNumber,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt) {

        private LinkBookingDocumentImportResponse toPlatformResponse() {
            if (candidates == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Python LINK PDF 候选响应不完整。");
            }
            return new LinkBookingDocumentImportResponse(
                    id,
                    status,
                    originalFilename,
                    fileSizeBytes,
                    parserVersion,
                    candidates.toPlatformResponse(),
                    createdApplicationNumber,
                    createdAt,
                    updatedAt);
        }
    }

    private record LinkBookingApplicationRemoteResponse(
            long id,
            @JsonProperty("application_number") String applicationNumber,
            @JsonProperty("booking_reference") String bookingReference,
            @JsonProperty("origin_port") String originPort,
            @JsonProperty("destination_port") String destinationPort,
            @JsonProperty("transport_mode") String transportMode,
            @JsonProperty("container_type") String containerType,
            @JsonProperty("requested_etd")
            @JsonFormat(shape = JsonFormat.Shape.STRING)
            LocalDate requestedEtd,
            @JsonProperty("booking_details") LinkBookingDetailsRemote bookingDetails,
            LinkBookingStatus status,
            @JsonProperty("created_at") String createdAt,
            @JsonProperty("updated_at") String updatedAt) {

        private LinkBookingApplicationResponse toPlatformResponse() {
            return new LinkBookingApplicationResponse(
                    id,
                    applicationNumber,
                    bookingReference,
                    originPort,
                    destinationPort,
                    transportMode,
                    containerType,
                    requestedEtd,
                    bookingDetails == null ? null : bookingDetails.toPlatformResponse(),
                    status,
                    createdAt,
                    updatedAt);
        }
    }
}
