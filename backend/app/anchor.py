"""DLT-якорь: anchoring of the pre-clearance hash-chain ledger root into a
REAL blockchain.

Provenance (honest, by design):
  * This is a REAL EVM: py-evm (via ``eth-tester``) executing real EVM bytecode.
  * This is a REAL smart contract: ``contracts/ClearanceAnchor.sol`` (Solidity
    ^0.8.20), compiled with solc-js to ``app/contracts/ClearanceAnchor.json``;
    every anchor is a REAL on-chain transaction with a receipt, gas cost and
    block number.
  * It is a LOCAL in-process devnet: in-memory, single-node, no external RPC,
    no faucet, NOT mainnet/testnet, NOT production. When the app process
    restarts, the chain resets and the contract is re-deployed (the anchor
    journal survives in its own sqlite file ``anchor.db``).

The module never crashes the app: all heavy imports (web3/eth_tester) are
lazy, :func:`available` reports readiness, and on-chain operations raise a
clear ``RuntimeError`` when the stack is missing.

Anchor history is persisted in its OWN sqlite DB (``anchor.db`` inside
``store.DATA_DIR``); ``store.py`` is not touched.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Public constants -------------------------------------------------------

#: Honest label of the chain we run (surfaced verbatim in the API/UI).
CHAIN_LABEL = "local EVM devnet (py-evm, in-memory)"

#: Honest Russian note shown in status() — real EVM, but not mainnet.
NOTE_RU = (
    "Настоящий локальный EVM devnet (py-evm, in-memory): реальный Solidity-"
    "контракт и реальные транзакции, исполняются in-process. Это НЕ mainnet/"
    "testnet и НЕ production; при рестарте процесса цепочка обнуляется и "
    "контракт развертывается заново (журнал якорей сохраняется в anchor.db)."
)

#: Compiled artifact produced by solc-js (see contracts/ClearanceAnchor.sol).
ARTIFACT_PATH = Path(__file__).resolve().parent / "contracts" / "ClearanceAnchor.json"

_DEPLOY_GAS = 6_000_000
_ANCHOR_GAS = 400_000

# RLock: re-entrant so verify()/anchor_root() can call _require_chain()
# (which itself takes the lock) without deadlocking. eth-tester/py-evm is a
# single-node in-process chain: serializing all on-chain ops also prevents
# nonce races between FastAPI worker threads.
_LOCK = threading.RLock()

# Module-level singleton: deployed chain + contract (lazily created).
_STATE: dict[str, Any] = {}


# --- Helpers ----------------------------------------------------------------


def _artifact_ok() -> bool:
    """True when the compiled artifact exists and looks deployable."""
    try:
        data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        isinstance(data.get("abi"), list)
        and len(data["abi"]) > 0
        and isinstance(data.get("bytecode"), str)
        and data["bytecode"].startswith("0x")
        and len(data["bytecode"]) > 10
    )


def available() -> bool:
    """True when the real-EVM anchor stack is usable in this process.

    Checks that ``web3`` and ``eth_tester`` import and that the compiled
    contract artifact is present. Never raises — safe to call from anywhere
    (e.g. FastAPI status endpoints).
    """
    try:
        import eth_tester  # noqa: F401
        import web3  # noqa: F401
    except Exception:
        return False
    return _artifact_ok()


def _require_chain() -> dict[str, Any]:
    """Return the singleton chain state, deploying the contract on first use.

    Raises:
        RuntimeError: with a clear message when web3/eth-tester or the
            compiled artifact are missing, or when deployment reverts.
    """
    with _LOCK:
        if _STATE.get("contract") is not None:
            return _STATE
        if not available():
            raise RuntimeError(
                "DLT-якорь недоступен: нужны пакеты web3 + eth-tester[py-evm] "
                "и артефакт app/contracts/ClearanceAnchor.json "
                f"(найдено: artifact={'ok' if _artifact_ok() else 'missing'})"
            )

        from web3 import Web3
        from web3.providers.eth_tester import EthereumTesterProvider

        artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
        # Real py-evm EVM, in-process; eth-tester pre-funds its default accounts.
        w3 = Web3(EthereumTesterProvider())
        account = w3.eth.accounts[0]

        factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        deploy_tx = factory.constructor().transact(
            {"from": account, "gas": _DEPLOY_GAS}
        )
        receipt = w3.eth.wait_for_transaction_receipt(deploy_tx)
        if int(receipt["status"]) != 1:
            raise RuntimeError("ClearanceAnchor deployment reverted")
        address = receipt["contractAddress"]
        contract = w3.eth.contract(address=address, abi=artifact["abi"])
        # Sanity: owner must be our funded deployer account.
        if contract.functions.owner().call().lower() != account.lower():
            raise RuntimeError("ClearanceAnchor owner mismatch after deploy")

        _STATE.update(
            {
                "w3": w3,
                "account": account,
                "contract": contract,
                "contract_address": str(address),
                "deploy_tx_hash": "0x" + bytes(deploy_tx).hex().removeprefix("0x"),
                "chain_id": int(w3.eth.chain_id),
            }
        )
        return _STATE


def _normalize_root(root_hash: str) -> str:
    """Normalize a sha256 root to lowercase 64-hex (no 0x prefix).

    Raises:
        ValueError: when the input is not a 32-byte hex digest.
    """
    if not isinstance(root_hash, str):
        raise ValueError(f"root_hash must be str, got {type(root_hash).__name__}")
    h = root_hash.strip().lower().removeprefix("0x")
    if len(h) != 64 or any(ch not in "0123456789abcdef" for ch in h):
        raise ValueError(f"root_hash must be a 64-char sha256 hex, got {root_hash!r}")
    return h


def _hex32(value: Any) -> str:
    """Normalize a web3 bytes32 return value to lowercase 64-hex (no prefix).

    Handles bytes/HexBytes across hexbytes 1.x (.hex() prefixed) and 2.x
    (.hex() unprefixed), plus plain strings.
    """
    if isinstance(value, str):
        h = value.lower().removeprefix("0x")
    else:
        h = bytes(value).hex().lower().removeprefix("0x")
    return h.zfill(64)


def _tx_hex(value: Any) -> str:
    """Normalize a tx/receipt hash to a 0x-prefixed hex string."""
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    return "0x" + bytes(value).hex().removeprefix("0x")


def _db_path() -> Path:
    """Path of the anchor journal sqlite file (own DB, next to store's data)."""
    try:
        from app import store  # lazy: keep anchor.py import-cheap

        base = Path(store.DATA_DIR)
    except Exception:
        base = Path(__file__).resolve().parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "anchor.db"


def _db_record(row: dict[str, Any]) -> None:
    """Append one anchor record to the journal DB (append-only)."""
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS anchors ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " seq INTEGER NOT NULL,"
            " root TEXT NOT NULL,"
            " issuer TEXT,"
            " tx_hash TEXT NOT NULL,"
            " block_number INTEGER NOT NULL,"
            " contract_address TEXT NOT NULL,"
            " gas_used INTEGER,"
            " ts TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO anchors (seq,root,issuer,tx_hash,block_number,"
            "contract_address,gas_used,ts) VALUES (?,?,?,?,?,?,?,?)",
            (
                int(row["seq"]),
                row["root"],
                row.get("issuer"),
                row["tx_hash"],
                int(row["block_number"]),
                row["contract_address"],
                int(row.get("gas_used") or 0),
                row["ts"],
            ),
        )


# --- Public API -------------------------------------------------------------


def anchor_root(root_hash: str, seq: int, issuer: str) -> dict[str, Any]:
    """Anchor a ledger root hash on-chain with a REAL contract transaction.

    Deploys the ``ClearanceAnchor`` contract on first call (local py-evm
    devnet), sends ``anchor(bytes32 root, uint256 seq, string issuer)``,
    waits for the receipt, journals the result in ``anchor.db`` and returns
    transaction provenance.

    Args:
        root_hash: sha256 hex (64 chars, optional ``0x`` prefix) of the
            ledger root — normally ``store.last_ledger_entry()["hash"]``.
        seq: ledger sequence number the root corresponds to (uint256).
        issuer: human-readable issuer label stored with the anchor.

    Returns:
        dict with keys ``tx_hash``, ``block_number``, ``contract_address``,
        ``root``, ``seq``, ``gas_used``, ``status`` (1 = success), plus
        ``issuer`` and ``ts`` (host UTC time of the journal record).

    Raises:
        RuntimeError: when web3/eth-tester or the artifact are missing, or
            the transaction reverts.
        ValueError: when ``root_hash`` is not a 64-char hex digest or ``seq``
            is negative.
    """
    root = _normalize_root(root_hash)
    seq = int(seq)
    if seq < 0:
        raise ValueError(f"seq must be >= 0, got {seq}")

    with _LOCK:
        state = _require_chain()
        contract = state["contract"]
        tx_hash = contract.functions.anchor(
            bytes.fromhex(root), seq, str(issuer)
        ).transact({"from": state["account"], "gas": _ANCHOR_GAS})
        receipt = state["w3"].eth.wait_for_transaction_receipt(tx_hash)
        status_code = int(receipt["status"])
        if status_code != 1:
            raise RuntimeError(
                f"anchor transaction reverted (tx {_tx_hex(tx_hash)}, "
                f"block {int(receipt['blockNumber'])})"
            )

        row: dict[str, Any] = {
            "tx_hash": _tx_hex(tx_hash),
            "block_number": int(receipt["blockNumber"]),
            "contract_address": state["contract_address"],
            "root": root,
            "seq": seq,
            "issuer": str(issuer),
            "gas_used": int(receipt["gasUsed"]),
            "status": status_code,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _db_record(row)
        return row


def verify(root_hash: str, seq: int) -> dict[str, Any]:
    """Verify an off-chain ledger root against the on-chain anchor history.

    Reads the REAL chain state: ``latest()`` when its seq matches, otherwise
    ``rootAt(seq - 1)`` from the append-only history array.

    Args:
        root_hash: off-chain sha256 hex root to check.
        seq: ledger sequence number of that root.

    Returns:
        dict ``{matches, on_chain_root, off_chain_root, seq, source,
        anchored_count}`` where ``on_chain_root`` is 64-hex or None (nothing
        anchored for that seq) and ``source`` is "latest"/"history"/None.

    Raises:
        RuntimeError: when the anchor stack is unavailable.
        ValueError: when ``root_hash`` is malformed.
    """
    off_chain = _normalize_root(root_hash)
    seq = int(seq)

    with _LOCK:
        state = _require_chain()
        fn = state["contract"].functions
        anchored_count = int(fn.count().call())
        on_chain: str | None = None
        source: str | None = None
        if anchored_count > 0:
            latest_root, latest_seq, _issuer, _ts = fn.latest().call()
            if int(latest_seq) == seq:
                on_chain, source = _hex32(latest_root), "latest"
            elif 1 <= seq <= anchored_count:
                on_chain, source = _hex32(fn.rootAt(seq - 1).call()), "history"

    return {
        "matches": on_chain is not None and on_chain == off_chain,
        "on_chain_root": on_chain,
        "off_chain_root": off_chain,
        "seq": seq,
        "source": source,
        "anchored_count": anchored_count,
    }


def status() -> dict[str, Any]:
    """Anchor subsystem status for the API/UI (never raises).

    Ensures the contract is deployed when the stack is available (deploy is
    sub-second on the local devnet), then reports chain provenance, contract
    address, number of anchored roots and the latest anchor.
    """
    base: dict[str, Any] = {
        "available": False,
        "chain": CHAIN_LABEL,
        "contract_address": None,
        "chain_id": None,
        "deployed": False,
        "anchored_count": 0,
        "latest": None,
        "note": NOTE_RU,
    }
    if not available():
        base["note"] = (
            "DLT-якорь отключен: не установлены web3/eth-tester или нет "
            "артефакта контракта. " + NOTE_RU
        )
        return base

    try:
        with _LOCK:
            state = _require_chain()
            fn = state["contract"].functions
            count = int(fn.count().call())
            latest: dict[str, Any] | None = None
            if count > 0:
                root, seq, issuer, ts = fn.latest().call()
                latest = {
                    "root": _hex32(root),
                    "seq": int(seq),
                    "issuer": str(issuer),
                    "ts": int(ts),
                }
    except Exception as exc:  # honest degradation, never crash the app
        base["error"] = str(exc)
        return base

    base.update(
        {
            "available": True,
            "deployed": True,
            "contract_address": state["contract_address"],
            "chain_id": state.get("chain_id"),
            "deploy_tx_hash": state.get("deploy_tx_hash"),
            "anchored_count": count,
            "latest": latest,
        }
    )
    return base


def history(limit: int = 50) -> list[dict[str, Any]]:
    """Recent anchor journal entries (newest first) from ``anchor.db``.

    The journal survives process restarts even though the in-memory chain
    does not, so ``contract_address`` per row tells which deployment it
    belongs to. Returns [] when nothing has been anchored yet.
    """
    path = _db_path()
    if not path.exists():
        return []
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT seq,root,issuer,tx_hash,block_number,contract_address,"
                "gas_used,ts FROM anchors ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
