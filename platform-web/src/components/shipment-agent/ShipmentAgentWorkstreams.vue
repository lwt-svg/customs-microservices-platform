<script setup lang="ts">
import { computed } from 'vue'

import type { ShipmentAgentCapability, ShipmentAgentRun } from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{
  run: ShipmentAgentRun
}>()

interface WorkstreamView {
  capability: ShipmentAgentCapability | 'DATA_READINESS'
  eyebrow: string
  title: string
  icon: 'box' | 'database' | 'file'
  required: boolean
  reference: string | null
  state: 'ready' | 'attention' | 'waiting' | 'inactive'
  statusLabel: string
  description: string
  metricLabel: string
  metricValue: string
  route: string | null
  actionLabel: string
}

const loadStages = new Set([
  'STARTING_LOAD_AGENT',
  'AWAITING_LOAD_CONSTRAINTS',
  'AWAITING_LOAD_REVIEW',
  'AWAITING_LOAD_RELEASE',
  'AWAITING_LOAD_REPLAN',
])

const bookingStages = new Set([
  'AWAITING_BOOKING_SUBMIT_APPROVAL',
  'SUBMITTING_BOOKING',
  'AWAITING_BOOKING_RESULT',
  'AWAITING_BOOKING_RETRY_APPROVAL',
  'AWAITING_BOOKING_REPLACEMENT',
])

const workstreams = computed<WorkstreamView[]>(() => [
  buildDataReadinessWorkstream(),
  buildLoadWorkstream(),
  buildBookingWorkstream(),
])

function buildDataReadinessWorkstream(): WorkstreamView {
  const readiness = props.run.dataReadiness
  const issueCount =
    readiness?.blockingIssueCount ??
    readiness?.issueCounts?.blocking ??
    readiness?.issues.length ??
    0

  let state: WorkstreamView['state'] = 'waiting'
  let statusLabel = '等待检查'
  let description = '系统会先检查库存、货物和托盘主数据，再进入装载与订舱。'

  if (readiness?.status === 'READY') {
    state = 'ready'
    statusLabel = '业务数据已就绪'
    description = '当前策略要求的数据已经通过检查，专业支线可以继续推进。'
  } else if (readiness?.status === 'DEMO_READY') {
    state = 'waiting'
    statusLabel = '演示数据已就绪'
    description = '当前使用明确标记的脱敏演示数据，不代表已经连接企业 ERP 或 WMS。'
  } else if (readiness?.status === 'ACTION_REQUIRED') {
    state = 'attention'
    statusLabel = `${issueCount} 项待补充`
    description = 'Agent 已暂停，等待仓库或业务人员补充库存、核对主数据。'
  } else if (readiness?.status === 'LEGACY_BYPASSED') {
    state = 'inactive'
    statusLabel = '历史运行兼容'
    description = '这次旧运行没有经过 V2 数据门禁；不能据此宣称库存或主数据已经确认。'
  }

  return {
    capability: 'DATA_READINESS',
    eyebrow: 'BUSINESS DATA GATE',
    title: '数据准备与库存核对',
    icon: 'file',
    required: true,
    reference: readiness?.snapshotHash ? readiness.snapshotHash.slice(0, 12) : null,
    state,
    statusLabel,
    description,
    metricLabel: '阻塞问题',
    metricValue: `${issueCount}`,
    route: null,
    actionLabel:
      readiness?.status === 'ACTION_REQUIRED'
        ? '请在上方补数任务中处理'
        : '由总 Agent 自动检查',
  }
}

function buildLoadWorkstream(): WorkstreamView {
  const required = props.run.requiredCapabilities.includes('LOAD_PLANNING')
  const reference = props.run.selectedLoadTaskNumber
  const locked = props.run.lockedLoadVersionNumber !== null
  const inStage = loadStages.has(props.run.currentStage)

  let state: WorkstreamView['state'] = required ? 'waiting' : 'inactive'
  let statusLabel = required ? '等待编排' : '本票不启用'
  let description = required
    ? '等待总 Agent 读取装载任务和专业运行状态。'
    : '当前能力策略没有要求装载规划。'

  if (locked) {
    state = 'ready'
    statusLabel = `正式 V${props.run.lockedLoadVersionNumber}`
    description = '装载方案已经人工批准并锁定为不可变正式版本。'
  } else if (inStage) {
    state =
      props.run.currentStage === 'STARTING_LOAD_AGENT' ? 'waiting' : 'attention'
    const copy: Partial<Record<ShipmentAgentRun['currentStage'], [string, string]>> = {
      STARTING_LOAD_AGENT: ['正在启动', '总 Agent 正在幂等启动已绑定任务的装载 Agent。'],
      AWAITING_LOAD_CONSTRAINTS: [
        '等待补约束',
        '装载子 Agent 已暂停，需在专业工作台补充箱规或安全约束。',
      ],
      AWAITING_LOAD_REVIEW: [
        '等待方案审批',
        '候选方案已生成，需在 3D 工作台完成业务复核。',
      ],
      AWAITING_LOAD_RELEASE: [
        '等待正式发布',
        '方案已批准，仍需人工锁定为现场执行版本。',
      ],
      AWAITING_LOAD_REPLAN: [
        '等待重新规划',
        '上一轮被驳回或失败，请在装载工作台处理后重新检查。',
      ],
    }
    ;[statusLabel, description] = copy[props.run.currentStage] ?? [
      '需要处理',
      '装载支线正在等待专业动作。',
    ]
  } else if (required && !reference) {
    state = 'attention'
    statusLabel = '缺少绑定'
    description = '案件尚未选定装载任务，总 Agent 不会自动创建业务资源。'
  } else if (required && reference) {
    state = 'waiting'
    statusLabel = '已选定任务'
    description = '总 Agent 将按案件顺序同步该任务的实时状态。'
  }

  return {
    capability: 'LOAD_PLANNING',
    eyebrow: 'WAREHOUSE & LOAD',
    title: '智能装载 Agent',
    icon: 'box',
    required,
    reference,
    state,
    statusLabel,
    description,
    metricLabel: '正式版本',
    metricValue:
      props.run.lockedLoadVersionNumber !== null
        ? `V${props.run.lockedLoadVersionNumber}`
        : '—',
    route: reference
      ? `/supply-chain/load-planning/tasks?taskNumber=${encodeURIComponent(reference)}&view=planning`
      : '/supply-chain/load-planning/tasks',
    actionLabel: reference ? '进入装载任务' : '打开装载任务中心',
  }
}

function buildBookingWorkstream(): WorkstreamView {
  const required = props.run.requiredCapabilities.includes('LINK_BOOKING')
  const reference = props.run.selectedBookingApplicationNumber
  const confirmed = props.run.bookingStatus === 'CONFIRMED'
  const inStage = bookingStages.has(props.run.currentStage)

  let state: WorkstreamView['state'] = required ? 'waiting' : 'inactive'
  let statusLabel = required ? '等待编排' : '本票不启用'
  let description = required
    ? '等待总 Agent 读取 LINK 申请和承运人提交状态。'
    : '当前能力策略没有要求 LINK 订舱。'

  if (confirmed) {
    state = 'ready'
    statusLabel = '承运人已确认'
    description = 'LINK 已收到异步确认，订舱参考号已回写到专业系统。'
  } else if (inStage) {
    state =
      props.run.currentStage === 'AWAITING_BOOKING_RESULT' ? 'waiting' : 'attention'
    const copy: Partial<Record<ShipmentAgentRun['currentStage'], [string, string]>> = {
      AWAITING_BOOKING_SUBMIT_APPROVAL: [
        '等待提交批准',
        '七步填单草稿已就绪，提交外部系统前必须人工确认。',
      ],
      SUBMITTING_BOOKING: ['正在提交', '总 Agent 正在执行已批准的 LINK 提交动作。'],
      AWAITING_BOOKING_RESULT: [
        '等待承运人',
        '申请已提交；流程不占用线程，等待异步回调后再重新检查。',
      ],
      AWAITING_BOOKING_RETRY_APPROVAL: [
        '等待重试批准',
        '投递已进入可人工重试状态，系统不会盲目重复提交。',
      ],
      AWAITING_BOOKING_REPLACEMENT: [
        '等待替换申请',
        '原申请被拒绝或取消，请准备新申请并绑定到案件。',
      ],
    }
    ;[statusLabel, description] = copy[props.run.currentStage] ?? [
      '需要处理',
      '订舱支线正在等待专业动作。',
    ]
  } else if (required && !reference) {
    state = 'attention'
    statusLabel = '缺少绑定'
    description = '案件尚未选定 LINK 申请，总 Agent 不会自动创建订舱草稿。'
  } else if (required && reference) {
    state = 'waiting'
    statusLabel = props.run.bookingStatus || '已选定申请'
    description = '总 Agent 将根据申请和承运人状态决定下一步。'
  }

  return {
    capability: 'LINK_BOOKING',
    eyebrow: 'CARRIER BOOKING',
    title: 'LINK 七步订舱技能',
    icon: 'database',
    required,
    reference,
    state,
    statusLabel,
    description,
    metricLabel: '承运人状态',
    metricValue: props.run.carrierSubmissionStatus || props.run.bookingStatus || '—',
    route: reference
      ? `/link-booking/applications?applicationNumber=${encodeURIComponent(reference)}&view=detail`
      : '/link-booking/applications',
    actionLabel: reference ? '进入 LINK 申请' : '打开 LINK 工作台',
  }
}
</script>

<template>
  <section class="agent-workstreams">
    <div class="workstreams-heading">
      <div>
        <span>业务执行支线</span>
        <h4>数据、装载与订舱进度</h4>
        <p>先保证输入可信，再协调专业作业；遇到问题会生成明确的人工任务。</p>
      </div>
      <span class="workstream-legend"><i></i> 最近一次权威状态</span>
    </div>

    <div class="workstream-cards">
      <article
        v-for="workstream in workstreams"
        :key="workstream.capability"
        class="agent-workstream-card"
        :data-state="workstream.state"
      >
        <div class="workstream-topline">
          <span class="workstream-mark"><UiIcon :name="workstream.icon" /></span>
          <div>
            <span>{{ workstream.eyebrow }}</span>
            <h5>{{ workstream.title }}</h5>
          </div>
          <span class="workstream-state">{{ workstream.statusLabel }}</span>
        </div>

        <p>{{ workstream.description }}</p>

        <dl>
          <div>
            <dt>{{ workstream.capability === 'DATA_READINESS' ? '数据快照' : '业务引用' }}</dt>
            <dd>{{ workstream.reference || '尚未选定' }}</dd>
          </div>
          <div>
            <dt>{{ workstream.metricLabel }}</dt>
            <dd>{{ workstream.metricValue }}</dd>
          </div>
        </dl>

        <div
          v-if="workstream.capability === 'LOAD_PLANNING' && run.childAgent"
          class="child-agent-strip"
        >
          <span>
            <small>子 Agent 运行</small>
            <code>{{ run.childAgent.runId }}</code>
          </span>
          <span>
            <small>状态 / 版本</small>
            <strong>{{ run.childAgent.status }} · R{{ run.childAgent.runVersion }}</strong>
          </span>
          <span>
            <small>当前步骤</small>
            <strong>{{ run.childAgent.currentStep }}</strong>
          </span>
          <p>{{ run.childAgent.nextAction }}</p>
        </div>

        <div
          v-if="
            workstream.capability === 'LINK_BOOKING' &&
            run.bookingSubmissionUncertain
          "
          class="uncertain-command"
        >
          <UiIcon name="alert" />
          <span>
            上一次 LINK 写请求的结果不明确。系统不会盲目重复提交，请先重新读取权威状态。
          </span>
        </div>

        <RouterLink v-if="workstream.route" :to="workstream.route">
          {{ workstream.actionLabel }}
          <UiIcon name="arrow" />
        </RouterLink>
        <span v-else class="workstream-inline-action">
          {{ workstream.actionLabel }}
        </span>
      </article>
    </div>
  </section>
</template>

<style scoped>
.agent-workstreams {
  display: grid;
  gap: 13px;
}

.workstreams-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.workstreams-heading > div > span {
  color: #89948f;
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.14em;
}

.workstreams-heading h4,
.workstreams-heading p {
  margin: 0;
}

.workstreams-heading h4 {
  margin-top: 5px;
  color: #26322e;
  font-size: 17px;
}

.workstreams-heading p {
  margin-top: 5px;
  color: #7d8783;
  font-size: 12px;
}

.workstream-legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #84908b;
  font-size: 11px;
  white-space: nowrap;
}

.workstream-legend i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4e9a78;
  box-shadow: 0 0 0 3px #e8f4ee;
}

.workstream-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 11px;
}

.agent-workstream-card {
  min-width: 0;
  padding: 15px;
  border: 1px solid #e2e8e5;
  border-radius: 13px;
  background: #fcfdfd;
}

.agent-workstream-card[data-state="ready"] {
  border-color: #c8e1d5;
  background: #f5faf7;
}

.agent-workstream-card[data-state="attention"] {
  border-color: #ead6b5;
  background: #fffaf1;
}

.agent-workstream-card[data-state="inactive"] {
  opacity: 0.65;
}

.workstream-topline {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.workstream-mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 10px;
  color: #456e60;
  background: #e5f0eb;
}

.workstream-mark :deep(.ui-icon) {
  width: 17px;
}

.workstream-topline > div > span {
  color: #8c9692;
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.workstream-topline h5 {
  margin: 4px 0 0;
  color: #2d3935;
  font-size: 14px;
}

.workstream-state {
  padding: 5px 7px;
  border-radius: 999px;
  color: #5d6b66;
  background: #eef1f0;
  font-size: 11px;
  white-space: nowrap;
}

[data-state="ready"] .workstream-state {
  color: #347055;
  background: #dfeee6;
}

[data-state="attention"] .workstream-state {
  color: #8a6828;
  background: #f4e7c9;
}

.agent-workstream-card > p {
  min-height: 34px;
  margin: 12px 0;
  color: #727e79;
  font-size: 12px;
  line-height: 1.6;
}

.agent-workstream-card dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
  margin: 0;
}

.agent-workstream-card dl > div {
  min-width: 0;
  padding: 9px;
  border-radius: 9px;
  background: rgba(239, 243, 241, 0.72);
}

.agent-workstream-card dt,
.agent-workstream-card dd {
  margin: 0;
}

.agent-workstream-card dt {
  color: #919a96;
  font-size: 10px;
}

.agent-workstream-card dd {
  overflow: hidden;
  margin-top: 4px;
  color: #38443f;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.child-agent-strip {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 0.8fr) minmax(0, 0.8fr);
  gap: 7px;
  margin-top: 9px;
  padding: 9px;
  border: 1px solid #dde7e2;
  border-radius: 9px;
  background: #f7faf8;
}

.child-agent-strip span {
  min-width: 0;
}

.child-agent-strip small,
.child-agent-strip strong,
.child-agent-strip code {
  display: block;
}

.child-agent-strip small {
  color: #919a96;
  font-size: 10px;
}

.child-agent-strip strong,
.child-agent-strip code {
  overflow: hidden;
  margin-top: 4px;
  color: #3a4a44;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.child-agent-strip p {
  grid-column: 1 / -1;
  margin: 2px 0 0;
  color: #68766f;
  font-size: 11px;
  line-height: 1.5;
}

.uncertain-command {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  margin-top: 9px;
  padding: 9px;
  border: 1px solid #e8c9a8;
  border-radius: 9px;
  color: #835b2c;
  background: #fff7ea;
  font-size: 11px;
  line-height: 1.55;
}

.uncertain-command :deep(.ui-icon) {
  width: 14px;
  flex: 0 0 14px;
}

.agent-workstream-card > a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 12px;
  color: #376b5a;
  font-size: 12px;
  font-weight: 700;
  text-decoration: none;
}

.agent-workstream-card > a :deep(.ui-icon) {
  width: 12px;
}

.workstream-inline-action {
  display: inline-flex;
  margin-top: 12px;
  color: #66756f;
  font-size: 12px;
  font-weight: 650;
}

@media (max-width: 1080px) {
  .workstream-cards {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .workstreams-heading {
    display: grid;
  }

  .workstream-legend {
    justify-self: start;
  }

  .workstream-cards {
    grid-template-columns: 1fr;
  }
}
</style>
