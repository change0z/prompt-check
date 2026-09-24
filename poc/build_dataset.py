import argparse
import re
from pathlib import Path

import pandas as pd
from datasets import load_dataset

OUT = Path(__file__).resolve().parent.parent / "data" / "prompts.parquet"
SEED = 42

def _frame(ds, split: str, text_col: str, source: str) -> pd.DataFrame:
    df = ds.to_pandas().rename(columns={text_col: "text"})
    df["split"] = split
    df["source"] = source
    df["harm_subcategory"] = None
    return df

def load_deepset() -> pd.DataFrame:
    # Apache 2.0 License. Label 1 = injection, 0 = benign. Mixed English/German.
    ds = load_dataset("deepset/prompt-injections")
    frames = [_frame(ds[s], s, "text", "deepset") for s in ("train", "test")]
    df = pd.concat(frames)
    df["category"] = df["label"].map({1: "prompt_injection", 0: "benign"})
    return df

def load_gandalf() -> pd.DataFrame:
    # MIT. Every row is an injection attempt against the Gandalf game.
    ds = load_dataset("Lakera/gandalf_ignore_instructions")
    frames = [
        _frame(ds["train"], "train", "text", "gandalf"),
        _frame(ds["validation"], "train", "text", "gandalf"),
        _frame(ds["test"], "test", "text", "gandalf"),
    ]
    df = pd.concat(frames)
    df["category"] = "prompt_injection"
    return df

def load_jailbreak() -> pd.DataFrame:
    # Apache-2.0. type = "jailbreak" | "benign".
    ds = load_dataset("jackhhao/jailbreak-classification")
    frames = [_frame(ds[s], s, "prompt", "jackhhao") for s in ("train", "test")]
    df = pd.concat(frames)
    df["category"] = df["type"]
    return df


def load_aegis() -> pd.DataFrame:
    # CC-BY-4.0. Human-labelled safe/unsafe prompts with harm categories.
    ds = load_dataset("nvidia/Aegis-AI-Content-Safety-Dataset-2.0")
    frames = [_frame(ds[s], s, "prompt", "aegis") for s in ("train", "test")]
    df = pd.concat(frames)
    # Redacted rows (Suicide Detection samples) only contain the placeholder "REDACTED".
    df = df[df["reconstruction_id_if_redacted"].isna()]
    df["category"] = df["prompt_label"].map({"unsafe": "harmful", "safe": "benign"})
    unsafe = df["category"] == "harmful"
    df.loc[unsafe, "harm_subcategory"] = df.loc[unsafe, "violated_categories"]
    return df

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def clean(df: pd.DataFrame) -> pd.DataFrame:
    CATEGORY_PRIORITY = {"jailbreak": 0, "prompt_injection": 1, "harmful": 2, "benign": 3}

    df = df[df["text"].notna() & df["category"].notna()].copy()
    df = df[df["text"].str.strip().str.len() > 0]
    df["norm"] = df["text"].map(_norm)

    # Same text labelled differently across sources: keep the most specific label.
    # e.g. Aegis re-uses DAN jailbreaks but labels their *content* (harmful/safe),
    # while jackhhao labels the *technique* (jailbreak), which is what we want.
    df["priority"] = df["category"].map(CATEGORY_PRIORITY)
    top = df.groupby("norm")["priority"].transform("min")
    df = df[df["priority"] == top].drop(columns="priority")

    # Prevent train/test leakage: anything in test is removed from train.
    test_texts = set(df.loc[df["split"] == "test", "norm"])
    df = df[(df["split"] == "test") | ~df["norm"].isin(test_texts)]

    return df.drop_duplicates(subset=["norm", "split"]).drop(columns="norm")

def balance(df: pd.DataFrame, train_cap: int, test_cap: int) -> pd.DataFrame:
    """Cap each category per split so no single source/class dominates."""
    parts = []
    for (split, _), group in df.groupby(["split", "category"]):
        cap = train_cap if split == "train" else test_cap
        parts.append(group.sample(n=min(cap, len(group)), random_state=SEED))
    return pd.concat(parts, ignore_index=True)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-per-class", type=int, default=2000)
    parser.add_argument("--test-per-class", type=int, default=150)
    args = parser.parse_args()

    raw = pd.concat([load_deepset(), load_gandalf(), load_jailbreak(), load_aegis()])
    df = balance(clean(raw), args.train_per_class, args.test_per_class)
    df = df[["text", "category", "harm_subcategory", "source", "split"]]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)

    print(f"Wrote {len(df)} rows to {OUT}\n")
    print(pd.crosstab([df["split"], df["category"]], df["source"], margins=True))


if __name__ == "__main__":
    main()