"""
Turn a demo export (GET /admin/export.csv) into candidate training rows for review.

Players only vote block or allow, so a vote alone does not give a category, module or
intent. This script proposes labels and marks how sure it is:

    auto          vote allow, or vote block with the fine-tuned models naming the same
                  attack category; module and intent come from where those models agree
                  (prompts older than the multilingual model use the English one alone)
    needs_review  everything else: unvoted rows, block votes where the models disagree or
                  both said benign, and rows whose module or intent the models disagree on

Nothing here is trained on directly. Review the needs_review rows (and spot-check the
auto ones), then pass the reviewed file to train_gliner.py with --train. Rows that
duplicate each other, a training file or an eval set are dropped, so the eval sets stay
held out.

Usage:
    uv run python poc/review_demo_prompts.py data/demo_prompts_2026-10-21.csv
Writes data/demo_candidates.csv next to the export and prints a summary.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from train_gliner import (ERP_CATEGORIES, EVAL_SETS, INTENTS, MODULES, ROLES, TRAIN_DATA,
                          normalise)

ROOT = Path(__file__).resolve().parent.parent
TRAIN_FILES = [TRAIN_DATA, ROOT / "data" / "erp_train_multi.csv"]
# Hoot's model first: the multilingual adapter, then the English one.
FINE_TUNED = ["mft", "ft"]
# A few common words per language; enough to tag short chat prompts for the summary.
STOPWORDS = {
    "fr": {"le", "la", "les", "des", "est", "pour", "une", "du", "je", "pas", "et", "de"},
    "es": {"el", "la", "los", "las", "para", "una", "por", "es", "que", "del", "y", "de"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ich", "für", "mit", "den", "ein"},
    "pt": {"o", "os", "as", "para", "uma", "não", "é", "do", "da", "dos", "que", "de"},
}


def guess_lang(text: str) -> str:
    words = set(normalise(text).replace("?", " ").replace(",", " ").split())
    hits = {lang: len(words & sw) for lang, sw in STOPWORDS.items()}
    lang, n = max(hits.items(), key=lambda kv: kv[1])
    return lang if n >= 2 else "en"


def agreed(row: pd.Series, field: str) -> str | None:
    """The label the fine-tuned models gave, or None if they differ. Rows from before the
    multilingual model existed have only the English verdict, which is used alone."""
    values = [v for v in (row.get(f"{m}_{field}") for m in FINE_TUNED) if not pd.isna(v)]
    return values[0] if values and len(set(values)) == 1 else None


def propose(row: pd.Series) -> pd.Series:
    reasons = []
    if row["vote"] == "allow":
        category = "benign"
    elif row["vote"] == "block":
        category = agreed(row, "category")
        if category in (None, "benign"):
            reasons.append("block vote but models disagree on the attack type"
                           if category is None else "block vote but the models said benign")
            # Offer whichever model named an attack; if none did, leave it for the reviewer.
            attacks = [row.get(f"{m}_category") for m in FINE_TUNED if row.get(f"{m}_category") not in (None, "benign")]
            category = next((c for c in attacks if not pd.isna(c)), "")
    else:
        reasons.append("no vote")
        category = row.get("mft_category")
    module, intent = agreed(row, "module"), agreed(row, "intent")
    if module is None or intent is None:
        reasons.append("models disagree on module or intent")
    return pd.Series({
        "category": category if category in ERP_CATEGORIES else "",
        "module": module or row.get("mft_module") or row.get("ft_module"),
        "intent": intent or row.get("mft_intent") or row.get("ft_intent"),
        "status": "needs_review" if reasons else "auto",
        "notes": "; ".join(reasons) or f"from demo vote={row['vote']}",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "demo_candidates.csv")
    args = parser.parse_args()

    raw = pd.read_csv(args.export)
    print(f"{len(raw)} prompts in the export, {raw['vote'].notna().sum()} voted\n")

    df = raw[raw["role"].isin(ROLES) & raw["text"].notna()].copy()
    df["norm"] = df["text"].map(normalise)
    # Players often resubmit or tweak a prompt; keep the latest vote for each text and role.
    df = df.sort_values("created_at").drop_duplicates(["norm", "role"], keep="last")
    print(f"{len(raw) - len(df)} duplicate or invalid rows dropped")

    held_out = set().union(*(set(pd.read_csv(e)["text"].map(normalise)) for e in EVAL_SETS if e.exists()))
    trained = set().union(*(set(pd.read_csv(t)["text"].map(normalise)) for t in TRAIN_FILES if t.exists()))
    in_eval, in_train = df["norm"].isin(held_out), df["norm"].isin(trained)
    print(f"{in_eval.sum()} match an eval set and {in_train.sum()} match training data; dropped\n")
    df = df[~in_eval & ~in_train]

    out = pd.concat([df[["text", "role"]], df.apply(propose, axis=1)], axis=1)
    out["lang"] = df["text"].map(guess_lang)
    out = out[["text", "category", "role", "module", "intent", "lang", "notes", "status"]]
    bad = ~out["category"].isin(ERP_CATEGORIES) | ~out["module"].isin(MODULES) | ~out["intent"].isin(INTENTS)
    out.loc[bad, "status"] = "needs_review"
    args.out.parent.mkdir(exist_ok=True)
    out.to_csv(args.out, index=False, quoting=1)

    print(f"{len(out)} candidate rows written to {args.out}")
    print(pd.crosstab(out["category"], out["status"], margins=True), "\n")
    print(pd.crosstab(out["lang"], out["status"], margins=True), "\n")
    voted = raw[raw["vote"].notna()]
    for m in ["base", "ft", "mbase", "mft"]:
        col = f"{m}_category"
        if col in voted:
            v = voted[voted[col].notna()]
            agree = ((v[col] != "benign") == (v["vote"] == "block")).mean() if len(v) else float("nan")
            print(f"{m:>6}: agrees with players on {agree:.0%} of {len(v)} voted prompts")


if __name__ == "__main__":
    main()
