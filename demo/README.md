# promptcheck demo

A public one-page web app where visitors write prompts for an ERP assistant and see how
the classifier that guards it judges them.
Each round assigns a random ERP role, which the models see, because data snooping
depends on who is asking. The base GLiNER2.5-Decide model and the fine-tuned adapter in
`models/gliner-erp/best` judge the prompt side by side. The player then votes on whether
it should have been blocked, and the scoreboard counts how often each model agreed.
Every submission and vote is stored for later labelling and retraining.

## Run locally

```
uv run --group demo uvicorn demo.app:app --reload
```

Open http://localhost:8000. Submissions go to `demo_data/prompts.db`.

Or run the same image that Railway will run:

```
docker build -t prompt-check-demo .
docker run -p 8000:8000 -e ADMIN_TOKEN=change-me -v "$PWD/demo_data:/data" prompt-check-demo
```

## Deploy on Railway

Railway builds from the `Dockerfile`, and `railway.json` sets the health check.

1. Commit and push everything the image copies: `Dockerfile`, `railway.json`,
   `.dockerignore`, `demo/`, `poc/train_gliner.py`, `models/gliner-erp/best/`,
   `pyproject.toml` and `uv.lock`.
2. In Railway, choose New Project, then Deploy from GitHub repo, and pick this repo
   and branch.
3. On the service, add a volume with mount path `/data`. Without it, prompts are lost
   on every redeploy.
4. Under Variables, set `ADMIN_TOKEN` to a long random string, for example the output
   of `openssl rand -hex 24`.
5. Under Settings, then Networking, generate a public domain and share that link.

Alternatively, `railway up` from the repo root uploads the working directory without
going through GitHub.

**Resources.** The service needs about 3 GB of memory: it settles at 2.5 GB and peaks
higher while loading. Startup takes 10 to 30 seconds. Railway bills memory by the
minute, so an always-on service costs noticeably more than a small web app. Turning on
app sleeping (serverless) in the service settings stops billing while idle, at the cost
of a slow first request after a quiet period.

**Volume note.** SQLite on a volume supports only one replica, which is what
`railway.json` sets.

## Collect the prompts

```
curl -o demo_prompts.csv "https://<your-domain>/admin/export.csv?token=<ADMIN_TOKEN>"
```

The export is disabled when `ADMIN_TOKEN` is unset. Columns: timestamp, prompt, role,
the player's vote (`block`, `allow`, or empty if they did not vote), and both models'
category, confidence, module and intent. The last column, `attempt`, holds the stated
goal from rounds played before the vote replaced it, and is empty for newer rows.

## Feed them back into training

The export is not training data yet. The vote says whether to block, not which
category applies, and players sometimes vote wrongly. For each row worth keeping:

1. Set `category`, `module` and `intent` using the rules in
   `docs/gliner-training-data-guide.md`, and add a short `notes` reason.
2. Append it to `data/erp_train.csv` with the columns `text, category, role, module,
   intent, notes`.
3. Never add demo prompts to `eval/erp_eval.csv`, and check that none paraphrase an
   eval row.

Prompts where the fine-tuned model disagreed with the vote are the most valuable ones
to label.

## Limits

- Prompts are capped at 2,000 characters, and each client can submit 10 per minute.
- Requests are judged one at a time. Each takes about 0.5 seconds, so a room of people
  submitting together will queue briefly.
- There is no login. Anyone with the link can play, and the page warns players not to
  paste real data.
