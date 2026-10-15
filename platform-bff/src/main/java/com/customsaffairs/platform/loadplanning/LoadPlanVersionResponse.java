package com.customsaffairs.platform.loadplanning;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;

/**
 * 已锁定的正式装载方案版本。
 *
 * <p>三个 snapshot 都是发布时冻结的 JSON。后续作业指导书和现场执行必须读取这些快照，
 * 不能重新读取可能已经变化的任务主数据，也不能重新运行算法后假装还是同一个版本。
 */
public record LoadPlanVersionResponse(
    String versionId,
    String status,
    String taskNumber,
    Integer versionNumber,
    String sourceRunId,
    String sourcePlanId,
    String planCode,
    String selectedPalletCode,
    String supersedesVersionId,
    Integer sourceRunVersion,
    String snapshotSchemaVersion,
    String inputSnapshotHash,
    String constraintSnapshotHash,
    String planSnapshotHash,
    String algorithmVersion,
    String scoreVersion,
    String approvalActorSubject,
    String approvalActorDisplayName,
    String approvalActorSource,
    String approvedAt,
    String approvalComment,
    String lockedBySubject,
    String lockedByDisplayName,
    String lockedBySource,
    String lockReason,
    String lockedAt,
    JsonNode inputSnapshot,
    JsonNode constraintSnapshot,
    JsonNode planSnapshot,
    List<LoadPlanVersionEventResponse> events) {}
