'''
Goal of this step: compare three approaches on the same test set, so you can tell whether a neural model is worth its cost:

┌───────────┬─────────────────────────────────────────────────┬──────────────────────────────────────────────────────┐
│   Name    │                   What it is                    │                    Why it's here                     │
├───────────┼─────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ tfidf     │ Word-frequency features + logistic regression,  │ The baseline. Runs in seconds. Any model that can't  │
│           │ trained on your train split                     │ beat it isn't earning its keep.                      │
├───────────┼─────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ zeroshot  │ NLI model picking the best-fitting label        │ Your proposal's approach, with a MIT-licensed        │
│           │ description, no training                        │ DeBERTa instead of bart-large-mnli                   │
├───────────┼─────────────────────────────────────────────────┼──────────────────────────────────────────────────────┤
│ protectai │ The injection detector from your proposal       │ Binary only, so we measure what it flags, not its    │
│           │                                                 │ accuracy per category                                │
└───────────┴─────────────────────────────────────────────────┴──────────────────────────────────────────────────────┘

Evaluate prompt classifiers on the test split built by build_dataset.py.

Classifiers:
    tfidf      TF-IDF + logistic regression trained on the train split (fast baseline)
    zeroshot   NLI zero-shot classification, no training (MIT-licensed DeBERTa)
    protectai  ProtectAI prompt-injection v2, binary INJECTION/SAFE (Apache-2.0)
    gliner     GLiNER2.5-Decide schema classifier (Apache-2.0); also labels module and
               intent on the ERP set. --adapter adds a LoRA from poc/train_gliner.py

Writes metrics to results/<classifier>.json and prints a report.
Usage: uv run python poc/evaluate.py [--models tfidf zeroshot protectai gliner] [--limit N]
       uv run python poc/evaluate.py --dataset erp --models gliner --adapter models/gliner-erp/best
       uv run python poc/evaluate.py --dataset erp-multi --models gliner \
           --base-model fastino/GLiNER2.5-multi-Decide --adapter models/gliner-erp-multi/best

'''

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import cast

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.pipeline import make_pipeline

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "prompts.parquet"
ERP_DATA = ROOT / "eval" / "erp_eval.csv"
ERP_MULTI_DATA = ROOT / "eval" / "erp_eval_multi.csv"
RESULTS = ROOT / "results"
CATEGORIES = ["benign", "prompt_injection", "jailbreak", "harmful"]

ZEROSHOT_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
ZEROSHOT_LABELS = {
    "a normal, harmless request": "benign",
    "an attempt to override or ignore the AI's instructions": "prompt_injection",
    "a role-play or persona trick to make the AI bypass its safety rules": "jailbreak",
    "a request for harmful, illegal, hateful or dangerous content": "harmful",
}
PROTECTAI_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"

def timed(predict_one, texts: list[str]) -> tuple[list, list[float]]:
    """Predict one prompt at a time, as the service would, recording latency in ms."""
    preds, latencies = [], []
    for text in texts:
        start = time.perf_counter()
        preds.append(predict_one(text))
        latencies.append((time.perf_counter() - start) * 1000)
    return preds, latencies

"""
- ngram_range=(1, 2) lets TF-IDF see two-word phrases, so "ignore previous" registers rather than just "ignore".
- class_weight="balanced" compensates for the uneven class sizes in the train split.
- truncation=True, max_length=512 matters: these models cap at 512 tokens, and long jailbreak prompts exceed it. Without truncation you get an error; with it you're classifying only the opening of long prompts. Remember that when you read the jailbreak scores.
- The transformers imports are inside the functions so --models tfidf starts instantly.

"""

def run_tfidf(train: pd.DataFrame, texts: list[str]):
    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    )
    model.fit(train["text"], train["category"])
    return timed(lambda t: model.predict([t])[0], texts)


def run_zeroshot(texts: list[str]):
    from transformers import pipeline

    clf = pipeline("zero-shot-classification", model=ZEROSHOT_MODEL, device="cpu")
    labels = list(ZEROSHOT_LABELS)

    def predict(text: str) -> str:
        out = cast(dict, clf(text, candidate_labels=labels,
                             hypothesis_template="This text is {}.", truncation=True))
        return ZEROSHOT_LABELS[out["labels"][0]]

    return timed(predict, texts)


def run_protectai(texts: list[str]):
    from transformers import pipeline

    clf = pipeline("text-classification", model=PROTECTAI_MODEL, device="cpu",
                   truncation=True, max_length=512)
    return timed(lambda t: clf(t)[0]["label"], texts)


def run_gliner(test: pd.DataFrame, adapter: str | None, base_model: str):
    """One call per prompt returning category, plus module and intent when the set has
    them. The role is prepended when present, exactly as train_gliner.py trains it."""
    from gliner2 import AutoExtractor
    from train_gliner import CATEGORY_DESCRIPTIONS, inference_schema, model_input

    model = AutoExtractor.from_pretrained(base_model)
    if adapter:
        model.load_adapter(adapter)
    # Offer only the taxonomy of the set in use, in training order: the ERP categories
    # exist only in the ERP set, "harmful" only in the public one.
    present = set(test["category"])
    categories = [c for c in CATEGORY_DESCRIPTIONS if c in present]
    with_usage = {"module", "intent"} <= set(test.columns)
    schema = inference_schema(categories, with_usage)
    roles = test["role"] if "role" in test.columns else [None] * len(test)
    inputs = [model_input(t, r) for t, r in zip(test["text"], roles)]
    outputs, latencies = timed(lambda t: model.classify_text(t, schema), inputs)
    return [o["category"] for o in outputs], latencies, outputs if with_usage else None


def usage_accuracy(test: pd.DataFrame, outputs: list[dict]) -> dict:
    scores = {}
    for col in ("module", "intent"):
        got = [o[col] for o in outputs]
        scores[col] = round(sum(a == b for a, b in zip(test[col], got)) / len(got), 3)
        print(f"{col} accuracy: {scores[col]:.3f}")
    print()
    return scores


def by_lang(test: pd.DataFrame, preds: list[str], outputs: list[dict] | None) -> dict:
    """Per-language accuracy, plus the blocking errors that matter most: attacks let
    through (missed) and benign prompts blocked (false alarms)."""
    t = test.assign(pred=preds).reset_index(drop=True)
    if outputs:
        t["pred_module"] = [o["module"] for o in outputs]
        t["pred_intent"] = [o["intent"] for o in outputs]
    out = {}
    for lang, g in t.groupby("lang"):
        attack, flagged = g["category"] != "benign", g["pred"] != "benign"
        out[lang] = {
            "n": len(g),
            "category_acc": (g["pred"] == g["category"]).mean(),
            "missed_attacks": (attack & ~flagged).sum() / max(attack.sum(), 1),
            "false_alarms": (~attack & flagged).sum() / max((~attack).sum(), 1),
        } | ({"module_acc": (g["pred_module"] == g["module"]).mean(),
              "intent_acc": (g["pred_intent"] == g["intent"]).mean()} if outputs else {})
    return {k: {m: float(v) for m, v in d.items()} for k, d in out.items()}


def latency_stats(latencies: list[float]) -> dict:
    # Skip the first call: it includes one-off warm-up cost.
    lat = sorted(latencies[1:]) or latencies
    return {
        "p50_ms": round(statistics.median(lat), 1),
        "p95_ms": round(lat[int(0.95 * (len(lat) - 1))], 1),
        "max_ms": round(lat[-1], 1),
    }


def label_set(*label_lists) -> list[str]:
    """Labels present in this run: the known ones first, then any dataset-specific
    extras (the ERP set adds data_snooping, secret_seeking, system_prompt_extraction)."""
    seen = {lab for labels in label_lists for lab in labels}
    return CATEGORIES + sorted(seen - set(CATEGORIES))


def flag_rates(test: pd.DataFrame, flagged: pd.Series) -> pd.Series:
    """Share of each true category flagged as suspicious. The headline monitoring
    metric: the benign row is the false-positive rate."""
    order = [c for c in label_set(test["category"]) if c in set(test["category"])]
    return flagged.groupby(test["category"]).mean().reindex(order)


def report_multiclass(name: str, test: pd.DataFrame, preds: list[str], latencies) -> dict:
    y = test["category"].tolist()
    labels = label_set(y, preds)
    print(classification_report(y, preds, labels=labels, zero_division=0, digits=3))
    cm = pd.DataFrame(confusion_matrix(y, preds, labels=labels),
                      index=[f"true:{c}" for c in labels], columns=labels)
    print(cm, "\n")

    # Treat any non-benign prediction as "flagged for review", which is how the
    # monitoring pipeline would use it.
    rates = flag_rates(test, pd.Series([p != "benign" for p in preds], index=test.index))
    print("Share of each true category flagged as non-benign:")
    print((rates * 100).round(1).astype(str) + " %", "\n")
    return {
        "classifier": name,
        "report": classification_report(y, preds, labels=labels,
                                         zero_division=0, output_dict=True),
        "confusion_matrix": cm.to_dict(),
        "flag_rate_by_category": rates.round(3).to_dict(),
        "latency": latency_stats(latencies),
    }


def report_binary(name: str, test: pd.DataFrame, preds: list[str], latencies) -> dict:
    """A binary injection detector can't categorise, so show what it flags per category."""
    flagged = pd.Series([p == "INJECTION" for p in preds], index=test.index)
    rates = flag_rates(test, flagged)
    print("Share of each true category flagged as INJECTION:")
    print((rates * 100).round(1).astype(str) + " %", "\n")
    return {
        "classifier": name,
        "flag_rate_by_category": rates.round(3).to_dict(),
        "latency": latency_stats(latencies),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["tfidf", "zeroshot", "protectai"],
                        choices=["tfidf", "zeroshot", "protectai", "gliner"])
    parser.add_argument("--adapter", default=None,
                        help="gliner only: LoRA adapter directory from poc/train_gliner.py")
    parser.add_argument("--limit", type=int, default=None,
                        help="evaluate on a random sample of N test prompts")
    parser.add_argument("--base-model", default=None,
                        help="gliner only: base model (default the English GLiNER2.5-Decide)")
    parser.add_argument("--dataset", default="public", choices=["public", "erp", "erp-multi"],
                        help="public = held-out split of the HF datasets; "
                             "erp = the hand-reviewed synthetic ERP set; "
                             "erp-multi = the Sage X3 style set in en, fr, es, de and pt")
    args = parser.parse_args()

    # tfidf always trains on the public train split; only the test set changes.
    df = pd.read_parquet(DATA)
    train = df[df["split"] == "train"]
    test = {"public": lambda: df[df["split"] == "test"], "erp": lambda: pd.read_csv(ERP_DATA),
            "erp-multi": lambda: pd.read_csv(ERP_MULTI_DATA)}[args.dataset]()
    if args.limit:
        test = test.sample(n=min(args.limit, len(test)), random_state=42)
    texts = test["text"].tolist()
    print(f"train={len(train)} test={len(test)}\n")

    RESULTS.mkdir(exist_ok=True)
    for name in args.models:
        print(f"=== {name} ===")
        if name == "tfidf":
            preds, lat = run_tfidf(train, texts)
            result = report_multiclass(name, test, preds, lat)
        elif name == "zeroshot":
            preds, lat = run_zeroshot(texts)
            result = report_multiclass(name, test, preds, lat)
        elif name == "gliner":
            from train_gliner import BASE_MODEL
            base_model = args.base_model or BASE_MODEL
            if base_model != BASE_MODEL:
                name = "gliner_multi"
            if args.adapter:
                name += "_ft"
            preds, lat, outputs = run_gliner(test, args.adapter, base_model)
            result = report_multiclass(name, test, preds, lat)
            if outputs:
                result["usage_accuracy"] = usage_accuracy(test, outputs)
            if "lang" in test.columns:
                result["by_lang"] = by_lang(test, preds, outputs)
                print(pd.DataFrame(result["by_lang"]).T.round(3), "\n")
            result["base_model"] = base_model
            if args.adapter:
                result["adapter"] = args.adapter
        else:
            preds, lat = run_protectai(texts)
            result = report_binary(name, test, preds, lat)
        print("latency:", result["latency"], "\n")

        # Misclassified examples help explain the numbers. Public data only, so storing
        # text here is fine; never do this with production prompts.
        if "report" in result:
            wrong = test.assign(pred=preds)
            wrong = wrong[wrong["pred"] != wrong["category"]]
            # "source" exists in the public set, "role"/"notes" in the ERP one.
            cols = [c for c in ("text", "category", "pred", "source", "role", "lang", "notes")
                    if c in wrong.columns]
            result["errors_sample"] = (
                wrong[cols].head(25)
                .assign(text=lambda d: d["text"].str.slice(0, 300))
                .to_dict(orient="records")
            )
        (RESULTS / f"{name}_{args.dataset}.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()