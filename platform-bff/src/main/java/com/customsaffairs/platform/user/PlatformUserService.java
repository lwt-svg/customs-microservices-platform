package com.customsaffairs.platform.user;

import com.customsaffairs.platform.organization.Organization;
import com.customsaffairs.platform.organization.OrganizationRepository;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/**
 * 平台用户的业务服务。
 *
 * <p>本类组织一次完整的用户操作：校验组织、检查用户名、创建实体、保存并转换响应。
 * Controller 不直接访问 Repository，因此 HTTP 层与数据库规则不会混在一起。</p>
 */
@Service
public class PlatformUserService {

    /** 负责读写用户表。 */
    private final PlatformUserRepository platformUserRepository;

    /** 负责确认请求中指定的组织真实存在。 */
    private final OrganizationRepository organizationRepository;

    /**
     * Spring 创建 Service 时自动传入两个 Repository。
     *
     * @param platformUserRepository 用户数据访问接口。
     * @param organizationRepository 组织数据访问接口。
     */
    public PlatformUserService(
            PlatformUserRepository platformUserRepository,
            OrganizationRepository organizationRepository) {
        this.platformUserRepository = platformUserRepository;
        this.organizationRepository = organizationRepository;
    }

    /**
     * 创建一个属于指定组织的平台用户。
     *
     * @param request 已通过基础格式校验的创建请求。
     * @return 已保存的用户响应。
     */
    @Transactional
    public PlatformUserResponse create(CreatePlatformUserRequest request) {
        if (platformUserRepository.existsByUsername(request.username())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "用户名已存在。");
        }

        Organization organization = organizationRepository.findById(request.organizationId())
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "用户所属组织不存在。"));

        PlatformUser user = new PlatformUser(
                request.username(), request.displayName(), organization);
        PlatformUser savedUser = platformUserRepository.save(user);
        return PlatformUserResponse.from(savedUser);
    }

    /**
     * 查询全部平台用户。
     *
     * <p>方法处于只读事务中，确保转换响应时仍可以安全读取延迟加载的所属组织。</p>
     *
     * @return 按用户主键升序排列的响应列表。
     */
    @Transactional(readOnly = true)
    public List<PlatformUserResponse> findAll() {
        return platformUserRepository.findAllByOrderByIdAsc().stream()
                .map(PlatformUserResponse::from)
                .toList();
    }

    /**
     * 根据主键查询一个平台用户。
     *
     * @param userId URL 中的用户主键。
     * @return 单个用户响应。
     */
    @Transactional(readOnly = true)
    public PlatformUserResponse findById(Long userId) {
        PlatformUser user = platformUserRepository.findById(userId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "用户不存在。"));
        return PlatformUserResponse.from(user);
    }
}
