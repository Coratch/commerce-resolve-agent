import type { components } from "./generated";

export type SessionResponse = components["schemas"]["SessionResponse"];
export type AdminCustomer = components["schemas"]["AdminCustomer"];
export type AdminInvitation = components["schemas"]["AdminInvitation"];
export type AdminInvitationInput =
  components["schemas"]["AdminInvitationCreateRequest"];
export type AdminInvitationCreated = components["schemas"]["InvitationIssued"];
export type AdminAuditRecord = components["schemas"]["AdminAuditRecord"];
export type AdminRunSummary = components["schemas"]["AdminRunSummary"];
export type AdminRunDetail = components["schemas"]["AdminRunDetail"];
export type AdminEvalSnapshot = components["schemas"]["AdminEvalSnapshot"];
export type AdminSystemSnapshot = components["schemas"]["AdminSystemSnapshot"];
export type AdminOverview = components["schemas"]["AdminOverview"];
export type DemoWorkspaceStatus =
  components["schemas"]["DemoWorkspaceStatus"];
export type WorkspaceResetResult =
  components["schemas"]["WorkspaceResetResult"];
export type ConversationResponse =
  components["schemas"]["ConversationResponse"];
export type PolicyCitation = components["schemas"]["PolicyCitation"];
export type ChatResponse = components["schemas"]["ChatResponse"];
export type ServiceResolution = components["schemas"]["ServiceResolution"];
export type AgentRun = components["schemas"]["PublicAgentRun"];
export type AgentRunResponse = components["schemas"]["AgentRunResponse"];
export type RunAcceptedResponse = components["schemas"]["RunAcceptedResponse"];
export type ConversationSummary = components["schemas"]["ConversationSummary"];
export type ConversationMessage = components["schemas"]["ConversationMessage"];
export type ConversationListResponse =
  components["schemas"]["ConversationListResponse"];
export type ConversationMessagesResponse =
  components["schemas"]["ConversationMessagesResponse"];
export type SupportOverview = components["schemas"]["SupportOverview"];
export type SupportOrdersPage = components["schemas"]["SupportOrdersPage"];
export type SupportOrderSummary = components["schemas"]["SupportOrderSummary"];
export type SupportOrderDetail = components["schemas"]["SupportOrderDetail"];
export type SupportOrderItem = components["schemas"]["SupportOrderItem"];
export type SupportShipmentPackage =
  components["schemas"]["SupportShipmentPackage"];
export type SupportProductPreview =
  components["schemas"]["SupportProductPreview"];
export type SupportServicesPage = components["schemas"]["SupportServicesPage"];
export type ServiceRecordSummary = components["schemas"]["ServiceRecordSummary"];
export type ServiceRecordDetail = components["schemas"]["ServiceRecordDetail"];
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
