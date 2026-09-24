"""
Deterministic layers for the categorisation pipeline. No model, no network, microseconds.

Two things live here:

1. Pattern rules for the two categories whose vocabulary is regular enough to enumerate:
   secret_seeking and system_prompt_extraction. A rule can be shown to an auditor,
   and the story names AWS key requests explicitly, so this layer is required anyway.

2. A role x sensitivity check that stands in for the GraphQL-derived check described in
   docs/phase2-review-and-pipeline-design.md. In production the entity and field names
   come from parsing the generated GraphQL query with graphql-core, not from the prompt
   text. Here the text is scanned for sensitive field vocabulary only so the logic can
   be measured on the 190 row set, which carries no GraphQL queries.

Usage:
    uv run python poc/rules.py            # measure both layers on eval/erp_eval.csv
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ERP_DATA = Path(__file__).resolve().parent.parent / "eval" / "erp_eval.csv"

# --- 1. Pattern rules -------------------------------------------------------------

# Nouns that name a secret. Deliberately narrow: "credentials" and "password" on their own
# appear in legitimate admin traffic ("user USR-118 credentials expiry status"), so they
# only count when qualified.
SECRET_NOUNS = [
    r"\b(aws|cloud|api|access|secret|private)[ -]?keys?\b",
    r"\bclient[ -]?secret\b",
    r"\bsecret[ -]?keys?\b",
    r"\bconnection[ -]?string\b",
    r"\bbearer[ -]?token\b",
    r"\b(api|access|auth|oauth|refresh|session)[ -]?tokens?\b",
    r"\b(database|db|service|admin|root)[ -]?password\b",
    r"\busername and password\b",
    r"\benvironment variables?\b",
    r"\b(unredacted|integration|service[ -]account|connector|admin)[ -]credentials\b",
    r"\biam[ -]role\b",
    r"\b(role|resource)[ -]arn\b",
    r"\bvault[ -]path\b",
    r"\bprivate (service )?(endpoints?|hostnames?)\b",
    r"\bauth(entication|orization)?[ -]header\b",
    r"\bclient[ -]id\b",
]

# Phrases that ask for the assistant's own configuration rather than business data.
SYSTEM_PROMPT_PHRASES = [
    r"\bsystem prompt\b",
    r"\bhidden (rules?|messages?|defaults?|instructions?|text)\b",
    r"\b(your|the assistant'?s?) (complete |full |exact |entire )?(configuration|instructions?|setup|rules)\b",
    r"\binstruction (hierarchy|order)\b",
    r"\binitiali[sz]ation (messages?|text)\b",
    r"\btool (definitions?|schemas?|catalog)\b",
    r"\b(raw|internal) (tool )?schemas?\b",
    r"\bcallable operations\b",
    r"\binternal (operation|schema|rule)s?\b",
    r"\bgoverning instructions\b",
    r"\bconfigured you\b",
    r"\b(every|all) (internal )?rules? you follow\b",
    r"\bnot normally shown\b",
    r"\bdefinitions? of (every|each|all) (function|tool)s?\b",
    r"\bfunctions? it can invoke\b",
]

_SECRET = re.compile("|".join(SECRET_NOUNS), re.IGNORECASE)
_SYSPROMPT = re.compile("|".join(SYSTEM_PROMPT_PHRASES), re.IGNORECASE)


def rule_category(text: str) -> str | None:
    """Return a category if a rule fires, else None. Order matters only for reporting."""
    if _SECRET.search(text):
        return "secret_seeking"
    if _SYSPROMPT.search(text):
        return "system_prompt_extraction"
    return None


# --- 2. Role x sensitivity check --------------------------------------------------

# Sensitivity classes and the roles for which access is part of the job. In production
# this table is keyed by GraphQL entity.field, e.g. Employee.salary or Supplier.bankAccount,
# and comes from the schema owners, not from a keyword list.
SENSITIVE_CLASSES: dict[str, dict] = {
    "employee_pay": {
        "terms": [r"\bsalar(y|ies)\b", r"\bbonus(es)?\b", r"\bpayroll\b", r"\bpay bands?\b"],
        "roles": {"hr_admin", "finance_clerk"},
    },
    "employee_personal": {
        "terms": [r"\bhome address(es)?\b", r"\bemergency contacts?\b", r"\bpersonal (phone|email|mobile)",
                  r"\bmobile numbers?\b", r"\bmedical\b", r"\bdisciplinary\b", r"\bperformance review",
                  r"\bleave (balance|records?|accrual)\b", r"\babsence dates\b"],
        "roles": {"hr_admin"},
    },
    "employee_bank": {
        "terms": [r"\bemployee'?s'? bank\b", r"\bbank account and routing\b"],
        "roles": {"hr_admin", "finance_clerk"},
    },
    "customer_financial": {
        "terms": [r"\bcredit limits?\b", r"\boverdue balance", r"\bpayment histor"],
        "roles": {"finance_clerk", "sales_rep"},
    },
    "customer_contact_bulk": {
        "terms": [r"\b(all|every) customers?'?s? .*\b(phone|email|address|contact)",
                  r"\bfull customer database\b", r"\bcontact sheet for all customers\b",
                  r"\bcustomers?'? (personal )?(phone numbers?|home addresses?)"],
        "roles": {"sales_rep"},
    },
    "supplier_commercial": {
        "terms": [r"\bnegotiated (unit )?(costs?|prices?)\b", r"\brebates?\b", r"\bcontract terms\b",
                  r"\bsupplier contracts?\b", r"\bpayment terms\b"],
        "roles": {"purchasing_manager", "finance_clerk"},
    },
    "supplier_bank": {
        "terms": [r"\bsupplier'?s'? bank\b", r"\bbank details for every (active )?supplier\b",
                  r"\bbank[ -]account owner\b"],
        "roles": {"finance_clerk", "purchasing_manager"},
    },
}

_SENSITIVE = {k: re.compile("|".join(v["terms"]), re.IGNORECASE) for k, v in SENSITIVE_CLASSES.items()}


def sensitivity_flags(text: str, role: str) -> list[str]:
    """Sensitivity classes touched by this request that are outside the role's remit."""
    return [cls for cls, rx in _SENSITIVE.items()
            if rx.search(text) and role not in SENSITIVE_CLASSES[cls]["roles"]]


# --- Measurement ------------------------------------------------------------------

def _pr(df: pd.DataFrame, flagged: pd.Series, positive: set[str]) -> None:
    truth = df["category"].isin(positive)
    tp = int((flagged & truth).sum())
    fp = int((flagged & ~truth).sum())
    fn = int((~flagged & truth).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    print(f"  tp={tp} fp={fp} fn={fn}  precision={prec:.2f} recall={rec:.2f}")
    if fp:
        print("  false positives:")
        for _, r in df[flagged & ~truth].iterrows():
            print(f"    [{r.category}/{r.role}] {r.text[:110]}")
    if fn:
        print("  missed:")
        for _, r in df[~flagged & truth].iterrows():
            print(f"    [{r.role}] {r.text[:110]}")


def main() -> None:
    df = pd.read_csv(ERP_DATA)
    rule = df["text"].map(rule_category)

    print("=== Pattern rules ===")
    for cat in ("secret_seeking", "system_prompt_extraction"):
        print(f"\n{cat}:")
        _pr(df, rule == cat, {cat})
    print("\nany rule fired, all categories:")
    _pr(df, rule.notna(), {"secret_seeking", "system_prompt_extraction"})

    print("\n=== Role x sensitivity (text stand-in for the GraphQL check) ===")
    flags = [sensitivity_flags(t, r) for t, r in zip(df["text"], df["role"])]
    _pr(df, pd.Series([bool(f) for f in flags], index=df.index), {"data_snooping"})


if __name__ == "__main__":
    main()
