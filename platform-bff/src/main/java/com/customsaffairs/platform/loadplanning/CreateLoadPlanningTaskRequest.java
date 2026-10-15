package com.customsaffairs.platform.loadplanning;

import jakarta.validation.Valid;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.HashSet;
import java.util.List;

/** 浏览器通过 Java BFF 创建装载任务时使用的 camelCase 请求。 */
public record CreateLoadPlanningTaskRequest(
        @NotBlank
        @Size(max = 64)
        @Pattern(
                regexp = "^LP-[A-Za-z0-9_-]+$",
                message = "装载任务编号必须以 LP- 开头")
        String taskNumber,
        @NotBlank @Size(max = 128) String taskName,
        @Size(max = 2_000) String description,
        @NotEmpty @Size(max = 1_000) List<@Valid LoadPlanningCargoItemRequest> cargoItems,
        @NotEmpty @Size(max = 100) List<@Valid LoadPlanningPalletSpecRequest> palletSpecs) {

    /** 提前在平台边界拒绝任务内重复编码，Python 和数据库仍会做最终校验。 */
    @AssertTrue(message = "同一任务内货物编码和托盘编码分别必须唯一")
    public boolean isBusinessCodeUnique() {
        if (cargoItems == null || palletSpecs == null) {
            return true;
        }
        var itemCodes = new HashSet<String>();
        var palletCodes = new HashSet<String>();
        boolean itemUnique = cargoItems.stream()
                .map(LoadPlanningCargoItemRequest::itemCode)
                .allMatch(itemCodes::add);
        boolean palletUnique = palletSpecs.stream()
                .map(LoadPlanningPalletSpecRequest::palletCode)
                .allMatch(palletCodes::add);
        return itemUnique && palletUnique;
    }
}
