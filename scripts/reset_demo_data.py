#!/usr/bin/env python3
"""Reset user-created demo state (created shipments + clearance ledger).
Static seed shipments/routes are untouched. Run from repo root:
    python3 scripts/reset_demo_data.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import store  # noqa: E402

if __name__ == "__main__":
    print(store.reset_demo())