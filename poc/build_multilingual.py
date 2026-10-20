"""
Build the multilingual training and eval sets for the GLiNER2.5-multi-Decide adapter.

Two sources, because no public dataset covers our ERP taxonomy outside English:

1. Machine translation of our own benign and data_snooping rows into French, Spanish,
   German and Portuguese with open OPUS-MT models (run locally). These are ordinary
   business requests, so they teach module and intent in each language and keep the
   model from learning "not English means attack". Training rows are translated from
   data/erp_train.csv and eval rows from eval/erp_eval.csv, so the two never mix.
   Contrast pairs share text, so a pair is translated once and keeps both its rows.
2. Direct prompt injections in the same four languages from dmilush/shieldlm-prompt-injection
   (MIT; these rows originate in yanismiraoui/prompt_injections, Apache-2.0). Its train
   split feeds training and its test split feeds eval, minus any text seen in training.
   They are generic attacks, not ERP requests, so they get module "none", intent
   "off_topic" and a random role.

Jailbreak, secret_seeking and system_prompt_extraction have no multilingual source, so
they stay English only. See DATA_SOURCES.md.

Usage:
    uv run python poc/build_multilingual.py
Writes data/erp_train_multi.csv and eval/erp_eval_multi.csv.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pandas as pd

from train_gliner import ERP_DATA, ERP_MULTI_DATA, ROLES, TRAIN_DATA, normalise

ROOT = Path(__file__).resolve().parent.parent
OUT_TRAIN = ROOT / "data" / "erp_train_multi.csv"
SHIELDLM = "dmilush/shieldlm-prompt-injection"
LANGS = ["fr", "es", "de", "pt"]
# OPUS-MT model per language, and the target token where the model needs one. Portuguese
# alternates European (por) and Brazilian (pob) variants.
MT_MODELS = {"fr": "Helsinki-NLP/opus-mt-en-fr", "es": "Helsinki-NLP/opus-mt-en-es",
             "de": "Helsinki-NLP/opus-mt-en-de", "pt": "Helsinki-NLP/opus-mt-tc-big-en-pt"}
TRANSLATED = ["benign", "data_snooping"]
COLUMNS = ["text", "category", "role", "module", "intent", "lang", "notes"]


def translator(lang: str):
    from transformers import MarianMTModel, MarianTokenizer

    tok = MarianTokenizer.from_pretrained(MT_MODELS[lang])
    model = MarianMTModel.from_pretrained(MT_MODELS[lang]).eval()

    def translate(texts: list[str]) -> list[str]:
        import torch

        # Translate line by line so pasted multi-line content keeps its shape.
        lines = [(i, line) for i, t in enumerate(texts) for line in str(t).split("\n")]
        srcs = [line for _, line in lines]
        if lang == "pt":
            srcs = [(">>por<< " if i % 2 else ">>pob<< ") + s for i, s in enumerate(srcs)]
        out = []
        for b in range(0, len(srcs), 16):
            batch = tok(srcs[b:b + 16], return_tensors="pt", padding=True, truncation=True)
            with torch.no_grad():
                gen = model.generate(**batch, max_new_tokens=400, num_beams=4)
            out += tok.batch_decode(gen, skip_special_tokens=True)
        joined = [[] for _ in texts]
        for (i, src), tgt in zip(lines, out):
            joined[i].append(tgt if src.strip() else "")
        return ["\n".join(parts) for parts in joined]

    return translate


def translated_rows(df: pd.DataFrame, per_lang: int | None, source: str, seed: int) -> pd.DataFrame:
    """Translate the benign and data_snooping rows. With per_lang, each language gets its
    own random sample of texts, so together the languages cover more of the source."""
    pool = df[df["category"].isin(TRANSLATED)]
    texts = pool["text"].drop_duplicates().tolist()
    frames = []
    for n, lang in enumerate(LANGS):
        pick = texts if per_lang is None else random.Random(seed + n).sample(texts, min(per_lang, len(texts)))
        print(f"translating {len(pick)} {source} texts into {lang}")
        mapping = dict(zip(pick, translator(lang)(pick)))
        rows = pool[pool["text"].isin(mapping)].copy()
        rows["notes"] = rows["notes"].fillna("").map(lambda s: f"{s}; machine translated from {source} (OPUS-MT)".lstrip("; "))
        rows["text"] = rows["text"].map(mapping)
        rows["lang"] = lang
        frames.append(rows)
    return pd.concat(frames, ignore_index=True)[COLUMNS]


def injection_rows(split: str, seed: int, exclude: set[str]) -> pd.DataFrame:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(SHIELDLM, f"data/{split}-00000-of-00001.parquet", repo_type="dataset")
    df = pd.read_parquet(path)
    df = df[df["language"].isin(LANGS) & (df["label_category"] == "direct_injection")]
    df = df[~df["text"].map(normalise).isin(exclude)].drop_duplicates("text")
    rng = random.Random(seed)
    return pd.DataFrame({
        "text": df["text"], "category": "prompt_injection",
        "role": [rng.choice(ROLES) for _ in range(len(df))],
        "module": "none", "intent": "off_topic", "lang": df["language"],
        "notes": f"generic direct injection from {SHIELDLM} {split} split",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--per-lang", type=int, default=120,
                        help="translated training texts per language (eval translates all)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    train_src, eval_src = pd.read_csv(TRAIN_DATA), pd.read_csv(ERP_DATA)
    train_inj = injection_rows("train", args.seed, set())
    train = pd.concat([translated_rows(train_src, args.per_lang, TRAIN_DATA.name, args.seed), train_inj],
                      ignore_index=True)

    # Eval: everything translatable, plus test-split injections never seen in training.
    seen = set(train_inj["text"].map(normalise))
    evals = pd.concat([translated_rows(eval_src, None, ERP_DATA.name, args.seed),
                       injection_rows("test", args.seed + 1, seen)], ignore_index=True)
    # The English eval rows are kept too, so per-language results share one file.
    evals = pd.concat([eval_src.assign(lang="en").reindex(columns=COLUMNS), evals], ignore_index=True)

    OUT_TRAIN.parent.mkdir(exist_ok=True)
    train.to_csv(OUT_TRAIN, index=False, quoting=1)
    evals.to_csv(ERP_MULTI_DATA, index=False, quoting=1)
    for name, df in [(OUT_TRAIN, train), (ERP_MULTI_DATA, evals)]:
        print(f"\n{name.relative_to(ROOT)}: {len(df)} rows")
        print(pd.crosstab(df["category"], df["lang"], margins=True))


if __name__ == "__main__":
    main()
