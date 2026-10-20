"""
Fine-tune fastino/GLiNER2.5-Decide (or its multilingual sibling) on ERP prompts with a
LoRA adapter.

The base model already scores 91.6 percent category accuracy on eval/erp_eval.csv with no
training, but module and intent are weak (0.71 and 0.46). Training targets those, plus
the two category errors that remain: benign prompts using security vocabulary in a
business sense, and data snooping that only shows up once the role is known.

Training data is one or more CSVs with the same columns as eval/erp_eval.csv:
    text, category, role, module, intent[, lang, notes]
written separately from the eval sets (see docs/gliner-training-data-guide.md). The eval
sets are never trained on: this script refuses the eval files themselves and drops any
training row whose normalised text also appears in one. Rows without a lang are English.

The model sees "User role: <role>. Request: <text>", so the role reaches the classifier
the same way it reaches Claude in evaluate_llm.py. evaluate.py imports model_input and
inference_schema from here, so training and evaluation always use the same schema.

Usage:
    uv run python poc/train_gliner.py                          # data/erp_train.csv
    uv run python poc/train_gliner.py --train data/my.csv --epochs 8
    uv run python poc/train_gliner.py --base-model fastino/GLiNER2.5-multi-Decide \
        --train data/erp_train.csv data/x3_*.csv --output models/gliner-erp-multi
    uv run python poc/evaluate.py --dataset erp --models gliner --adapter models/gliner-erp/best

Writes the adapter to <output>/{best,final} (a few MB, not the full model).
CPU only, matching the project's torch build. A few hundred rows train in minutes.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
ERP_DATA = ROOT / "eval" / "erp_eval.csv"
ERP_MULTI_DATA = ROOT / "eval" / "erp_eval_multi.csv"
EVAL_SETS = [ERP_DATA, ERP_MULTI_DATA]
TRAIN_DATA = ROOT / "data" / "erp_train.csv"
OUTPUT = ROOT / "models" / "gliner-erp"
BASE_MODEL = "fastino/GLiNER2.5-Decide"
MULTI_BASE_MODEL = "fastino/GLiNER2.5-multi-Decide"

# Label descriptions are part of the model input, so they must be identical at training
# and inference. Wording follows the taxonomy in evaluate_llm.py. "harmful" is only used
# for the public dataset, which has no ERP-specific categories.
CATEGORY_DESCRIPTIONS = {
    "benign": "An ordinary business request within the user's role, even if it uses words "
              "like ignore, override, admin or credentials in a business sense",
    "prompt_injection": "Tries to override the assistant's instructions, or asks it to treat "
                        "embedded content such as notes, emails or CSV cells as instructions",
    "jailbreak": "Uses a persona, role play or hypothetical framing to remove the "
                 "assistant's controls",
    "secret_seeking": "Asks for credentials, API keys, tokens, connection strings, "
                      "environment variables or infrastructure details",
    "system_prompt_extraction": "Asks for the assistant's own instructions, configuration, "
                                "hidden rules, tool definitions or schemas",
    "data_snooping": "Asks for data outside the user's role, or bulk personal or commercially "
                     "sensitive data with no business need",
    "harmful": "A request for harmful, illegal, hateful or dangerous content",
}
ERP_CATEGORIES = ["benign", "prompt_injection", "jailbreak", "secret_seeking",
                  "system_prompt_extraction", "data_snooping"]
# Same enums as evaluate_llm.py.
MODULES = ["hr", "finance", "inventory", "purchasing", "sales", "admin", "none"]
INTENTS = ["lookup", "aggregate", "export", "mutate", "explain", "troubleshoot",
           "follow_up", "off_topic"]
ROLES = ["hr_admin", "finance_clerk", "sales_rep", "purchasing_manager",
         "warehouse_operator", "system_admin"]
REQUIRED = ["text", "category", "role", "module", "intent"]


def model_input(text: str, role: str | None) -> str:
    return f"User role: {role}. Request: {text}" if role else text


def inference_schema(categories: list[str], with_usage: bool) -> dict:
    """The classify_text schema. with_usage adds the module and intent heads."""
    schema: dict = {"category": {"labels": {c: CATEGORY_DESCRIPTIONS[c] for c in categories}}}
    if with_usage:
        schema |= {"module": MODULES, "intent": INTENTS}
    return schema


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def load_training_csv(path: Path) -> pd.DataFrame:
    if any(path.resolve() == e.resolve() for e in EVAL_SETS):
        raise SystemExit(f"{path} is a held-out eval set and must not be trained on.")
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"{path} is missing columns: {missing}")
    df = df.dropna(subset=REQUIRED).copy()
    for col in REQUIRED[1:]:
        df[col] = df[col].str.strip()

    # Report every invalid row at once rather than failing on the first.
    allowed = {"category": ERP_CATEGORIES, "role": ROLES, "module": MODULES, "intent": INTENTS}
    errors = [f"  row {i + 2}: {col}={row[col]!r}"
              for i, row in df.iterrows() for col, ok in allowed.items() if row[col] not in ok]
    if errors:
        raise SystemExit("Labels outside the taxonomy:\n" + "\n".join(errors))

    # Leakage control, as in build_dataset.py: the eval sets win any overlap.
    eval_texts = set().union(*(set(pd.read_csv(e)["text"].map(normalise))
                               for e in EVAL_SETS if e.exists()))
    leaked = df["text"].map(normalise).isin(eval_texts)
    if leaked.any():
        print(f"dropping {leaked.sum()} rows of {path.name} that also appear in an eval set")
    df = df[~leaked]
    # Contrast pairs share text but differ in role and label, so dedupe on both.
    dupes = pd.DataFrame({"t": df["text"].map(normalise), "r": df["role"]}).duplicated()
    if dupes.any():
        print(f"dropping {dupes.sum()} duplicate training rows")
    return df[~dupes]


def to_examples(df: pd.DataFrame) -> list:
    from gliner2.training.data import Classification, InputExample

    descriptions = {c: CATEGORY_DESCRIPTIONS[c] for c in ERP_CATEGORIES}
    return [
        InputExample(
            text=model_input(row.text, row.role),
            classifications=[
                Classification(task="category", labels=ERP_CATEGORIES, true_label=row.category,
                               label_descriptions=descriptions),
                Classification(task="module", labels=MODULES, true_label=row.module),
                Classification(task="intent", labels=INTENTS, true_label=row.intent),
            ],
        )
        for row in df.itertuples()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train", type=Path, nargs="+", default=[TRAIN_DATA])
    parser.add_argument("--base-model", default=BASE_MODEL,
                        help=f"e.g. {MULTI_BASE_MODEL} for the multilingual model")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--val-frac", type=float, default=0.15,
                        help="share of the training CSV held back to pick the best epoch")
    parser.add_argument("--lora-r", type=int, default=16)
    args = parser.parse_args()

    df = pd.concat([load_training_csv(p) for p in args.train], ignore_index=True)
    df["lang"] = df["lang"].fillna("en") if "lang" in df.columns else "en"
    # Contrast pairs share text but differ in role, so dedupe across files on both.
    dupes = pd.DataFrame({"t": df["text"].map(normalise), "r": df["role"]}).duplicated()
    if dupes.any():
        print(f"dropping {dupes.sum()} rows duplicated across training files")
    df = df[~dupes]
    print(f"{len(df)} training rows\n")
    print(pd.crosstab(df["category"], df["role"], margins=True), "\n")
    print(pd.crosstab(df["category"], df["lang"], margins=True), "\n")

    # Stratify so every category reaches the validation split; fall back when a
    # category is too small to split.
    stratify = df["category"] if df["category"].value_counts().min() >= 2 else None
    train, val = train_test_split(df, test_size=args.val_frac, random_state=42, stratify=stratify)
    print(f"train={len(train)} val={len(val)}\n")

    from gliner2 import AutoExtractor
    from gliner2.training.trainer import ExtractorTrainer, TrainingConfig

    model = AutoExtractor.from_pretrained(args.base_model)
    config = TrainingConfig(
        output_dir=str(args.output),
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        eval_strategy="epoch",
        save_best=True,
        use_lora=True,
        lora_r=args.lora_r,
        lora_alpha=2.0 * args.lora_r,
        # CPU torch build: no pinned memory, and worker processes add nothing at this size.
        pin_memory=False,
        num_workers=0,
    )
    ExtractorTrainer(model, config).train(train_data=to_examples(train),
                                          eval_data=to_examples(val))

    meta = {
        "base_model": args.base_model,
        "train_csv": [str(p) for p in args.train],
        "rows": {"train": len(train), "val": len(val)},
        "category_counts": df["category"].value_counts().to_dict(),
        "lang_counts": df["lang"].value_counts().to_dict(),
        "args": {k: str(v) for k, v in vars(args).items()},
    }
    (args.output / "train_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\nadapter written to {args.output}/best and {args.output}/final")


if __name__ == "__main__":
    main()
