"""
test_human_capital_seniority_governance.py — CCHH Catalog Foundation §6:
enforcement server-side del catálogo GOBERNADO de Seniority, migration-safe.

CREATE: solo valores del catálogo activo (sin fallback a texto libre).
EDIT: preserva un valor legacy fuera-de-catálogo si NO cambia; si cambia, el
nuevo valor debe pertenecer al catálogo activo. El import bulk NO se gobierna
(vector de migración)."""
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
        {"seniority": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"},
    )
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


# --- CREATE -----------------------------------------------------------------
def test_create_accepts_active_catalog_value():
    _seed_catalog("Junior", "Senior")
    saved = human_capital_service.create_profile(
        {"first_name": "A", "last_name": "B", "seniority": "Senior"})
    assert saved["seniority"] == "Senior"


def test_create_accepts_empty_seniority():
    _seed_catalog("Senior")
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B"})
    assert saved["seniority"] is None


def test_create_rejects_value_outside_catalog():
    _seed_catalog("Junior", "Senior")
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "seniority": "Guru"})


def test_create_rejects_nonempty_when_catalog_unavailable():
    # sin snapshot -> no hay fallback a texto libre
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "seniority": "Senior"})


# --- EDIT (migration-safe) --------------------------------------------------
def test_edit_preserves_unchanged_legacy_value_outside_catalog():
    # Sembrar un profile con seniority legacy fuera de catálogo, saltando la
    # gobernanza del alta (create directo en el repository).
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "seniority": "ArquitectoLegacy"})
    _seed_catalog("Junior", "Senior")  # el catálogo NO incluye el legacy
    # Edición NO relacionada (cambia el nombre, mantiene seniority legacy) -> OK
    updated = human_capital_service.update_profile(
        legacy["profile_id"], legacy["revision"],
        {"first_name": "A2", "last_name": "B", "seniority": "ArquitectoLegacy"})
    assert updated["seniority"] == "ArquitectoLegacy"
    assert updated["first_name"] == "A2"


def test_edit_rejects_changing_to_value_outside_catalog():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "seniority": "ArquitectoLegacy"})
    _seed_catalog("Junior", "Senior")
    with pytest.raises(ValueError):
        human_capital_service.update_profile(
            legacy["profile_id"], legacy["revision"],
            {"first_name": "A", "last_name": "B", "seniority": "OtroInvento"})


def test_edit_allows_changing_to_active_catalog_value():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "seniority": "ArquitectoLegacy"})
    _seed_catalog("Junior", "Senior")
    updated = human_capital_service.update_profile(
        legacy["profile_id"], legacy["revision"],
        {"first_name": "A", "last_name": "B", "seniority": "Senior"})
    assert updated["seniority"] == "Senior"
