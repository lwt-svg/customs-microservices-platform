<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  assignRoleToUser,
  createPlatformUser,
  getOrganizations,
  getPlatformRoles,
  getPlatformUsers,
  getUserRoles,
  type Organization,
  type PlatformRole,
  type PlatformUser,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const users = ref<PlatformUser[]>([])
const organizations = ref<Organization[]>([])
const loading = ref(true)
const submitting = ref(false)
const loadError = ref('')
const formError = ref('')
const successMessage = ref('')
const keyword = ref('')
const organizationFilter = ref('')
const showCreateForm = ref(false)
const showRoleForm = ref(false)
const selectedUser = ref<PlatformUser | null>(null)
const roles = ref<PlatformRole[]>([])
const selectedUserRoles = ref<PlatformRole[]>([])
const loadingRoles = ref(false)
const assigningRoleId = ref<number | null>(null)

const form = reactive({
  username: '',
  displayName: '',
  organizationId: '',
})

const activeOrganizations = computed(() =>
  organizations.value.filter((organization) => organization.status === 'ACTIVE'),
)

const filteredUsers = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLowerCase()

  return users.value.filter((user) => {
    const matchesKeyword =
      !normalizedKeyword ||
      user.username.toLowerCase().includes(normalizedKeyword) ||
      user.displayName.toLowerCase().includes(normalizedKeyword) ||
      user.organizationName.toLowerCase().includes(normalizedKeyword)
    const matchesOrganization =
      !organizationFilter.value ||
      user.organizationId === Number(organizationFilter.value)

    return matchesKeyword && matchesOrganization
  })
})

const availableRoles = computed(() => {
  const assignedIds = new Set(selectedUserRoles.value.map((role) => role.id))
  return roles.value.filter((role) => !assignedIds.has(role.id))
})

/** 并行读取用户列表和组织选项，避免表单出现不可用的手填组织 ID。 */
async function loadPageData(): Promise<void> {
  loading.value = true
  loadError.value = ''

  try {
    const [userData, organizationData, roleData] = await Promise.all([
      getPlatformUsers(),
      getOrganizations(),
      getPlatformRoles(),
    ])
    users.value = userData
    organizations.value = organizationData
    roles.value = roleData
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取用户管理数据失败'
  } finally {
    loading.value = false
  }
}

async function openRoleFormForUser(user: PlatformUser): Promise<void> {
  selectedUser.value = user
  showRoleForm.value = true
  formError.value = ''
  loadingRoles.value = true
  try {
    selectedUserRoles.value = await getUserRoles(user.id)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '读取用户角色失败'
  } finally {
    loadingRoles.value = false
  }
}

async function assignRole(role: PlatformRole): Promise<void> {
  if (!selectedUser.value || assigningRoleId.value !== null) return
  assigningRoleId.value = role.id
  formError.value = ''
  try {
    selectedUserRoles.value = await assignRoleToUser(selectedUser.value.id, role.id)
    successMessage.value = `已将“${role.roleName}”分配给用户“${selectedUser.value.displayName}”。`
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '分配角色失败'
  } finally {
    assigningRoleId.value = null
  }
}

function validateForm(): string {
  if (!form.username.trim()) {
    return '请输入用户名。'
  }

  if (form.username.trim().length > 64) {
    return '用户名不能超过 64 个字符。'
  }

  if (!form.displayName.trim()) {
    return '请输入用户显示名称。'
  }

  if (form.displayName.trim().length > 128) {
    return '用户显示名称不能超过 128 个字符。'
  }

  if (!form.organizationId || Number(form.organizationId) <= 0) {
    return '请选择用户所属组织。'
  }

  return ''
}

/** 创建用户成功后，将 Java BFF 返回的组织摘要一并显示到列表。 */
async function submitUser(): Promise<void> {
  formError.value = validateForm()
  successMessage.value = ''

  if (formError.value) {
    return
  }

  submitting.value = true

  try {
    const createdUser = await createPlatformUser({
      username: form.username.trim(),
      displayName: form.displayName.trim(),
      organizationId: Number(form.organizationId),
    })

    users.value = [...users.value, createdUser].sort((left, right) => left.id - right.id)
    successMessage.value = `用户“${createdUser.displayName}”创建成功。`
    form.username = ''
    form.displayName = ''
    form.organizationId = ''
    showCreateForm.value = false
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建用户失败'
  } finally {
    submitting.value = false
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}

function openCreateForm(): void {
  formError.value = ''
  successMessage.value = ''
  showCreateForm.value = true
}

onMounted(loadPageData)
</script>

<template>
  <section class="user-page">
    <div class="content-actions">
      <p>用户必须归属于一个组织；当前阶段不包含密码和登录认证。</p>
      <button
        class="primary-button"
        type="button"
        :disabled="activeOrganizations.length === 0"
        @click="openCreateForm"
      >
        <UiIcon name="plus" />
        新增用户
      </button>
    </div>

    <div v-if="successMessage" class="feedback-banner success" role="status">
      <span>✓</span>
      <p>{{ successMessage }}</p>
    </div>

    <div v-if="loadError" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ loadError }}，请确认 Java BFF 已启动。</p>
      <button type="button" @click="loadPageData">重新加载</button>
    </div>

    <div v-if="!loading && activeOrganizations.length === 0" class="feedback-banner warning">
      <span>i</span>
      <p>当前没有可用组织，请先在组织管理中创建或启用组织，再新增用户。</p>
    </div>

    <div class="panel user-panel">
      <div class="management-toolbar">
        <div>
          <h3>用户列表</h3>
          <p>共 {{ users.length }} 个用户</p>
        </div>
        <div class="toolbar-filters">
          <label class="select-box">
            <select v-model="organizationFilter" aria-label="按组织筛选">
              <option value="">全部组织</option>
              <option
                v-for="organization in organizations"
                :key="organization.id"
                :value="String(organization.id)"
              >
                {{ organization.organizationName }}
              </option>
            </select>
          </label>
          <label class="search-box">
            <UiIcon name="search" />
            <input v-model="keyword" type="search" placeholder="搜索用户名、姓名或组织" />
          </label>
        </div>
      </div>

      <div v-if="loading" class="empty-state">
        <span class="loading-ring"></span>
        <strong>正在读取用户</strong>
        <p>同时加载用户及组织数据。</p>
      </div>

      <div v-else-if="filteredUsers.length === 0" class="empty-state">
        <span class="empty-symbol">空</span>
        <strong>{{ keyword || organizationFilter ? '没有匹配的用户' : '还没有用户' }}</strong>
        <p>{{ keyword || organizationFilter ? '请调整搜索或筛选条件。' : '点击“新增用户”创建第一条记录。' }}</p>
      </div>

      <div v-else class="table-wrap">
        <table class="data-table user-table">
          <thead>
            <tr>
              <th>用户</th>
              <th>用户名</th>
              <th>所属组织</th>
              <th>角色</th>
              <th>状态</th>
              <th>创建日期</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in filteredUsers" :key="user.id">
              <td>
                <div class="organization-name user-name">
                  <span>{{ user.displayName.slice(0, 1) }}</span>
                  <div>
                    <strong>{{ user.displayName }}</strong>
                    <small>ID {{ user.id }}</small>
                  </div>
                </div>
              </td>
              <td><code>{{ user.username }}</code></td>
              <td>
                <div class="organization-summary">
                  <strong>{{ user.organizationName }}</strong>
                  <small>{{ user.organizationCode }}</small>
                </div>
              </td>
              <td>
                <button class="table-action-button" type="button" @click="openRoleFormForUser(user)">
                  配置角色
                </button>
              </td>
              <td>
                <span class="status-pill" :class="{ active: user.status === 'ACTIVE' }">
                  <i></i>
                  {{ user.status === 'ACTIVE' ? '启用' : '停用' }}
                </span>
              </td>
              <td>{{ formatDate(user.createdAt) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showCreateForm" class="drawer-backdrop" @click.self="showCreateForm = false">
      <aside class="form-drawer" aria-labelledby="create-user-title">
        <div class="drawer-header">
          <div>
            <h3 id="create-user-title">创建用户</h3>
            <p>填写平台用户信息并选择所属组织。</p>
          </div>
          <button class="close-button" type="button" aria-label="关闭" @click="showCreateForm = false">
            <UiIcon name="close" />
          </button>
        </div>

        <form @submit.prevent="submitUser">
          <label class="form-field">
            <span>用户名 <em>必填</em></span>
            <input
              v-model="form.username"
              maxlength="64"
              autocomplete="off"
              placeholder="例如 zhangsan"
            />
            <small>稳定的账号标识，建议使用英文或数字组合。</small>
          </label>

          <label class="form-field">
            <span>显示名称 <em>必填</em></span>
            <input
              v-model="form.displayName"
              maxlength="128"
              autocomplete="off"
              placeholder="例如 张三"
            />
            <small>显示在后台列表中的用户名称。</small>
          </label>

          <label class="form-field">
            <span>所属组织 <em>必填</em></span>
            <select v-model="form.organizationId">
              <option value="" disabled>请选择组织</option>
              <option
                v-for="organization in organizations"
                :key="organization.id"
                :value="String(organization.id)"
                :disabled="organization.status !== 'ACTIVE'"
              >
                {{ organization.organizationName }}（{{ organization.organizationCode }}）
                {{ organization.status === 'ACTIVE' ? '' : '— 已停用' }}
              </option>
            </select>
            <small>用户创建后会归属到所选组织。</small>
          </label>

          <div v-if="formError" class="inline-error" role="alert">{{ formError }}</div>

          <div class="drawer-actions">
            <button class="secondary-button" type="button" @click="showCreateForm = false">取消</button>
            <button class="primary-button" type="submit" :disabled="submitting">
              {{ submitting ? '创建中…' : '确认创建' }}
            </button>
          </div>
        </form>
      </aside>
    </div>

    <div v-if="showRoleForm && selectedUser" class="drawer-backdrop" @click.self="showRoleForm = false">
      <aside class="form-drawer role-drawer" aria-labelledby="user-role-title">
        <div class="drawer-header">
          <div>
            <h3 id="user-role-title">配置用户角色</h3>
            <p>{{ selectedUser.displayName }}（{{ selectedUser.username }}）</p>
          </div>
          <button class="close-button" type="button" aria-label="关闭" @click="showRoleForm = false">
            <UiIcon name="close" />
          </button>
        </div>

        <div class="role-drawer-section">
          <div class="detail-section-title">
            <strong>已分配角色</strong>
            <small>{{ selectedUserRoles.length }} 项</small>
          </div>
          <div v-if="loadingRoles" class="detail-loading">正在读取角色…</div>
          <div v-else-if="selectedUserRoles.length === 0" class="detail-empty-line">
            这个用户还没有角色。
          </div>
          <div v-else class="assigned-permissions">
            <div v-for="role in selectedUserRoles" :key="role.id" class="assigned-permission">
              <div>
                <strong>{{ role.roleName }}</strong>
                <small>{{ role.roleCode }}</small>
              </div>
              <span class="check-mark">✓</span>
            </div>
          </div>
        </div>

        <div class="role-drawer-section">
          <div class="detail-section-title">
            <strong>可分配角色</strong>
            <small>{{ availableRoles.length }} 项</small>
          </div>
          <div v-if="availableRoles.length === 0" class="detail-empty-line">
            没有可继续分配的角色。
          </div>
          <div v-else class="assign-list">
            <div v-for="role in availableRoles" :key="role.id" class="assign-row">
              <div>
                <strong>{{ role.roleName }}</strong>
                <small>{{ role.roleCode }}</small>
              </div>
              <button
                class="ghost-button"
                type="button"
                :disabled="assigningRoleId !== null"
                @click="assignRole(role)"
              >
                {{ assigningRoleId === role.id ? '分配中…' : '分配' }}
              </button>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </section>
</template>
