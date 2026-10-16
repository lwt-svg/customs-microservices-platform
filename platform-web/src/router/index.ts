import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

/**
 * 前端真实路由表是“一个 URL 应该渲染哪个 Vue 页面”的唯一依据。
 *
 * Java 菜单接口只负责菜单名称、层级和未来的权限过滤。即使数据库误配了一个前端尚未
 * 实现的 routePath，侧栏也只会显示“开发中”，不会导航到不存在的页面。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/workspaces',
    name: 'workspaces',
    component: () => import('@/views/PortalView.vue'),
    meta: {
      title: '能力目录',
      description: '查看 TradeOps 已接入的专业作业能力',
    },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('@/views/DashboardView.vue'),
    meta: {
      title: '运营驾驶舱',
      description: '查看跨境发运案件、待办与专业作业运行态势',
    },
  },
  {
    path: '/organizations',
    name: 'organizations',
    component: () => import('@/views/OrganizationView.vue'),
    meta: {
      title: '组织管理',
      description: '维护平台组织和用户归属关系',
    },
  },
  {
    path: '/users',
    name: 'users',
    component: () => import('@/views/UserView.vue'),
    meta: {
      title: '用户管理',
      description: '维护平台用户及其所属组织',
    },
  },
  {
    path: '/authorization',
    name: 'authorization',
    component: () => import('@/views/AuthorizationView.vue'),
    meta: {
      title: '角色与权限',
      description: '配置角色包含的权限能力',
    },
  },
  {
    path: '/tradeops/shipment-cases',
    name: 'shipment-cases',
    component: () => import('@/views/ShipmentCasesView.vue'),
    meta: {
      title: '发运案件中心',
      description: '统一管理发运案件、专业资源、状态流转与审计事件',
    },
  },
  {
    path: '/tradeops/agents',
    name: 'agent-center',
    component: () => import('@/views/AgentCenterView.vue'),
    meta: {
      title: 'Agent 运行中心',
      description: '查看总编排 Agent、专业能力、人工待办与可审计运行轨迹',
    },
  },
  {
    path: '/tradeops/shipment-cases/:caseNumber',
    name: 'shipment-case-workspace',
    component: () => import('@/views/ShipmentCaseWorkspaceView.vue'),
    meta: {
      title: '发运案件工作台',
      description: '查看单票案件的协同阶段、专业资源、当前建议与审计时间线',
      // 动态详情页仍属于“发运案件”菜单，侧栏不应在进入案件后失去高亮。
      activeMenuPath: '/tradeops/shipment-cases',
    },
  },
  {
    path: '/link-booking/applications',
    name: 'link-booking-applications',
    component: () => import('@/views/LinkBookingView.vue'),
    meta: {
      title: 'LINK 订舱申请',
      description: '自动填写 CEVA LINK 七步流程并等待业务确认',
    },
  },
  {
    path: '/supply-chain/load-planning',
    name: 'load-planning-overview',
    component: () => import('@/views/LoadPlanningOverviewView.vue'),
    meta: {
      title: '智能装载中心',
      description: '面向箱到托盘的装载规划、3D 复核与审批工作台',
    },
  },
  {
    path: '/supply-chain/load-planning/tasks',
    name: 'load-planning-tasks',
    component: () => import('@/views/LoadPlanningTasksView.vue'),
    meta: {
      title: '装载任务',
      description: '创建、校验和跟踪装载规划任务',
    },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

/** AdminLayout 用它判断 Java 返回的菜单地址是否已有对应页面。 */
export const supportedRoutePaths = new Set(
  routes
    .map((route) => route.path)
    .filter((path) => !path.includes(':pathMatch')),
)

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

export default router
