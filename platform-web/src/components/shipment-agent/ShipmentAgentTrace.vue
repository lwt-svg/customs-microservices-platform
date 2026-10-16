<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ShipmentAgentEvent } from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{
  events: ShipmentAgentEvent[]
}>()

const expanded = ref(false)

const newestEvents = computed(() =>
  [...props.events].sort((left, right) => right.sequence - left.sequence),
)
const visibleEvents = computed(() =>
  expanded.value ? newestEvents.value : newestEvents.value.slice(0, 6),
)

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function eventTone(event: ShipmentAgentEvent): 'success' | 'attention' | 'neutral' | 'danger' {
  const value = `${event.eventType} ${event.stage}`.toUpperCase()
  if (value.includes('FAILED') || value.includes('REJECT') || value.includes('ERROR')) {
    return 'danger'
  }
  if (value.includes('APPROV') || value.includes('WAIT') || value.includes('PAUSE')) {
    return 'attention'
  }
  if (value.includes('COMPLET') || value.includes('CONFIRM') || value.includes('LOCK')) {
    return 'success'
  }
  return 'neutral'
}

function hasStructuredData(event: ShipmentAgentEvent): boolean {
  return Object.keys(event.data).length > 0
}
</script>

<template>
  <section class="shipment-trace">
    <div class="trace-heading">
      <div>
        <span>RUN TRACE</span>
        <h4>可审计运行轨迹</h4>
        <p>展示节点、工具结果和人工等待；不展示模型隐藏思维链。</p>
      </div>
      <div class="trace-count">
        <strong>{{ events.length }}</strong>
        <span>条事件</span>
      </div>
    </div>

    <ol v-if="visibleEvents.length" class="trace-list">
      <li
        v-for="event in visibleEvents"
        :key="event.id"
        :data-tone="eventTone(event)"
      >
        <span class="trace-node">
          <UiIcon
            :name="
              eventTone(event) === 'success'
                ? 'check'
                : eventTone(event) === 'danger'
                  ? 'alert'
                  : 'clock'
            "
          />
        </span>
        <div class="trace-event">
          <div class="trace-event-heading">
            <div>
              <span>{{ event.stage }}</span>
              <strong>{{ event.title }}</strong>
            </div>
            <time>{{ formatDateTime(event.occurredAt) }}</time>
          </div>
          <p>{{ event.detail }}</p>
          <div class="trace-meta">
            <span>#{{ String(event.sequence).padStart(2, '0') }} · {{ event.eventType }}</span>
            <code v-if="event.resourceNumber">
              {{ event.resourceType }} / {{ event.resourceNumber }}
            </code>
            <span>{{ event.actorDisplayName }}</span>
            <code :title="event.requestId">REQ {{ event.requestId.slice(0, 8) }}</code>
          </div>
          <details v-if="hasStructuredData(event)" class="trace-data">
            <summary>查看结构化事件数据</summary>
            <pre>{{ JSON.stringify(event.data, null, 2) }}</pre>
          </details>
        </div>
      </li>
    </ol>

    <div v-else class="trace-empty">
      <UiIcon name="clock" />
      <p>运行创建后，真实节点迁移会出现在这里。</p>
    </div>

    <button
      v-if="events.length > 6"
      class="trace-toggle"
      type="button"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起较早轨迹' : `查看全部 ${events.length} 条轨迹` }}
      <UiIcon name="arrow" />
    </button>
  </section>
</template>

<style scoped>
.shipment-trace {
  display: grid;
  gap: 14px;
}

.trace-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.trace-heading > div:first-child > span {
  color: #8b9692;
  font-size: 8px;
  font-weight: 760;
  letter-spacing: 0.14em;
}

.trace-heading h4,
.trace-heading p {
  margin: 0;
}

.trace-heading h4 {
  margin-top: 5px;
  color: #26322e;
  font-size: 14px;
}

.trace-heading p {
  margin-top: 5px;
  color: #7e8884;
  font-size: 9px;
}

.trace-count {
  display: flex;
  align-items: baseline;
  gap: 5px;
  color: #74817c;
}

.trace-count strong {
  color: #2e4f44;
  font-size: 18px;
}

.trace-count span {
  font-size: 8px;
}

.trace-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.trace-list li {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 11px;
  padding-bottom: 10px;
}

.trace-list li:not(:last-child)::before {
  position: absolute;
  top: 25px;
  bottom: -1px;
  left: 11px;
  width: 1px;
  background: #e3e8e6;
  content: "";
}

.trace-node {
  z-index: 1;
  display: grid;
  width: 23px;
  height: 23px;
  place-items: center;
  border: 1px solid #dce4e0;
  border-radius: 50%;
  color: #73817b;
  background: #fff;
}

.trace-node :deep(.ui-icon) {
  width: 11px;
}

[data-tone="success"] .trace-node {
  border-color: #bcdacb;
  color: #347054;
  background: #edf7f1;
}

[data-tone="attention"] .trace-node {
  border-color: #e4d2a9;
  color: #956f2b;
  background: #fff8e8;
}

[data-tone="danger"] .trace-node {
  border-color: #e9c9c9;
  color: #9a4b4b;
  background: #fff4f4;
}

.trace-event {
  min-width: 0;
  padding: 11px 12px;
  border: 1px solid #e7ebe9;
  border-radius: 11px;
  background: #fcfdfd;
}

.trace-event-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.trace-event-heading > div > span {
  display: block;
  color: #8f9995;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 7px;
}

.trace-event-heading strong {
  display: block;
  margin-top: 4px;
  color: #34413c;
  font-size: 10px;
}

.trace-event-heading time {
  color: #9aa39f;
  font-size: 8px;
  white-space: nowrap;
}

.trace-event > p {
  margin: 8px 0 0;
  color: #6f7a76;
  font-size: 9px;
  line-height: 1.58;
}

.trace-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 9px;
  margin-top: 9px;
  color: #939c98;
  font-size: 7px;
}

.trace-meta code {
  overflow: hidden;
  max-width: 100%;
  color: #6e7b76;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-data {
  margin-top: 9px;
  border-top: 1px dashed #dfe5e2;
  color: #69756f;
}

.trace-data summary {
  padding-top: 8px;
  cursor: pointer;
  font-size: 8px;
  font-weight: 700;
}

.trace-data pre {
  max-height: 210px;
  overflow: auto;
  margin: 8px 0 0;
  padding: 9px;
  border-radius: 8px;
  color: #405048;
  background: #f1f5f3;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 7px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.trace-empty {
  display: grid;
  min-height: 110px;
  place-items: center;
  align-content: center;
  gap: 7px;
  border: 1px dashed #dfe5e2;
  border-radius: 11px;
  color: #929c98;
}

.trace-empty :deep(.ui-icon) {
  width: 18px;
}

.trace-empty p {
  margin: 0;
  font-size: 9px;
}

.trace-toggle {
  display: inline-flex;
  justify-self: center;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  color: #466c5f;
  background: transparent;
  cursor: pointer;
  font-size: 9px;
  font-weight: 700;
}

.trace-toggle :deep(.ui-icon) {
  width: 12px;
  transform: rotate(90deg);
}
</style>
