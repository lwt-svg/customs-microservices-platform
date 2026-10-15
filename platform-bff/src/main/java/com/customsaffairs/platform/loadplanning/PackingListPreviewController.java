package com.customsaffairs.platform.loadplanning;

import com.customsaffairs.platform.common.api.ApiResponse;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/** Vue 上传 Packing List 并查看候选数据的统一 Java BFF 入口。 */
@RestController
@RequestMapping("/api/v1/platform/load-planning/packing-list-imports")
public class PackingListPreviewController {

    private final PackingListPreviewService packingListPreviewService;

    public PackingListPreviewController(PackingListPreviewService packingListPreviewService) {
        this.packingListPreviewService = packingListPreviewService;
    }

    @PostMapping(value = "/preview", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ApiResponse<PackingListPreviewResponse> preview(
            @RequestPart("file") MultipartFile file) {
        return ApiResponse.success(packingListPreviewService.preview(file));
    }
}
