package com.customsaffairs.platform.authorization;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

/** {@code platform_role} 表的数据访问接口。 */
public interface PlatformRoleRepository extends JpaRepository<PlatformRole, Long> {

    /** @return 指定角色编码已经存在时为 true。 */
    boolean existsByRoleCode(String roleCode);

    /** @return 按主键升序排列的全部角色。 */
    List<PlatformRole> findAllByOrderByIdAsc();
}
