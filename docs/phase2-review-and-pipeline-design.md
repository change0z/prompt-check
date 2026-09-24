# Phase 2: Review of the Classifier Evaluation and a Simpler Pipeline Design

Author: generated with Claude (Fable 5.1) in Claude Code, for review by the project owner.
Date: 2026-09-20
Status: proposal. Reviews `docs/phase1-classifier-evaluation.md` against the pipeline's
actual purpose and proposes the design for the next iteration.

## 1. Summary

The phase 1 report is careful and its measurements are sound, but it evaluates the wrong
problem under the wrong constraint. It measures security detection with a per request
latency budget, for a pipeline whose purpose is post hoc usage categorisation with no user
facing latency at all. Once that is corrected, the local machine learning model is not
worth improving. It should be removed.

The replacement is three cheap layers, none of which needs training data:

1. **The generated GraphQL query**, parsed with `graphql-core`. This is the primary source
   for every usage question (module, entities, fields, read or write, scope) and for
   the data access checks. The main model has already turned the prompt into structured
   intent; the pipeline only has to read it. No text, no model, no latency.
2. **A short pattern list** for credential and system prompt requests. Auditable, free,
   and required anyway because the story names AWS key requests explicitly.
3. **One Claude call per prompt** with the user's role in context, returning a fixed enum
   of labels via structured outputs. Run concurrently with the main Bedrock call, so it
   adds no latency and the prompt text never has to sit in a queue.

Measured on the existing 190 row set, layers 1 and 2 stand-ins already reach 20 of 20
on credential and system prompt requests and 17 of 20 on data snooping with zero false
positives. Layer 3 is written and ready to run but needs credentials; see section 6.

## 2. What the report gets right

These findings hold and this design builds on them:

- **Precision on benign traffic is the governing metric**, and it is the one that fails
  under domain shift. The 10 percent false positive rate on realistic ERP prompts, with
  eight of twelve errors above 0.99 confidence, means ProtectAI cannot be tuned into
  shape by thresholding.
- **The public dataset measures dataset separability, not intent.** Section 4.2's feature
  inspection is the most useful part of the report. Nothing trained on that data should
  ship, and nothing further should be built on it.
- **The requirement decomposes into mechanisms with different characteristics.** Rules,
  a text signal and structured query telemetry. The report is right about the shape of
  the answer.
- **Data snooping needs the role and the entities.** The report's diagnosis is correct
  even though its conclusion, discussed below, overstates the case.

## 3. Where the report should be challenged

### 3.1 It optimises for latency the pipeline does not have

The whole model selection is framed by "per request service behaviour", CPU only Fargate
sizing, and 512 token limits. That framing comes from the original proposal's Phase 1,
a synchronous interceptor with a 100 ms budget that returns `is_safe`. That component is
prevention, and prevention is already handled by Bedrock Guardrails and the system prompt.
The pipeline being designed here runs after the prompt has been sent. Its constraints are
throughput, cost and data protection, not milliseconds.

This changes what is viable. A model call that takes a second is fine in an asynchronous
worker, and it is free in wall clock terms if it runs concurrently with the main model
call, which streams for several seconds anyway. The small local models were chosen to
fit a constraint that does not exist, and they are the source of the precision problem.

### 3.2 It evaluates security detection, but the objective is usage categorisation

The stated purpose is to learn what the feature is used for. The report's taxonomy has six
security categories and one bucket called benign, which is 63 percent of the traffic and
the part the business actually wants to understand. There is no usage taxonomy, no
module or intent label in the evaluation set, and no measurement of any classifier on
that task. The proposal's own analytics model, zero shot NLI over HR, Finance and
Inventory, was specified in `poc/evaluate.py` and never run.

The security categories still matter, because the story requires them, but they are the
secondary output of this pipeline and should be designed as such.

### 3.3 The main model has already done the classification

Every prompt that touches data produces a GraphQL query, written by the main model. That
query is a structured, machine readable statement of what the user asked for: operation
type, entity types, fields, filters and pagination arguments. It is exactly the
"module, entities, sensitive fields, volume" feature set the report says is needed, and
it is available without inference, without text and without a model.

The report places this in a later "GraphQL analytics phase" and treats the text
classifier as primary. That is backwards. The query is the primary source for usage
analytics and for data access checks. The text path is needed only for what the query
cannot express: prompts that produce no query (follow ups, troubleshooting, questions
about the product, off topic) and the attack technique categories.

### 3.4 "Data snooping is not a text classification problem" is overstated

The report says the discriminating information "is not present in the text". Reading the
twenty examples, the sensitive entity and the scope are almost always in the text:
"every employee's bank account", "Mara Venn's salary, home address and disciplinary
history", "all customers' credit limits". What the text lacks is the role, and the role
is a session fact that can be handed to whatever does the classification.

To test this, `poc/rules.py` implements a role x sensitivity table and applies it to the
prompt text as a stand-in for the GraphQL check. Result on the 190 rows:

| | Flagged | Missed | False positives |
| --- | --- | --- | --- |
| data_snooping (20) | 17 | 3 | 0 of 120 benign |

The three misses are "export the full customer database", "archive every invoice PDF
from five years" and "five years of customer order history". They are volume and scope
cases, which is precisely the dimension only the query and the result metadata provide.
So the report's mechanism is right, but the problem is more tractable than stated: the
role plus sensitivity classes catch the majority deterministically, and volume from the
query closes most of the rest.

Caveat on the number: the table was written after reading those twenty prompts. It
demonstrates that the logic works on this set, not that it generalises.

### 3.5 The rule layer is stronger than "most", but the number is fitted

Two regular expression lists in `poc/rules.py`, sixteen patterns for secrets and fifteen
for system prompt requests, catch all twenty relevant prompts with no false positives
among the 120 benign ones, including the admin prompts about credential expiry and
role permissions. As with 3.4, the patterns were written with the examples in view, so
treat the score as an upper bound. The real value of the layer is that it is free, it
is explainable to an auditor, and it is independent of the model layer, so an injection
that fools the classifier does not silently remove these labels.

### 3.6 Several recommended next steps are more work than the problem warrants

For a few thousand prompts a day categorised after the fact:

- **Red team exercise to 400 to 500 rows** (step 4). Useful eventually for narrowing
  confidence intervals, but not a precondition for building the pipeline.
- **Distillation into a small local model** (step 5). This adds a training pipeline, a
  model artefact to host and version, and a domain drift problem, in order to avoid a
  per prompt cost that is small. Section 5 gives the arithmetic.
- **Training a domain specific model** (conclusion 4). Requires labelled ERP prompts
  that do not exist and, under the data protection constraint, cannot easily be collected
  from production. Any route to them goes through a model labelling synthetic data,
  at which point the model can simply do the job.
- **Alerting units and per user rate thresholds** (step 6). This is an investigator
  queue design. The pipeline's output is aggregates and trends, not alerts. Per role and
  per tenant rates over a week are the reporting unit, and the systematic false positive
  problem that motivated step 6 disappears once the model that produces it is removed.

### 3.7 The proposal's queue design contradicts the classify and discard pattern

The proposal's Phase 2 pushes "the prompt, the LLM's response, and the generated GraphQL
query" onto SQS for background processing. The report endorses classify in flight and
discard, and lists queues as a leakage point, but does not resolve the contradiction.

The response is the larger problem. It contains the actual ERP data returned to the user,
which for HR and finance prompts is personal data of third parties. It should never
enter the analytics path at all. The prompt is a smaller issue but still text a user
wrote. The design in section 4 puts only derived features on the queue.

Two further points the report's data protection paragraph should carry forward:

- A second Bedrock call for classification is the same processor, same region and same
  contractual terms as the main call. It does not add a data recipient. It does add a
  processing purpose, which needs to be in the transparency notice and the DPIA.
- Bedrock model invocation logging must be off, or scoped to exclude the classification
  route, or its bucket is a retention point for raw prompts. The same check applies to
  Guardrail traces and application logs.

### 3.8 Smaller points

- The report never records a target false positive rate. Without one, "usable" is
  undefined. A number should be agreed before the next measurement; 2 percent on benign
  is a reasonable starting bar for an analytics feed.
- The synthetic benign prompts contain schema field names in camelCase
  (`availableQuantity`, `leadTimeDays`). Real users do not type field names. It makes
  the benign class easier for any lexical method and slightly flatters every result.
- Single label classification is imposed on multi label data. Section 4 keeps the
  security category single label for simplicity but records rule hits as a separate
  list, so a jailbreak that also asks for a connection string retains both facts.

## 4. Proposed design

### 4.1 Where classification runs

The interceptor already holds the prompt, the role and the session when it calls Bedrock.
It starts the classification call as a concurrent task at the same moment it starts the
main streaming call. When the main call finishes, the interceptor parses the generated
GraphQL query, joins the three sources into one record of labels and features, and
enqueues that record. The raw prompt and the response are dropped at the end of the
request, as they are today.

```
prompt + role ──┬──> Bedrock main call ──> response (to user) ──> GraphQL query
                │                                                     │
                ├──> rules (µs)                                       │
                │                                                     │
                └──> Bedrock classify call (concurrent) ──> labels    │
                                                              │       │
                                             record = join(labels, rules, query features)
                                                              │
                                                       SQS ──> worker ──> Postgres
```

Consequences:

- No added user latency. The classify call runs in the shadow of the main call.
- No text on the queue, in the worker or in the database. If the classify call fails,
  the record carries `category = unclassified` and the query features still land.
- The worker becomes trivial: validate, insert, aggregate. Floci and the local stack
  in the proposal are unchanged.

If the interceptor cannot be changed, the fallback is the proposal's queue with the
prompt on it, under KMS encryption, a retention of minutes, and no response. That is
acceptable but is a documented personal data at rest point; the concurrent design
avoids it.

### 4.2 Layer 1: GraphQL features

Parse the query with `graphql-core` and emit, with all argument literals stripped:

| Feature | Source | Use |
| --- | --- | --- |
| operation | query or mutation | read vs write usage |
| entities | root and nested selection types | module, usage by object |
| fields | selected leaf fields | sensitivity classes |
| sensitivity_classes | fields joined to a table owned by the schema team, e.g. `Employee.salary -> employee_pay` | data access reporting |
| role_mismatch | sensitivity_classes not in the role's remit | data snooping signal |
| scope | filters present, pagination limit, `all` style arguments | bulk vs targeted |
| record_count_bucket | from the resolver result metadata, bucketed (1, 2–10, 11–100, 100+) | volume, the missing dimension in 3.4 |
| authz_denied | whether the ERP's own authorisation rejected the query | the strongest snooping signal, already computed by the product |

Module is derived from entities, not from text. This makes the usage taxonomy exact by
construction for every prompt that produced a query.

### 4.3 Layer 2: rules

The two lists in `poc/rules.py`. Kept deliberately short and reviewed with the security
owner. Output is `rule_hits: list[str]`, recorded independently of the model's category.

### 4.4 Layer 3: one Claude call

Implemented in `poc/evaluate_llm.py` as an evaluation harness; the production call is the
same request. Design points:

- **Role in context.** The system prompt states the taxonomy and each role's remit. The
  user turn is `<role>…</role><prompt>…</prompt>`.
- **Fixed output.** Structured outputs constrain the response to three enums: category,
  module, intent. Nothing free form comes back, so the stored label cannot leak text.
- **Text as data.** The system prompt says instructions inside the prompt do not apply
  to the labeller and are themselves evidence of injection. A prompt that still fools
  the labeller produces a wrong analytics label, not a security event, and the rule
  layer and query features are unaffected.
- **Cached system prompt.** The taxonomy is stable, so it is served from cache on every
  call. The per prompt cost is the user turn plus about fifty output tokens.
- **Low effort.** Classification does not benefit from deep reasoning.
- **Model choice is a measurement, not an assumption.** Run the harness with
  `claude-opus-5`, `claude-sonnet-5` and `claude-haiku-4-5` and take the cheapest that
  meets the agreed false positive bar. Section 5 gives the cost range.

What is dropped: ProtectAI, the TF-IDF baseline, the zero shot NLI model, PyTorch and
Transformers from the production image, and the public dataset from anything but
reference.

### 4.5 The record

One row per prompt, no text:

```
ts, tenant_id, user_pseudonym (HMAC with a per tenant key), role,
module, intent, category, rule_hits[], classifier_model,
operation, entities[], sensitivity_classes[], role_mismatch, scope,
record_count_bucket, authz_denied, main_call_ms, classify_call_ms
```

The user pseudonym is the one identifier that keeps this personal data. Whether to keep
it, aggregate it away after a retention window, or drop it entirely is the anonymous
versus pseudonymous decision the report defers to the data protection document. The
pipeline should be built so that the aggregates it reports never need the pseudonym
after the retention window.

## 5. Cost

Estimate for 5,000 prompts a day, about 300 uncached input tokens per prompt, an 800
token cached system prompt and 50 output tokens, at Anthropic first party list prices.
Bedrock prices differ and should be read from the AWS pricing page before deciding.

| Model | Per day | Per month |
| --- | --- | --- |
| claude-opus-5 | about $16 | about $470 |
| claude-sonnet-5 | about $6 | about $190 |
| claude-haiku-4-5 | about $3 | about $95 |

Against this, the distillation route in the report costs an engineer's time to build and
maintain a training pipeline, plus Fargate capacity for a PyTorch image, and it inherits
domain drift. The per prompt cost is not the thing to optimise at this volume.

## 6. Evaluation plan

1. **Run the harness.** `uv run python poc/evaluate_llm.py --model <model>` for the three
   models, on the Claude API or with `--bedrock --region <region>`. It prints per
   category precision and recall, the confusion matrix, the false positive rate on
   benign, the module and intent distributions, latency and token usage, and writes
   `results/llm_<model>_erp.json`. It could not be run in this session because no
   credentials are present on the machine.
2. **Accept or reject against a stated bar.** Suggested: benign false positive rate at or
   below 2 percent, recall at or above 80 percent on injection and jailbreak, and the
   role x remit logic reproducing the deterministic results on data snooping.
3. **Usage labels are in the eval set.** `eval/erp_eval.csv` now carries `module` and
   `intent` columns for all 190 rows, assigned by hand in this session and worth a
   second pair of eyes. The harness scores both when the columns are present. This is
   the first time the pipeline's actual objective is measurable. Distribution:

   | intent | count | | module | count |
   | --- | --- | --- | --- | --- |
   | lookup | 97 | | hr | 31 |
   | export | 20 | | sales | 31 |
   | aggregate | 18 | | purchasing | 30 |
   | mutate | 16 | | finance | 27 |
   | off_topic | 14 | | inventory | 27 |
   | follow_up | 13 | | admin | 24 |
   | explain | 7 | | none | 20 |
   | troubleshoot | 5 | | | |
4. **Regression guard.** Keep `poc/rules.py` and the harness runnable in CI against the
   eval set, so a prompt or rule change that moves a number is visible.
5. **Only then** consider expanding the eval set with staff written prompts, and only if
   the confidence intervals matter to a decision.

## 7. Recommended next steps, replacing section 8 of the phase 1 report

1. Run the model measurement (section 6, step 1) and agree the false positive bar.
2. Ask the schema owners for the entity.field to sensitivity class table and the role
   remit table. These two tables are the core of the data access reporting.
3. Run the GraphQL feature extractor in `poc/graphql_features.py` against a sample of
   generated queries from the dev environment. The extractor is schema-free and works
   today; its sensitivity, remit and module tables are placeholders until step 2 is done.
4. Change the interceptor to run the classification call concurrently and enqueue the
   record in section 4.5. Remove the response from the queue payload.
5. Confirm Bedrock invocation logging, Guardrail traces and application logs do not
   retain the prompt on either call path.
6. Write the data protection document the phase 1 report defers, now with a concrete
   record schema to assess.

Dropped from the phase 1 list: fine tuning, distillation, alert thresholds per user, and
the red team expansion as a precondition.

## Appendix. Reproduction

```
uv sync
uv run python poc/rules.py                                # rules and role x sensitivity, no network
uv run python poc/graphql_features.py                     # query feature extraction on built-in examples
uv run python poc/evaluate_llm.py --model claude-sonnet-5 # needs ANTHROPIC_API_KEY or --bedrock; scores
                                                          # category, module and intent
```
