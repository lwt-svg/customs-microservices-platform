package com.customsaffairs.platform.organization;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * 组织表的数据访问接口。
 *
 * <p>继承 {@link JpaRepository} 后，Spring Data JPA 会自动提供 save、findById、existsById
 * 等常用操作，不需要本项目手写 INSERT 或 SELECT SQL。</p>
 */
public interface OrganizationRepository extends JpaRepository<Organization, Long> {

    /**
     * 在保存前检查编码是否已存在，给调用方一个清晰的业务错误，而不是直接泄露数据库异常。
     *
     * @param organizationCode 需要检查的组织编码。
     * @return 编码已存在时为 true。
     */
    boolean existsByOrganizationCode(String organizationCode);

    /**
     * 按主键升序查询全部组织，使学习环境中的列表返回顺序稳定、容易验证。
     *
     * @return 所有组织记录。
     */
    List<Organization> findAllByOrderByIdAsc();
}
