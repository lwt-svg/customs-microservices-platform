package com.customsaffairs.platform.organization;

import com.customsaffairs.platform.common.api.ApiResponse;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * 平台组织管理的 HTTP 入口。
 *
 * <p>本类不直接写 SQL，也不直接判断编码是否重复；它把 HTTP 输入交给 OrganizationService，
 * 再把业务结果包装成统一 ApiResponse JSON。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/organizations")
public class OrganizationController {

    /** 组织领域的业务服务。 */
    private final OrganizationService organizationService;

    /**
     * @param organizationService Spring 自动注入的组织业务服务。
     */
    public OrganizationController(OrganizationService organizationService) {
        this.organizationService = organizationService;
    }

    /**
     * 创建组织。
     *
     * <p>{@code @Valid} 会先执行 CreateOrganizationRequest 上的输入校验。格式正确后才进入
     * Service；成功时返回 201 Created。</p>
     *
     * @param request 浏览器或 curl 提交的 JSON。
     * @return 统一 JSON 外壳；data 是刚创建的组织。
     */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<OrganizationResponse> create(@Valid @RequestBody CreateOrganizationRequest request) {
        OrganizationResponse organization = organizationService.create(request);
        return ApiResponse.success(organization);
    }

    /**
     * 查询全部组织。
     *
     * @return 统一 JSON 外壳；data 是组织列表。
     */
    @GetMapping
    public ApiResponse<List<OrganizationResponse>> findAll() {
        return ApiResponse.success(organizationService.findAll());
    }

    /**
     * 根据主键查询一条组织。
     *
     * @param organizationId URL 中的数字主键，例如 /organizations/1 中的 1。
     * @return 统一 JSON 外壳；data 是单个组织。
     */
    @GetMapping("/{organizationId}")
    public ApiResponse<OrganizationResponse> findById(@PathVariable Long organizationId) {
        return ApiResponse.success(organizationService.findById(organizationId));
    }
}
