package com.customsaffairs.platform.menu;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

/** {@code platform_menu} 表的数据访问接口。 */
public interface PlatformMenuRepository extends JpaRepository<PlatformMenu, Long> {

    boolean existsByMenuCode(String menuCode);

    List<PlatformMenu> findAllByOrderBySortOrderAscIdAsc();
}
