package com.customsaffairs.platform.user;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * {@code platform_user} 表的数据访问接口。
 *
 * <p>Spring Data JPA 根据接口和方法名自动生成常用查询实现。</p>
 */
public interface PlatformUserRepository extends JpaRepository<PlatformUser, Long> {

    /** @return 指定 username 已被使用时为 true。 */
    boolean existsByUsername(String username);

    /** @return 按主键升序排列的全部用户。 */
    List<PlatformUser> findAllByOrderByIdAsc();
}
