<script setup lang="ts">
import UiIcon from '@/components/UiIcon.vue'
</script>

<template>
  <section class="portal-page">
    <div class="portal-hero">
      <div class="portal-hero-copy">
        <span class="eyebrow">SAND DUNE OPERATING SYSTEM</span>
        <h2>一个入口，连接关务与供应链</h2>
        <p>
          沙丘 OS 负责统一入口、组织权限和审计能力；各业务域保持独立边界，
          既可以分别演进，也能在未来通过经过确认的业务数据协作。
        </p>
      </div>
      <div class="portal-principle">
        <span>平台设计原则</span>
        <strong>业务分域 · 数据可追溯 · 人工可确认</strong>
      </div>
    </div>

    <div class="domain-grid">
      <article class="domain-card customs-domain">
        <div class="domain-card-heading">
          <span class="domain-icon"><UiIcon name="file" /></span>
          <span class="domain-status active">业务闭环已接入</span>
        </div>
        <span class="domain-index">01 / CUSTOMS</span>
        <h3>关务 OS</h3>
        <p>面向出口单证与 CEVA LINK 订舱场景，保留业务复核、提交状态和完整操作流水。</p>
        <ul>
          <li>装箱单 PDF 规则解析与人工复核</li>
          <li>LINK 七步自动填单与业务确认</li>
          <li>异步提交、幂等和回调状态跟踪</li>
        </ul>
        <RouterLink class="domain-entry" to="/link-booking/applications">
          进入关务 OS
          <UiIcon name="arrow" />
        </RouterLink>
      </article>

      <article class="domain-card supply-domain">
        <div class="domain-card-heading">
          <span class="domain-icon"><UiIcon name="layers" /></span>
          <span class="domain-status active">托盘闭环已接入</span>
        </div>
        <span class="domain-index">02 / SUPPLY CHAIN</span>
        <h3>供应链 OS</h3>
        <p>
          当前完成箱到托盘的多约束规划、3D 复核和正式版本；托盘到集装箱仍是下一阶段。
        </p>
        <ul>
          <li class="done">已完成：PL 解析、任务主数据和来源追踪</li>
          <li class="done">已完成：确定性三方案、LangGraph、托盘 3D、审批与正式锁定</li>
          <li class="done">可选增强：规则 / Codex / OpenAI 只读方案解读</li>
          <li class="planned">下一阶段：集装箱规划、作业指导书和现场反馈</li>
        </ul>
        <RouterLink class="domain-entry" to="/supply-chain/load-planning">
          进入供应链 OS
          <UiIcon name="arrow" />
        </RouterLink>
      </article>
    </div>

    <div class="portal-boundary">
      <UiIcon name="shield" />
      <div>
        <strong>当前边界</strong>
        <p>
          两个业务域目前共用统一门户，但还没有真实登录和部门权限隔离。智能装载结果也不会
          自动写入 LINK；只有业务字段、审批责任和接口边界完成确认后，才考虑受控共享。
        </p>
      </div>
    </div>
  </section>
</template>
