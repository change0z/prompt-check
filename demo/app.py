"""
promptcheck demo: visitors get a random ERP role, submit a prompt, and four classifiers
judge it side by side: the English GLiNER2.5-Decide and the multilingual
GLiNER2.5-multi-Decide, each with and without our fine-tuned adapter. The player then
votes on whether the prompt should have been blocked. Every submission and vote is stored
so it can be labelled and added to the training set later.

Each base model and its adapter share one copy of the weights. The adapter is wrapped with
PEFT and switched off for the base model's turn, which reproduces a separately loaded base
model exactly while using half the memory. Switching changes shared model state, so
inference runs one request at a time behind a lock.

Run locally:
    uv run --group demo uvicorn demo.app:app --reload
Environment:
    ADAPTER_PATH        English LoRA adapter (default models/gliner-erp/best)
    MULTI_ADAPTER_PATH  multilingual LoRA adapter (default models/gliner-erp-multi/best)
    DATA_DIR       where prompts.db lives (default demo_data/; mount a volume here)
    ADMIN_TOKEN    enables GET /admin/export.csv?token=...; export is off when unset
"""

from __future__ import annotations

import csv
import io
import os
import secrets
import sqlite3
import sys
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poc"))
# Same input format and schema as training and evaluation, so the demo scores exactly
# what the eval measured.
from train_gliner import (BASE_MODEL, ERP_CATEGORIES, MULTI_BASE_MODEL, ROLES,  # noqa: E402
                          inference_schema, model_input)

ADAPTER_PATH = Path(os.environ.get("ADAPTER_PATH", ROOT / "models" / "gliner-erp" / "best"))
MULTI_ADAPTER_PATH = Path(os.environ.get("MULTI_ADAPTER_PATH",
                                         ROOT / "models" / "gliner-erp-multi" / "best"))
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "demo_data"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
MAX_CHARS = 2000
RATE_LIMIT = 10          # submissions per client per window
RATE_WINDOW_S = 60
VOTES = ("block", "allow")   # the player's answer to "should this have been blocked?"

SCHEMA = inference_schema(ERP_CATEGORIES, with_usage=True)
# Contestant id -> (base model, adapter or None). The ids prefix the stored columns.
CONTESTANTS = {"base": (BASE_MODEL, None), "ft": (BASE_MODEL, ADAPTER_PATH),
               "mbase": (MULTI_BASE_MODEL, None), "mft": (MULTI_BASE_MODEL, MULTI_ADAPTER_PATH)}
FIELDS = ["category", "confidence", "module", "intent"]
COLUMNS = ["created_at", "text", "role", "vote"] + [f"{c}_{f}" for c in CONTESTANTS for f in FIELDS]
# Kept so databases from the first version still load. "attempt" was the player's stated
# goal, chosen before seeing the verdicts; new rows leave it empty.
LEGACY_COLUMNS = ["attempt"]

state: dict = {}
model_lock = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA_DIR / "prompts.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {", ".join(f"{c} {'REAL' if c.endswith('confidence') else 'TEXT'}" for c in COLUMNS)})""")
        # Add columns introduced after the table was first created.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(submissions)")}
        for col in COLUMNS + LEGACY_COLUMNS + ["vote_key"]:
            if col not in have:
                kind = "REAL" if col.endswith("confidence") else "TEXT"
                conn.execute(f"ALTER TABLE submissions ADD COLUMN {col} {kind}")


def load_models() -> None:
    from gliner2 import AutoExtractor
    from peft import PeftModel

    for name, adapter in {BASE_MODEL: ADAPTER_PATH, MULTI_BASE_MODEL: MULTI_ADAPTER_PATH}.items():
        base = AutoExtractor.from_pretrained(name)
        peft_model = PeftModel.from_pretrained(base, str(adapter))
        peft_model.eval()
        state[name] = (base, peft_model)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    load_models()
    yield


app = FastAPI(title="promptcheck", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
# Fonts are self-hosted so visitors' browsers make no third-party requests.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Battle(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    role: str


class Vote(BaseModel):
    id: int
    key: str
    vote: str


def classify(text: str, role: str) -> dict:
    x = model_input(text, role)
    flat = lambda o: {k: o[k]["label"] for k in ("category", "module", "intent")} | {
        "confidence": round(o["category"]["confidence"], 4)}
    out = {}
    with model_lock:
        for cid, (name, adapter) in CONTESTANTS.items():
            extractor, peft_model = state[name]
            if adapter:
                out[cid] = flat(extractor.classify_text(x, SCHEMA, include_confidence=True))
            else:
                with peft_model.disable_adapter():
                    out[cid] = flat(extractor.classify_text(x, SCHEMA, include_confidence=True))
    return out


_hits: dict[str, deque] = defaultdict(deque)


def check_rate(request: Request) -> None:
    # Behind Railway's proxy the client address is the first X-Forwarded-For entry.
    fwd = request.headers.get("x-forwarded-for", "")
    client = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    now, hits = time.monotonic(), _hits[client]
    while hits and now - hits[0] > RATE_WINDOW_S:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        raise HTTPException(429, "Slow down a little: try again in a minute.")
    hits.append(now)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": MULTI_BASE_MODEL in state}


@app.get("/api/options")
def options():
    return {"roles": ROLES, "max_chars": MAX_CHARS}


@app.post("/api/battle")
async def battle(body: Battle, request: Request):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Prompt is empty.")
    check_rate(request)

    result = await run_in_threadpool(classify, text, body.role)
    row = [datetime.now(timezone.utc).isoformat(timespec="seconds"), text, body.role, None]
    row += [result[c][f] for c in CONTESTANTS for f in FIELDS]
    # The key lets only this player's browser vote on this submission.
    key = secrets.token_urlsafe(16)
    with db() as conn:
        cur = conn.execute(f"INSERT INTO submissions ({', '.join(COLUMNS)}, vote_key) "
                           f"VALUES ({', '.join('?' * (len(COLUMNS) + 1))})", row + [key])
    return result | {"id": cur.lastrowid, "key": key, "stats": stats()}


@app.post("/api/vote")
def vote(body: Vote):
    """Record whether the player thinks the prompt should have been blocked. A player
    may change their mind, so a later vote replaces an earlier one."""
    if body.vote not in VOTES:
        raise HTTPException(422, f"vote must be one of {list(VOTES)}")
    with db() as conn:
        updated = conn.execute("UPDATE submissions SET vote = ? WHERE id = ? AND vote_key = ?",
                               (body.vote, body.id, body.key)).rowcount
    if not updated:
        raise HTTPException(404, "Unknown submission.")
    return {"stats": stats()}


@app.get("/api/stats")
def stats():
    """Scored against the players' votes, cast after seeing the verdicts. Should block:
    how often each model flagged a prompt players said should be blocked. Fine: how often
    each model flagged a prompt players said was fine. Unvoted prompts are not scored.
    Rows from before a contestant existed have no verdict for it and are not counted."""
    per = ", ".join(
        f"SUM(vote = 'block' AND {c}_category != 'benign') AS {c}_caught, "
        f"SUM(vote = 'allow' AND {c}_category != 'benign') AS {c}_false_alarms, "
        f"SUM(vote = 'block' AND {c}_category IS NOT NULL) AS {c}_should_block, "
        f"SUM(vote = 'allow' AND {c}_category IS NOT NULL) AS {c}_fine" for c in CONTESTANTS)
    with db() as conn:
        r = conn.execute(f"""SELECT COUNT(*) AS total, SUM(vote = 'block') AS should_block,
            SUM(vote = 'allow') AS fine, {per} FROM submissions""").fetchone()
        # Role by verdict counts for the heatmap. Hoot speaks for the multilingual adapter;
        # rows from before it existed fall back to the English adapter's verdict.
        grid = conn.execute("""SELECT role, COALESCE(mft_category, ft_category) AS category,
            COUNT(*) AS n FROM submissions GROUP BY 1, 2""").fetchall()
    return {k: r[k] or 0 for k in r.keys()} | {"grid": [list(g) for g in grid]}


@app.get("/admin/export.csv")
def export(token: str = ""):
    if not ADMIN_TOKEN or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(404)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    cols = COLUMNS + LEGACY_COLUMNS
    writer.writerow(cols)
    with db() as conn:
        writer.writerows(conn.execute(f"SELECT {', '.join(cols)} FROM submissions ORDER BY id"))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=demo_prompts.csv"})
