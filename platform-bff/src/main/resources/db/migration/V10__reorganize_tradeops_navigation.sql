--
-- V10 将历史上的“多个 OS 入口”收口为一套以发运案件为主线的 TradeOps 导航。
--
-- 这次迁移只调整菜单名称、顺序和父子关系，不授予权限，也不代表已经实现用户级 RBAC。
-- menu_code 和实际业务 URL 尽量保持稳定，避免影响已经存在的接口与前端书签。
--

-- 第一组：运营驾驶舱和发运案件是每天进入系统后的核心工作台。
UPDATE platform_menu
SET menu_name = '核心工作台',
    route_path = '/tradeops',
    icon = 'dashboard',
    sort_order = 10,
    status = 'ACTIVE',
    parent_id = NULL
WHERE menu_code = 'tradeops';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'tradeops'
SET child.menu_name = '运营驾驶舱',
    child.route_path = '/dashboard',
    child.icon = 'dashboard',
    child.sort_order = 10,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'dashboard';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'tradeops'
SET child.menu_name = '发运案件',
    child.route_path = '/tradeops/shipment-cases',
    child.icon = 'truck',
    child.sort_order = 20,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'tradeops-shipment-cases';

-- 第二组：LINK 与智能装载是案件流程调用的专业作业能力。
UPDATE platform_menu
SET menu_name = '专业作业',
    route_path = '/operations',
    icon = 'layers',
    sort_order = 20,
    status = 'ACTIVE',
    parent_id = NULL
WHERE menu_code = 'link-booking';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'link-booking'
SET child.menu_name = 'LINK 自动订舱',
    child.icon = 'database',
    child.sort_order = 10,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'link-booking-applications';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'link-booking'
SET child.menu_name = '智能装载中心',
    child.icon = 'layers',
    child.sort_order = 20,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'supply-chain-load-planning';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'link-booking'
SET child.menu_name = '装载任务',
    child.icon = 'box',
    child.sort_order = 30,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'supply-chain-load-tasks';

-- 原供应链父节点已被“专业作业”分组替代，保留记录便于追溯，但不再展示。
UPDATE platform_menu
SET status = 'DISABLED',
    sort_order = 80
WHERE menu_code = 'supply-chain';

-- 第三组：治理能力放在业务工作台之后；旧门户改为辅助性的能力目录。
UPDATE platform_menu
SET menu_name = '平台治理',
    route_path = '/platform',
    icon = 'shield',
    sort_order = 90,
    status = 'ACTIVE',
    parent_id = NULL
WHERE menu_code = 'platform';

UPDATE platform_menu AS child
JOIN platform_menu AS parent
  ON parent.menu_code = 'platform'
SET child.menu_name = '能力目录',
    child.route_path = '/workspaces',
    child.icon = 'server',
    child.sort_order = 40,
    child.status = 'ACTIVE',
    child.parent_id = parent.id
WHERE child.menu_code = 'portal';
