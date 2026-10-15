package com.customsaffairs.platform.loadplanning;

/** 浏览器时间线展示的一条智能体审计事件。 */
public record LoadPlanningAgentEventResponse(
    Long sequence, String eventType, String title, String detail, String createdAt) {}
