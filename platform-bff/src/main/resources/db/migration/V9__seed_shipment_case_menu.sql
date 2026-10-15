--
-- V9 增加 TradeOps 发运案件菜单。
--
-- TradeOps 是跨关务与供应链的案件协同入口。父菜单只用于侧栏分组，
-- 子菜单 route_path 必须与 platform-web/src/router/index.ts 中的真实路由一致。
--
INSERT INTO platform_menu (
    menu_code,
    menu_name,
    route_path,
    icon,
    sort_order,
    status
)
SELECT
    'tradeops',
    'TradeOps',
    '/tradeops',
    'truck',
    25,
    'ACTIVE'
WHERE NOT EXISTS (
    SELECT 1
    FROM platform_menu
    WHERE menu_code = 'tradeops'
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
    'tradeops-shipment-cases',
    '发运案件中心',
    '/tradeops/shipment-cases',
    'truck',
    10,
    'ACTIVE',
    parent.id
FROM platform_menu AS parent
WHERE parent.menu_code = 'tradeops'
  AND NOT EXISTS (
      SELECT 1
      FROM platform_menu
      WHERE menu_code = 'tradeops-shipment-cases'
  );
