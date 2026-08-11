"""
test_feedback_module_identity.py — MODULE IDENTIFIER CONVENTION V2, Phase 2
(Canonical Module Adoption — Feedback). Prueba que Feedback (`webapp/routers/
feedback.py`) adopta el `module_id` canónico del registro (Fase 1, CLOSED/
GREEN) para escrituras NUEVAS, y que el seam de normalización de lectura
(`normalize_feedback_module`) preserva compatibilidad histórica sin
reescribir NUNCA el YAML persistido.

NO cubre: rename de archivos, migración de YAML histórico, Academy feedback
(`webapp/routers/academy.py`, fuera de alcance de esta fase).
"""
from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import feedback as feedback_router

client = TestClient(app)


# ── normalize_feedback_module (seam puro, sin I/O) ───────────────────────────

# test 1
def test_historical_feedback_with_legacy_stem_normalizes_to_cpxx():
    raw, module = feedback_router.normalize_feedback_module("01_Arquitectura_y_Conectividad")
    assert raw == "01_Arquitectura_y_Conectividad"
    assert module == "CP01"


# test 2
def test_raw_module_preserves_persisted_legacy_value():
    raw, _module = feedback_router.normalize_feedback_module("A01_Arquitectura_y_Modelado")
    assert raw == "A01_Arquitectura_y_Modelado"


# test 3
def test_already_canonical_feedback_module_remains_canonical():
    raw, module = feedback_router.normalize_feedback_module("CP01")
    assert raw == "CP01" and module == "CP01"


# test 4
def test_unknown_feedback_module_remains_lossless():
    raw, module = feedback_router.normalize_feedback_module("un_modulo_que_no_existe")
    assert raw == "un_modulo_que_no_existe"
    assert module == "un_modulo_que_no_existe"  # nunca se fabrica un ID


# test 8 (a nivel de función — ver también el test HTTP más abajo)
def test_historical_and_new_module_values_normalize_to_the_same_canonical_bucket():
    _, from_legacy = feedback_router.normalize_feedback_module("01_Arquitectura_y_Conectividad")
    _, from_canonical = feedback_router.normalize_feedback_module("CP01")
    assert from_legacy == from_canonical == "CP01"


# ── Escrituras NUEVAS vía HTTP (module_content / section_content) ───────────

# test 5
def test_new_module_feedback_write_persists_canonical_module_id(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    resp = client.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert resp.status_code == 200
    saved = list(tmp_path.glob("module_feedback_*.yaml"))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["module"] == "CP01"


def test_new_section_feedback_write_persists_canonical_module_id(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    resp = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/espacios-transportes/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert resp.status_code == 200
    saved = list(tmp_path.glob("section_feedback_*.yaml"))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["module"] == "CP01"


def test_appendix_module_feedback_write_persists_canonical_cpa_id(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    resp = client.post(
        "/modules/A01_Arquitectura_y_Modelado/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert resp.status_code == 200
    saved = list(tmp_path.glob("module_feedback_*.yaml"))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["module"] == "CPA01"


# ── Compatibilidad histórica — mismo route/stem, discoverabilidad ───────────

# tests 6/7: no existe hoy un lector de feedback dentro de la webapp (el único
# consumidor que agrupa por módulo es el script standalone analyze_feedback.py,
# deliberadamente desacoplado de webapp.* — ver STOP report). "Discoverable
# desde la misma ruta de stem legacy" se prueba acá al nivel del seam: un
# registro histórico (module=stem legacy, escrito ANTES de esta fase) y uno
# nuevo (module=CP01, escrito DESPUÉS, misma ruta/stem) deben normalizar al
# MISMO valor — la garantía que cualquier lector futuro necesitaría para no
# perder acceso al feedback viejo.
def test_historical_and_new_feedback_for_the_same_route_stem_resolve_together(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    # Histórico: YA existe un YAML con el stem legacy (simula datos previos a
    # esta fase) — NUNCA se toca.
    historical = tmp_path / "module_feedback_20260101_000000_aaaaaaaa.yaml"
    historical.write_text(
        yaml.safe_dump({
            "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
            "module_title": "Arquitectura y Conectividad", "pack": "datasphere_core",
            "rating": "up", "user": "old@d.com", "ts": "2026-01-01T00:00:00+00:00",
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    before = historical.read_text(encoding="utf-8")

    # Escritura NUEVA vía la MISMA ruta (mismo stem legacy en la URL — el
    # route contract no cambia, ver HANDOFF).
    resp = client.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert resp.status_code == 200

    # test 9: el YAML histórico jamás se reescribió.
    assert historical.read_text(encoding="utf-8") == before

    # Ambos normalizan al MISMO module_id canónico — no se dividen en dos
    # buckets lógicos distintos (test 8, a nivel de datos reales).
    all_files = list(tmp_path.glob("module_feedback_*.yaml"))
    assert len(all_files) == 2
    resolved_modules = set()
    for f in all_files:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))["module"]
        _, canonical = feedback_router.normalize_feedback_module(raw)
        resolved_modules.add(canonical)
    assert resolved_modules == {"CP01"}


# test 10: comportamiento de auth/permisos sin cambios — feedback de módulo
# sigue sin exigir sesión (anónimo → 200, mismo criterio que antes de esta
# fase) y la validación de rating sigue rechazando valores inválidos igual.
def test_feedback_auth_and_validation_behavior_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    anon = TestClient(app)
    ok = anon.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert ok.status_code == 200
    bad_rating = anon.post(
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        data={"rating": "sideways", "pack": "datasphere_core"},
    )
    assert bad_rating.status_code == 400
