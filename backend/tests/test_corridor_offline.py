"""Portable proof verifies real API output without the server or its private key."""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app import main, store

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_corridor_receipt.py"
spec = importlib.util.spec_from_file_location("verify_corridor_receipt", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_offline_cli_verifies_export_and_rejects_tamper_and_wrong_issuer(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    main._rate_buckets.clear()
    with TestClient(main.app) as client:
        path = "/api/operations/SHP-1042/corridor"
        assessment = client.get(path).json()["assessment"]
        issued = client.post(path + "/issue", json={
            "plan_version": assessment["plan_version"], "evidence_hash": assessment["evidence_hash"],
        }, headers={"Idempotency-Key": "offline-proof"})
        assert issued.status_code == 201, issued.text
        receipt_id = issued.json()["receipt"]["payload"]["id"]
        exported = client.get(path + f"/receipts/{receipt_id}?download=true")
        receipt = exported.json()
        issuer = client.get("/api/operations/corridor/issuer").json()
    main._rate_buckets.clear()
    receipt_path, key_path = tmp_path / "receipt.json", tmp_path / "issuer.json"
    receipt_path.write_bytes(exported.content)
    key_path.write_text(json.dumps(issuer), encoding="utf-8")
    command = [sys.executable, str(SCRIPT), str(receipt_path), "--public-key", str(key_path)]
    valid = subprocess.run(command, capture_output=True, text=True)
    assert valid.returncode == 0, valid.stderr
    report = json.loads(valid.stdout)
    assert report["valid"] and report["signature_valid"] and report["hash_matches"]
    assert report["current_documents_and_plan"] == "not_checked_offline"

    forged = copy.deepcopy(receipt)
    forged["payload"]["decision"] = "forged_customs_release"
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")
    tampered = subprocess.run(command, capture_output=True, text=True)
    assert tampered.returncode == 1
    assert not json.loads(tampered.stdout)["valid"]

    # A self-selected key packaged with a forged receipt is never a trust anchor.
    other_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    assert not verifier.verify_receipt(receipt, other_key)["issuer_key_matches"]

    receipt_path.write_text('{"payload":{},"payload":{}}', encoding="utf-8")
    duplicate = subprocess.run(command, capture_output=True, text=True)
    assert duplicate.returncode == 2
    assert "Duplicate JSON field" in duplicate.stderr


def test_cli_rejects_missing_trusted_key(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("{}")
    result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
    assert result.returncode == 2 and "--public-key" in result.stderr
