import type { components } from "./generated";

export type SessionResponse = components["schemas"]["SessionResponse"];
export type PolicyCitation = components["schemas"]["PolicyCitation"];
export type ChatResponse = components["schemas"]["ChatResponse"];
export type AgentRun = components["schemas"]["PublicAgentRun"];
export type AgentRunResponse = components["schemas"]["AgentRunResponse"];
export type RunAcceptedResponse = components["schemas"]["RunAcceptedResponse"];
export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationMessage = components["schemas"]["ConversationMessage"];
export type ConversationListResponse =
  components["schemas"]["ConversationListResponse"];
export type ConversationMessagesResponse =
  components["schemas"]["ConversationMessagesResponse"];
export type ShipmentInput = components["schemas"]["ShipmentInput"];
export type OrderInput = components["schemas"]["OrderCreate"];
export type PublicOrder = components["schemas"]["PublicOrder"];
export type MockPaymentInput = components["schemas"]["MockPaymentInput"];
export type PublicPayment = components["schemas"]["PublicPayment"];
export type PublicRefund = components["schemas"]["PublicRefund"];
export type PublicRefundPreview = components["schemas"]["PublicRefundPreview"];
export type PublicRefundResult = components["schemas"]["PublicRefundResult"];
export type PendingRefundResponse = components["schemas"]["PendingRefundResponse"];
export type PendingL2Response = components["schemas"]["PendingL2Response"];
export type PublicL2UpgradePreview =
  components["schemas"]["PublicL2UpgradePreview"];
export type PublicL2CaseSummary =
  components["schemas"]["PublicL2CaseSummary"];
export type PublicL2CaseDetail = components["schemas"]["PublicL2CaseDetail"];
export type PublicL2CaseMetrics =
  components["schemas"]["PublicL2CaseMetrics"];
export type PublicL2TraceEvent = components["schemas"]["PublicL2TraceEvent"];
export type PublicL2TracePage = components["schemas"]["PublicL2TracePage"];
export type PublicMemoryProposal =
  components["schemas"]["PublicMemoryProposal"];
export type PublicCustomerPreference =
  components["schemas"]["PublicCustomerPreference"];
export type MemoryValue = components["schemas"]["MemoryUpdateRequest"]["value"];
export type AccessMode = SessionResponse["mode"];
export type OrderStatus = OrderInput["status"];
export type ShipmentStatus = ShipmentInput["status"];
export type PaymentChannel = MockPaymentInput["channel"];
export type PaymentStatus = MockPaymentInput["status"];
export type OrderUpdate = Omit<
  components["schemas"]["OrderUpdate"],
  "remove_shipment"
> & { remove_shipment?: boolean };

export interface ApiErrorBody {
  error_code: string;
  message: string;
}

export interface RunEvent {
  event_id: number;
  run_id: string;
  event_type:
    | "run.accepted"
    | "run.started"
    | "step.updated"
    | "action.required"
    | "message.completed"
    | "run.completed"
    | "run.failed"
    | "run.interrupted";
  payload_version: number;
  payload: Record<string, unknown>;
  created_at: string;
}
