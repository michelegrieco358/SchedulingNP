"""Ensure project root is importable in tests without setting PYTHONPATH.

This file is collected by pytest as a root-level conftest and inserts the
repository root on sys.path so `from src import ...` works out of the box.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

