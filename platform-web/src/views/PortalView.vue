<script setup lang="ts">
import UiIcon from '@/components/UiIcon.vue'
</script>

<template>
  <section class="portal-page">
    <div class="portal-hero">
      <div class="portal-hero-copy">
        <span class="eyebrow">TRADE OPERATIONS CONTROL CENTER</span>
        <h2>以发运案件为主线，协同关务与供应链</h2>
        <p>
          为每票发运建立统一案件，集中查看运输场景、专业任务、办理进度和审计记录；
          关务与供应链团队在各自职责范围内协同处理。
        </p>
      </div>
      <div class="portal-principle">
        <span>协同原则</span>
        <strong>统一案件 · 专业分工 · 全程留痕</strong>
      </div>
    </div>

    <RouterLink
      class="shipment-entry"
      to="/tradeops/shipment-cases"
      aria-label="进入发运案件中心"
    >
      <span class="shipment-entry-icon"><UiIcon name="truck" /></span>
      <div class="shipment-entry-copy">
        <span>TRADEOPS CORE WORKSPACE</span>
        <h3>发运案件中心</h3>
        <p>统一管理案件基本信息、运输场景、装载与订舱资源、生命周期状态和审计事件。</p>
      </div>
      <div class="shipment-entry-action">
        <span>跨部门业务主入口</span>
        <strong>
          进入案件中心
          <UiIcon name="arrow" />
        </strong>
      </div>
    </RouterLink>

    <RouterLink
      class="agent-governance-entry"
      to="/tradeops/agents"
      aria-label="进入 Agent 运行中心"
    >
      <span class="agent-governance-icon"><UiIcon name="server" /></span>
      <div>
        <span>AGENT RUNTIME &amp; GOVERNANCE</span>
        <strong>查看真实运行、人工待办、外部等待和 Trace</strong>
      </div>
      <span>
        Agent 运行中心
        <UiIcon name="arrow" />
      </span>
    </RouterLink>

    <div class="domain-grid">
      <article class="domain-card customs-domain">
        <div class="domain-card-heading">
          <span class="domain-icon"><UiIcon name="file" /></span>
          <span class="domain-status active">关务专业能力</span>
        </div>
        <span class="domain-index">01 / CUSTOMS</span>
        <h3>关务与订舱能力</h3>
        <p>处理出口单证与 CEVA LINK 订舱业务，支持业务复核、提交跟踪和操作留痕。</p>
        <ul>
          <li>装箱单信息提取与人工复核</li>
          <li>LINK 七步自动填单与业务确认</li>
          <li>异步提交、幂等控制与状态跟踪</li>
        </ul>
        <RouterLink class="domain-entry" to="/link-booking/applications">
          进入 LINK 作业
          <UiIcon name="arrow" />
        </RouterLink>
      </article>

      <article class="domain-card supply-domain">
        <div class="domain-card-heading">
          <span class="domain-icon"><UiIcon name="layers" /></span>
          <span class="domain-status active">供应链专业能力</span>
        </div>
        <span class="domain-index">02 / SUPPLY CHAIN</span>
        <h3>智能装载能力</h3>
        <p>面向箱到托盘的装载规划与执行复核，统一管理任务、方案、审批和正式版本。</p>
        <ul>
          <li>PL 解析、任务主数据与来源追踪</li>
          <li>多约束装载规划与多方案比较</li>
          <li>托盘 3D 复核、审批与正式版本锁定</li>
          <li>面向业务人员的方案说明与风险提示</li>
        </ul>
        <RouterLink class="domain-entry" to="/supply-chain/load-planning">
          进入智能装载
          <UiIcon name="arrow" />
        </RouterLink>
      </article>
    </div>

    <div class="portal-boundary">
      <UiIcon name="shield" />
      <div>
        <strong>数据与责任边界</strong>
        <p>
          发运案件负责串联业务编号、专业资源和审计事件；关务与供应链能力分别维护自身数据和
          业务规则。跨域信息只通过明确接口关联，关键状态变更保留人工确认。
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.shipment-entry {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 20px;
  align-items: center;
  padding: 24px 26px;
  border: 1px solid #cfe0d8;
  border-radius: 16px;
  color: inherit;
  background:
    radial-gradient(circle at 88% 14%, rgba(78, 139, 112, 0.12), transparent 30%),
    linear-gradient(120deg, #ffffff 0%, #f6faf8 100%);
  box-shadow: 0 10px 28px rgba(35, 77, 63, 0.06);
  text-decoration: none;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}

.agent-governance-entry {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 13px;
  align-items: center;
  padding: 14px 17px;
  border: 1px solid #dce5e1;
  border-radius: 13px;
  color: inherit;
  background: #fff;
  box-shadow: 0 8px 24px rgba(35, 77, 63, 0.04);
  text-decoration: none;
}

.agent-governance-entry:hover {
  border-color: #aac7b9;
  background: #f8fbf9;
}

.agent-governance-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 10px;
  color: #dff1e9;
  background: #284e40;
}

.agent-governance-icon .ui-icon {
  width: 17px;
}

.agent-governance-entry > div > span {
  color: #7b8d85;
  font-size: 8px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.agent-governance-entry > div > strong {
  display: block;
  margin-top: 4px;
  color: #34433d;
  font-size: 10px;
}

.agent-governance-entry > span:last-child {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #356a58;
  font-size: 9px;
  font-weight: 700;
}

.agent-governance-entry > span:last-child .ui-icon {
  width: 12px;
}

.shipment-entry:hover {
  border-color: #9fc2b2;
  box-shadow: 0 14px 34px rgba(35, 77, 63, 0.1);
  transform: translateY(-1px);
}

.shipment-entry:focus-visible {
  outline: 3px solid rgba(60, 108, 91, 0.16);
  outline-offset: 2px;
}

.shipment-entry-icon {
  display: grid;
  width: 50px;
  height: 50px;
  place-items: center;
  border-radius: 14px;
  color: #ffffff;
  background: #356a5a;
}

.shipment-entry-icon .ui-icon {
  width: 24px;
  height: 24px;
}

.shipment-entry-copy {
  min-width: 0;
}

.shipment-entry-copy > span,
.shipment-entry-action > span {
  color: #72857c;
  font-size: 9px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.shipment-entry-copy h3 {
  margin: 6px 0 5px;
  font-size: 20px;
  letter-spacing: -0.02em;
}

.shipment-entry-copy p {
  margin: 0;
  color: #68756f;
  font-size: 11px;
  line-height: 1.7;
}

.shipment-entry-action {
  display: grid;
  gap: 8px;
  justify-items: end;
}

.shipment-entry-action strong {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #356a5a;
  font-size: 11px;
}

.shipment-entry-action .ui-icon {
  width: 14px;
  transition: transform 160ms ease;
}

.shipment-entry:hover .shipment-entry-action .ui-icon {
  transform: translateX(3px);
}

@media (max-width: 720px) {
  .shipment-entry {
    grid-template-columns: auto minmax(0, 1fr);
    padding: 22px;
  }

  .shipment-entry-action {
    grid-column: 2;
    justify-items: start;
  }

  .agent-governance-entry {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .agent-governance-entry > span:last-child {
    grid-column: 2;
  }
}

@media (max-width: 480px) {
  .shipment-entry {
    grid-template-columns: 1fr;
  }

  .shipment-entry-action {
    grid-column: 1;
  }

  .agent-governance-entry {
    grid-template-columns: 1fr;
  }

  .agent-governance-entry > span:last-child {
    grid-column: 1;
  }
}
</style>
