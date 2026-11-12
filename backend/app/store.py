"""Transactional SQLite store for runtime demo state (shipments + ledger).

Replaces the racy JSON files: concurrent creates lost rows, corrupt JSON
silently reset to [] and was then overwritten, IDs depended on list length.
Stdlib only. WAL journal, one short connection per operation, writes
serialised with BEGIN IMMEDIATE. IDs come from a monotonic counter (never
reused, even after a demo reset). Legacy JSON is migrated once and kept
as *.migrated backup. Reset: POST /api/demo/reset or scripts/reset_demo_data.py.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "sct.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cargo TEXT NOT NULL,
    weight_t REAL NOT NULL,
    route_id TEXT NOT NULL,
    current_leg INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    eta TEXT NOT NULL,
    docs_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    missing_json TEXT NOT NULL DEFAULT '[]',
    doc_hash TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    ts TEXT NOT NULL,
    issuer TEXT,
    rule_version TEXT,
    docs_json TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    shipment_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    status TEXT NOT NULL,
    ref TEXT,
    content_hash TEXT,
    issuer TEXT,
    source TEXT,
    note TEXT,
    rule_version TEXT,
    submitted_at TEXT,
    decided_at TEXT,
    PRIMARY KEY (shipment_id, doc_type)
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL,
    leg_id TEXT,
    event_type TEXT NOT NULL,
    planned_ts TEXT,
    actual_ts TEXT,
    occurred_ts TEXT,
    received_ts TEXT NOT NULL,
    source TEXT NOT NULL,
    location TEXT,
    mode TEXT,
    confidence TEXT,
    note TEXT,
    correction_of TEXT
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO counters (name, value) VALUES ('shipment', 2000);
"""

_migrate_lock = threading.Lock()
_migrated = False


@contextmanager
def _conn():
    """Connection with transaction semantics, always closed."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_SCHEMA)
    _migrate_legacy_once()
    return conn


def _migrate_legacy_once() -> None:
    global _migrated
    if _migrated:
        return
    with _migrate_lock:
        if _migrated:
            return
        _migrated = True
        _migrate_schema()
        _migrate_legacy_shipments()
        _migrate_legacy_ledger()


def _migrate_schema() -> None:
    """Add columns introduced after v1 to an existing database."""
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger)")}
        for col, ddl in (("issuer", "TEXT"), ("rule_version", "TEXT"), ("docs_json", "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE ledger ADD COLUMN {col} {ddl}")


def _migrate_legacy_shipments() -> None:
    legacy = DATA_DIR / "shipments.json"
    if not legacy.exists():
        return
    try:
        rows = json.loads(legacy.read_text(encoding="utf-8"))
        assert isinstance(rows, list)
    except (ValueError, AssertionError):
        return  # corrupt legacy file: leave it untouched, never overwrite
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT COUNT(*) c FROM shipments").fetchone()["c"]:
            return
        max_num = 2000
        for r in rows:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO shipments (id,name,cargo,weight_t,route_id,"
                    "current_leg,status,eta,docs_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r["id"], r["name"], r["cargo"], float(r["weight_t"]), r["route_id"],
                     int(r.get("current_leg", 0)), r.get("status", "создан"), r["eta"],
                     json.dumps(r.get("docs", {}), ensure_ascii=False),
                     r.get("created_at", datetime.now(timezone.utc).isoformat())))
                num = int(str(r["id"]).split("-")[-1])
                max_num = max(max_num, num)
            except (KeyError, ValueError, TypeError):
                continue
        conn.execute("UPDATE counters SET value=? WHERE name='shipment' AND value<?",
                     (max_num, max_num))
    legacy.rename(DATA_DIR / "shipments.json.migrated")


def _migrate_legacy_ledger() -> None:
    legacy = DATA_DIR / "ledger.json"
    if not legacy.exists():
        return
    try:
        rows = json.loads(legacy.read_text(encoding="utf-8"))
        assert isinstance(rows, list)
    except (ValueError, AssertionError):
        return
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]:
            return
        for r in rows:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO ledger (seq,shipment_id,decision,missing_json,"
                    "doc_hash,prev_hash,hash,ts) VALUES (?,?,?,?,?,?,?,?)",
                    (int(r["seq"]), r["shipment_id"], r["decision"],
                     json.dumps(r.get("missing", []), ensure_ascii=False),
                     r["doc_hash"], r["prev_hash"], r["hash"], r["ts"]))
            except (KeyError, ValueError, TypeError):
                continue
    legacy.rename(DATA_DIR / "ledger.json.migrated")


def _shipment_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "cargo": r["cargo"],
            "weight_t": r["weight_t"], "route_id": r["route_id"],
            "current_leg": r["current_leg"], "status": r["status"],
            "eta": r["eta"], "docs": json.loads(r["docs_json"])}


def list_shipments() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM shipments ORDER BY id").fetchall()
    return [_shipment_dict(r) for r in rows]


def get_shipment(shipment_id: str) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
    return _shipment_dict(r) if r else None


def create_shipment_record(*, name: str, cargo: str, weight_t: float, route_id: str,
                           docs: dict, eta: str, status: str = "создан",
                           idempotency_key: str | None = None) -> dict:
    """Insert atomically; monotonic ID; idempotency key returns prior row."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if idempotency_key:
            r = conn.execute("SELECT * FROM shipments WHERE idempotency_key=?",
                             (idempotency_key,)).fetchone()
            if r:
                conn.rollback()
                return _shipment_dict(r)
        num = conn.execute(
            "UPDATE counters SET value=value+1 WHERE name='shipment' RETURNING value"
        ).fetchone()["value"]
        sid = f"SHP-{num}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO shipments (id,name,cargo,weight_t,route_id,current_leg,status,"
            "eta,docs_json,idempotency_key,created_at) VALUES (?,?,?,?,?,0,?,?,?,?,?)",
            (sid, name, cargo, weight_t, route_id, status, eta,
             json.dumps(docs, ensure_ascii=False), idempotency_key, now))
        conn.commit()
        return {"id": sid, "name": name, "cargo": cargo, "weight_t": weight_t,
                "route_id": route_id, "current_leg": 0, "status": status,
                "eta": eta, "docs": docs}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def clear_shipments() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM shipments")


def _ledger_dict(r: sqlite3.Row) -> dict:
    return {"seq": r["seq"], "shipment_id": r["shipment_id"], "decision": r["decision"],
            "missing": json.loads(r["missing_json"]), "doc_hash": r["doc_hash"],
            "prev_hash": r["prev_hash"], "hash": r["hash"], "ts": r["ts"],
            "issuer": r["issuer"], "rule_version": r["rule_version"],
            "docs": json.loads(r["docs_json"]) if r["docs_json"] else None}


def ledger_entries() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
    return [_ledger_dict(r) for r in rows]


def last_ledger_entry(shipment_id: str | None = None) -> dict | None:
    with _conn() as conn:
        if shipment_id:
            r = conn.execute("SELECT * FROM ledger WHERE shipment_id=? "
                             "ORDER BY seq DESC LIMIT 1", (shipment_id,)).fetchone()
        else:
            r = conn.execute("SELECT * FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
    return _ledger_dict(r) if r else None


def append_ledger_entry(*, shipment_id: str, decision: str, missing: list[str],
                        doc_hash: str, issuer: str | None = None,
                        rule_version: str | None = None,
                        docs_snapshot: list[dict] | None = None) -> dict:
    """Hash-chain append inside one write transaction: seq, prev_hash and
    hash are computed atomically, so concurrent submits cannot fork the chain.
    The hash covers decision + document snapshot + issuer + rule version."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prev_row = conn.execute("SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        prev = prev_row["hash"] if prev_row else "0" * 64
        payload = f"{prev}|{shipment_id}|{decision}|{doc_hash}|{rule_version}|{issuer}"
        h = hashlib.sha256(payload.encode()).hexdigest()
        ts = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO ledger (shipment_id,decision,missing_json,doc_hash,prev_hash,hash,ts,"
            "issuer,rule_version,docs_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (shipment_id, decision, json.dumps(missing, ensure_ascii=False),
             doc_hash, prev, h, ts, issuer, rule_version,
             json.dumps(docs_snapshot, ensure_ascii=False) if docs_snapshot is not None else None))
        conn.commit()
        return {"seq": cur.lastrowid, "shipment_id": shipment_id, "decision": decision,
                "missing": missing, "doc_hash": doc_hash, "prev_hash": prev,
                "hash": h, "ts": ts, "issuer": issuer, "rule_version": rule_version,
                "docs": docs_snapshot}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def clear_ledger() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM ledger")


def verify_ledger() -> bool:
    prev = "0" * 64
    for e in ledger_entries():
        if e["prev_hash"] != prev:
            return False
        if e["rule_version"] is None and e["issuer"] is None:
            # legacy v1 entry (pre-provenance): original payload format
            payload = f"{e['prev_hash']}|{e['shipment_id']}|{e['decision']}|{e['doc_hash']}"
        else:
            payload = (f"{e['prev_hash']}|{e['shipment_id']}|{e['decision']}|"
                       f"{e['doc_hash']}|{e['rule_version']}|{e['issuer']}")
        if hashlib.sha256(payload.encode()).hexdigest() != e["hash"]:
            return False
        prev = e["hash"]
    return True


# --- Document registry: evidence-level statuses with provenance.
DOC_STATUSES = ("missing", "submitted", "verified", "rejected", "expired", "unknown")


def _doc_dict(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in ("shipment_id", "doc_type", "status", "ref", "content_hash",
                              "issuer", "source", "note", "rule_version",
                              "submitted_at", "decided_at")}


def get_documents(shipment_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM documents WHERE shipment_id=? ORDER BY doc_type",
                            (shipment_id,)).fetchall()
    return [_doc_dict(r) for r in rows]


def upsert_document(shipment_id: str, doc_type: str, *, status: str,
                    ref: str | None = None, issuer: str | None = None,
                    source: str | None = None, note: str | None = None,
                    rule_version: str | None = None) -> dict:
    if status not in DOC_STATUSES:
        raise ValueError(f"invalid document status {status}")
    now = datetime.now(timezone.utc).isoformat()
    content_hash = hashlib.sha256(ref.encode()).hexdigest() if ref else None
    submitted_at = now if status == "submitted" else None
    decided_at = now if status in ("verified", "rejected", "expired") else None
    with _conn() as conn:
        prev = conn.execute("SELECT submitted_at FROM documents WHERE shipment_id=? AND doc_type=?",
                            (shipment_id, doc_type)).fetchone()
        if submitted_at is None and prev:
            submitted_at = prev["submitted_at"]
        conn.execute(
            "INSERT INTO documents (shipment_id,doc_type,status,ref,content_hash,issuer,source,"
            "note,rule_version,submitted_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(shipment_id,doc_type) DO UPDATE SET status=excluded.status, "
            "ref=excluded.ref, content_hash=excluded.content_hash, issuer=excluded.issuer, "
            "source=excluded.source, note=excluded.note, rule_version=excluded.rule_version, "
            "submitted_at=excluded.submitted_at, decided_at=excluded.decided_at",
            (shipment_id, doc_type, status, ref, content_hash, issuer, source, note,
             rule_version, submitted_at, decided_at))
    return next(d for d in get_documents(shipment_id) if d["doc_type"] == doc_type)


def clear_documents() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM documents")


# --- Shipment events (DCSA T&T-inspired): actual facts vs model timeline.
EVENT_TYPES = ("departure", "arrival", "gate_in", "gate_out", "load", "discharge",
               "customs_start", "customs_done", "delay", "note")


def add_event(*, shipment_id: str, event_type: str, leg_id: str | None = None,
              planned_ts: str | None = None, actual_ts: str | None = None,
              occurred_ts: str | None = None, source: str,
              location: str | None = None, mode: str | None = None,
              confidence: str | None = None, note: str | None = None,
              correction_of: str | None = None, event_id: str | None = None) -> dict:
    import uuid
    if event_type not in EVENT_TYPES:
        raise ValueError(f"invalid event_type {event_type}")
    eid = event_id or uuid.uuid4().hex
    received = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (id,shipment_id,leg_id,event_type,planned_ts,actual_ts,"
            "occurred_ts,received_ts,source,location,mode,confidence,note,correction_of) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, shipment_id, leg_id, event_type, planned_ts, actual_ts, occurred_ts,
             received, source, location, mode, confidence, note, correction_of))
    return {"id": eid, "shipment_id": shipment_id, "leg_id": leg_id,
            "event_type": event_type, "planned_ts": planned_ts, "actual_ts": actual_ts,
            "occurred_ts": occurred_ts, "received_ts": received, "source": source,
            "location": location, "mode": mode, "confidence": confidence,
            "note": note, "correction_of": correction_of}


def list_events(shipment_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM events WHERE shipment_id=? "
                            "ORDER BY COALESCE(occurred_ts, received_ts)", (shipment_id,)).fetchall()
    return [dict(r) for r in rows]


def clear_events() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM events")


def reset_demo() -> dict:
    """Clear user-created runtime state; static seed data is untouched."""
    with _conn() as conn:
        n_ship = conn.execute("SELECT COUNT(*) c FROM shipments").fetchone()["c"]
        n_led = conn.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]
        conn.execute("DELETE FROM shipments")
        conn.execute("DELETE FROM ledger")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM events")
    return {"shipments_removed": n_ship, "ledger_entries_removed": n_led}