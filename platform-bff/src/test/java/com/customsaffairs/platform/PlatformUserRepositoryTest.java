package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;

import com.customsaffairs.platform.organization.Organization;
import com.customsaffairs.platform.organization.OrganizationRepository;
import com.customsaffairs.platform.user.PlatformUser;
import com.customsaffairs.platform.user.PlatformUserRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.test.context.ActiveProfiles;

/**
 * 用户与组织关联的 Repository 测试。
 *
 * <p>测试在临时 H2 数据库中先保存组织，再保存属于该组织的用户，验证外键对应的对象关系。</p>
 */
@DataJpaTest
@ActiveProfiles("test")
class PlatformUserRepositoryTest {

    /** 测试数据库中的组织数据访问接口。 */
    @Autowired
    private OrganizationRepository organizationRepository;

    /** 测试数据库中的用户数据访问接口。 */
    @Autowired
    private PlatformUserRepository platformUserRepository;

    /** 验证用户可以保存，并能读取其所属组织。 */
    @Test
    void shouldSaveUserBelongingToOrganization() {
        Organization organization = organizationRepository.save(
                new Organization("CUSTOMS_DEMO", "关务演示组织"));

        platformUserRepository.save(
                new PlatformUser("customs_demo", "关务演示用户", organization));

        PlatformUser savedUser = platformUserRepository.findAllByOrderByIdAsc().getFirst();

        assertThat(savedUser.getUsername()).isEqualTo("customs_demo");
        assertThat(savedUser.getOrganization().getOrganizationCode()).isEqualTo("CUSTOMS_DEMO");
        assertThat(platformUserRepository.existsByUsername("customs_demo")).isTrue();
    }
}
