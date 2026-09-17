package com.customsaffairs.platform.authorization;

import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/** 平台权限的业务服务，负责重复校验、保存和查询。 */
@Service
public class PlatformPermissionService {

    private final PlatformPermissionRepository permissionRepository;

    public PlatformPermissionService(PlatformPermissionRepository permissionRepository) {
        this.permissionRepository = permissionRepository;
    }

    /** 创建一个权限编码不能重复的平台权限。 */
    @Transactional
    public PlatformPermissionResponse create(CreatePlatformPermissionRequest request) {
        if (permissionRepository.existsByPermissionCode(request.permissionCode())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "权限编码已存在。");
        }

        PlatformPermission permission =
                new PlatformPermission(request.permissionCode(), request.permissionName());
        return PlatformPermissionResponse.from(permissionRepository.save(permission));
    }

    /** 查询全部权限。 */
    @Transactional(readOnly = true)
    public List<PlatformPermissionResponse> findAll() {
        return permissionRepository.findAllByOrderByIdAsc().stream()
                .map(PlatformPermissionResponse::from)
                .toList();
    }

    /** 根据主键查询权限。 */
    @Transactional(readOnly = true)
    public PlatformPermissionResponse findById(Long permissionId) {
        PlatformPermission permission = permissionRepository.findById(permissionId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "权限不存在。"));
        return PlatformPermissionResponse.from(permission);
    }
}
