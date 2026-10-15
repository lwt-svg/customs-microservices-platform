<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  getLinkBookingHealth,
  getOrganizations,
  getPlatformHealth,
  getPlatformUsers,
  type HealthData,
  type LinkHealthData,
  type Organization,
  type PlatformUser,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const router = useRouter()

const loading = ref(true)
const errorMessage = ref('')
const platformHealth = ref<HealthData | null>(null)
const linkHealth = ref<LinkHealthData | null>(null)
const organizations = ref<Organization[]>([])
const users = ref<PlatformUser[]>([])

const recentOrganizations = computed(() => organizations.value.slice(-4).reverse())
const recentUsers = computed(() => users.value.slice(-4).reverse())

const platformOnline = computed(() => platformHealth.value?.status === 'UP')
const linkOnline = computed(() => ['UP', 'ok', 'OK'].includes(linkHealth.value?.status ?? ''))

/** 并行读取各项数据；LINK 暂时不可用时不影响其他工作台数据展示。 */
async function loadDashboard(): Promise<void> {
  loading.value = true
  errorMessage.value = ''

  const [platformResult, organizationResult, userResult, linkResult] = await Promise.allSettled([
    getPlatformHealth(),
    getOrganizations(),
    getPlatformUsers(),
    getLinkBookingHealth(),
  ])

  if (platformResult.status === 'fulfilled') {
    platformHealth.value = platformResult.value
  } else {
    platformHealth.value = null
  }

  if (organizationResult.status === 'fulfilled') {
    organizations.value = organizationResult.value
  }

  if (userResult.status === 'fulfilled') {
    users.value = userResult.value
  }

  linkHealth.value = linkResult.status === 'fulfilled' ? linkResult.value : null

  const coreFailures = [platformResult, organizationResult, userResult].filter(
    (result) => result.status === 'rejected',
  )
  if (coreFailures.length > 0) {
    errorMessage.value = '部分平台数据读取失败，请确认 Java BFF 已正常启动。'
  }

  loading.value = false
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}

onMounted(loadDashboard)
</script>

<template>
  <section class="dashboard">
    <div class="content-actions">
      <p>数据来自本地 Java BFF，刷新后可获取最新状态。</p>
      <button class="ghost-button" type="button" :disabled="loading" @click="loadDashboard">
        <UiIcon name="refresh" />
        {{ loading ? '正在刷新' : '刷新' }}
      </button>
    </div>

    <div v-if="errorMessage" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ errorMessage }}</p>
      <button type="button" @click="loadDashboard">重新加载</button>
    </div>

    <div class="metric-grid">
      <article class="metric-card">
        <div class="metric-icon blue"><UiIcon name="organization" /></div>
        <div>
          <span>组织</span>
          <strong>{{ loading ? '—' : organizations.length }}</strong>
        </div>
        <small>个平台组织</small>
      </article>

      <article class="metric-card">
        <div class="metric-icon violet"><UiIcon name="users" /></div>
        <div>
          <span>用户</span>
          <strong>{{ loading ? '—' : users.length }}</strong>
        </div>
        <small>个平台用户</small>
      </article>

      <article class="metric-card">
        <div class="metric-icon green"><UiIcon name="server" /></div>
        <div>
          <span>平台服务</span>
          <strong class="service-value">{{ platformOnline ? '正常' : '异常' }}</strong>
        </div>
        <small>Java BFF</small>
      </article>

      <article class="metric-card">
        <div class="metric-icon orange"><UiIcon name="database" /></div>
        <div>
          <span>LINK 服务</span>
          <strong class="service-value">{{ linkOnline ? '正常' : '未连接' }}</strong>
        </div>
        <small>Python 微服务</small>
      </article>
    </div>

    <div class="dashboard-columns">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>服务状态</h2>
            <p>平台核心服务的实时连接情况</p>
          </div>
        </div>

        <div class="service-list">
          <div class="service-row">
            <div class="service-mark"><UiIcon name="server" /></div>
            <div class="service-info">
              <strong>Java Platform BFF</strong>
              <span>{{ platformHealth?.service ?? 'platform-bff' }}</span>
            </div>
            <span class="health-status" :class="{ online: platformOnline }">
              <i></i>{{ platformOnline ? '运行中' : '不可用' }}
            </span>
          </div>

          <div class="service-row">
            <div class="service-mark"><UiIcon name="database" /></div>
            <div class="service-info">
              <strong>LINK Booking Service</strong>
              <span>{{ linkHealth?.environment ?? 'local' }} · Python</span>
            </div>
            <span class="health-status" :class="{ online: linkOnline }">
              <i></i>{{ linkOnline ? '运行中' : '未连接' }}
            </span>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>快捷操作</h2>
            <p>进入常用的平台管理功能</p>
          </div>
        </div>

        <div class="quick-actions">
          <button type="button" @click="router.push('/organizations')">
            <span class="quick-icon blue"><UiIcon name="organization" /></span>
            <span><strong>组织管理</strong><small>查看和创建组织</small></span>
            <UiIcon name="arrow" />
          </button>
          <button type="button" @click="router.push('/users')">
            <span class="quick-icon violet"><UiIcon name="users" /></span>
            <span><strong>用户管理</strong><small>查看和创建用户</small></span>
            <UiIcon name="arrow" />
          </button>
          <button type="button" @click="router.push('/link-booking/applications')">
            <span class="quick-icon green"><UiIcon name="database" /></span>
            <span><strong>LINK 订舱</strong><small>查看 Python 订舱申请</small></span>
            <UiIcon name="arrow" />
          </button>
        </div>
      </section>
    </div>

    <div class="dashboard-columns">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>最近组织</h2>
            <p>最新创建的平台组织</p>
          </div>
          <button class="text-button" type="button" @click="router.push('/organizations')">
            查看全部
          </button>
        </div>

        <div v-if="recentOrganizations.length" class="compact-list">
          <div v-for="organization in recentOrganizations" :key="organization.id" class="compact-row">
            <span class="entity-avatar">{{ organization.organizationName.slice(0, 1) }}</span>
            <div>
              <strong>{{ organization.organizationName }}</strong>
              <small>{{ organization.organizationCode }}</small>
            </div>
            <time>{{ formatDate(organization.createdAt) }}</time>
          </div>
        </div>
        <div v-else class="panel-empty">暂无组织数据</div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>最近用户</h2>
            <p>最新加入平台的用户</p>
          </div>
        </div>

        <div v-if="recentUsers.length" class="compact-list">
          <div v-for="user in recentUsers" :key="user.id" class="compact-row">
            <span class="entity-avatar violet">{{ user.displayName.slice(0, 1) }}</span>
            <div>
              <strong>{{ user.displayName }}</strong>
              <small>{{ user.organizationName }} · {{ user.username }}</small>
            </div>
            <span class="mini-status">启用</span>
          </div>
        </div>
        <div v-else class="panel-empty">暂无用户数据</div>
      </section>
    </div>
  </section>
</template>
