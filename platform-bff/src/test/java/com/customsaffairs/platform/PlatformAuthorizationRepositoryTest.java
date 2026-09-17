package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;

import com.customsaffairs.platform.authorization.PlatformPermission;
import com.customsaffairs.platform.authorization.PlatformPermissionRepository;
import com.customsaffairs.platform.authorization.PlatformRole;
import com.customsaffairs.platform.authorization.PlatformRoleRepository;
import com.customsaffairs.platform.organization.Organization;
import com.customsaffairs.platform.organization.OrganizationRepository;
import com.customsaffairs.platform.user.PlatformUser;
import com.customsaffairs.platform.user.PlatformUserRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.test.context.ActiveProfiles;

/**
 * 角色权限关系的 Repository 测试。
 *
 * <p>使用临时 H2 数据库验证两张中间表能够保存和读取，不访问本机 MySQL。</p>
 */
@DataJpaTest
@ActiveProfiles("test")
class PlatformAuthorizationRepositoryTest {

    @Autowired
    private OrganizationRepository organizationRepository;

    @Autowired
    private PlatformUserRepository userRepository;

    @Autowired
    private PlatformRoleRepository roleRepository;

    @Autowired
    private PlatformPermissionRepository permissionRepository;

    /** 用于把待保存内容写入数据库，并清空 JPA 的一级内存缓存。 */
    @Autowired
    private TestEntityManager entityManager;

    /** 验证用户角色和角色权限两层关系都可以保存。 */
    @Test
    void shouldSaveUserRoleAndRolePermissionRelations() {
        Organization organization = organizationRepository.save(
                new Organization("SAND_DUNE_OS", "沙丘 OS"));
        PlatformUser user = userRepository.save(
                new PlatformUser("admin", "系统管理员", organization));
        PlatformRole role = roleRepository.save(
                new PlatformRole("SYSTEM_ADMIN", "系统管理员"));
        PlatformPermission permission = permissionRepository.save(
                new PlatformPermission("platform:user:read", "查看平台用户"));

        role.assignPermission(permission);
        roleRepository.save(role);

        user.assignRole(role);
        userRepository.save(user);

        // 强制把两张中间表的关系写入数据库，再清空内存对象。
        // 后面的查询若仍能读到关系，才证明关系表确实保存成功。
        entityManager.flush();
        entityManager.clear();

        PlatformUser savedUser = userRepository.findById(user.getId()).orElseThrow();
        PlatformRole savedRole = roleRepository.findById(role.getId()).orElseThrow();

        assertThat(savedUser.getRoles())
                .extracting(PlatformRole::getRoleCode)
                .containsExactly("SYSTEM_ADMIN");
        assertThat(savedRole.getPermissions())
                .extracting(PlatformPermission::getPermissionCode)
                .containsExactly("platform:user:read");
    }
}
