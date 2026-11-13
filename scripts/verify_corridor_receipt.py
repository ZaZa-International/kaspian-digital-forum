#!/usr/bin/env python3
"""Verify a corridor receipt offline against a separately trusted issuer key.

The public key must come from the issuer through a trusted channel. A valid
signature proves integrity and issuer possession of the key, not customs release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _read(path: Path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Input exceeds 2 MiB")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def verify_receipt(receipt: dict, trusted_public_key: str) -> dict:
    if not isinstance(receipt, dict) or set(receipt) != {"payload", "key_id", "signature", "previous_hash", "hash"}:
        raise ValueError("Expected a receipt with exactly payload, key_id, signature, previous_hash and hash")
    if not isinstance(receipt["payload"], dict):
        raise ValueError("Invalid payload")
    for key, size in (("key_id", 64), ("hash", 64), ("previous_hash", 64), ("signature", 128)):
        if not isinstance(receipt[key], str) or not re.fullmatch(f"[0-9a-f]{{{size}}}", receipt[key]):
            raise ValueError(f"Invalid {key}")
    if not isinstance(trusted_public_key, str) or not re.fullmatch("[0-9a-fA-F]{64}", trusted_public_key):
        raise ValueError("Trusted public key must be a 32-byte hexadecimal string")
    raw_key = bytes.fromhex(trusted_public_key)
    key_matches = hashlib.sha256(raw_key).hexdigest() == receipt["key_id"]
    signed = {key: receipt[key] for key in ("payload", "key_id", "previous_hash")}
    signed_bytes = _canonical(signed)
    hash_matches = hashlib.sha256(_canonical({**signed, "signature": receipt["signature"]})).hexdigest() == receipt["hash"]
    signature_valid = False
    if key_matches:
        try:
            Ed25519PublicKey.from_public_bytes(raw_key).verify(bytes.fromhex(receipt["signature"]), signed_bytes)
            signature_valid = True
        except InvalidSignature:
            pass
    return {
        "valid": key_matches and hash_matches and signature_valid,
        "issuer_key_matches": key_matches, "hash_matches": hash_matches,
        "signature_valid": signature_valid, "key_id": receipt["key_id"],
        "chain_status": "not_checked_single_receipt",
        "current_documents_and_plan": "not_checked_offline",
        "meaning": "Local issuer signature; not government customs clearance",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--public-key", required=True, type=Path, help="Separately trusted issuer JSON from /api/operations/corridor/issuer")
    args = parser.parse_args()
    try:
        issuer = _read(args.public_key)
        if not isinstance(issuer, dict):
            raise ValueError("Expected issuer JSON")
        issuer = issuer.get("issuer", issuer)
        if not isinstance(issuer, dict) or "public_key" not in issuer:
            raise ValueError("Issuer key is not initialized or missing")
        result = verify_receipt(_read(args.receipt), issuer["public_key"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
