package com.customsaffairs.platform.link;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.util.List;

/**
 * Python snake_case 与平台 camelCase 之间的装箱单明细适配器。
 *
 * <p>它只在 link 包内部使用，避免 snake_case 扩散到 Controller 和 Vue。</p>
 */
record LinkBookingDetailsRemote(
        @JsonProperty("invoice_number") String invoiceNumber,
        @JsonProperty("document_date") String documentDate,
        @JsonProperty("ciq_number") String ciqNumber,
        String shipper,
        String consignee,
        @JsonProperty("notify_party") String notifyParty,
        String marks,
        @JsonProperty("total_pallets") BigDecimal totalPallets,
        @JsonProperty("total_cartons") BigDecimal totalCartons,
        @JsonProperty("total_quantity") BigDecimal totalQuantity,
        @JsonProperty("total_cbm") BigDecimal totalCbm,
        @JsonProperty("total_net_weight_kg") BigDecimal totalNetWeightKg,
        @JsonProperty("total_gross_weight_kg") BigDecimal totalGrossWeightKg,
        List<LinkBookingItemRemote> items,
        List<LinkBookingPackageRemote> packages,
        List<String> checks) {

    static LinkBookingDetailsRemote fromPlatform(LinkBookingDetailsResponse details) {
        if (details == null) {
            return null;
        }
        return new LinkBookingDetailsRemote(
                details.invoiceNumber(),
                details.documentDate(),
                details.ciqNumber(),
                details.shipper(),
                details.consignee(),
                details.notifyParty(),
                details.marks(),
                details.totalPallets(),
                details.totalCartons(),
                details.totalQuantity(),
                details.totalCbm(),
                details.totalNetWeightKg(),
                details.totalGrossWeightKg(),
                safe(details.items()).stream().map(LinkBookingItemRemote::fromPlatform).toList(),
                safe(details.packages()).stream()
                        .map(LinkBookingPackageRemote::fromPlatform)
                        .toList(),
                safe(details.checks()));
    }

    LinkBookingDetailsResponse toPlatformResponse() {
        return new LinkBookingDetailsResponse(
                invoiceNumber,
                documentDate,
                ciqNumber,
                shipper,
                consignee,
                notifyParty,
                marks,
                totalPallets,
                totalCartons,
                totalQuantity,
                totalCbm,
                totalNetWeightKg,
                totalGrossWeightKg,
                safe(items).stream().map(LinkBookingItemRemote::toPlatformResponse).toList(),
                safe(packages).stream().map(LinkBookingPackageRemote::toPlatformResponse).toList(),
                safe(checks));
    }

    private static <T> List<T> safe(List<T> values) {
        return values == null ? List.of() : List.copyOf(values);
    }
}

record LinkBookingItemRemote(
        @JsonProperty("item_code") String itemCode,
        @JsonProperty("lot_number") String lotNumber,
        @JsonProperty("po_number") String poNumber,
        String description,
        BigDecimal quantity,
        BigDecimal cartons,
        @JsonProperty("net_weight_kg") BigDecimal netWeightKg,
        @JsonProperty("gross_weight_kg") BigDecimal grossWeightKg) {

    static LinkBookingItemRemote fromPlatform(LinkBookingItemResponse item) {
        return new LinkBookingItemRemote(
                item.itemCode(),
                item.lotNumber(),
                item.poNumber(),
                item.description(),
                item.quantity(),
                item.cartons(),
                item.netWeightKg(),
                item.grossWeightKg());
    }

    LinkBookingItemResponse toPlatformResponse() {
        return new LinkBookingItemResponse(
                itemCode,
                lotNumber,
                poNumber,
                description,
                quantity,
                cartons,
                netWeightKg,
                grossWeightKg);
    }
}

record LinkBookingPackageRemote(
        String description,
        BigDecimal pallets,
        BigDecimal cartons,
        BigDecimal quantity,
        @JsonProperty("net_weight_kg") BigDecimal netWeightKg,
        @JsonProperty("gross_weight_kg") BigDecimal grossWeightKg,
        BigDecimal cbm,
        @JsonProperty("po_allocations") List<LinkBookingPoAllocationRemote> poAllocations) {

    static LinkBookingPackageRemote fromPlatform(LinkBookingPackageResponse packageGroup) {
        return new LinkBookingPackageRemote(
                packageGroup.description(),
                packageGroup.pallets(),
                packageGroup.cartons(),
                packageGroup.quantity(),
                packageGroup.netWeightKg(),
                packageGroup.grossWeightKg(),
                packageGroup.cbm(),
                packageGroup.poAllocations() == null
                        ? List.of()
                        : packageGroup.poAllocations().stream()
                                .map(LinkBookingPoAllocationRemote::fromPlatform)
                                .toList());
    }

    LinkBookingPackageResponse toPlatformResponse() {
        return new LinkBookingPackageResponse(
                description,
                pallets,
                cartons,
                quantity,
                netWeightKg,
                grossWeightKg,
                cbm,
                poAllocations == null
                        ? List.of()
                        : poAllocations.stream()
                                .map(LinkBookingPoAllocationRemote::toPlatformResponse)
                                .toList());
    }
}

record LinkBookingPoAllocationRemote(
        @JsonProperty("po_number") String poNumber,
        BigDecimal quantity) {

    static LinkBookingPoAllocationRemote fromPlatform(LinkBookingPoAllocationResponse allocation) {
        return new LinkBookingPoAllocationRemote(allocation.poNumber(), allocation.quantity());
    }

    LinkBookingPoAllocationResponse toPlatformResponse() {
        return new LinkBookingPoAllocationResponse(poNumber, quantity);
    }
}
