"""
test_human_capital_catalog_repository.py — CCHH Catalog Foundation.

Cubre el contrato del workbook (parse + validación FAIL-CLOSED), el contrato
del snapshot, la lectura tokenless del read-model, y la MÁQUINA DE ESTADOS de
durabilidad del refresh (A en memoria, escritura atómica de B, push_path,
verificación por BYTES EXACTOS, compensación, estado terminal
REMOTE_OUTCOME_UNKNOWN). El límite remoto se mockea en `remote_store`."""
from __future__ import annotations

import io
import json
from pathlib import Path

import openpyxl
import pytest
from openpyxl.worksheet.table import Table

import config
from core import remote_store
from webapp.services import human_capital_catalog_repository as repo


def _make_workbook(rows, *, headers=("catalogo", "valor", "activo", "orden"),
                   table_name="tblCatalogosCCHH", with_table=True) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    if with_table:
        ref = f"A1:{chr(ord('A') + len(headers) - 1)}{1 + len(rows)}"
        ws.add_table(Table(displayName=table_name, ref=ref))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["human-capital"])
    monkeypatch.setattr(config, "SPACES_BUCKET", "danwor-test")
    monkeypatch.setattr(config, "SPACES_PREFIX", "prod")
    # Por defecto: sin remoto (dev). Los tests de la máquina de estados lo activan.
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)


# ---------------------------------------------------------------------------
# 1. Workbook contract — parse + FAIL-CLOSED
# ---------------------------------------------------------------------------
def test_parse_happy_path_active_sorted_inactive_excluded():
    data = _make_workbook([
        ["seniority", "Senior", "Sí", 3],
        ["seniority", "Junior", "Sí", 1],
        ["seniority", "Semi Senior", "Sí", 2],
        ["seniority", "Trainee", "No", 4],   # inactiva -> excluida
        ["fuente", "LinkedIn", "Sí", 1],
    ])
    out = repo.parse_and_validate_workbook(data)
    assert [r["valor"] for r in out["seniority"]] == ["Junior", "Semi Senior", "Senior"]
    assert [r["valor"] for r in out["fuente"]] == ["LinkedIn"]
    assert out["canal_publicacion"] == []


def test_parse_rejects_missing_table():
    data = _make_workbook([["seniority", "Senior", "Sí", 1]], with_table=False)
    with pytest.raises(repo.CatalogWorkbookError) as ei:
        repo.parse_and_validate_workbook(data)
    assert any("tblCatalogosCCHH" in e for e in ei.value.errors)


def test_parse_rejects_wrong_headers():
    data = _make_workbook([["seniority", "Senior", "Sí", 1]],
                          headers=("catalogo", "value", "activo", "orden"))
    with pytest.raises(repo.CatalogWorkbookError):
        repo.parse_and_validate_workbook(data)


def test_parse_fail_closed_collects_all_row_errors():
    data = _make_workbook([
        ["desconocido", "X", "Sí", 1],     # catálogo desconocido
        ["seniority", "", "Sí", 2],        # valor vacío
        ["seniority", "Senior", "quizas", 3],  # activo inválido
        ["seniority", "Junior", "Sí", "x"],    # orden inválido
    ])
    with pytest.raises(repo.CatalogWorkbookError) as ei:
        repo.parse_and_validate_workbook(data)
    joined = " | ".join(ei.value.errors)
    assert "desconocido" in joined and "valor vacío" in joined
    assert "activo inválido" in joined and "orden inválido" in joined


def test_parse_rejects_duplicate_normalized_key():
    data = _make_workbook([
        ["seniority", "Senior", "Sí", 1],
        ["seniority", "  senior ", "No", 2],  # mismo (catalogo, valor norm.) -> duplicado
    ])
    with pytest.raises(repo.CatalogWorkbookError) as ei:
        repo.parse_and_validate_workbook(data)
    assert any("duplicado" in e for e in ei.value.errors)


# ---------------------------------------------------------------------------
# 2. Snapshot contract
# ---------------------------------------------------------------------------
def test_build_snapshot_shape():
    snap = repo.build_snapshot(
        {"seniority": [{"valor": "Senior", "orden": 1}]},
        refreshed_by="innovacion@danwor.com",
        source={"drive_id": "b!D", "item_id": "01I", "etag": "\"e,1\""},
    )
    assert snap["schema_version"] == repo.SCHEMA_VERSION
    assert snap["refreshed_by"] == "innovacion@danwor.com"
    assert snap["source"] == {"drive_id": "b!D", "item_id": "01I", "etag": "\"e,1\""}
    assert set(snap["catalogs"]) == set(repo.GOVERNED_CATALOGS)  # los 5 siempre presentes
    assert snap["catalogs"]["seniority"] == [{"valor": "Senior", "orden": 1}]
    assert snap["catalogs"]["fuente"] == []


# ---------------------------------------------------------------------------
# 4. Read-model (SSR tokenless, fail-soft)
# ---------------------------------------------------------------------------
def test_catalog_values_empty_when_no_snapshot():
    assert repo.catalog_values("seniority") == []


def test_catalog_values_reads_materialized_snapshot():
    p = repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    snap = repo.build_snapshot({"seniority": [{"valor": "Senior", "orden": 1},
                                              {"valor": "Junior", "orden": 2}]},
                               refreshed_by="x", source={"drive_id": "d", "item_id": "i"})
    p.write_text(json.dumps(snap), encoding="utf-8")
    assert repo.catalog_values("seniority") == ["Senior", "Junior"]
    assert repo.catalog_values("fuente") == []


# ---------------------------------------------------------------------------
# 5. Durability state machine
# ---------------------------------------------------------------------------
def _valid_bytes():
    return _make_workbook([["seniority", "Senior", "Sí", 1]])


def _enable_remote(monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(remote_store, "enabled", lambda: True)


def test_disabled_is_local_only_success(monkeypatch):
    res = repo.refresh_from_workbook(_valid_bytes(), refreshed_by="x",
                                     source={"drive_id": "d", "item_id": "i"})
    assert res["outcome"] == repo.SUCCESS
    assert res["counts"]["seniority"] == 1
    assert repo.catalog_values("seniority") == ["Senior"]


def test_push_true_is_success(monkeypatch):
    _enable_remote(monkeypatch)
    monkeypatch.setattr(remote_store, "push_path", lambda p: True)
    res = repo.refresh_from_workbook(_valid_bytes(), refreshed_by="x",
                                     source={"drive_id": "d", "item_id": "i"})
    assert res["outcome"] == repo.SUCCESS
    assert repo.catalog_values("seniority") == ["Senior"]


def test_push_false_remote_equals_b_is_success(monkeypatch):
    _enable_remote(monkeypatch)
    monkeypatch.setattr(remote_store, "push_path", lambda p: False)
    # remote == B: devolver los bytes actuales del archivo (== B recién escrito)
    monkeypatch.setattr(remote_store, "read_object_bytes", lambda p: Path(p).read_bytes())
    res = repo.refresh_from_workbook(_valid_bytes(), refreshed_by="x",
                                     source={"drive_id": "d", "item_id": "i"})
    assert res["outcome"] == repo.SUCCESS


def test_push_false_remote_equals_a_is_confirmed_failure(monkeypatch):
    _enable_remote(monkeypatch)
    # Pre-sembrar A (snapshot previo) en disco.
    A_snap = repo.build_snapshot({"seniority": [{"valor": "Previo", "orden": 1}]},
                                 refreshed_by="old", source={"drive_id": "d", "item_id": "i"})
    A_bytes = repo._serialize(A_snap)
    p = repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(A_bytes)

    monkeypatch.setattr(remote_store, "push_path", lambda p: False)
    monkeypatch.setattr(remote_store, "read_object_bytes", lambda p: A_bytes)  # remote == A
    res = repo.refresh_from_workbook(_valid_bytes(), refreshed_by="new",
                                     source={"drive_id": "d", "item_id": "i"})
    assert res["outcome"] == repo.CONFIRMED_FAILURE
    # local restaurado a A
    assert repo.snapshot_path().read_bytes() == A_bytes
    assert repo.catalog_values("seniority") == ["Previo"]


def test_push_false_remote_absent_is_confirmed_failure(monkeypatch):
    _enable_remote(monkeypatch)
    A_bytes = repo._serialize(repo.build_snapshot(
        {"seniority": [{"valor": "Previo", "orden": 1}]}, refreshed_by="old",
        source={"drive_id": "d", "item_id": "i"}))
    p = repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(A_bytes)
    monkeypatch.setattr(remote_store, "push_path", lambda p: False)
    monkeypatch.setattr(remote_store, "read_object_bytes", lambda p: None)  # ausente
    res = repo.refresh_from_workbook(_valid_bytes(), refreshed_by="new",
                                     source={"drive_id": "d", "item_id": "i"})
    assert res["outcome"] == repo.CONFIRMED_FAILURE
    assert repo.snapshot_path().read_bytes() == A_bytes


def test_unresolved_with_previous_compensating_push_ok_is_confirmed_failure(monkeypatch):
    _enable_remote(monkeypatch)
    A_bytes = repo._serialize(repo.build_snapshot(
        {"seniority": [{"valor": "Previo", "orden": 1}]}, refreshed_by="old",
        source={"drive_id": "d", "item_id": "i"}))
    p = repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(A_bytes)

    def _read_unresolved(_p):
        raise remote_store.RemoteReadError("boom")
    pushes = {"n": 0}
    def _push(_p):
        pushes["n"] += 1
        return pushes["n"] >= 2  # primer push (B) False; push compensatorio (A) True
    monkeypatch.setattr(remote_store, "push_path", _push)
    monkeypatch.setattr(remote_store, "read_object_bytes", _read_unresolved)
    res = repo.refresh_from_workbook(_valid_bytes(), refreshed_by="new",
                                     source={"drive_id": "d", "item_id": "i"})
    assert res["outcome"] == repo.CONFIRMED_FAILURE
    assert repo.snapshot_path().read_bytes() == A_bytes  # convergido a A (KNOWN_A)


def test_unresolved_with_previous_compensating_push_fails_is_terminal(monkeypatch):
    _enable_remote(monkeypatch)
    A_bytes = repo._serialize(repo.build_snapshot(
        {"seniority": [{"valor": "Previo", "orden": 1}]}, refreshed_by="old",
        source={"drive_id": "d", "item_id": "i"}))
    p = repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(A_bytes)
    monkeypatch.setattr(remote_store, "push_path", lambda p: False)  # B y compensación fallan
    monkeypatch.setattr(remote_store, "read_object_bytes",
                        lambda p: (_ for _ in ()).throw(remote_store.RemoteReadError("x")))
    with pytest.raises(repo.CatalogRemoteOutcomeUnknownError):
        repo.refresh_from_workbook(_valid_bytes(), refreshed_by="new",
                                   source={"drive_id": "d", "item_id": "i"})


def test_unresolved_without_previous_is_terminal(monkeypatch):
    _enable_remote(monkeypatch)
    monkeypatch.setattr(remote_store, "push_path", lambda p: False)
    monkeypatch.setattr(remote_store, "read_object_bytes",
                        lambda p: (_ for _ in ()).throw(remote_store.RemoteReadError("x")))
    with pytest.raises(repo.CatalogRemoteOutcomeUnknownError):
        repo.refresh_from_workbook(_valid_bytes(), refreshed_by="new",
                                   source={"drive_id": "d", "item_id": "i"})


def test_invalid_workbook_preserves_last_known_good_snapshot(monkeypatch):
    # Un workbook candidato INVÁLIDO nunca debe llegar a `_persist_snapshot`
    # (falla en `parse_and_validate_workbook`, antes de tocar disco/remoto) --
    # el snapshot last-known-good en disco queda exactamente como estaba.
    A_snap = repo.build_snapshot({"seniority": [{"valor": "Previo", "orden": 1}]},
                                 refreshed_by="old", source={"drive_id": "d", "item_id": "i"})
    A_bytes = repo._serialize(A_snap)
    p = repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(A_bytes)

    invalid = _make_workbook([["seniority", "", "Sí", 1]])  # valor vacío -> fail-closed
    with pytest.raises(repo.CatalogWorkbookError):
        repo.refresh_from_workbook(invalid, refreshed_by="new",
                                   source={"drive_id": "d", "item_id": "i"})
    assert repo.snapshot_path().read_bytes() == A_bytes  # LKG intacto, byte a byte
    assert repo.catalog_values("seniority") == ["Previo"]
