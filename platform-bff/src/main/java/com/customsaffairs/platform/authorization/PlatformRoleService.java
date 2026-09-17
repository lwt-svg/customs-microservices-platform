package com.customsaffairs.platform.authorization;

import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/** 平台角色的业务服务，负责角色本身以及角色与权限之间的关系。 */
@Service
public class PlatformRoleService {

    private final PlatformRoleRepository roleRepository;
    private final PlatformPermissionRepository permissionRepository;

    public PlatformRoleService(
            PlatformRoleRepository roleRepository,
            PlatformPermissionRepository permissionRepository) {
        this.roleRepository = roleRepository;
        this.permissionRepository = permissionRepository;
    }

    /** 创建角色；角色编码不能重复。 */
    @Transactional
    public PlatformRoleResponse create(CreatePlatformRoleRequest request) {
        if (roleRepository.existsByRoleCode(request.roleCode())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "角色编码已存在。");
        }

        PlatformRole role = new PlatformRole(request.roleCode(), request.roleName());
        return PlatformRoleResponse.from(roleRepository.save(role));
    }

    /** 查询全部角色。 */
    @Transactional(readOnly = true)
    public List<PlatformRoleResponse> findAll() {
        return roleRepository.findAllByOrderByIdAsc().stream()
                .map(PlatformRoleResponse::from)
                .toList();
    }

    /** 根据主键查询角色。 */
    @Transactional(readOnly = true)
    public PlatformRoleResponse findById(Long roleId) {
        return PlatformRoleResponse.from(findRole(roleId));
    }

    /**
     * 给角色分配一个权限。
     *
     * <p>保存角色时，JPA 会向 platform_role_permission 中间表写入关系。</p>
     */
    @Transactional   //@Transactional：事务—— 方法里所有数据库操作要么一起成功，中途一旦抛异常就全部回滚，不会改一半
    public List<PlatformPermissionResponse> assignPermission(Long roleId, Long permissionId) {
        PlatformRole role = findRole(roleId);
        PlatformPermission permission = permissionRepository.findById(permissionId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "权限不存在。"));

        role.assignPermission(permission);  //role调用自己的添加权限方法
        roleRepository.save(role);
        return toPermissionResponses(role);
    }

    /** 查询一个角色当前包含的权限。 */
    @Transactional(readOnly = true)
    public List<PlatformPermissionResponse> findPermissions(Long roleId) {
        return toPermissionResponses(findRole(roleId));
    }

    private PlatformRole findRole(Long roleId) {
        return roleRepository.findById(roleId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "角色不存在。"));
    }

    private List<PlatformPermissionResponse> toPermissionResponses(PlatformRole role) {
        return role.getPermissions().stream()
                .sorted((left, right) -> left.getId().compareTo(right.getId()))
                .map(PlatformPermissionResponse::from)
                .toList();
    }
}
