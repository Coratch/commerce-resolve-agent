const CUSTOMER_TERM_REPLACEMENTS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bMOCK REFUND\b/gi, "演示退款"],
  [/\bMOCK-RFD-([A-Za-z0-9-]+)\b/g, "DEMO-RFD-$1"],
  [/\bmock_card\b/gi, "演示支付渠道"],
  [/\bmock_wallet\b/gi, "演示支付钱包"],
  [/Mock\s*退款/g, "演示退款"],
  [/Mock\s*支付/g, "演示支付"],
  [/Mock\s*订单/g, "演示订单"],
  [/\bFake\b/gi, "演示"],
  [/\bProvider\b/gi, "模型服务"],
  [/\bLLM\b/gi, "智能服务"],
  [/\bR2\s*演示资金动作/g, "需确认的演示资金操作"],
];

/**
 * 将历史公开消息中的研发术语转换为客户可理解的业务语言。
 * 该函数只改变展示文案，不改变持久化 Payload、状态或权限判断。
 */
export function customerFacingText(value: string): string {
  return CUSTOMER_TERM_REPLACEMENTS.reduce(
    (current, [pattern, replacement]) => current.replace(pattern, replacement),
    value,
  );
}
