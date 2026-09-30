"""test_admin_catalog_generation_error_handling.py — regresión de producción
(2026-09-30): `POST /admin/catalog/generate` devolvía un 500 opaco cuando el
mixed-mode real de SharePoint (legacy root JSON + carpeta CLI nueva, ver
Ticket A) contenía una colisión de identidad real (`CatalogCollisionError`,
`tools/catalog/generate_catalog.py`).

Root cause CONFIRMADA por repro local (ver
`_handoffs/catalog-cli-production-regeneration-regression.md`):
`admin_tools.run_catalog_generation()` SÍ captura la excepción del generador
(`except Exception as exc:`) pero su propio manejador crashea con
`NameError: name 'traceback' is not defined` (el módulo `traceback` nunca se
importa en `webapp/services/admin_tools.py`) — ese `NameError` secundario,
no el `CatalogCollisionError` original, es lo que escapa sin capturar y
produce el 500 real.

Esta suite NO toca el contrato de colisión de Ticket A (eso está bien,
`CatalogCollisionError` para contenido semánticamente divergente en
mixed-mode es el comportamiento DESEADO) — sólo prueba que el *manejo* de
esa excepción (y de cualquier otra) funciona: nunca deja escapar nada,
siempre devuelve un string `"[ERROR] ..."` legible."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access
from webapp.main import app
from webapp.services import admin_tools, auth_service

access.APP_OWNER = "test@danwor.com"
auth_service.ALLOWED_USERS = "ALL"
_SESSION = auth_service.create_session(
    {"email": "test@danwor.com", "name": "Test User", "first_name": "Test", "family_name": "User"}
)
diag_client = TestClient(app)
diag_client.cookies.set(auth_service.COOKIE_NAME, _SESSION)
diag_client.cookies.set("dw-diagnostic", "on")

# tests/conftest.py redirige config.BASE_DIR a un tmp vacío en TODO test
# (autouse, para que nadie escriba sobre el repo real). run_catalog_generation
# necesita el BASE_DIR REAL para poder importar tools/catalog/generate_catalog.py
# (código fuente, no datos runtime) -- mismo criterio que los tests de
# persistencia real documentados en conftest.py: revertimos el monkeypatch
# explícitamente en estos tests, capturando el valor real ANTES de que el
# fixture autouse lo pise.
_REAL_BASE_DIR = config.BASE_DIR


def _table(name: str) -> dict:
    return {"definitions": {name: {
        "kind": "entity", "@EndUserText.label": name,
        "@DataWarehouse.repository": {"technicalType": {"#": "DWC_LOCAL_TABLE"}},
        "elements": {"x": {"type": "cds.String", "length": 10}},
    }}}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_mixed_mode_divergent_collision(inbound: Path) -> None:
    """MISMA forma exacta que el mixed-mode productivo real (ver HANDOFF del
    incidente): un cliente con el MISMO artefacto (`TABLE`) definido en el
    JSON legacy de la raíz Y en una carpeta CLI tipada (`local-tables/`),
    con contenido semánticamente DISTINTO -- dispara `CatalogCollisionError`
    real (no simulado), MISMO escenario que
    `tests/webapp/test_catalog_cli_artifact_folders.py::
    test_mixed_mode_divergent_content_raises_deterministic_collision`, pero
    acá se ejercita a través de la capa completa de `admin_tools`, no
    directo contra `generate_catalog.load_folder`."""
    _write(inbound / "ClienteX" / "TABLE.json", _table("TABLE"))
    diff = _table("TABLE")
    diff["definitions"]["TABLE"]["elements"]["y"] = {"type": "cds.Integer"}
    _write(inbound / "ClienteX" / "local-tables" / "TABLE.json", diff)


def test_run_catalog_generation_never_raises_on_a_real_collision(tmp_path, monkeypatch):
    # Root cause del incidente: ANTES del fix, esto lanzaba NameError (no
    # CatalogCollisionError, no nada capturable por el caller) -- el test
    # documenta el CONTRATO correcto: la función NUNCA deja escapar una
    # excepción, pase lo que pase en los generadores que orquesta.
    inbound = tmp_path / "inbound"
    out = tmp_path / "out"
    _seed_mixed_mode_divergent_collision(inbound)
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.setattr(config, "BASE_DIR", _REAL_BASE_DIR, raising=False)

    result = admin_tools.run_catalog_generation(graph_token="")

    assert isinstance(result, str)


def test_a_real_collision_surfaces_the_original_catalog_error_not_a_name_error(tmp_path, monkeypatch):
    # No alcanza con "no explota" -- el mensaje visible en el panel debe ser
    # el `CatalogCollisionError` REAL (información accionable: qué artefacto,
    # qué carpetas), nunca un `NameError` que no dice nada del problema real.
    inbound = tmp_path / "inbound"
    out = tmp_path / "out"
    _seed_mixed_mode_divergent_collision(inbound)
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.setattr(config, "BASE_DIR", _REAL_BASE_DIR, raising=False)

    result = admin_tools.run_catalog_generation(graph_token="")

    assert "CatalogCollisionError" in result
    assert "NameError" not in result
    assert "TABLE" in result  # identifica el artefacto en colisión


def test_an_unrelated_generator_exception_still_returns_a_diagnosable_string(tmp_path, monkeypatch):
    # Cualquier otra excepción inesperada (no sólo CatalogCollisionError)
    # debe seguir siendo diagnosticable -- nunca tragada en silencio, nunca
    # una excepción sin capturar. Un JSON malformado se saltea con [WARN] (no
    # es una excepción -- comportamiento existente correcto, no se toca), así
    # que para forzar una excepción real y no relacionada con colisiones se
    # apunta DANWOR_CATALOG_DIR a un ARCHIVO (no directorio): el generador
    # falla al intentar crear carpetas de salida dentro de él.
    inbound = tmp_path / "inbound"
    out = tmp_path / "out_is_actually_a_file"
    _write(inbound / "ClienteX" / "TABLE.json", _table("TABLE"))
    out.write_text("no soy un directorio", encoding="utf-8")
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.setattr(config, "BASE_DIR", _REAL_BASE_DIR, raising=False)

    result = admin_tools.run_catalog_generation(graph_token="")

    assert isinstance(result, str)
    assert "[ERROR]" in result
    assert "NameError" not in result


def test_a_successful_generation_path_still_works(tmp_path, monkeypatch):
    # Camino feliz -- flat + nested semánticamente equivalentes, SIN
    # colisión -- debe completar normalmente, sin "[ERROR]" en el resultado.
    inbound = tmp_path / "inbound"
    out = tmp_path / "out"
    _write(inbound / "ClienteX" / "TABLE.json", _table("TABLE"))
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.setattr(config, "BASE_DIR", _REAL_BASE_DIR, raising=False)

    result = admin_tools.run_catalog_generation(graph_token="")

    assert "[ERROR]" not in result


def test_the_real_http_route_never_500s_on_a_collision_and_surfaces_it(tmp_path, monkeypatch):
    # Ejercita la ruta HTTP REAL (no sólo la función de servicio) -- el 500 en
    # producción lo vio el owner acá, a través de FastAPI, no llamando a
    # admin_tools directo. `webapp/main.py::admin_catalog_generate` no tiene
    # try/except propio, así que esto confirma que la respuesta completa
    # (status + body renderizado) ya es correcta con el fix aplicado.
    inbound = tmp_path / "inbound"
    out = tmp_path / "out"
    _seed_mixed_mode_divergent_collision(inbound)
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.setattr(config, "BASE_DIR", _REAL_BASE_DIR, raising=False)

    response = diag_client.post("/admin/catalog/generate", data={"graph_token": ""})

    assert response.status_code == 200
    assert "CatalogCollisionError" in response.text
    assert "NameError" not in response.text
