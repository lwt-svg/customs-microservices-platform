package com.customsaffairs.platform.authorization;

import com.customsaffairs.platform.user.PlatformUser;
import com.customsaffairs.platform.user.PlatformUserRepository;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/** 负责用户与角色关系的业务服务。 */
@Service
public class PlatformUserRoleService {

    private final PlatformUserRepository userRepository;
    private final PlatformRoleRepository roleRepository;

    public PlatformUserRoleService(
            PlatformUserRepository userRepository,
            PlatformRoleRepository roleRepository) {
        this.userRepository = userRepository;
        this.roleRepository = roleRepository;
    }

    /**
     * 给用户分配角色。
     *
     * <p>保存用户时，JPA 会向 platform_user_role 中间表写入关系。</p>
     */
    @Transactional
    public List<PlatformRoleResponse> assignRole(Long userId, Long roleId) {
        PlatformUser user = findUser(userId);
        PlatformRole role = roleRepository.findById(roleId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "角色不存在。"));

        user.assignRole(role);
        userRepository.save(user);
        return toRoleResponses(user);
    }

    /** 查询一个用户当前拥有的角色。 */
    @Transactional(readOnly = true)
    public List<PlatformRoleResponse> findRoles(Long userId) {
        return toRoleResponses(findUser(userId));
    }

    private PlatformUser findUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "用户不存在。"));
    }

    private List<PlatformRoleResponse> toRoleResponses(PlatformUser user) {
        return user.getRoles().stream()
                .sorted((left, right) -> left.getId().compareTo(right.getId()))
                .map(PlatformRoleResponse::from)
                .toList();
    }
}
