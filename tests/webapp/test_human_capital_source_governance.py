"""
test_human_capital_source_governance.py — CCHH Catalog Foundation, follow-up:
enforcement server-side del catálogo GOBERNADO de Fuente, migration-safe --
MISMO contrato exacto que Seniority (ver
test_human_capital_seniority_governance.py).

CREATE: solo valores del catálogo activo (sin fallback a texto libre).
EDIT: preserva un valor legacy fuera-de-catálogo si NO cambia; si cambia, el
nuevo valor debe pertenecer al catálogo activo. El import bulk SÍ sigue
gobernado acá (a diferencia de Seniority) porque ya rechazaba una fuente
desconocida antes de este catálogo -- ver test_profile_import_service.py y
`human_capital_service.validate_source_for_import`, "no cambiar semántica
todavía"."""
from __future__ import annotations

import pytest

import config
from webapp.services import human_capital_service, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _seed_catalog(*values):
    snap = catalog_repo.build_snapshot(
        {"fuente": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"},
    )
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


# --- CREATE -----------------------------------------------------------------
def test_create_accepts_active_catalog_value():
    _seed_catalog("LinkedIn", "Referido")
    saved = human_capital_service.create_profile(
        {"first_name": "A", "last_name": "B", "source": "Referido"})
    assert saved["source"] == "Referido"


def test_create_accepts_empty_source():
    _seed_catalog("LinkedIn")
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B"})
    assert saved["source"] is None


def test_create_rejects_value_outside_catalog():
    _seed_catalog("LinkedIn", "Referido")
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "source": "Portal de empleo"})


def test_create_rejects_nonempty_when_catalog_unavailable():
    # sin snapshot -> no hay fallback a texto libre
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "source": "LinkedIn"})


def test_create_rejects_inactive_catalog_value():
    # Un valor "activo=No" nunca llega a `catalog_values()` (se excluye en
    # el parser, ver human_capital_catalog_repository.parse_and_validate_
    # workbook) -- desde la perspectiva del alta es indistinguible de un
    # valor que nunca existió: MISMO rechazo.
    _seed_catalog("LinkedIn")  # "Hunting" deliberadamente NO está en el catálogo activo
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "source": "Hunting"})


# --- EDIT (migration-safe) --------------------------------------------------
def test_edit_preserves_unchanged_legacy_value_outside_catalog():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "source": "Portal de empleo legacy"})
    _seed_catalog("LinkedIn", "Referido")  # el catálogo NO incluye el legacy
    updated = human_capital_service.update_profile(
        legacy["profile_id"], legacy["revision"],
        {"first_name": "A2", "last_name": "B", "source": "Portal de empleo legacy"})
    assert updated["source"] == "Portal de empleo legacy"
    assert updated["first_name"] == "A2"


def test_edit_rejects_changing_to_value_outside_catalog():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "source": "Portal de empleo legacy"})
    _seed_catalog("LinkedIn", "Referido")
    with pytest.raises(ValueError):
        human_capital_service.update_profile(
            legacy["profile_id"], legacy["revision"],
            {"first_name": "A", "last_name": "B", "source": "Otro invento"})


def test_edit_allows_changing_to_active_catalog_value():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "source": "Portal de empleo legacy"})
    _seed_catalog("LinkedIn", "Referido")
    updated = human_capital_service.update_profile(
        legacy["profile_id"], legacy["revision"],
        {"first_name": "A", "last_name": "B", "source": "Referido"})
    assert updated["source"] == "Referido"


# --- Import (SÍ gobernado, a diferencia de Seniority) -----------------------
def test_import_accepts_active_catalog_value():
    _seed_catalog("Flyer")
    human_capital_service.validate_source_for_import("Flyer")  # no debe levantar


def test_import_accepts_empty_source():
    # `shaped.get("source")` (el único caller real, ver profile_import_
    # service.py) ya normalizó "" -> None en validate_and_shape_fields --
    # nunca una cadena vacía cruda.
    human_capital_service.validate_source_for_import(None)  # no debe levantar, sin catálogo sembrado


def test_import_rejects_value_outside_catalog():
    _seed_catalog("Flyer")
    with pytest.raises(ValueError):
        human_capital_service.validate_source_for_import("Portal de empleo")


# --- No hardcoded authority restante -----------------------------------------
def test_valid_sources_constant_no_longer_exists_anywhere():
    # `VALID_SOURCES` migró por completo al catálogo -- ni el service ni el
    # repository deben seguir declarándolo (una segunda autoridad viva sería
    # exactamente el "duplicar autoridad" que este slice evita).
    assert not hasattr(human_capital_service, "VALID_SOURCES")
    assert not hasattr(profile_repository, "VALID_SOURCES")
