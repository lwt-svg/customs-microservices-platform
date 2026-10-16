<script setup lang="ts">
import { computed } from 'vue'

import type {
  LinkCarrierSubmission,
  ShipmentAgentCapability,
  ShipmentAgentRun,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{
  run: ShipmentAgentRun
}>()

interface WorkstreamView {
  capability: ShipmentAgentCapability
  category: string
  title: string
  icon: 'box' | 'database'
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
  buildLoadWorkstream(),
  buildBookingWorkstream(),
])

function buildLoadWorkstream(): WorkstreamView {
  const policyConfirmed = props.run.scenarioPolicyConfirmed
  const required = props.run.requiredCapabilities.includes('LOAD_PLANNING')
  const enabled = policyConfirmed && required
  const reference = props.run.selectedLoadTaskNumber
  const locked = props.run.lockedLoadVersionNumber !== null
  const inStage = loadStages.has(props.run.currentStage)

  let state: WorkstreamView['state'] = !policyConfirmed
    ? 'waiting'
    : enabled
      ? 'waiting'
      : 'inactive'
  let statusLabel = !policyConfirmed
    ? '待确认业务范围'
    : enabled
      ? '等待编排'
      : '本票不启用'
  let description = !policyConfirmed
    ? '请先确认本票是否需要智能装载；系统不会在范围未定时自行启动作业。'
    : enabled
      ? '系统正在读取装载任务的最新状态。'
      : '当前能力策略没有要求装载规划。'

  // enabled 必须参与每个分支判断。即使历史字段中残留作业编号，
  // 也不能把当前策略未启用的能力画成“已完成”。
  if (enabled && locked) {
    state = 'ready'
    statusLabel = `正式 V${props.run.lockedLoadVersionNumber}`
    description = '装载方案已经人工批准并锁定为不可变正式版本。'
  } else if (enabled && inStage) {
    state =
      props.run.currentStage === 'STARTING_LOAD_AGENT' ? 'waiting' : 'attention'
    const copy: Partial<Record<ShipmentAgentRun['currentStage'], [string, string]>> = {
      STARTING_LOAD_AGENT: ['正在启动', '系统正在安全启动已绑定的装载任务，不会重复创建。'],
      AWAITING_LOAD_CONSTRAINTS: [
        '等待补约束',
        '智能装载已暂停，请在专业工作台补充箱规或安全约束。',
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
      '装载作业正在等待业务人员处理。',
    ]
  } else if (enabled && !reference) {
    state = 'attention'
    statusLabel = '缺少绑定'
    description = '案件尚未选定装载任务，系统不会凭空创建业务任务。'
  } else if (enabled && reference) {
    state = 'waiting'
    statusLabel = '已选定任务'
    description = '系统会按本票流程同步该任务的最新状态。'
  }

  return {
    capability: 'LOAD_PLANNING',
    category: '装载作业',
    title: '智能装载',
    icon: 'box',
    required,
    reference,
    state,
    statusLabel,
    description,
    metricLabel: '现场执行版本',
    metricValue: !policyConfirmed
      ? '待确认'
      : !required
        ? '不适用'
        : props.run.lockedLoadVersionNumber !== null
          ? `已发布 V${props.run.lockedLoadVersionNumber}`
          : '尚未发布',
    route: !enabled
      ? null
      : reference
        ? `/supply-chain/load-planning/tasks?taskNumber=${encodeURIComponent(reference)}&view=planning`
        : '/supply-chain/load-planning/tasks',
    actionLabel: !policyConfirmed
      ? '确认业务范围后继续'
      : !required
        ? '本票无需操作'
        : reference
          ? '进入装载任务'
          : '打开装载任务中心',
  }
}

function bookingStatusLabel(value: string | null): string {
  const labels: Record<string, string> = {
    DRAFT: '草稿待核对',
    READY_TO_SUBMIT: '等待提交',
    SUBMITTED: '已提交',
    PENDING: '承运人处理中',
    CONFIRMED: '承运人已确认',
    REJECTED: '承运人已拒绝',
    CANCELLED: '申请已取消',
    NOT_SUBMITTED: '尚未提交',
    SUBMISSION_PENDING: '正在提交',
    SUBMISSION_UNCERTAIN: '提交结果待确认',
    DELIVERED: '已送达承运人',
    FAILED: '投递失败',
  }
  return value ? labels[value] ?? value : '尚未开始'
}

const carrierStatusLabels = {
  PENDING: '等待投递',
  SENDING: '正在发送',
  RETRY_WAIT: '等待重试',
  ACCEPTED: '承运人已受理',
  PROCESSING: '承运人处理中',
  CONFIRMED: '承运人已确认',
  REJECTED: '承运人已拒绝',
  DEAD: '投递失败，待人工重试',
  CANCELLED: '投递已取消',
} satisfies Record<LinkCarrierSubmission['status'], string>

function carrierStatusLabel(value: string): string {
  return (
    carrierStatusLabels[value as LinkCarrierSubmission['status']] ??
    bookingStatusLabel(value)
  )
}

function buildBookingWorkstream(): WorkstreamView {
  const policyConfirmed = props.run.scenarioPolicyConfirmed
  const required = props.run.requiredCapabilities.includes('LINK_BOOKING')
  const enabled = policyConfirmed && required
  const reference = props.run.selectedBookingApplicationNumber
  const confirmed = props.run.bookingStatus === 'CONFIRMED'
  const inStage = bookingStages.has(props.run.currentStage)

  let state: WorkstreamView['state'] = !policyConfirmed
    ? 'waiting'
    : enabled
      ? 'waiting'
      : 'inactive'
  let statusLabel = !policyConfirmed
    ? '待确认业务范围'
    : enabled
      ? '等待编排'
      : '本票不启用'
  let description = !policyConfirmed
    ? '请先确认本票是否需要 LINK 订舱；系统不会在范围未定时创建或提交申请。'
    : enabled
      ? '系统正在读取 LINK 申请和承运人状态。'
      : '当前能力策略没有要求 LINK 订舱。'

  // 非必需能力只说明策略结果，不显示为已完成，也不提供进入无关作业的操作入口。
  if (enabled && confirmed) {
    state = 'ready'
    statusLabel = '承运人已确认'
    description = 'LINK 已收到异步确认，订舱参考号已回写到专业系统。'
  } else if (enabled && inStage) {
    state =
      ['SUBMITTING_BOOKING', 'AWAITING_BOOKING_RESULT'].includes(
        props.run.currentStage,
      )
        ? 'waiting'
        : 'attention'
    const copy: Partial<Record<ShipmentAgentRun['currentStage'], [string, string]>> = {
      AWAITING_BOOKING_SUBMIT_APPROVAL: [
        '等待提交批准',
        '七步填单草稿已就绪，提交外部系统前必须人工确认。',
      ],
      SUBMITTING_BOOKING: ['正在提交', '系统正在执行已经批准的 LINK 提交动作。'],
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
      '订舱作业正在等待业务人员处理。',
    ]
  } else if (enabled && !reference) {
    state = 'attention'
    statusLabel = '缺少绑定'
    description = '案件尚未选定 LINK 申请，系统不会凭空创建订舱草稿。'
  } else if (enabled && reference) {
    state = 'waiting'
    statusLabel = bookingStatusLabel(props.run.bookingStatus) || '已选定申请'
    description = '系统会根据申请和承运人状态决定下一步。'
  }

  return {
    capability: 'LINK_BOOKING',
    category: '订舱作业',
    title: 'LINK 七步订舱',
    icon: 'database',
    required,
    reference,
    state,
    statusLabel,
    description,
    metricLabel: '承运人进度',
    metricValue: !policyConfirmed
      ? '待确认'
      : !required
        ? '不适用'
        : props.run.carrierSubmissionStatus
          ? carrierStatusLabel(props.run.carrierSubmissionStatus)
          : bookingStatusLabel(props.run.bookingStatus),
    route: !enabled
      ? null
      : reference
        ? `/link-booking/applications?applicationNumber=${encodeURIComponent(reference)}&view=detail`
        : '/link-booking/applications',
    actionLabel: !policyConfirmed
      ? '确认业务范围后继续'
      : !required
        ? '本票无需操作'
        : reference
          ? '进入 LINK 申请'
          : '打开 LINK 工作台',
  }
}
</script>

<template>
  <section class="agent-workstreams">
    <div class="workstreams-heading">
      <div>
        <span>专业作业结果</span>
        <h4>智能装载与 LINK 订舱</h4>
        <p>这里显示本票可能涉及的两项专业作业；未启用的环节会明确标记为不适用。</p>
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
            <span>{{ workstream.category }}</span>
            <h5>{{ workstream.title }}</h5>
          </div>
          <span class="workstream-state">{{ workstream.statusLabel }}</span>
        </div>

        <p>{{ workstream.description }}</p>

        <dl>
          <div>
            <dt>作业编号</dt>
            <dd>{{ workstream.reference || '尚未选定' }}</dd>
          </div>
          <div>
            <dt>{{ workstream.metricLabel }}</dt>
            <dd>{{ workstream.metricValue }}</dd>
          </div>
        </dl>

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
  grid-template-columns: repeat(2, minmax(0, 1fr));
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
