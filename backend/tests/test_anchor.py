"""Tests for the real local-EVM DLT anchor (app.anchor).

Real stack exercised here: py-evm (eth-tester) + web3 8.x + the solc-compiled
ClearanceAnchor contract (backend/app/contracts/ClearanceAnchor.json).
Skips cleanly when web3/eth-tester are not installed.
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("web3", reason="web3 not installed")
pytest.importorskip("eth_tester", reason="eth-tester not installed")

from app import anchor  # noqa: E402

ISSUER = "SCT Demo Customs Authority (симуляция)"


def _root(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_available_with_real_deps() -> None:
    assert anchor.available() is True


def test_status_deploys_real_contract() -> None:
    st = anchor.status()
    assert st["available"] is True
    assert st["deployed"] is True
    assert st["chain"] == "local EVM devnet (py-evm, in-memory)"
    addr = st["contract_address"]
    assert isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42
    assert st["anchored_count"] >= 0


def test_anchor_root_returns_real_tx() -> None:
    root = _root(b"ledger-root-1")
    res = anchor.anchor_root(root, 1, ISSUER)
    assert res["status"] == 1
    tx = res["tx_hash"]
    assert tx.startswith("0x") and len(tx) == 66
    assert res["block_number"] >= 1
    assert res["gas_used"] > 0
    assert res["root"] == root
    assert res["seq"] == 1
    assert res["contract_address"].startswith("0x")


def test_verify_matches_and_tamper_detected() -> None:
    root2 = _root(b"ledger-root-2")
    assert anchor.anchor_root(root2, 2, ISSUER)["status"] == 1

    # latest() path: seq 2 is the newest anchor.
    ok = anchor.verify(root2, 2)
    assert ok["matches"] is True
    assert ok["source"] == "latest"
    assert ok["on_chain_root"] == root2
    assert ok["off_chain_root"] == root2

    # Tampered root must NOT match.
    bad = anchor.verify(_root(b"tampered"), 2)
    assert bad["matches"] is False
    assert bad["on_chain_root"] == root2

    # history path: seq 1 is no longer latest -> rootAt(0).
    first = anchor.verify(_root(b"ledger-root-1"), 1)
    assert first["matches"] is True
    assert first["source"] == "history"

    # Never-anchored seq: nothing on-chain to compare against.
    missing = anchor.verify(root2, 9999)
    assert missing["matches"] is False
    assert missing["on_chain_root"] is None


def test_status_reflects_anchors() -> None:
    st = anchor.status()
    assert st["available"] is True
    assert st["anchored_count"] >= 2
    latest = st["latest"]
    assert latest is not None
    assert latest["seq"] >= 2
    assert len(latest["root"]) == 64
    assert latest["issuer"] == ISSUER


def test_invalid_root_rejected() -> None:
    with pytest.raises(ValueError):
        anchor.anchor_root("not-a-hash", 1, ISSUER)
    with pytest.raises(ValueError):
        anchor.verify("0xdeadbeef", 1)


def test_journal_history_persisted() -> None:
    hist = anchor.history(limit=10)
    assert isinstance(hist, list) and len(hist) >= 2
    assert all(row["tx_hash"].startswith("0x") for row in hist)
    assert hist[0]["seq"] >= hist[-1]["seq"]  # newest first
