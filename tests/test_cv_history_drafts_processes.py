"""Borradores — integridad ENTRE PROCESOS (dos workers sobre el mismo disco): una revisión obsoleta no puede sobrescribir una más reciente. Cada worker es un intérprete independiente
(`tests/support_draft_worker.py`) que usa el servicio real sobre la misma carpeta de datos temporal; el candado de hilos no los protege, el candado del sistema operativo entre procesos sí. Datos sintéticos."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import config
from tests.test_cv_history_recoverability import RUN, World, _ext
from webapp.services import cv_history_drafts as drafts, cv_history_manifest as mf

WORKER = Path(__file__).resolve().parent / "support_draft_worker.py"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)


def one_exception() -> str:
    w = World()
    key = w.doc(_ext(emails=["a@sintetico.test", "b@sintetico.test"], alts=[("Persona Sintetica", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    w.build()
    return key


def launch(tmp_path, key, n, **over):
    """Arranca el worker `n` como proceso independiente; no espera. Devuelve (proceso, archivo de salida)."""
    out = tmp_path / f"worker-{n}-{int(time.time() * 1000)}.json"
    params = {"base_dir": str(config.BASE_DIR), "run_id": RUN, "key": key, "actor": f"worker{n}@danwor.com", "expected_revision": 0, "first_name": f"Proceso{n}", "go_file": str(tmp_path / "go"), "hold": 0, "out": str(out),
              "ready_file": str(tmp_path / f"ready-{n}"), **over}
    return subprocess.Popen([sys.executable, str(WORKER), json.dumps(params)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True), out


def finish(procs, timeout=120):
    results = []
    for proc, out in procs:
        _stdout, stderr = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, stderr[-2000:]
        results.append(json.loads(out.read_text(encoding="utf-8")))
    return results


def wait_for(files, timeout=120):
    deadline = time.monotonic() + timeout
    while not all(f.exists() for f in files):
        assert time.monotonic() < deadline, "los workers no llegaron a tiempo"
        time.sleep(0.02)


def race(tmp_path, key, n, **over):
    procs = [launch(tmp_path, key, i, **over) for i in range(n)]
    wait_for([tmp_path / f"ready-{i}" for i in range(n)])                                        # todos terminaron de importar y esperan la señal de largada
    (tmp_path / "go").write_text("go", encoding="utf-8")
    return finish(procs)


def test_six_workers_racing_with_the_same_revision_let_exactly_one_win_and_nothing_is_overwritten(tmp_path):
    key = one_exception()
    results = race(tmp_path, key, 6, hold=0.3)                                                    # con 0,3 s dentro de la sección crítica, sin candado entre procesos todos pasarían la comparación
    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes == ["conflict"] * 5 + ["ok"], results
    doc = mf.get_draft(RUN, key)
    winner = next(r for r in results if r["outcome"] == "ok")
    assert doc["revision"] == 1 and winner["revision"] == 1 and len(doc["history"]) == 1 and doc["fields"]["first_name"].startswith("Proceso")
    assert all(r["current_revision"] == 1 for r in results if r["outcome"] == "conflict")                 # cada perdedor vio la revisión ganadora
    assert doc["updated_by"] == doc["history"][0]["by"] and doc["updated_by"].endswith("@danwor.com")


def test_a_stale_revision_from_another_process_can_never_overwrite_a_newer_one(tmp_path):
    key = one_exception()
    (tmp_path / "go").write_text("go", encoding="utf-8")
    first = finish([launch(tmp_path, key, 1, expected_revision=0, first_name="Primera")])[0]
    second = finish([launch(tmp_path, key, 2, expected_revision=1, first_name="Segunda")])[0]
    stale = finish([launch(tmp_path, key, 3, expected_revision=1, first_name="Obsoleta")])[0]                  # cargó la revisión 1, pero ya existe la 2
    assert (first["outcome"], second["outcome"], stale["outcome"]) == ("ok", "ok", "conflict") and stale["current_revision"] == 2
    doc = mf.get_draft(RUN, key)
    assert doc["revision"] == 2 and doc["fields"]["first_name"] == "Segunda" and doc["updated_by"] == "worker2@danwor.com"
    assert [h["by"] for h in doc["history"]] == ["worker1@danwor.com", "worker2@danwor.com"] and doc["history"][1]["before"]["first_name"] == "Primera"      # la corrección anterior sigue en el historial


def test_a_worker_and_this_process_cannot_both_write_the_same_revision(tmp_path):
    key = one_exception()
    entered = tmp_path / "entered"
    proc = launch(tmp_path, key, 1, hold=1.0, entered_file=str(entered))                         # el worker tarda 1 s dentro de su sección crítica
    wait_for([tmp_path / "ready-1"])
    (tmp_path / "go").write_text("go", encoding="utf-8")
    wait_for([entered])                                                                          # el worker ya tiene el candado y todavía no escribió
    with pytest.raises(drafts.DraftConflict) as conflict:
        drafts.save(RUN, key, {"first_name": "Este proceso"}, actor="local@danwor.com", expected_revision=0)       # espera el candado, y al entrar encuentra la revisión 1
    assert conflict.value.current["revision"] == 1 and conflict.value.current["fields"]["first_name"] == "Proceso1"
    assert finish([proc])[0]["outcome"] == "ok" and mf.get_draft(RUN, key)["fields"]["first_name"] == "Proceso1"


def test_when_another_process_holds_the_lock_too_long_nothing_is_written_and_retrying_works(tmp_path, monkeypatch):
    key = one_exception()
    path = mf._run_dir(RUN) / "drafts" / f"{key}.json"
    (tmp_path / "go").write_text("go", encoding="utf-8")
    with mf._interprocess_lock(path):                                                            # este proceso retiene el candado
        busy = finish([launch(tmp_path, key, 1, lock_timeout=0.4)])[0]
    assert busy["outcome"] == "error" and busy["kind"] == "DraftError" and "Otro proceso está guardando" in busy["message"] and mf.get_draft(RUN, key) is None
    assert finish([launch(tmp_path, key, 2, lock_timeout=5)])[0]["outcome"] == "ok" and mf.get_draft(RUN, key)["revision"] == 1      # liberado el candado, reintentar funciona


def test_the_lock_is_per_draft_so_different_exceptions_do_not_wait_for_each_other(tmp_path):
    w = World()
    k1, k2 = (w.doc(_ext(emails=[f"a{i}@sintetico.test", f"b{i}@sintetico.test"], alts=[(f"Persona{chr(97 + i)} Sintetica{chr(97 + i)}", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"])) for i in range(2))
    w.build()
    (tmp_path / "go").write_text("go", encoding="utf-8")
    with mf._interprocess_lock(mf._run_dir(RUN) / "drafts" / f"{k1}.json"):
        other = finish([launch(tmp_path, k2, 1, lock_timeout=0.4)])[0]
    assert other["outcome"] == "ok" and mf.get_draft(RUN, k2)["revision"] == 1 and mf.get_draft(RUN, k1) is None


def test_the_lock_files_live_outside_the_persisted_data_so_they_never_reach_spaces(tmp_path):
    key = one_exception()
    drafts.save(RUN, key, {"first_name": "Zulema"}, actor="a@danwor.com", expected_revision=0)
    import tempfile
    locks = Path(tempfile.gettempdir()) / "danwor-manifest-locks"
    assert locks.is_dir() and any(locks.glob("*.lock"))
    assert not list(Path(config.BASE_DIR).rglob("*.lock"))                                         # nada de candados dentro de los datos del negocio (se sincronizarían)
    assert [p.name for p in (mf._run_dir(RUN) / "drafts").iterdir()] == [f"{key}.json"]


def test_negative_control_without_the_inter_process_lock_every_worker_passes_the_revision_check(tmp_path):
    """Sin el candado entre procesos la comparación de revisión por sí sola NO protege: todos los workers la pasan a la vez (ninguno recibe un conflicto limpio) y solo fallan por azar —errores del sistema de archivos o la
    relectura posterior—. Es lo que hace imprescindible el candado (en Linux los pisados incluso «terminan bien»)."""
    key = one_exception()
    results = race(tmp_path, key, 4, hold=0.3, no_lock=True)
    assert "conflict" not in [r["outcome"] for r in results], results                              # todos pasaron la comparación a la vez; lo que ocurra después depende del sistema operativo (Windows: errores de reemplazo; Linux: pisados «exitosos»)
