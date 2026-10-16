<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { PlatformMenu } from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import { supportedRoutePaths } from '@/router'

type IconName =
  | 'dashboard'
  | 'organization'
  | 'users'
  | 'shield'
  | 'key'
  | 'server'
  | 'database'
  | 'layers'
  | 'box'
  | 'truck'

interface NavigationItem {
  label: string
  path: string
  icon: IconName
  enabled: boolean
}

interface NavigationGroup {
  title: string
  items: NavigationItem[]
}

const props = defineProps<{
  menuTree: PlatformMenu[]
  menuLoading: boolean
  menuError: string
}>()

const emit = defineEmits<{
  reloadMenu: []
}>()

const route = useRoute()
const router = useRouter()

/**
 * 当 Java BFF 尚未启动、菜单接口失败或返回空树时使用的最小导航。
 *
 * 它只是可用性兜底，不是第二份真实菜单数据，也不代表用户拥有这些权限。真正的权限
 * 过滤要等登录和 Spring Security 章节实现。
 */
const fallbackGroups: NavigationGroup[] = [
  {
    title: '核心工作台',
    items: [
      {
        label: '运营驾驶舱',
        path: '/dashboard',
        icon: 'dashboard',
        enabled: true,
      },
      {
        label: '发运案件',
        path: '/tradeops/shipment-cases',
        icon: 'truck',
        enabled: true,
      },
    ],
  },
  {
    title: '专业作业',
    items: [
      {
        label: 'LINK 自动订舱',
        path: '/link-booking/applications',
        icon: 'database',
        enabled: true,
      },
      {
        label: '智能装载中心',
        path: '/supply-chain/load-planning',
        icon: 'layers',
        enabled: true,
      },
      {
        label: '装载任务',
        path: '/supply-chain/load-planning/tasks',
        icon: 'box',
        enabled: true,
      },
    ],
  },
  {
    title: '平台治理',
    items: [
      {
        label: '组织管理',
        path: '/organizations',
        icon: 'organization',
        enabled: true,
      },
      { label: '用户管理', path: '/users', icon: 'users', enabled: true },
      {
        label: '角色与权限',
        path: '/authorization',
        icon: 'shield',
        enabled: true,
      },
      {
        label: '能力目录',
        path: '/workspaces',
        icon: 'server',
        enabled: true,
      },
    ],
  },
]

function toIconName(icon: string | null): IconName {
  const supportedIcons: IconName[] = [
    'dashboard',
    'organization',
    'users',
    'shield',
    'key',
    'server',
    'database',
    'layers',
    'box',
    'truck',
  ]
  return icon && supportedIcons.includes(icon as IconName) ? (icon as IconName) : 'dashboard'
}

/**
 * 把 Java 返回的父子菜单树转换成侧栏分组。
 *
 * enabled 目前只表示“Vue Router 是否已经注册对应页面”，不是鉴权结果。数据库里有
 * 菜单但前端没有对应路由时，侧栏会显示“开发中”并禁止点击。
 */
const navigationGroups = computed(() => {
  if (props.menuTree.length === 0) {
    return fallbackGroups
  }

  const groups: NavigationGroup[] = []
  const standaloneItems: NavigationItem[] = []

  for (const rootMenu of props.menuTree.filter((menu) => menu.status === 'ACTIVE')) {
    const activeChildren = rootMenu.children.filter((menu) => menu.status === 'ACTIVE')

    if (activeChildren.length === 0) {
      standaloneItems.push({
        label: rootMenu.menuName,
        path: rootMenu.routePath,
        icon: toIconName(rootMenu.icon),
        enabled: supportedRoutePaths.has(rootMenu.routePath),
      })
      continue
    }

    groups.push({
      title: rootMenu.menuName,
      items: activeChildren.map((menu) => ({
        label: menu.menuName,
        path: menu.routePath,
        icon: toIconName(menu.icon),
        enabled: supportedRoutePaths.has(menu.routePath),
      })),
    })
  }

  /**
   * 兼容尚未执行最新 Flyway 迁移的本地数据库：多个无父节点菜单合并为一个分组，
   * 避免每个节点都生成一个同名“工作空间”标题。V10 执行后，正常菜单均有明确分组。
   */
  if (standaloneItems.length > 0) {
    groups.unshift({ title: '快捷入口', items: standaloneItems })
  }

  return groups
})

/**
 * 动态详情路由可通过 activeMenuPath 指回所属列表菜单。
 * 这里只解决导航高亮，不进行权限判断。
 */
const activeNavigationPath = computed(() => {
  const configuredPath = route.meta.activeMenuPath
  return typeof configuredPath === 'string' ? configuredPath : route.path
})

/**
 * 页面标题优先采用 Java 菜单名称，页面说明来自前端路由 meta。
 * 这样后台可以调整菜单文案，但不会把大段体验文案塞进数据库。
 */
const pageTitle = computed(() => {
  let menuTitle = ''
  for (const rootMenu of props.menuTree) {
    const menus = rootMenu.children.length > 0 ? rootMenu.children : [rootMenu]
    const matchedMenu = menus.find((menu) => menu.routePath === route.path)
    if (matchedMenu) {
      menuTitle = matchedMenu.menuName
      break
    }
  }

  return {
    title: menuTitle || String(route.meta.title ?? '沙丘 TradeOps'),
    description: String(route.meta.description ?? ''),
  }
})

function navigate(path: string): void {
  if (supportedRoutePaths.has(path)) {
    void router.push(path)
  }
}
</script>

<template>
  <div class="admin-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/dashboard" aria-label="返回 TradeOps 运营驾驶舱">
        <span class="brand-mark"><span></span></span>
        <div>
          <strong>沙丘 TradeOps</strong>
          <small>跨境发运智能作业平台</small>
        </div>
      </RouterLink>

      <nav class="navigation" aria-label="TradeOps 主导航">
        <section v-for="group in navigationGroups" :key="group.title" class="navigation-group">
          <p>{{ group.title }}</p>
          <button
            v-for="item in group.items"
            :key="item.label"
            class="navigation-item"
            :class="{ active: item.path === activeNavigationPath, disabled: !item.enabled }"
            type="button"
            :disabled="!item.enabled"
            @click="navigate(item.path)"
          >
            <UiIcon :name="item.icon" />
            <span>{{ item.label }}</span>
            <span v-if="!item.enabled" class="coming-soon">开发中</span>
          </button>
        </section>

        <div v-if="menuLoading" class="navigation-status">正在读取菜单…</div>
        <div v-else-if="menuError" class="navigation-status error">
          <span>菜单读取失败</span>
          <button type="button" @click="emit('reloadMenu')">重试</button>
        </div>
      </nav>

      <div class="sidebar-footer">
        <span class="environment-dot"></span>
        本地沙箱环境
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="page-title">
          <h1>{{ pageTitle.title }}</h1>
          <p>{{ pageTitle.description }}</p>
        </div>
        <div class="account">
          <span class="avatar">A</span>
          <div>
            <strong>admin</strong>
            <small>本地演示身份</small>
          </div>
        </div>
      </header>

      <main class="main-content">
        <slot />
      </main>
    </div>
  </div>
</template>
