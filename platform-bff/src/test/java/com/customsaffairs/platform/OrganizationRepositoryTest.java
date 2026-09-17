package com.customsaffairs.platform;

import static org.assertj.core.api.Assertions.assertThat;

import com.customsaffairs.platform.organization.Organization;
import com.customsaffairs.platform.organization.OrganizationRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.test.context.ActiveProfiles;

/**
 * 组织 Repository 的数据库访问测试。
 *
 * <p>测试 profile 使用 H2 内存数据库和 create-drop，因此能验证 JPA 映射，却不会读写本机 MySQL。</p>
 */
@DataJpaTest
@ActiveProfiles("test")
class OrganizationRepositoryTest {

    /** Spring 注入的真实 Repository；底层连接的是测试专用 H2。 */
    @Autowired
    private OrganizationRepository organizationRepository;

    /** 验证保存组织后，Repository 能按编码发现它。 */
    @Test
    void shouldSaveOrganizationAndFindItsCode() {
        organizationRepository.save(new Organization("CUSTOMS", "关务部"));

        assertThat(organizationRepository.existsByOrganizationCode("CUSTOMS")).isTrue();
        assertThat(organizationRepository.findAllByOrderByIdAsc())
                .hasSize(1)
                .first()
                .extracting(Organization::getOrganizationName)
                .isEqualTo("关务部");
    }
}
