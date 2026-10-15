package com.customsaffairs.platform.loadplanning;

/** 正式装载方案版本的追加式审计事件。 */
public record LoadPlanVersionEventResponse(
    Integer sequence,
    String eventType,
    String actorSubject,
    String actorDisplayName,
    String actorSource,
    String requestId,
    String detail,
    String occurredAt) {}
