/**
 * Display-only helpers: turn raw snake_case feature keys into readable
 * labels, and look up plain-English descriptions for tooltips. Never use
 * these for data keys / API params — only for text shown to a user.
 */
const TOK: Record<string, string> = {
  txn: "Transaction",
  pct: "Percent",
  "7d": "7-day",
  "30d": "30-day",
  "90d": "90-day",
  id: "ID",
  ks: "KS",
  pvalue: "p-value",
  v1: "v1",
};

export function humanize(name: string): string {
  return name
    .split("_")
    .filter(Boolean)
    .map((w) => TOK[w] ?? w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export const FEATURE_DESCRIPTIONS: Record<string, string> = {
  txn_count_7d: "Count of successful transactions in the last 7 days.",
  txn_count_30d: "Count of successful transactions in the last 30 days.",
  txn_count_90d: "Count of successful transactions in the last 90 days.",
  total_spend_7d: "Sum of successful transaction amounts in the last 7 days.",
  total_spend_30d: "Sum of successful transaction amounts in the last 30 days.",
  total_spend_90d: "Sum of successful transaction amounts in the last 90 days.",
  avg_txn_amount_30d: "Average successful transaction amount over the last 30 days.",
  failed_txn_rate_30d: "Fraction of transactions that failed in the last 30 days (0–1).",
  days_since_last_txn: "Days since the user's most recent successful transaction.",
  open_tickets: "Number of currently unresolved support tickets.",
  ticket_rate_30d: "Support tickets opened in the last 30 days.",
  account_age_days: "Days since the user signed up.",
  plan_encoded: "Subscription tier as an ordinal: free=0, basic=1, pro=2, enterprise=3.",
};

/** Description for a feature-named column/label, with a sane fallback for
 * anything not in the table above (e.g. a feature added later). */
export function describeFeature(name: string): string {
  return FEATURE_DESCRIPTIONS[name] ?? `Feature value for ${humanize(name)}.`;
}
