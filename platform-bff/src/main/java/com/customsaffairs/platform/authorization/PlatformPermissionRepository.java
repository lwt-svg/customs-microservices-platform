package com.customsaffairs.platform.authorization;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

/** {@code platform_permission} 表的数据访问接口。 */
public interface PlatformPermissionRepository extends JpaRepository<PlatformPermission, Long> {

    /** @return 指定权限编码已经存在时为 true。 */
    boolean existsByPermissionCode(String permissionCode);

    /** @return 按主键升序排列的全部权限。 */
    List<PlatformPermission> findAllByOrderByIdAsc();
}
