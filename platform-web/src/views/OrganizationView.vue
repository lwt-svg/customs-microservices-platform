<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  createOrganization,
  getOrganizations,
  type Organization,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const organizations = ref<Organization[]>([])
const loading = ref(true)
const submitting = ref(false)
const loadError = ref('')
const formError = ref('')
const successMessage = ref('')
const keyword = ref('')
const showCreateForm = ref(false)

const form = reactive({
  organizationCode: '',
  organizationName: '',
})

const filteredOrganizations = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLowerCase()

  if (!normalizedKeyword) {
    return organizations.value
  }

  return organizations.value.filter((organization) => {
    return (
      organization.organizationCode.toLowerCase().includes(normalizedKeyword) ||
      organization.organizationName.toLowerCase().includes(normalizedKeyword)
    )
  })
})

/** 从 Java BFF 重新读取组织列表。 */
async function loadOrganizations(): Promise<void> {
  loading.value = true
  loadError.value = ''

  try {
    organizations.value = await getOrganizations()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取组织列表失败'
  } finally {
    loading.value = false
  }
}

/** 在浏览器端完成最基础的必填和长度校验。 */
function validateForm(): string {
  if (!form.organizationCode.trim()) {
    return '请输入组织编码。'
  }

  if (form.organizationCode.trim().length > 64) {
    return '组织编码不能超过 64 个字符。'
  }

  if (!form.organizationName.trim()) {
    return '请输入组织名称。'
  }

  if (form.organizationName.trim().length > 128) {
    return '组织名称不能超过 128 个字符。'
  }

  return ''
}

/** 提交创建请求，成功后把新组织立即加入列表。 */
async function submitOrganization(): Promise<void> {
  formError.value = validateForm()
  successMessage.value = ''

  if (formError.value) {
    return
  }

  submitting.value = true

  try {
    const createdOrganization = await createOrganization({
      organizationCode: form.organizationCode.trim(),
      organizationName: form.organizationName.trim(),
    })

    organizations.value = [...organizations.value, createdOrganization].sort((left, right) => left.id - right.id)
    successMessage.value = `组织“${createdOrganization.organizationName}”创建成功。`
    form.organizationCode = ''
    form.organizationName = ''
    showCreateForm.value = false
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建组织失败'
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

onMounted(loadOrganizations)
</script>

<template>
  <section class="organization-page">
    <div class="content-actions">
      <p>组织用于划分用户归属和平台权限范围。</p>
      <button class="primary-button" type="button" @click="openCreateForm">
        <UiIcon name="plus" />
        新增组织
      </button>
    </div>

    <div v-if="successMessage" class="feedback-banner success" role="status">
      <span>✓</span>
      <p>{{ successMessage }}</p>
    </div>

    <div v-if="loadError" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ loadError }}，请确认 Java BFF 已启动。</p>
      <button type="button" @click="loadOrganizations">重新加载</button>
    </div>

    <div class="panel organization-panel">
      <div class="organization-toolbar">
        <div>
          <h3>组织列表</h3>
          <p>共 {{ organizations.length }} 个组织</p>
        </div>
        <label class="search-box">
          <UiIcon name="search" />
          <input v-model="keyword" type="search" placeholder="搜索组织编码或名称" />
        </label>
      </div>

      <div v-if="loading" class="empty-state">
        <span class="loading-ring"></span>
        <strong>正在读取组织</strong>
        <p>数据来自 Java BFF。</p>
      </div>

      <div v-else-if="filteredOrganizations.length === 0" class="empty-state">
        <span class="empty-symbol">空</span>
        <strong>{{ keyword ? '没有匹配的组织' : '还没有组织' }}</strong>
        <p>{{ keyword ? '请尝试其他关键词。' : '点击“新增组织”创建第一条记录。' }}</p>
      </div>

      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>组织</th>
              <th>组织编码</th>
              <th>状态</th>
              <th>创建日期</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="organization in filteredOrganizations" :key="organization.id">
              <td>
                <div class="organization-name">
                  <span>{{ organization.organizationName.slice(0, 1) }}</span>
                  <div>
                    <strong>{{ organization.organizationName }}</strong>
                    <small>ID {{ organization.id }}</small>
                  </div>
                </div>
              </td>
              <td><code>{{ organization.organizationCode }}</code></td>
              <td>
                <span class="status-pill active">
                  <i></i>
                  {{ organization.status === 'ACTIVE' ? '启用' : organization.status }}
                </span>
              </td>
              <td>{{ formatDate(organization.createdAt) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showCreateForm" class="drawer-backdrop" @click.self="showCreateForm = false">
      <aside class="form-drawer" aria-labelledby="create-organization-title">
        <div class="drawer-header">
          <div>
            <h3 id="create-organization-title">创建组织</h3>
            <p>填写组织的基础信息，创建后默认为启用状态。</p>
          </div>
          <button class="close-button" type="button" aria-label="关闭" @click="showCreateForm = false">
            <UiIcon name="close" />
          </button>
        </div>

        <form @submit.prevent="submitOrganization">
          <label class="form-field">
            <span>组织编码 <em>必填</em></span>
            <input
              v-model="form.organizationCode"
              maxlength="64"
              autocomplete="off"
              placeholder="例如 SAND_DUNE_EAST"
            />
            <small>稳定的业务标识，创建后应尽量避免修改。</small>
          </label>

          <label class="form-field">
            <span>组织名称 <em>必填</em></span>
            <input
              v-model="form.organizationName"
              maxlength="128"
              autocomplete="off"
              placeholder="例如 沙丘华东运营中心"
            />
            <small>显示在后台列表和用户归属信息中。</small>
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
  </section>
</template>
