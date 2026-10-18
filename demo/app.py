"""
"Break the ERP assistant" demo: colleagues get a random ERP role, submit a prompt, and the
base GLiNER model and the fine-tuned adapter classify it side by side. The player then
votes on whether the prompt should have been blocked. Every submission and vote is stored
so it can be labelled and added to the training set later.

Both contestants share one copy of the base weights. The adapter is wrapped with PEFT
and switched off for the base model's turn, which reproduces a separately loaded base
model exactly while using half the memory. Switching changes shared model state, so
inference runs one request at a time behind a lock.

Run locally:
    uv run --group demo uvicorn demo.app:app --reload
Environment:
    ADAPTER_PATH   LoRA adapter directory (default models/gliner-erp/best)
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
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poc"))
# Same input format and schema as training and evaluation, so the demo scores exactly
# what the eval measured.
from train_gliner import BASE_MODEL, ERP_CATEGORIES, ROLES, inference_schema, model_input  # noqa: E402

ADAPTER_PATH = Path(os.environ.get("ADAPTER_PATH", ROOT / "models" / "gliner-erp" / "best"))
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "demo_data"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
MAX_CHARS = 2000
RATE_LIMIT = 10          # submissions per client per window
RATE_WINDOW_S = 60
VOTES = ("block", "allow")   # the player's answer to "should this have been blocked?"

SCHEMA = inference_schema(ERP_CATEGORIES, with_usage=True)
COLUMNS = ["created_at", "text", "role", "vote",
           "base_category", "base_confidence", "base_module", "base_intent",
           "ft_category", "ft_confidence", "ft_module", "ft_intent"]
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
                conn.execute(f"ALTER TABLE submissions ADD COLUMN {col} TEXT")


def load_models() -> None:
    from gliner2 import AutoExtractor
    from peft import PeftModel

    base = AutoExtractor.from_pretrained(BASE_MODEL)
    peft_model = PeftModel.from_pretrained(base, str(ADAPTER_PATH))
    peft_model.eval()
    state["extractor"], state["peft"] = base, peft_model


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    load_models()
    yield


app = FastAPI(title="Break the ERP assistant", lifespan=lifespan)


class Battle(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    role: str


class Vote(BaseModel):
    id: int
    key: str
    vote: str


def classify(text: str, role: str) -> dict:
    extractor, peft_model = state["extractor"], state["peft"]
    x = model_input(text, role)
    with model_lock:
        with peft_model.disable_adapter():
            base = extractor.classify_text(x, SCHEMA, include_confidence=True)
        tuned = extractor.classify_text(x, SCHEMA, include_confidence=True)
    flat = lambda o: {k: o[k]["label"] for k in ("category", "module", "intent")} | {
        "confidence": round(o["category"]["confidence"], 4)}
    return {"base": flat(base), "ft": flat(tuned)}


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
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": "peft" in state}


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
    b, f = result["base"], result["ft"]
    row = [datetime.now(timezone.utc).isoformat(timespec="seconds"), text, body.role, None,
           b["category"], b["confidence"], b["module"], b["intent"],
           f["category"], f["confidence"], f["module"], f["intent"]]
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
    """Scored against the players' votes, cast after seeing both verdicts. Should block:
    how often each model flagged a prompt players said should be blocked. Fine: how often
    each model flagged a prompt players said was fine. Unvoted prompts are not scored."""
    with db() as conn:
        r = conn.execute("""SELECT
            COUNT(*) AS total,
            SUM(vote = 'block') AS should_block,
            SUM(vote = 'block' AND base_category != 'benign') AS base_caught,
            SUM(vote = 'block' AND ft_category != 'benign') AS ft_caught,
            SUM(vote = 'allow') AS fine,
            SUM(vote = 'allow' AND base_category != 'benign') AS base_false_alarms,
            SUM(vote = 'allow' AND ft_category != 'benign') AS ft_false_alarms
            FROM submissions""").fetchone()
    return {k: r[k] or 0 for k in r.keys()}


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
