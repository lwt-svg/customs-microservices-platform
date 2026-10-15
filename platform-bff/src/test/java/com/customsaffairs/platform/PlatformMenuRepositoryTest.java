package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;

import com.customsaffairs.platform.authorization.PlatformPermission;
import com.customsaffairs.platform.authorization.PlatformPermissionRepository;
import com.customsaffairs.platform.menu.PlatformMenu;
import com.customsaffairs.platform.menu.PlatformMenuRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.test.context.ActiveProfiles;

/** 菜单父子关系和权限绑定的 Repository 测试。 */
@DataJpaTest
@ActiveProfiles("test")
class PlatformMenuRepositoryTest {

    @Autowired
    private PlatformMenuRepository menuRepository;

    @Autowired
    private PlatformPermissionRepository permissionRepository;

    @Autowired
    private TestEntityManager entityManager;

    @Test
    void shouldSaveMenuTreeWithOptionalPermission() {
        PlatformPermission permission = permissionRepository.save(
                new PlatformPermission("platform:user:read", "查看平台用户"));
        PlatformMenu root = menuRepository.save(new PlatformMenu(
                "platform", "平台管理", "/platform", "organization", 10, null, null));
        PlatformMenu child = menuRepository.save(new PlatformMenu(
                "platform-users",
                "用户管理",
                "/users",
                "users",
                20,
                root,
                permission));

        entityManager.flush();
        entityManager.clear();

        PlatformMenu savedChild = menuRepository.findById(child.getId()).orElseThrow();
        assertThat(savedChild.getParent().getMenuCode()).isEqualTo("platform");
        assertThat(savedChild.getPermission().getPermissionCode())
                .isEqualTo("platform:user:read");
    }
}
