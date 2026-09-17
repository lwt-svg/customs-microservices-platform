package com.customsaffairs.platform.user;

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
 * 平台用户管理的 HTTP 入口。
 *
 * <p>本节只开放创建和查询，不开放修改密码，因为当前还没有设计安全认证与密码哈希。</p>
 */
@RestController
@RequestMapping("/api/v1/platform/users")
public class PlatformUserController {

    /** 负责用户业务规则和数据转换的 Service。 */
    private final PlatformUserService platformUserService;

    /** @param platformUserService Spring 自动注入的用户业务服务。 */
    public PlatformUserController(PlatformUserService platformUserService) {
        this.platformUserService = platformUserService;
    }

    /**
     * 创建用户。
     *
     * @param request 请求 JSON 转换成的 Java 对象。
     * @return 统一响应外壳，data 中是新用户。
     */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<PlatformUserResponse> create(
            @Valid @RequestBody CreatePlatformUserRequest request) {
        return ApiResponse.success(platformUserService.create(request));
    }

    /**
     * 查全部用户
     * @return 统一响应外壳，data 中是用户列表。 */
    @GetMapping
    public ApiResponse<List<PlatformUserResponse>> findAll() {
        return ApiResponse.success(platformUserService.findAll());
    }

    /**
     * 根据主键查询用户。
     *
     * @param userId 路径中的数字主键，例如 /users/1 中的 1。
     * @return 统一响应外壳，data 中是单个用户。
     */
    @GetMapping("/{userId}")
    public ApiResponse<PlatformUserResponse> findById(@PathVariable Long userId) {
        return ApiResponse.success(platformUserService.findById(userId));
    }
}
