"""Proceso de apoyo de `test_cv_history_acceptance_processes.py`: otro INTÉRPRETE (como un segundo worker) que toma el candado COMÚN del libro de un contenido (`manifest.ledger_lock`) —el mismo que usan la tanda de importación, las
acciones manuales y la aceptación— y lo retiene hasta que la prueba lo suelta. No es una prueba (el nombre no empieza con `test_`). Uso: python tests/support_ledger_lock_holder.py <json con los parámetros>.

Parámetros: base_dir, run_id, key, ready_file (lo crea al tener el candado), release_file (espera a que exista para soltarlo)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

params = json.loads(sys.argv[1])
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402

config.BASE_DIR = Path(params["base_dir"])
config.PERSISTENCE_MODE = "local"
config.PERSIST_ENABLED = False

from webapp.services import cv_history_manifest as manifest  # noqa: E402

with manifest.ledger_lock(params["run_id"], params["key"]):
    Path(params["ready_file"]).write_text("candado tomado", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not Path(params["release_file"]).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
