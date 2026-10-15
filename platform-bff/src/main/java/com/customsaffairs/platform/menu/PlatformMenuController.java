package com.customsaffairs.platform.menu;

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

/** 平台菜单管理 HTTP 入口。 */
@RestController
@RequestMapping("/api/v1/platform/menus")
public class PlatformMenuController {

    private final PlatformMenuService menuService;

    public PlatformMenuController(PlatformMenuService menuService) {
        this.menuService = menuService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiResponse<PlatformMenuResponse> create(
            @Valid @RequestBody CreatePlatformMenuRequest request) {
        return ApiResponse.success(menuService.create(request));
    }

    @GetMapping
    public ApiResponse<List<PlatformMenuResponse>> findAll() {
        return ApiResponse.success(menuService.findAll());
    }

    @GetMapping("/tree")
    public ApiResponse<List<PlatformMenuResponse>> findTree() {
        return ApiResponse.success(menuService.findTree());
    }

    @GetMapping("/{menuId}")
    public ApiResponse<PlatformMenuResponse> findById(@PathVariable Long menuId) {
        return ApiResponse.success(menuService.findById(menuId));
    }
}
