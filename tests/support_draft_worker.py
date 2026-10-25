"""Proceso de apoyo de `test_cv_history_drafts_processes.py`: un WORKER independiente (otro intérprete, como un segundo worker de uvicorn) que guarda un borrador sobre el MISMO disco que el proceso de la prueba.
No es una prueba (el nombre no empieza con `test_`). Uso: python tests/support_draft_worker.py <json con los parámetros>.

Parámetros: base_dir, run_id, key, actor, expected_revision, first_name, go_file (el worker espera a que exista: todos arrancan juntos), hold (segundos que se demora DENTRO de la sección crítica, justo antes de escribir, para
ensanchar la ventana de carrera), entered_file/ready_file (señales hacia la prueba: ya dentro de la sección crítica / ya listo para largar), no_lock (negativo de control: anula el candado entre procesos), lock_timeout, out (archivo donde deja su resultado)."""
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

from webapp.services import cv_history_drafts as drafts, cv_history_manifest as manifest  # noqa: E402

if params.get("no_lock"):
    import contextlib

    @contextlib.contextmanager
    def _no_lock(path, timeout=None):
        yield
    manifest._interprocess_lock = _no_lock
if params.get("lock_timeout") is not None:
    manifest._DRAFT_LOCK_TIMEOUT_S = float(params["lock_timeout"])
if params.get("hold"):
    real_write = manifest._write

    def slow_write(path, payload):
        if params.get("entered_file"):
            Path(params["entered_file"]).write_text("dentro de la sección crítica", encoding="utf-8")        # avisa que ya tiene el candado y todavía no escribió
        time.sleep(float(params["hold"]))
        real_write(path, payload)
    manifest._write = slow_write

if params.get("ready_file"):
    Path(params["ready_file"]).write_text("listo", encoding="utf-8")                                      # terminó de importar: espera la señal de largada
go = Path(params["go_file"])
deadline = time.monotonic() + 60
while not go.exists() and time.monotonic() < deadline:
    time.sleep(0.005)

try:
    out = drafts.save(params["run_id"], params["key"], {"first_name": params["first_name"]}, actor=params["actor"], expected_revision=params["expected_revision"])
    result = {"outcome": "ok", "revision": out["doc"]["revision"]}
except drafts.DraftConflict as exc:
    result = {"outcome": "conflict", "current_revision": (exc.current or {}).get("revision")}
except drafts.DraftError as exc:
    result = {"outcome": "error", "kind": "DraftError", "message": str(exc)}
except Exception as exc:                                                                       # noqa: BLE001 — el resultado se informa tal cual al proceso de la prueba
    result = {"outcome": "error", "kind": type(exc).__name__, "message": str(exc)}
Path(params["out"]).write_text(json.dumps(result), encoding="utf-8")
