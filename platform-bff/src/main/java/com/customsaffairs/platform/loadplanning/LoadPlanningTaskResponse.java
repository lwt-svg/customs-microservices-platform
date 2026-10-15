package com.customsaffairs.platform.loadplanning;

import java.util.List;

/** 一份装载任务详情及其完整算法输入。 */
public record LoadPlanningTaskResponse(
        long id,
        String taskNumber,
        String taskName,
        String description,
        String status,
        LoadPlanningUnitsResponse units,
        List<LoadPlanningCargoItemResponse> cargoItems,
        List<LoadPlanningPalletSpecResponse> palletSpecs,
        String createdAt,
        String updatedAt) {
}
