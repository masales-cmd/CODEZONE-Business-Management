CODEZONE_AI_KNOWLEDGE_VERSION = "2026.10"

BUSINESS_DOMAINS = {
    "sales": ["sales trend", "average order value", "discounts", "returns", "customer concentration", "product mix"],
    "profit": ["gross margin", "cost drift", "discount leakage", "expense ratio", "contribution after expenses"],
    "cash_flow": ["cash balance", "paid sales", "receivables", "payables", "upcoming commitments"],
    "inventory": ["low stock", "slow movers", "stock value", "reorder priority", "expiry risk", "transfers"],
    "customers": ["credit limit", "receivables", "overdue risk", "payment behavior", "customer concentration"],
    "purchasing": ["open POs", "supplier balances", "reorder", "lead time", "purchase concentration"],
    "operations": ["approvals", "deliveries", "shifts", "returns", "adjustments", "exceptions"],
}

ADVISOR_RULES = [
    "Never treat revenue as cash.",
    "Never recommend reordering only from minimum stock; use velocity and cash constraints.",
    "Never call a margin healthy without checking discounts and cost trend.",
    "Prioritize overdue receivables by amount, age and customer credit exposure.",
    "Separate facts from inferences and recommendations.",
    "Prefer low-risk, reversible actions first.",
    "Never expose credentials, secrets or raw database contents.",
    "Do not execute write actions without explicit confirmation, permission and audit logging.",
]
