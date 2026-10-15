--
-- V8 增加沙丘 OS 总入口和供应链 OS 菜单。
--
-- route_path 必须与 platform-web/src/router/index.ts 中的真实路由一致。
-- 父菜单用于侧栏分组，本身不直接打开页面；子菜单才对应可访问的业务页面。
--
INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status
) VALUES (
    'portal',
    '沙丘 OS',
    '/',
    'dashboard',
    5,
    'ACTIVE'
);

-- LINK 是关务 OS 中当前已经落地的业务，因此只调整分组名称，不改变稳定编码和 URL。
UPDATE platform_menu
SET menu_name = '关务 OS'
WHERE menu_code = 'link-booking';

INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status
) VALUES (
    'supply-chain',
    '供应链 OS',
    '/supply-chain',
    'layers',
    40,
    'ACTIVE'
);

INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status,
    parent_id
)
SELECT
    'supply-chain-load-planning',
    '智能装载中心',
    '/supply-chain/load-planning',
    'layers',
    10,
    'ACTIVE',
    parent.id
FROM platform_menu AS parent
WHERE parent.menu_code = 'supply-chain';

INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status,
    parent_id
)
SELECT
    'supply-chain-load-tasks',
    '装载任务',
    '/supply-chain/load-planning/tasks',
    'box',
    20,
    'ACTIVE',
    parent.id
FROM platform_menu AS parent
WHERE parent.menu_code = 'supply-chain';
