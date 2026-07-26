import type {
  ServiceRecordSummary,
  SupportOrderSummary,
  SupportShipmentPackage,
} from "../../api/types";

/** 将履约状态转换为客户可理解的中文标签。 */
export function shipmentStatusLabel(
  status: SupportOrderSummary["shipment_status"] | SupportShipmentPackage["status"],
): string {
  if (status === "in_transit") return "运输中";
  if (status === "delivered") return "已送达";
  if (status === "preparing") return "备货中";
  return "暂无物流";
}

/** 将状态映射为跨页面统一的视觉语义。 */
export function statusTone(
  status:
    | SupportOrderSummary["status"]
    | NonNullable<SupportOrderSummary["shipment_status"]>
    | ServiceRecordSummary["status"],
): "neutral" | "info" | "success" | "warning" {
  if (status === "delivered" || status === "completed") return "success";
  if (status === "cancelled" || status === "needs_attention") return "warning";
  if (
    status === "shipped" ||
    status === "in_transit" ||
    status === "in_progress"
  ) {
    return "info";
  }
  return "neutral";
}

/** 将分为单位的商品金额格式化为稳定人民币展示。 */
export function formatMinorAmount(
  amount: number | null | undefined,
  currency = "CNY",
): string {
  if (amount === null || amount === undefined) return "金额未记录";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
  }).format(amount / 100);
}

/** 将字符串金额格式化为统一人民币展示。 */
export function formatAmount(
  amount: string | null | undefined,
  currency = "CNY",
): string {
  if (amount === null || amount === undefined) return "金额未记录";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
  }).format(Number(amount));
}
