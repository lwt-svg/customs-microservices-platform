package com.customsaffairs.platform.link;

import com.customsaffairs.platform.common.api.ApiResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/**
 * 平台侧 PDF 上传、查询与人工确认入口。
 *
 * <p>控制器只负责 HTTP 参数和统一响应；文件解析、候选生成以及原子创建草稿都由 Python
 * LINK 服务完成。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/link-booking/document-imports")
public class LinkBookingDocumentImportController {

    private final LinkBookingDocumentImportService service;

    public LinkBookingDocumentImportController(LinkBookingDocumentImportService service) {
        this.service = service;
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<LinkBookingDocumentImportResponse> upload(
            @RequestPart("file") MultipartFile file) {
        return ApiResponse.success(service.upload(file));
    }

    @GetMapping("/{documentId}")
    public ApiResponse<LinkBookingDocumentImportResponse> get(
            @PathVariable long documentId) {
        return ApiResponse.success(service.get(documentId));
    }

    @PostMapping("/{documentId}/confirm")
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<LinkBookingApplicationResponse> confirm(
            @PathVariable long documentId,
            @Valid @RequestBody ConfirmLinkBookingDocumentImportRequest request) {
        return ApiResponse.success(service.confirm(documentId, request));
    }
}
