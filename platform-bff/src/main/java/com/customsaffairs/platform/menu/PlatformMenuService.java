package com.customsaffairs.platform.menu;

import com.customsaffairs.platform.authorization.PlatformPermission;
import com.customsaffairs.platform.authorization.PlatformPermissionRepository;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/** 菜单业务服务，负责菜单层级和可选权限绑定。 */
@Service
public class PlatformMenuService {

    private final PlatformMenuRepository menuRepository;
    private final PlatformPermissionRepository permissionRepository;

    public PlatformMenuService(
            PlatformMenuRepository menuRepository,
            PlatformPermissionRepository permissionRepository) {
        this.menuRepository = menuRepository;
        this.permissionRepository = permissionRepository;
    }

    /** 创建菜单；父菜单和权限如果传入必须真实存在。 */
    @Transactional
    public PlatformMenuResponse create(CreatePlatformMenuRequest request) {
        if (menuRepository.existsByMenuCode(request.menuCode())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "菜单编码已存在。");
        }

        PlatformMenu parent = request.parentId() == null
                ? null
                : menuRepository.findById(request.parentId()).orElseThrow(() ->
                        new ResponseStatusException(HttpStatus.NOT_FOUND, "父菜单不存在。"));
        PlatformPermission permission = request.permissionId() == null
                ? null
                : permissionRepository.findById(request.permissionId()).orElseThrow(() ->
                        new ResponseStatusException(HttpStatus.NOT_FOUND, "菜单权限不存在。"));

        PlatformMenu menu = new PlatformMenu(
                request.menuCode(),
                request.menuName(),
                request.routePath(),
                request.icon(),
                request.sortOrder(),
                parent,
                permission);
        return PlatformMenuResponse.from(menuRepository.save(menu));
    }

    /** 查询全部菜单的平面列表。 */
    @Transactional(readOnly = true)
    public List<PlatformMenuResponse> findAll() {
        return menuRepository.findAllByOrderBySortOrderAscIdAsc().stream()
                .map(PlatformMenuResponse::from)
                .toList();
    }

    /** 查询菜单树，避免前端自己猜测 parentId 关系。 */
    @Transactional(readOnly = true)
    public List<PlatformMenuResponse> findTree() {
        List<PlatformMenu> menus = menuRepository.findAllByOrderBySortOrderAscIdAsc();
        Map<Long, List<PlatformMenu>> childrenByParent = new LinkedHashMap<>();
        for (PlatformMenu menu : menus) {
            Long parentId = menu.getParent() == null ? null : menu.getParent().getId();
            childrenByParent.computeIfAbsent(parentId, ignored -> new ArrayList<>()).add(menu);
        }
        return toTree(childrenByParent, null);
    }

    @Transactional(readOnly = true)
    public PlatformMenuResponse findById(Long menuId) {
        return PlatformMenuResponse.from(findMenu(menuId));
    }

    private PlatformMenu findMenu(Long menuId) {
        return menuRepository.findById(menuId).orElseThrow(() ->
                new ResponseStatusException(HttpStatus.NOT_FOUND, "菜单不存在。"));
    }

    private List<PlatformMenuResponse> toTree(
            Map<Long, List<PlatformMenu>> childrenByParent, Long parentId) {
        return childrenByParent.getOrDefault(parentId, List.of()).stream()
                .map(menu -> PlatformMenuResponse.from(
                        menu,
                        toTree(childrenByParent, menu.getId())))
                .toList();
    }
}
