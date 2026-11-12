"""Make `app` importable when pytest runs from the repo root (CI).

Local runs use `cd backend && pytest`; CI runs `pytest backend/tests`
from the root — this conftest puts backend/ on sys.path for both.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
