"""Pytest bootstrap for MimOSA.

Ensures the project root is on ``sys.path`` so tests can ``import mimosa``
without an editable install. Located at the repo root so pytest's rootdir
detection picks it up automatically.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
