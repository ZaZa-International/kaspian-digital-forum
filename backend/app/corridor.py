"""Signed local evidence receipts for the dispatcher document checklist."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from typing import Annotated

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import data, operation_documents, operations, store

RULE_VERSION = "sct-corridor-v1"
GENESIS = "0" * 64
PURPOSE = "pre_clearance_assessment"
_KEY_FILE = "corridor-issuer.ed25519"
_NOTICE = "Подпись подтверждает оценку оператора. Таможенный выпуск оформляется отдельно."
_NO_STORE = {"Cache-Control": "no-store"}
_RECEIPT_FIELDS = {"payload", "key_id", "previous_hash", "signature", "hash"}
_SUMMARY_FIELDS = ("id", "issued_at", "plan_version", "decision")


class IssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: int = Field(strict=True, ge=1)
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _assessment(conn, shipment_id: str) -> dict:
    shipment = operations._shipment(conn, shipment_id)
    plan = operations._plan(conn, shipment)
    events = store.list_events(shipment_id, conn)
    progress = operations._progress(shipment, plan, events, operations._now())
    passport = operation_documents.passport(conn, shipment, progress["remaining"], plan["legs"][-1]["to"])
    by_id = {file["id"]: file for file in passport["files"]}
    latest_hashes = {}
    for file in passport["files"]:
        latest_hashes.setdefault(file["kind"], file["sha256"])

    rules = []
    for check in passport["requirements"]:
        file = by_id.get(check["file_id"])
        status = {
            "matched": "pass", "missing": "missing", "mismatch": "mismatch",
            "demo_verified": "review_required", "review_required": "review_required",
        }[check["status"]]
        reason = check["reason"]
        if check["source"] == "demo":
            reason += ". Для проверки нужен загруженный документ"
        rules.append({
            "key": check["key"], "label": check["label"], "status": status, "reason": reason,
            "source": check["source"], "document_sha256": file["sha256"] if file else None,
            "mismatches": check["mismatches"],
        })
    statuses = {rule["status"] for rule in rules}
    decision = (
        "mismatch" if "mismatch" in statuses else "needs_documents" if "missing" in statuses
        else "review_required" if not rules or "review_required" in statuses else "ready_for_review"
    )
    evidence = {
        "shipment_id": shipment_id, "plan_version": plan["version"], "rule_version": RULE_VERSION,
        "plan_leg_ids": plan["leg_ids"], "remaining_leg_ids": [leg["id"] for leg in progress["remaining"]],
        "document_hashes": latest_hashes, "decision": decision, "demo": data.get_shipment(shipment_id) is not None,
        "rules": [
            {**rule, "mismatches": [item["field"] for item in rule["mismatches"]]}
            for rule in rules
        ],
    }
    return {**evidence, "evidence_hash": _hash(canonical_bytes(evidence)), "rules": rules}


def _key(create: bool = False) -> Ed25519PrivateKey | None:
    path = store.DATA_DIR / _KEY_FILE
    try:
        return Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    except FileNotFoundError:
        if not create:
            return None
    key = Ed25519PrivateKey.generate()
    with tempfile.NamedTemporaryFile(dir=path.parent) as candidate:
        candidate.write(key.private_bytes_raw())
        candidate.flush()
        os.fsync(candidate.fileno())
        try:
            os.link(candidate.name, path)  # Atomic first writer wins, with the temp file's private mode.
        except FileExistsError:
            return Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    return key


def _issuer(key: Ed25519PrivateKey | None) -> dict | None:
    if key is None:
        return None
    public_bytes = key.public_key().public_bytes_raw()
    return {"algorithm": "Ed25519", "key_id": _hash(public_bytes), "public_key": public_bytes.hex()}


def _signature_valid(receipt: dict, key: Ed25519PrivateKey | None, issuer: dict | None) -> bool:
    if key is None or receipt.get("key_id") != issuer["key_id"]:
        return False
    if set(receipt) != _RECEIPT_FIELDS:
        return False
    try:
        signature = receipt["signature"]
        signed = {part: receipt[part] for part in ("payload", "key_id", "previous_hash")}
        if not isinstance(signature, str) or len(signature) != 128:
            return False
        signature_bytes = bytes.fromhex(signature)
        if signature_bytes.hex() != signature:
            return False
        key.public_key().verify(signature_bytes, canonical_bytes(signed))
        return True
    except (InvalidSignature, TypeError, ValueError, RecursionError):
        return False


def _chain(conn, shipment_id: str, key: Ed25519PrivateKey | None, issuer: dict | None) -> tuple[dict, bool]:
    rows = conn.execute(
        "SELECT sequence,id,receipt_json FROM corridor_receipts WHERE shipment_id=? ORDER BY sequence",
        (shipment_id,),
    )
    receipts = {}
    previous_hash = GENESIS
    chain_valid = True
    for sequence, row in enumerate(rows, 1):
        try:
            receipt = json.loads(row["receipt_json"])
            if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_FIELDS:
                raise ValueError("Incomplete corridor receipt envelope")
            payload = receipt["payload"]
            if not isinstance(payload, dict) or any(field not in payload for field in _SUMMARY_FIELDS):
                raise ValueError("Incomplete corridor receipt")
            linked = (
                row["sequence"] == sequence == payload["sequence"]
                and payload["id"] == row["id"] and payload["shipment_id"] == shipment_id
                and receipt["previous_hash"] == previous_hash
                and _signature_valid(receipt, key, issuer)
                and receipt["hash"] == _hash(canonical_bytes({part: receipt[part] for part in _RECEIPT_FIELDS - {"hash"}}))
            )
            previous_hash = receipt["hash"]
        except (TypeError, ValueError, KeyError, RecursionError):
            linked, receipt = False, None
        receipts[row["id"]] = receipt
        chain_valid = chain_valid and linked
    return receipts, chain_valid


def _verify(receipt: dict, current: dict, receipts: dict, chain_valid: bool,
            key: Ed25519PrivateKey | None, issuer: dict | None) -> dict:
    payload = receipt.get("payload")
    receipt_id = payload.get("id") if isinstance(payload, dict) else None
    signature_valid = _signature_valid(receipt, key, issuer)
    belongs_to_chain = (
        signature_valid and isinstance(receipt_id, str) and chain_valid
        and receipts.get(receipt_id) == receipt
    )
    is_current = (
        belongs_to_chain and payload["plan_version"] == current["plan_version"]
        and payload["rule_version"] == current["rule_version"]
        and payload["evidence_hash"] == current["evidence_hash"]
    )
    return {
        "receipt_id": receipt_id if isinstance(receipt_id, str) and re.fullmatch(r"[a-f0-9]{32}", receipt_id) else None,
        "signature_valid": signature_valid, "chain_valid": belongs_to_chain, "current": is_current,
        "status": "valid" if is_current else "stale" if belongs_to_chain else "invalid",
    }


def _load(conn, shipment_id: str) -> tuple:
    assessment = _assessment(conn, shipment_id)
    key = _key()
    issuer = _issuer(key)
    receipts, chain_valid = _chain(conn, shipment_id, key, issuer)
    return assessment, receipts, chain_valid, key, issuer


def _issue(shipment_id: str, request: IssueRequest, idempotency_key: str) -> dict:
    if not idempotency_key.strip():
        raise HTTPException(422, "Idempotency-Key должен быть непустым")
    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assessment, receipts, chain_valid, key, issuer = _load(conn, shipment_id)
        if not chain_valid:
            raise HTTPException(409, "Нарушена целостность локального журнала квитанций.")
        existing = conn.execute(
            "SELECT id FROM corridor_receipts WHERE shipment_id=? AND idempotency_key=?",
            (shipment_id, idempotency_key),
        ).fetchone()
        if existing:
            receipt = receipts[existing["id"]]
            payload = receipt["payload"]
            if request.plan_version != payload["plan_version"] or request.evidence_hash != payload["evidence_hash"]:
                raise HTTPException(409, "Этот Idempotency-Key уже относится к другому состоянию документов.")
            return {"receipt": receipt, "verification": _verify(receipt, assessment, receipts, chain_valid, key, issuer)}
        if request.plan_version != assessment["plan_version"] or request.evidence_hash != assessment["evidence_hash"]:
            raise HTTPException(409, "Маршрут или документы изменились. Обновите оценку перед выдачей квитанции.")
        if key is None:
            key = _key(create=True)
            issuer = _issuer(key)
        last_id = next(reversed(receipts), None)
        previous_hash = receipts[last_id]["hash"] if last_id else GENESIS
        payload = {
            **assessment, "id": uuid.uuid4().hex, "sequence": len(receipts) + 1,
            "issued_at": operations._now().isoformat(), "purpose": PURPOSE,
            "rules": [
                {**rule, "mismatches": [item["field"] for item in rule["mismatches"]]}
                for rule in assessment["rules"]
            ],
        }
        signed = {"payload": payload, "key_id": issuer["key_id"], "previous_hash": previous_hash}
        record = {**signed, "signature": key.sign(canonical_bytes(signed)).hex()}
        receipt = {**record, "hash": _hash(canonical_bytes(record))}
        conn.execute(
            "INSERT INTO corridor_receipts (shipment_id,sequence,id,idempotency_key,receipt_json) VALUES (?,?,?,?,?)",
            (shipment_id, payload["sequence"], payload["id"], idempotency_key, canonical_bytes(receipt).decode("utf-8")),
        )
        receipts[payload["id"]] = receipt
        return {"receipt": receipt, "verification": _verify(receipt, assessment, receipts, True, key, issuer)}


def create_router(guard_write) -> APIRouter:
    router = APIRouter(prefix="/api/operations", tags=["digital corridor"])

    @router.get("/corridor/issuer")
    def issuer(response: Response):
        response.headers.update(_NO_STORE)
        return {"issuer": _issuer(_key())}

    @router.get("/{shipment_id}/corridor")
    def overview(shipment_id: str, response: Response):
        response.headers.update(_NO_STORE)
        with store._conn() as conn:
            conn.execute("BEGIN")
            assessment, receipts, chain_valid, key, issuer = _load(conn, shipment_id)
            summaries = []
            for receipt in reversed(receipts.values()):
                if receipt is None:
                    continue
                payload = receipt["payload"]
                summaries.append({
                    **{field: payload[field] for field in _SUMMARY_FIELDS},
                    "hash": receipt["hash"], "previous_hash": receipt["previous_hash"],
                    "verification": _verify(receipt, assessment, receipts, chain_valid, key, issuer),
                })
            return {"assessment": assessment, "issuer": issuer, "receipts": summaries,
                    "chain_valid": chain_valid, "notice": _NOTICE}

    @router.post("/{shipment_id}/corridor/issue", status_code=201, dependencies=[Depends(guard_write)])
    def issue(
        shipment_id: str, body: IssueRequest,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
    ):
        return _issue(shipment_id, body, idempotency_key)

    @router.get("/{shipment_id}/corridor/receipts/{receipt_id}")
    def receipt_detail(shipment_id: str, receipt_id: str, response: Response, download: Annotated[bool, Query()] = False):
        response.headers.update(_NO_STORE)
        with store._conn() as conn:
            conn.execute("BEGIN")
            assessment, receipts, chain_valid, key, issuer = _load(conn, shipment_id)
            receipt = receipts.get(receipt_id)
            if receipt is None:
                raise HTTPException(404, "Квитанция не найдена.")
            if download:
                return Response(canonical_bytes(receipt), media_type="application/json", headers={
                    "Content-Disposition": f'attachment; filename="corridor-{receipt_id}.json"',
                    **_NO_STORE,
                })
            return {"receipt": receipt, "verification": _verify(receipt, assessment, receipts, chain_valid, key, issuer)}

    async def parse_receipt(request: Request) -> dict:
        def unique_object(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("Duplicate JSON fields")
            return result

        try:
            receipt = json.loads(await request.body(), object_pairs_hook=unique_object)
        except (ValueError, RecursionError) as exc:
            raise HTTPException(422, "Требуется корректный JSON квитанции.") from exc
        if not isinstance(receipt, dict):
            raise HTTPException(422, "Требуется объект JSON квитанции.")
        return receipt

    @router.post("/{shipment_id}/corridor/verify")
    def verify(shipment_id: str, response: Response, receipt: dict = Depends(parse_receipt)):
        response.headers.update(_NO_STORE)
        with store._conn() as conn:
            conn.execute("BEGIN")
            assessment, receipts, chain_valid, key, issuer = _load(conn, shipment_id)
            return _verify(receipt, assessment, receipts, chain_valid, key, issuer)

    return router
