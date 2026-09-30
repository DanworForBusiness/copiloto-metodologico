"""test_security_catalog_cli_data_access_controls.py — el generador de
Seguridad/DAC (`tools/catalog/generate_security_catalog.py`) sólo reconocía
la carpeta legacy `Seguridad/` — la carpeta FÍSICA del export CLI de
Datasphere, `data-access-controls/` (ya reconocida por el generador de
DOMINIOS desde Ticket A, `CATALOG_ARTIFACT_TYPE_DIRS`), era invisible acá,
dejando clientes migrados al layout CLI (p. ej. Biogenesis, 16 DAC reales
`AC_*`) completamente fuera del catálogo de seguridad — "0 cliente(s)" sin
ningún error visible.

Forense real (ver `_handoffs/security-catalog-cli-data-access-controls.md`):
identidad de DAC YA es 100% basada en contenido
(`@DataWarehouse.dataAccessControl.definition`), nunca en prefijo de nombre
técnico — confirmado corriendo `load_security()` directo contra los 16
archivos reales `AC_*.json`, sin ningún cambio de parser. El único bug real
era DESCUBRIMIENTO DE CARPETA."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "catalog"))
import generate_security_catalog as sec  # noqa: E402


def _dac(name: str, source: str = "") -> dict:
    defn = {
        "kind": "entity",
        "@EndUserText.label": f"{name} - Control de acceso",
        "@DataWarehouse.dataAccessControl.definition": {
            "sourceEntity": source, "principalElement": "REGION",
        },
        "elements": {"REGION": {"type": "cds.String", "length": 10}},
    }
    return {"definitions": {name: defn}}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ── Caso 1: sólo CLI (sin legacy) ───────────────────────────────────────────

def test_cli_only_client_is_discovered(tmp_path):
    # ANTES del fix: invisible ("0 cliente(s)"). DESPUÉS: descubierto.
    _write(tmp_path / "Biogenesis" / "data-access-controls" / "AC_CANAL.json", _dac("AC_CANAL"))
    roots = sec._discover_client_roots(tmp_path)
    assert len(roots) == 1
    cliente, dirs = roots[0]
    assert cliente == "Biogenesis"
    assert len(dirs) == 1
    assert dirs[0].name == "data-access-controls"
    artifacts = sec.load_security(dirs, cliente)
    assert [a.dac_name for a in artifacts] == ["AC_CANAL"]


# ── Caso 2: legacy en minúscula, explícito (no por accidente de NTFS) ──────

def test_lowercase_legacy_seguridad_is_discovered_explicitly(tmp_path):
    _write(tmp_path / "ClienteX" / "seguridad" / "DAC_FOO.json", _dac("DAC_FOO"))
    roots = sec._discover_client_roots(tmp_path)
    assert len(roots) == 1
    cliente, dirs = roots[0]
    assert cliente == "ClienteX"
    assert dirs[0].name == "seguridad"
    # La normalización debe ser explícita (no depender del filesystem):
    assert sec._security_dir_kind("seguridad") == sec._security_dir_kind("Seguridad") == sec._security_dir_kind("SEGURIDAD")
    assert sec._security_dir_kind("data-access-controls") == sec._security_dir_kind("Data_Access_Controls")


# ── Caso 3: mixed-mode, legacy + CLI coexisten ──────────────────────────────

def test_mixed_mode_legacy_and_cli_union_without_duplicates(tmp_path):
    _write(tmp_path / "ClienteX" / "Seguridad" / "DAC_LEGACY.json", _dac("DAC_LEGACY"))
    _write(tmp_path / "ClienteX" / "data-access-controls" / "AC_NUEVO.json", _dac("AC_NUEVO"))
    roots = sec._discover_client_roots(tmp_path)
    assert len(roots) == 1
    cliente, dirs = roots[0]
    assert {d.name for d in dirs} == {"Seguridad", "data-access-controls"}
    artifacts = sec.load_security(dirs, cliente)
    assert sorted(a.dac_name for a in artifacts) == ["AC_NUEVO", "DAC_LEGACY"]  # sin duplicados


def test_mixed_mode_identical_dac_in_both_dedups_no_duplicate(tmp_path):
    # Mismo DAC, mismo contenido, en legacy Y en CLI (p. ej. no borrado todavía
    # del lado legacy) -> UNA sola entidad, no dos.
    _write(tmp_path / "ClienteX" / "Seguridad" / "AC_CANAL.json", _dac("AC_CANAL"))
    _write(tmp_path / "ClienteX" / "data-access-controls" / "AC_CANAL.json", _dac("AC_CANAL"))
    roots = sec._discover_client_roots(tmp_path)
    _, dirs = roots[0]
    artifacts = sec.load_security(dirs, "ClienteX")
    assert [a.dac_name for a in artifacts] == ["AC_CANAL"]


def test_mixed_mode_divergent_same_dac_raises_collision_not_silent_choice(tmp_path):
    # Mismo DAC, contenido DISTINTO entre legacy y CLI -> nunca elegir uno en
    # silencio, reportar colisión determinística (mismo contrato que el
    # catálogo de dominios).
    _write(tmp_path / "ClienteX" / "Seguridad" / "AC_CANAL.json", _dac("AC_CANAL", source="VP_CANAL_OLD"))
    _write(tmp_path / "ClienteX" / "data-access-controls" / "AC_CANAL.json", _dac("AC_CANAL", source="VP_CANAL_NEW"))
    roots = sec._discover_client_roots(tmp_path)
    _, dirs = roots[0]
    with pytest.raises(sec.SecurityCollisionError):
        sec.load_security(dirs, "ClienteX")


# ── Caso 4: nombre AC_-prefixed, forma real, descubierto sin requerir DAC_ ──

def test_ac_prefixed_real_shaped_dac_is_discovered_without_dac_prefix(tmp_path):
    _write(tmp_path / "Biogenesis" / "data-access-controls" / "AC_VENTAS_1.json", _dac("AC_VENTAS_1"))
    roots = sec._discover_client_roots(tmp_path)
    _, dirs = roots[0]
    artifacts = sec.load_security(dirs, "Biogenesis")
    assert artifacts and artifacts[0].dac_name == "AC_VENTAS_1"


# ── Caso 5: JSON no-DAC dentro de data-access-controls no se cuela ─────────

def test_non_dac_json_inside_data_access_controls_is_not_falsely_included(tmp_path):
    # Un archivo en la carpeta correcta pero SIN la anotación DAC real no debe
    # aparecer como si fuera un control de acceso sólo por estar en esa carpeta.
    not_a_dac = {"definitions": {"SOME_VIEW": {
        "kind": "entity", "@EndUserText.label": "Vista cualquiera",
        "elements": {"X": {"type": "cds.String", "length": 5}},
    }}}
    _write(tmp_path / "Biogenesis" / "data-access-controls" / "SOME_VIEW.json", not_a_dac)
    roots = sec._discover_client_roots(tmp_path)
    _, dirs = roots[0]
    artifacts = sec.load_security(dirs, "Biogenesis")
    assert artifacts == []


# ── Caso 6: comportamiento legacy existente sin cambios ─────────────────────

def test_existing_legacy_seguridad_only_behavior_unchanged(tmp_path):
    _write(tmp_path / "ClienteX" / "Seguridad" / "DAC_FOO.json", _dac("DAC_FOO"))
    roots = sec._discover_client_roots(tmp_path)
    assert len(roots) == 1
    cliente, dirs = roots[0]
    assert cliente == "ClienteX"
    assert [d.name for d in dirs] == ["Seguridad"]
    artifacts = sec.load_security(dirs, cliente)
    assert [a.dac_name for a in artifacts] == ["DAC_FOO"]


def test_no_recognized_security_folder_at_all_returns_no_roots(tmp_path):
    (tmp_path / "ClienteX" / "OtraCosa").mkdir(parents=True)
    assert sec._discover_client_roots(tmp_path) == []
