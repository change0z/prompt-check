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

## Datasets rejected

| Source | Reason |
|---|---|
| PKU-Alignment/BeaverTails | CC-BY-NC-4.0, non-commercial |
| lmsys/toxic-chat | CC-BY-NC-4.0, non-commercial |
| TrustAIRLab/in-the-wild-jailbreak-prompts | Scraped from real users' posts; avoid personal data |
| xTRam1/safe-guard-prompt-injection | No licence declared |
| allenai/wildjailbreak | Gated access |

## Models

| Model | Licence | Notes |
|---|---|---|
| protectai/deberta-v3-base-prompt-injection-v2 | Apache-2.0 | Card states it does NOT detect jailbreak attacks and is English-only; also not recommended for scanning system prompts (false positives) |
| MoritzLaurer/deberta-v3-base-zeroshot-v2.0 | MIT | Zero-shot NLI classifier |

## Notes

- No production or customer data is used in this POC. All prompts come from the public
  datasets above, so no personal data of our users is processed.
- Aegis requires attribution under CC-BY-4.0; keep this file with any published results.
- Non-commercial datasets are usable for learning only. Nothing licensed NC may be used
  to train or evaluate a model that ships in the product.
- Content warning: these datasets contain offensive, violent and sexual material by design.
