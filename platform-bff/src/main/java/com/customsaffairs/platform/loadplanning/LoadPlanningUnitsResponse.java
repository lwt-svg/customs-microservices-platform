package com.customsaffairs.platform.loadplanning;

/** Python 明确返回的单位约定，页面不根据字段数值猜测单位。 */
public record LoadPlanningUnitsResponse(String dimension, String weight) {
}
