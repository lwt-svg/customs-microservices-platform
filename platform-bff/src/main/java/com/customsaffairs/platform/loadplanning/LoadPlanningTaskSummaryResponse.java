package com.customsaffairs.platform.loadplanning;

/** 装载任务列表摘要，不在列表接口传输全部箱规明细。 */
public record LoadPlanningTaskSummaryResponse(
        long id,
        String taskNumber,
        String taskName,
        String status,
        int cargoItemCount,
        int cargoBoxCount,
        int palletSpecCount,
        String createdAt,
        String updatedAt) {
}
