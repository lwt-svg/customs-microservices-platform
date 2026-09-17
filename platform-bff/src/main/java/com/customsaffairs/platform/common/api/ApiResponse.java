package com.customsaffairs.platform.common.api;

/**
 * BFF 对 Vue 返回的统一 JSON 外壳。
 *
 * <p>前端不必为每个接口猜测字段名称：成功响应始终有 {@code code}、{@code message} 和
 * {@code data}。真正的业务数据放进 {@code data}，失败响应和全局异常处理会在后续章节加入。</p>
 *
 * <p>{@code record} 是 Java 16 起提供的简洁不可变数据类型。它会自动生成构造方法、
 * 访问器、equals 和 hashCode，适合只承载数据的响应对象。</p>
 *
 * @param <T> data 字段中实际承载的数据类型，例如 String、用户对象或菜单列表。
 * @param code 业务状态码；第一阶段成功固定为 0。
 * @param message 给调用方阅读的简短说明。
 * @param data 成功时的实际数据；没有数据时可为 null。
 */
public record ApiResponse<T>(int code, String message, T data) {

    /**
     * 快速创建一份成功响应，避免每个控制器重复填写相同的成功码和文案。
     *
     * @param data 需要返回给 Vue 的数据。
     * @return 标准成功 JSON 对象。
     */
    public static <T> ApiResponse<T> success(T data) {
        return new ApiResponse<>(0, "success", data);
    }
}
