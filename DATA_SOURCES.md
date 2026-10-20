# Data sources

Licences verified 2026-09-20 from Hugging Face card metadata
(`https://huggingface.co/api/datasets/<name>` → `cardData.license`).

## Datasets used

| Source | Use | Licence | Commercial | Notes |
|---|---|---|---|---|
| deepset/prompt-injections | injection + benign | Apache-2.0 | Yes | ~660 rows, English + German |
| Lakera/gandalf_ignore_instructions | injection | MIT | Yes | Attacks against the Gandalf game |
| jackhhao/jailbreak-classification | jailbreak + benign | Apache-2.0 | Yes | Mostly DAN-style prompts |
| nvidia/Aegis-AI-Content-Safety-Dataset-2.0 | harmful + benign, with harm subcategories | CC-BY-4.0 | Yes, with attribution | Human-labelled. Prompts derived from Anthropic HH-RLHF. NVIDIA states PII was removed but some may remain. "REDACTED" rows (Suicide Detection samples) excluded by our loader. |
| dmilush/shieldlm-prompt-injection | multilingual prompt_injection (fr, es, de, pt) for the multilingual adapter, train split for training and test split for eval | MIT | Yes | Only the non-English direct injections are used; they originate in yanismiraoui/prompt_injections (Apache-2.0). Generic attacks, so labelled module none, intent off_topic, random role. Built by poc/build_multilingual.py |

## Datasets rejected

| Source | Reason |
|---|---|
| PKU-Alignment/BeaverTails | CC-BY-NC-4.0, non-commercial |
| lmsys/toxic-chat | CC-BY-NC-4.0, non-commercial |
| TrustAIRLab/in-the-wild-jailbreak-prompts | Scraped from real users' posts; avoid personal data |
| xTRam1/safe-guard-prompt-injection | No licence declared |
| allenai/wildjailbreak | Gated access |

## Machine translation

poc/build_multilingual.py translates only benign and data_snooping rows of our own synthetic
sets (data/erp_train.csv into data/erp_train_multi.csv, eval/erp_eval.csv into
eval/erp_eval_multi.csv). Train and eval are translated separately, so they never mix.

| Model | Licence | Language |
|---|---|---|
| Helsinki-NLP/opus-mt-en-fr | Apache-2.0 | French |
| Helsinki-NLP/opus-mt-en-es | Apache-2.0 | Spanish |
| Helsinki-NLP/opus-mt-en-de | CC-BY-4.0 | German |
| Helsinki-NLP/opus-mt-tc-big-en-pt | CC-BY-4.0 | Portuguese (European and Brazilian) |

Translated text reads more formally than real users type, and jailbreak, secret_seeking and
system_prompt_extraction have no non-English rows, so multilingual results for those
categories are not measured.

## Models

| Model | Licence | Notes |
|---|---|---|
| protectai/deberta-v3-base-prompt-injection-v2 | Apache-2.0 | Card states it does NOT detect jailbreak attacks and is English-only; also not recommended for scanning system prompts (false positives) |
| MoritzLaurer/deberta-v3-base-zeroshot-v2.0 | MIT | Zero-shot NLI classifier |
| fastino/GLiNER2.5-Decide | Apache-2.0 | English base for the ERP adapter |
| fastino/GLiNER2.5-multi-Decide | Apache-2.0 | Multilingual base (287M) for the multilingual ERP adapter |

## Notes

- No production or customer data is used in this POC. All prompts come from the public
  datasets above, so no personal data of our users is processed.
- Aegis requires attribution under CC-BY-4.0; keep this file with any published results.
- Non-commercial datasets are usable for learning only. Nothing licensed NC may be used
  to train or evaluate a model that ships in the product.
- Content warning: these datasets contain offensive, violent and sexual material by design.
