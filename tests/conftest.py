"""
conftest.py — Setup de tests.

Agrega el repo root a sys.path antes de colectar/importar los test files para que
`import webapp...` / `import core...` resuelvan. (El stub de Streamlit se retiró
junto con el runtime legacy: la webapp no depende de streamlit.)
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
