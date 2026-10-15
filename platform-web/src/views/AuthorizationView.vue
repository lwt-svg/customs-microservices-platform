<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  assignPermissionToRole,
  createPlatformPermission,
  createPlatformRole,
  getPlatformPermissions,
  getPlatformRoles,
  getRolePermissions,
  type PlatformPermission,
  type PlatformRole,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const roles = ref<PlatformRole[]>([])
const permissions = ref<PlatformPermission[]>([])
const selectedRoleId = ref<number | null>(null)
const selectedRolePermissions = ref<PlatformPermission[]>([])
const loading = ref(true)
const loadingDetails = ref(false)
const loadError = ref('')
const formError = ref('')
const successMessage = ref('')
const submitting = ref(false)
const assigningPermissionId = ref<number | null>(null)
const activeTab = ref<'roles' | 'permissions'>('roles')
const showRoleForm = ref(false)
const showPermissionForm = ref(false)

const roleForm = reactive({ roleCode: '', roleName: '' })
const permissionForm = reactive({ permissionCode: '', permissionName: '' })

const selectedRole = computed(() =>
  roles.value.find((role) => role.id === selectedRoleId.value) ?? null,
)

const availablePermissions = computed(() => {
  const assignedIds = new Set(selectedRolePermissions.value.map((permission) => permission.id))
  return permissions.value.filter((permission) => !assignedIds.has(permission.id))
})

async function loadPageData(): Promise<void> {
  loading.value = true
  loadError.value = ''

  try {
    const [roleData, permissionData] = await Promise.all([
      getPlatformRoles(),
      getPlatformPermissions(),
    ])
    roles.value = roleData
    permissions.value = permissionData
    if (selectedRoleId.value && !roleData.some((role) => role.id === selectedRoleId.value)) {
      selectedRoleId.value = null
      selectedRolePermissions.value = []
    }
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取角色权限数据失败'
  } finally {
    loading.value = false
  }
}

async function selectRole(role: PlatformRole): Promise<void> {
  selectedRoleId.value = role.id
  loadingDetails.value = true
  try {
    selectedRolePermissions.value = await getRolePermissions(role.id)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '读取角色权限失败'
  } finally {
    loadingDetails.value = false
  }
}

function resetForms(): void {
  roleForm.roleCode = ''
  roleForm.roleName = ''
  permissionForm.permissionCode = ''
  permissionForm.permissionName = ''
  formError.value = ''
}

function openRoleForm(): void {
  resetForms()
  showPermissionForm.value = false
  showRoleForm.value = true
}

function openPermissionForm(): void {
  resetForms()
  showRoleForm.value = false
  showPermissionForm.value = true
}

function validateRole(): string {
  if (!roleForm.roleCode.trim()) return '请输入角色编码。'
  if (roleForm.roleCode.trim().length > 64) return '角色编码不能超过 64 个字符。'
  if (!roleForm.roleName.trim()) return '请输入角色名称。'
  if (roleForm.roleName.trim().length > 128) return '角色名称不能超过 128 个字符。'
  return ''
}

function validatePermission(): string {
  if (!permissionForm.permissionCode.trim()) return '请输入权限编码。'
  if (permissionForm.permissionCode.trim().length > 128) return '权限编码不能超过 128 个字符。'
  if (!permissionForm.permissionName.trim()) return '请输入权限名称。'
  if (permissionForm.permissionName.trim().length > 128) return '权限名称不能超过 128 个字符。'
  return ''
}

async function submitRole(): Promise<void> {
  formError.value = validateRole()
  successMessage.value = ''
  if (formError.value) return
  submitting.value = true
  try {
    const role = await createPlatformRole({
      roleCode: roleForm.roleCode.trim(),
      roleName: roleForm.roleName.trim(),
    })
    roles.value = [...roles.value, role].sort((left, right) => left.id - right.id)
    successMessage.value = `角色“${role.roleName}”创建成功。`
    showRoleForm.value = false
    resetForms()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建角色失败'
  } finally {
    submitting.value = false
  }
}

async function submitPermission(): Promise<void> {
  formError.value = validatePermission()
  successMessage.value = ''
  if (formError.value) return
  submitting.value = true
  try {
    const permission = await createPlatformPermission({
      permissionCode: permissionForm.permissionCode.trim(),
      permissionName: permissionForm.permissionName.trim(),
    })
    permissions.value = [...permissions.value, permission].sort((left, right) => left.id - right.id)
    successMessage.value = `权限“${permission.permissionName}”创建成功。`
    showPermissionForm.value = false
    resetForms()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建权限失败'
  } finally {
    submitting.value = false
  }
}

async function assignPermission(permission: PlatformPermission): Promise<void> {
  if (!selectedRole.value || assigningPermissionId.value !== null) return
  assigningPermissionId.value = permission.id
  formError.value = ''
  try {
    selectedRolePermissions.value = await assignPermissionToRole(
      selectedRole.value.id,
      permission.id,
    )
    successMessage.value = `已将“${permission.permissionName}”分配给角色。`
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '分配权限失败'
  } finally {
    assigningPermissionId.value = null
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}

onMounted(loadPageData)
</script>

<template>
  <section class="authorization-page">
    <div class="content-actions">
      <p>角色是一组权限的集合；本阶段支持创建角色、创建权限和给角色分配权限。</p>
      <div class="action-group">
        <button class="secondary-button" type="button" @click="openPermissionForm">
          <UiIcon name="plus" />
          新增权限
        </button>
        <button class="primary-button" type="button" @click="openRoleForm">
          <UiIcon name="plus" />
          新增角色
        </button>
      </div>
    </div>

    <div v-if="successMessage" class="feedback-banner success" role="status">
      <span>✓</span><p>{{ successMessage }}</p>
    </div>
    <div v-if="formError" class="feedback-banner error" role="alert">
      <span>!</span><p>{{ formError }}</p>
    </div>
    <div v-if="loadError" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ loadError }}，请确认 Java BFF 已启动。</p>
      <button type="button" @click="loadPageData">重新加载</button>
    </div>

    <div class="authorization-grid">
      <div class="panel authorization-list-panel">
        <div class="management-toolbar">
          <div>
            <h3>访问控制资源</h3>
            <p>选择角色查看并配置权限</p>
          </div>
          <div class="segmented-control" role="tablist" aria-label="资源类型">
            <button
              type="button"
              :class="{ active: activeTab === 'roles' }"
              @click="activeTab = 'roles'"
            >角色 {{ roles.length }}</button>
            <button
              type="button"
              :class="{ active: activeTab === 'permissions' }"
              @click="activeTab = 'permissions'"
            >权限 {{ permissions.length }}</button>
          </div>
        </div>

        <div v-if="loading" class="empty-state">
          <span class="loading-ring"></span><strong>正在读取角色权限</strong>
          <p>从 Java BFF 加载访问控制数据。</p>
        </div>
        <div v-else-if="activeTab === 'roles' && roles.length === 0" class="empty-state">
          <span class="empty-symbol">空</span><strong>还没有角色</strong>
          <p>点击“新增角色”创建第一条角色记录。</p>
        </div>
        <div v-else-if="activeTab === 'permissions' && permissions.length === 0" class="empty-state">
          <span class="empty-symbol">空</span><strong>还没有权限</strong>
          <p>点击“新增权限”创建第一条权限记录。</p>
        </div>
        <div v-else class="resource-list">
          <button
            v-for="role in roles"
            v-if="activeTab === 'roles'"
            :key="role.id"
            type="button"
            class="resource-row"
            :class="{ selected: selectedRoleId === role.id }"
            @click="selectRole(role)"
          >
            <span class="resource-icon role-icon"><UiIcon name="shield" /></span>
            <span class="resource-copy">
              <strong>{{ role.roleName }}</strong>
              <small>{{ role.roleCode }} · {{ formatDate(role.createdAt) }}</small>
            </span>
            <span class="status-pill" :class="{ active: role.status === 'ACTIVE' }"><i></i>{{ role.status === 'ACTIVE' ? '启用' : '停用' }}</span>
          </button>
          <div v-for="permission in permissions" v-if="activeTab === 'permissions'" :key="permission.id" class="resource-row">
            <span class="resource-icon permission-icon"><UiIcon name="key" /></span>
            <span class="resource-copy">
              <strong>{{ permission.permissionName }}</strong>
              <small>{{ permission.permissionCode }} · {{ formatDate(permission.createdAt) }}</small>
            </span>
            <span class="status-pill" :class="{ active: permission.status === 'ACTIVE' }"><i></i>{{ permission.status === 'ACTIVE' ? '启用' : '停用' }}</span>
          </div>
        </div>
      </div>

      <div class="panel permission-detail-panel">
        <div class="panel-header">
          <div>
            <h2>{{ selectedRole ? selectedRole.roleName : '角色权限配置' }}</h2>
            <p>{{ selectedRole ? `${selectedRole.roleCode} · 已分配 ${selectedRolePermissions.length} 项权限` : '从左侧选择一个角色开始配置' }}</p>
          </div>
        </div>
        <div v-if="!selectedRole" class="empty-state detail-empty">
          <span class="empty-symbol">→</span><strong>请选择一个角色</strong>
          <p>角色的权限关系会显示在这里。</p>
        </div>
        <template v-else>
          <div class="detail-section">
            <div class="detail-section-title"><strong>已分配权限</strong><small>{{ selectedRolePermissions.length }} 项</small></div>
            <div v-if="loadingDetails" class="detail-loading">正在读取权限…</div>
            <div v-else-if="selectedRolePermissions.length === 0" class="detail-empty-line">这个角色还没有权限。</div>
            <div v-else class="assigned-permissions">
              <div v-for="permission in selectedRolePermissions" :key="permission.id" class="assigned-permission">
                <div><strong>{{ permission.permissionName }}</strong><small>{{ permission.permissionCode }}</small></div>
                <span class="check-mark">✓</span>
              </div>
            </div>
          </div>
          <div class="detail-section">
            <div class="detail-section-title"><strong>可分配权限</strong><small>{{ availablePermissions.length }} 项</small></div>
            <div v-if="availablePermissions.length === 0" class="detail-empty-line">没有可继续分配的权限。</div>
            <div v-else class="assign-list">
              <div v-for="permission in availablePermissions" :key="permission.id" class="assign-row">
                <div><strong>{{ permission.permissionName }}</strong><small>{{ permission.permissionCode }}</small></div>
                <button class="ghost-button" type="button" :disabled="assigningPermissionId !== null" @click="assignPermission(permission)">
                  {{ assigningPermissionId === permission.id ? '分配中…' : '分配' }}
                </button>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <div v-if="showRoleForm || showPermissionForm" class="drawer-backdrop" @click.self="showRoleForm = false; showPermissionForm = false">
      <aside class="form-drawer" :aria-labelledby="showRoleForm ? 'create-role-title' : 'create-permission-title'">
        <div class="drawer-header">
          <div>
            <h3 :id="showRoleForm ? 'create-role-title' : 'create-permission-title'">{{ showRoleForm ? '创建角色' : '创建权限' }}</h3>
            <p>{{ showRoleForm ? '角色是权限的集合，后续可以在右侧分配权限。' : '权限代表一个可被角色引用的最小操作能力。' }}</p>
          </div>
          <button class="close-button" type="button" aria-label="关闭" @click="showRoleForm = false; showPermissionForm = false"><UiIcon name="close" /></button>
        </div>
        <form @submit.prevent="showRoleForm ? submitRole() : submitPermission()">
          <template v-if="showRoleForm">
            <label class="form-field"><span>角色编码 <em>必填</em></span><input v-model="roleForm.roleCode" maxlength="64" autocomplete="off" placeholder="例如 SYSTEM_ADMIN" /><small>程序用于识别角色的稳定编码。</small></label>
            <label class="form-field"><span>角色名称 <em>必填</em></span><input v-model="roleForm.roleName" maxlength="128" autocomplete="off" placeholder="例如 系统管理员" /><small>显示在角色列表中的名称。</small></label>
          </template>
          <template v-else>
            <label class="form-field"><span>权限编码 <em>必填</em></span><input v-model="permissionForm.permissionCode" maxlength="128" autocomplete="off" placeholder="例如 platform:user:read" /><small>程序用于判断能力的稳定编码。</small></label>
            <label class="form-field"><span>权限名称 <em>必填</em></span><input v-model="permissionForm.permissionName" maxlength="128" autocomplete="off" placeholder="例如 查看平台用户" /><small>显示在权限列表中的名称。</small></label>
          </template>
          <div class="drawer-actions">
            <button class="secondary-button" type="button" @click="showRoleForm = false; showPermissionForm = false">取消</button>
            <button class="primary-button" type="submit" :disabled="submitting">{{ submitting ? '保存中…' : '确认创建' }}</button>
          </div>
        </form>
      </aside>
    </div>
  </section>
</template>
