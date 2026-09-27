"""
test_human_capital_potencial_governance.py — CCHH Catalog Foundation, P2:
enforcement server-side del catálogo GOBERNADO de Marca de potencial
(`potencial`), migration-safe -- MISMO contrato exacto que Seniority (ver
test_human_capital_seniority_governance.py).

CREATE: solo valores del catálogo activo (sin fallback a texto libre).
EDIT: preserva un valor legacy fuera-de-catálogo si NO cambia; si cambia, el
nuevo valor debe pertenecer al catálogo activo. El import bulk NO se
gobierna (mismo criterio que Seniority, no el de Fuente)."""
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
        {"potencial": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"},
    )
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


# --- CREATE -----------------------------------------------------------------
def test_create_accepts_active_catalog_value():
    _seed_catalog("Terna futura", "No reconsiderar")
    saved = human_capital_service.create_profile(
        {"first_name": "A", "last_name": "B", "potential_mark": "Terna futura"})
    assert saved["potential_mark"] == "Terna futura"


def test_create_accepts_empty_potential_mark():
    _seed_catalog("Terna futura")
    saved = human_capital_service.create_profile({"first_name": "A", "last_name": "B"})
    assert saved["potential_mark"] is None


def test_create_rejects_value_outside_catalog():
    _seed_catalog("Terna futura", "No reconsiderar")
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "potential_mark": "Invento"})


def test_create_rejects_nonempty_when_catalog_unavailable():
    # sin snapshot -> no hay fallback a texto libre
    with pytest.raises(ValueError):
        human_capital_service.create_profile(
            {"first_name": "A", "last_name": "B", "potential_mark": "Terna futura"})


# --- EDIT (migration-safe) --------------------------------------------------
def test_edit_preserves_unchanged_legacy_value_outside_catalog():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "potential_mark": "recons (legacy)"})
    _seed_catalog("Terna futura", "No reconsiderar")  # el catálogo NO incluye el legacy
    updated = human_capital_service.update_profile(
        legacy["profile_id"], legacy["revision"],
        {"first_name": "A2", "last_name": "B", "potential_mark": "recons (legacy)"})
    assert updated["potential_mark"] == "recons (legacy)"
    assert updated["first_name"] == "A2"


def test_edit_rejects_changing_to_value_outside_catalog():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "potential_mark": "recons (legacy)"})
    _seed_catalog("Terna futura", "No reconsiderar")
    with pytest.raises(ValueError):
        human_capital_service.update_profile(
            legacy["profile_id"], legacy["revision"],
            {"first_name": "A", "last_name": "B", "potential_mark": "OtroInvento"})


def test_edit_allows_changing_to_active_catalog_value():
    legacy = profile_repository.create({"first_name": "A", "last_name": "B", "potential_mark": "recons (legacy)"})
    _seed_catalog("Terna futura", "No reconsiderar")
    updated = human_capital_service.update_profile(
        legacy["profile_id"], legacy["revision"],
        {"first_name": "A", "last_name": "B", "potential_mark": "No reconsiderar"})
    assert updated["potential_mark"] == "No reconsiderar"
