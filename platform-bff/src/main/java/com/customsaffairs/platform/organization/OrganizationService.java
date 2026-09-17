package com.customsaffairs.platform.organization;

import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

/**
 * 组织领域的业务服务。
 *
 * <p>Controller 只处理 HTTP，Repository 只处理数据库；“编码不能重复”“找不到组织怎么办”等规则放在
 * Service，后续其他接口复用时不会散落多份逻辑。</p>
 */
@Service
public class OrganizationService {

    /** Spring 注入的组织数据访问接口。 */
    private final OrganizationRepository organizationRepository;

    /**
     * @param organizationRepository 用于读写 platform_organization 表。
     */
    public OrganizationService(OrganizationRepository organizationRepository) {
        this.organizationRepository = organizationRepository;
    }

    /**
     * 创建一条默认启用的组织记录。
     *
     * @param request Controller 已校验过格式的创建请求。
     * @return 已保存后的组织响应数据。
     */
    @Transactional
    public OrganizationResponse create(CreateOrganizationRequest request) {
        if (organizationRepository.existsByOrganizationCode(request.organizationCode())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "组织编码已存在。");
        }

        Organization organization = new Organization(
                request.organizationCode(), request.organizationName());
        Organization savedOrganization = organizationRepository.save(organization);
        return OrganizationResponse.from(savedOrganization);
    }

    /**
     * 查询全部组织。
     *
     * @return 按主键升序排列的组织响应列表。
     */
    @Transactional(readOnly = true)
    public List<OrganizationResponse> findAll() {
        return organizationRepository.findAllByOrderByIdAsc().stream()
                .map(OrganizationResponse::from)
                .toList();
    }

    /**
     * 根据主键查询一条组织。
     *
     * @param organizationId URL 中传入的组织主键。
     * @return 找到的组织响应数据。
     */
    @Transactional(readOnly = true)
    public OrganizationResponse findById(Long organizationId) {
        Organization organization = organizationRepository.findById(organizationId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "组织不存在。"));
        return OrganizationResponse.from(organization);
    }
}
