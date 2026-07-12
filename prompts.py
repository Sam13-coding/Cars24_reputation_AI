"""Copy templates used by report_generator.py to render human-readable report sections."""

EXECUTIVE_SUMMARY_TEMPLATE = (
    "Across {total} monitored Reddit discussions, {critical} were flagged Critical, "
    "{high} High, {medium} Medium, and {low} Low priority. "
    "The most common risk factor was '{top_reason}', appearing in {top_reason_count} discussions."
)

# risk-factor reason -> suggested action, shown in the report's Recommended Actions section
RECOMMENDED_ACTIONS: dict[str, str] = {
    "fraud allegations": "Escalate fraud allegations to the fraud/legal team for verification and public response.",
    "scam allegations": "Investigate scam allegations and issue a clarifying public statement where warranted.",
    "refund issues": "Audit refund SLA compliance and proactively follow up on open refund complaints.",
    "consumer court mentions": "Loop in legal/compliance on any consumer court or legal notice mentions.",
    "highly emotional language": "Prioritize customer support outreach on high-emotion threads to prevent escalation.",
    "large discussion size": "Monitor high-engagement threads closely; visibility amplifies reputational impact.",
}

DEFAULT_RECOMMENDED_ACTION = "Continue routine monitoring; no elevated action required."
