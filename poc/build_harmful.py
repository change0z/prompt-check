"""
Build training and eval rows for the "harmful" category from public safety data.

English rows come from the Aegis 2.0 harmful prompts already in data/prompts.parquet
(CC-BY-4.0); French, Spanish, German and Portuguese rows from the translated harmful
requests in usezn/zn-prompt-injection-bench (MIT and CC-BY-4.0 rows only). Each source's
train split feeds training and its test split feeds eval/erp_eval_harmful.csv, so the two
never mix. Rows are sampled across Aegis harm subcategories so no single kind dominates.

Left out, because they clash with the ERP taxonomy rather than teach "harmful":
- PII/Privacy prompts, which overlap with data_snooping and secret_seeking
- prompts whose only subcategories are profanity, unauthorised advice, politics or "other"

These are generic requests, not ERP tasks, so they get module "none", intent
"off_topic" and a random role. Without a matching set of harmless generic prompts the model
learns "not about the ERP means harmful", so the same number of safe Aegis prompts go in
as benign, machine-translated into the other four languages with OPUS-MT (see
build_multilingual.py). Only a third of them are kept for training (--safe-share): a
one-to-one mix made both adapters miss far more attacks, because the safe Aegis prompts
are often edgy themselves, while none at all made them block harmless chit-chat. The eval
file keeps all its harmless rows, to measure that mistake.

Usage:
    uv run python poc/build_harmful.py
Writes data/erp_train_harmful.csv and eval/erp_eval_harmful.csv.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pandas as pd

from train_gliner import ROLES, normalise

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "data" / "prompts.parquet"
OUT_TRAIN = ROOT / "data" / "erp_train_harmful.csv"
OUT_EVAL = ROOT / "eval" / "erp_eval_harmful.csv"
ZN = "usezn/zn-prompt-injection-bench"
ZN_HARMFUL_SOURCES = {"aegis_2.0", "hh_rlhf", "advbench", "jbb_behaviors"}
ZN_LICENCES = {"MIT", "CC-BY-4.0"}
LANGS = ["fr", "es", "de", "pt"]
NOT_HARMFUL_ALONE = {"Profanity", "Unauthorized Advice", "Political/Misinformation/Conspiracy",
                     "Other", "Needs Caution"}
COLUMNS = ["text", "category", "role", "module", "intent", "lang", "notes"]


def keep_aegis(sub: str) -> bool:
    parts = {p.strip() for p in str(sub).split(",")}
    return "PII/Privacy" not in parts and not parts <= NOT_HARMFUL_ALONE


def rows(texts: pd.Series, lang: pd.Series | str, note: str, rng: random.Random,
         category: str = "harmful") -> pd.DataFrame:
    return pd.DataFrame({"text": texts.values, "category": category,
                         "role": [rng.choice(ROLES) for _ in range(len(texts))],
                         "module": "none", "intent": "off_topic",
                         "lang": lang.values if isinstance(lang, pd.Series) else lang, "notes": note})


def aegis(split: str, n: int, rng: random.Random) -> pd.DataFrame:
    df = pd.read_parquet(PROMPTS)
    df = df[(df["category"] == "harmful") & (df["split"] == split) & df["harm_subcategory"].map(keep_aegis)]
    df = df[df["text"].str.len().between(10, 600)].drop_duplicates("text")
    # Round-robin over the first-listed subcategory so the rarer kinds are represented.
    groups = [g.sample(frac=1, random_state=rng.randrange(10**6))
              for _, g in df.groupby(df["harm_subcategory"].str.split(",").str[0])]
    picked = []
    while len(picked) < min(n, len(df)):
        for g in groups:
            if len(g) > len(picked) // len(groups):
                picked.append(g.iloc[len(picked) // len(groups)])
    sample = pd.DataFrame(picked[:n])
    return rows(sample["text"], "en", f"Aegis 2.0 {split} split ({PROMPTS.name})", rng)


def zn(split: str, per_lang: int | None, rng: random.Random) -> pd.DataFrame:
    from huggingface_hub import hf_hub_download

    df = pd.read_parquet(hf_hub_download(ZN, f"data/{split}.parquet", repo_type="dataset"))
    df = df[df["lang"].isin(LANGS) & (df["label"] == "ataque") & df["source"].isin(ZN_HARMFUL_SOURCES)
            & df["license"].isin(ZN_LICENCES)].drop_duplicates("text")
    if per_lang is not None:
        df = df.sample(frac=1, random_state=rng.randrange(10**6)).groupby("lang").head(per_lang)
    return rows(df["text"], df["lang"], f"translated harmful request from {ZN} {split} split", rng)


def safe(split: str, n: int, per_lang: int, rng: random.Random) -> pd.DataFrame:
    """Harmless Aegis prompts in English, plus translations of a separate sample of them
    into each other language."""
    from build_multilingual import translator

    df = pd.read_parquet(PROMPTS)
    df = df[(df["category"] == "benign") & (df["source"] == "aegis") & (df["split"] == split)]
    df = df[df["text"].str.len().between(10, 600)].drop_duplicates("text")
    pool = df.sample(frac=1, random_state=rng.randrange(10**6))["text"]
    frames = [rows(pool.iloc[:n], "en", f"safe Aegis 2.0 {split} prompt", rng, "benign")]
    for k, lang in enumerate(LANGS):
        src = pool.iloc[n + k * per_lang: n + (k + 1) * per_lang]
        print(f"translating {len(src)} safe {split} prompts into {lang}")
        frames.append(rows(pd.Series(translator(lang)(src.tolist())), lang,
                           f"safe Aegis 2.0 {split} prompt, machine translated (OPUS-MT)", rng, "benign"))
    return pd.concat(frames, ignore_index=True)


def thin_safe(df: pd.DataFrame, share: float, seed: int = 7) -> pd.DataFrame:
    """Keep every harmful row and the given share of each language's benign rows."""
    safe = df[df["category"] == "benign"].sample(frac=1, random_state=seed)
    keep = safe.groupby("lang").cumcount() < (safe.groupby("lang")["text"].transform("size") * share).astype(int)
    return pd.concat([df[df["category"] != "benign"], safe[keep]], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--en", type=int, default=150, help="English training rows")
    parser.add_argument("--per-lang", type=int, default=40, help="training rows per other language")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--safe-share", type=float, default=1 / 3,
                        help="share of the translated safe prompts kept for training")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    train = pd.concat([aegis("train", args.en, rng), zn("train", args.per_lang, rng),
                       safe("train", args.en, args.per_lang, rng)], ignore_index=True)
    evals = pd.concat([aegis("test", 100, rng), zn("test", None, rng),
                       safe("test", 40, 15, rng)], ignore_index=True)
    evals = evals[~evals["text"].map(normalise).isin(set(train["text"].map(normalise)))]
    train = thin_safe(train, args.safe_share)

    for path, df in [(OUT_TRAIN, train), (OUT_EVAL, evals)]:
        df[COLUMNS].to_csv(path, index=False, quoting=1)
        print(f"{path.relative_to(ROOT)}: {len(df)} rows")
        print(pd.crosstab(df["category"], df["lang"], margins=True))


if __name__ == "__main__":
    main()
