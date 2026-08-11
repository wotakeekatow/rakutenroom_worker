from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from queue_worker import (
    AFTERNOON_KEYWORDS,
    MORNING_KEYWORDS,
    ranking_items,
    search_items,
    select_candidates,
)

JST = ZoneInfo("Asia/Tokyo")

st.set_page_config(page_title="ROOM queue worker", page_icon="⚙️", layout="centered")


def read_rakuten_secrets() -> tuple[str, str, str]:
    try:
        r = st.secrets["rakuten"]
        app_id = str(r["application_id"]).strip()
        access_key = str(r["access_key"]).strip()
        affiliate_id = str(r.get("affiliate_id", "")).strip()
    except Exception:
        st.error("Worker secrets are not configured.")
        st.stop()
    if not app_id or not access_key:
        st.error("Worker secrets are incomplete.")
        st.stop()
    return app_id, access_key, affiliate_id


def valid_signature(access_key: str, slot: str, timestamp: int, signature: str) -> bool:
    if abs(int(time.time()) - timestamp) > 600:
        return False
    message = f"{slot}:{timestamp}".encode("utf-8")
    expected = hmac.new(access_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


slot = str(st.query_params.get("slot", "")).strip().lower()
sig = str(st.query_params.get("sig", "")).strip().lower()
try:
    ts = int(str(st.query_params.get("ts", "0")))
except ValueError:
    ts = 0

if slot not in {"morning", "afternoon"}:
    st.error("Invalid worker slot.")
    st.stop()

app_id, access_key, affiliate_id = read_rakuten_secrets()
if not sig or not ts or not valid_signature(access_key, slot, ts, sig):
    st.error("Worker authorization failed.")
    st.stop()

keywords = MORNING_KEYWORDS if slot == "morning" else AFTERNOON_KEYWORDS
target_count = 10 if slot == "morning" else 20

try:
    pool = []
    pool.extend(ranking_items(app_id, access_key, affiliate_id))
    for keyword in keywords:
        time.sleep(1.1)
        pool.extend(search_items(app_id, access_key, affiliate_id, keyword))
except Exception as exc:
    st.error(f"ROOM_WORKER_ERROR:{type(exc).__name__}:{str(exc)[:160]}")
    st.stop()

selected = select_candidates(pool, target_count)
now = datetime.now(JST)
payload = {
    "version": "0.6",
    "generated_at": now.isoformat(timespec="seconds"),
    "slot": slot,
    "target_count": target_count,
    "candidate_pool": len(pool),
    "ready_count": len(selected),
    "strategy": "収益バランス",
    "review_mode": "automated_safety_review_v0.6",
    "ai_review": "自動安全レビュー済み（生成AI APIは未使用）",
    "items": selected,
}

raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
encoded = base64.urlsafe_b64encode(raw).decode("ascii")

st.success("ROOM_QUEUE_READY")
st.caption(f"{slot}: {len(selected)}/{target_count} candidates")
st.code(f"ROOM_QUEUE_JSON_B64:{encoded}", language=None)
