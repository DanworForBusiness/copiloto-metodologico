"""
test_profile_import_service.py — Capital Humano Phase 3.8 (Safe Bulk
Profile Import): contrato de `webapp.services.profile_import_service` —
parseo XLSX, preview (cero writes), integridad de token firmado, y
confirm — sin HTTP (ver test_human_capital_profile_import_routes.py para
eso), sin Spaces real."""
from __future__ import annotations

import io

import openpyxl
import pytest
from jose import jwt

import config
from webapp.services import human_capital_service, profile_repository, profile_import_service as pis


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _workbook_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_ALL_HEADERS = ["nombre", "apellido", "email", "telefono", "linkedin", "ubicacion",
                "perfil_skills", "seniority", "fuente", "tags", "notas", "potencial", "marca_potencial"]


def _row(nombre="Juan", apellido="Perez", email="", fuente="", potencial="") -> list:
    return [nombre, apellido, email, "", "", "", "", "", fuente, "", "", potencial, ""]


# ============================================================================
# Upload / parsing (§28 "Upload")
# ============================================================================

def test_valid_xlsx_parses_successfully():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    rows = pis.parse_workbook(data)
    assert len(rows) == 1
    assert rows[0]["nombre"] == "Juan"


def test_empty_workbook_raises_import_file_error():
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(pis.ImportFileError):
        pis.parse_workbook(buf.getvalue())


def test_invalid_workbook_bytes_raise_import_file_error_not_a_raw_traceback():
    with pytest.raises(pis.ImportFileError):
        pis.parse_workbook(b"esto no es un xlsx valido")


def test_missing_required_headers_raise_import_file_error():
    data = _workbook_bytes(["email", "telefono"], [["a@x.com", "123"]])
    with pytest.raises(pis.ImportFileError):
        pis.parse_workbook(data)


def test_row_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(pis, "MAX_IMPORT_ROWS", 3)
    data = _workbook_bytes(_ALL_HEADERS, [_row() for _ in range(5)])
    with pytest.raises(pis.ImportFileError):
        pis.parse_workbook(data)


def test_blank_rows_are_skipped_and_do_not_count_toward_the_limit(monkeypatch):
    monkeypatch.setattr(pis, "MAX_IMPORT_ROWS", 2)
    data = _workbook_bytes(_ALL_HEADERS, [_row(), [None] * len(_ALL_HEADERS), _row(nombre="Ana")])
    rows = pis.parse_workbook(data)
    assert len(rows) == 2


def test_optional_columns_may_be_entirely_absent():
    data = _workbook_bytes(["nombre", "apellido"], [["Juan", "Perez"]])
    rows = pis.parse_workbook(data)
    assert len(rows) == 1
    preview = pis.build_preview(rows)
    assert preview[0].status == "eligible"


# ============================================================================
# Row normalization (§8 del ticket 3.8) — reusa validate_and_shape_fields,
# nunca un validador bulk paralelo
# ============================================================================

def test_flyer_source_is_accepted_via_bulk_exactly_like_manual():
    data = _workbook_bytes(_ALL_HEADERS, [_row(fuente="Flyer")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "eligible"
    assert preview[0].fields["source"] == "Flyer"


def test_fractional_potential_rating_is_rejected_not_silently_truncated():
    data = _workbook_bytes(_ALL_HEADERS, [_row(potencial="1.5")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "invalid"


def test_integral_float_potential_rating_cell_is_accepted():
    # Una celda XLSX que guarda "1" como float 1.0 (típico de Excel) debe
    # seguir siendo válida -- _cell_to_str la normaliza a "1", nunca a
    # algo que int() rechace.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_ALL_HEADERS)
    ws.append(["Juan", "Perez", "", "", "", "", "", "", "", "", "", 1.0, ""])
    buf = io.BytesIO()
    wb.save(buf)
    preview = pis.build_preview(pis.parse_workbook(buf.getvalue()))
    assert preview[0].status == "eligible"
    assert preview[0].fields["potential_rating"] == 1


def test_invalid_source_is_rejected():
    data = _workbook_bytes(_ALL_HEADERS, [_row(fuente="Portal de empleo")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "invalid"


def test_missing_required_field_within_row_is_rejected():
    data = _workbook_bytes(_ALL_HEADERS, [_row(nombre="")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "invalid"


# ============================================================================
# Preview — zero writes, list_profiles() exactamente una vez (§9/§11)
# ============================================================================

def test_preview_performs_zero_persistence_calls(monkeypatch):
    calls = {"create": 0, "create_explicit": 0, "push": 0}
    monkeypatch.setattr(profile_repository, "create", lambda *a, **k: calls.__setitem__("create", calls["create"] + 1))
    monkeypatch.setattr(profile_repository, "create_with_explicit_id", lambda *a, **k: calls.__setitem__("create_explicit", calls["create_explicit"] + 1))
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda p: calls.__setitem__("push", calls["push"] + 1) or True)
    data = _workbook_bytes(_ALL_HEADERS, [_row(), _row(nombre="Ana", apellido="Gomez")])
    pis.build_preview(pis.parse_workbook(data))
    assert calls == {"create": 0, "create_explicit": 0, "push": 0}


def test_preview_calls_list_profiles_exactly_once(monkeypatch):
    calls = []
    original = human_capital_service.list_profiles
    monkeypatch.setattr(human_capital_service, "list_profiles", lambda: (calls.append(1), original())[1])
    data = _workbook_bytes(_ALL_HEADERS, [_row(), _row(nombre="Ana", apellido="Gomez"), _row(nombre="Luis", apellido="Diaz")])
    pis.build_preview(pis.parse_workbook(data))
    assert len(calls) == 1


def test_eligible_row_has_no_existing_profiles():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "eligible"
    assert preview[0].token is not None
    assert preview[0].profile_id is not None


def test_row_matching_an_existing_profile_is_flagged():
    human_capital_service.create_profile({"first_name": "Juan", "last_name": "Perez", "email": "juan@x.com"})
    data = _workbook_bytes(_ALL_HEADERS, [_row(email="juan@x.com")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "existing_duplicate"
    assert preview[0].token is not None  # sigue elegible, solo con advisory


def test_within_file_duplicate_flags_the_second_occurrence_against_the_first():
    data = _workbook_bytes(_ALL_HEADERS, [
        _row(email="juan@x.com"),
        _row(email="JUAN@x.com"),
    ])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].status == "eligible"
    assert preview[1].status == "within_file_duplicate"
    assert preview[1].duplicate_row_number == 1


def test_a_third_matching_row_still_anchors_to_the_first_occurrence():
    data = _workbook_bytes(_ALL_HEADERS, [
        _row(email="juan@x.com"),
        _row(email="juan@x.com"),
        _row(email="juan@x.com"),
    ])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[1].duplicate_row_number == 1
    assert preview[2].duplicate_row_number == 1


def test_both_within_file_duplicate_rows_remain_visible_in_preview():
    data = _workbook_bytes(_ALL_HEADERS, [_row(email="juan@x.com"), _row(email="juan@x.com")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert len(preview) == 2
    assert all(p.status in ("eligible", "within_file_duplicate") for p in preview)


def test_invalid_row_never_gets_a_profile_id_or_token():
    data = _workbook_bytes(_ALL_HEADERS, [_row(nombre="")])
    preview = pis.build_preview(pis.parse_workbook(data))
    assert preview[0].profile_id is None
    assert preview[0].token is None


# ============================================================================
# Token integrity (§13/§24) — nunca confiar en el estado del browser sin
# verificar la firma primero
# ============================================================================

def test_valid_signed_token_verifies():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    claims = pis.verify_row_token(preview[0].token)
    assert claims["profile_id"] == preview[0].profile_id
    assert claims["fields"]["first_name"] == "Juan"


def test_tampered_token_is_rejected():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    tampered = preview[0].token[:-2] + ("aa" if preview[0].token[-2:] != "aa" else "bb")
    with pytest.raises(pis.RowTokenError):
        pis.verify_row_token(tampered)


def test_swapped_profile_id_inside_the_token_is_rejected():
    # Decodifica SIN verificar (alg=none no soportado por jose.jwt.decode
    # sin algorithms explícito -- acá directamente re-firmamos con una
    # key DISTINTA para simular un intento de forjar el claim).
    forged = jwt.encode({"profile_id": "attacker-chosen-id", "row_number": 1, "fields": {}, "has_duplicate_advisory": False, "exp": 9999999999}, "wrong-secret", algorithm="HS256")
    with pytest.raises(pis.RowTokenError):
        pis.verify_row_token(forged)


def test_changed_payload_after_signing_is_rejected():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    # Cualquier alteración de un solo caracter en el cuerpo del JWT
    # invalida la firma -- probamos alterando un caracter del payload
    # (segunda sección, separada por ".").
    header, payload, sig = token.split(".")
    altered_payload = payload[:-1] + ("A" if payload[-1] != "A" else "B")
    tampered_token = f"{header}.{altered_payload}.{sig}"
    with pytest.raises(pis.RowTokenError):
        pis.verify_row_token(tampered_token)


def test_expired_token_is_rejected(monkeypatch):
    import time
    monkeypatch.setattr(pis, "_ROW_TOKEN_MAX_AGE_S", -1)  # ya expirado al firmarlo
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    with pytest.raises(pis.RowTokenError):
        pis.verify_row_token(preview[0].token)


# ============================================================================
# Idempotencia / confirm (§28 "Idempotency"/"Collision"/"Unknown")
# ============================================================================

def test_first_confirm_creates_the_profile():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    results = pis.confirm_rows([token], [token])
    assert results[0].status == "CREATED"
    assert len(profile_repository.list()) == 1


def test_same_signed_confirm_again_is_already_committed_with_no_rewrite():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    first = pis.confirm_rows([token], [token])[0]
    assert first.status == "CREATED"
    saved_before = profile_repository.get(preview[0].profile_id)

    second = pis.confirm_rows([token], [token])[0]
    assert second.status == "ALREADY_COMMITTED"
    saved_after = profile_repository.get(preview[0].profile_id)
    assert saved_after["created_at"] == saved_before["created_at"]
    assert saved_after["updated_at"] == saved_before["updated_at"]
    assert saved_after["revision"] == saved_before["revision"]
    assert len(profile_repository.list()) == 1


def test_unincluded_token_is_reported_as_duplicate_skipped_without_persisting():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    results = pis.confirm_rows([token], [])  # presente en all_tokens, ausente de included
    assert results[0].status == "DUPLICATE_SKIPPED"
    assert profile_repository.list() == []


def test_unknown_outcome_preserves_the_same_token_for_retry(monkeypatch):
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    results = pis.confirm_rows([token], [token])
    assert results[0].status == "REMOTE_OUTCOME_UNKNOWN"
    assert results[0].token == token  # MISMO token, para reintentar esta fila puntual


def test_retrying_an_unknown_row_with_the_same_token_never_mints_a_new_uuid(monkeypatch):
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    profile_id_before = preview[0].profile_id

    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    first = pis.confirm_rows([token], [token])[0]
    assert first.status == "REMOTE_OUTCOME_UNKNOWN"

    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    second = pis.confirm_rows([first.token], [first.token])[0]
    assert second.status == "CREATED"
    saved = profile_repository.list()
    assert len(saved) == 1
    assert saved[0]["profile_id"] == profile_id_before


def test_invalid_token_is_reported_without_raising():
    results = pis.confirm_rows(["not-a-real-token"], ["not-a-real-token"])
    assert results[0].status == "INVALID"


def test_no_batch_stop_when_one_row_is_unknown(monkeypatch):
    data = _workbook_bytes(_ALL_HEADERS, [_row(nombre="Uno"), _row(nombre="Dos", apellido="B")])
    preview = pis.build_preview(pis.parse_workbook(data))
    tokens = [p.token for p in preview]

    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return calls["n"] != 1  # solo la 1ra fila falla (y su reintento -- ver abajo)

    # Fuerza a que la PRIMERA fila falle en ambos intentos (push+retry) y
    # la segunda tenga éxito -- confirma que el batch sigue procesando la
    # fila independiente restante.
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    fail_first_row_calls = {"n": 0}

    def _push_fail_first_row_twice(path):
        fail_first_row_calls["n"] += 1
        return fail_first_row_calls["n"] > 2

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push_fail_first_row_twice)
    results = pis.confirm_rows(tokens, tokens)
    statuses = [r.status for r in results]
    assert "REMOTE_OUTCOME_UNKNOWN" in statuses
    assert "CREATED" in statuses


def test_summarize_counts_each_status():
    data = _workbook_bytes(_ALL_HEADERS, [_row()])
    preview = pis.build_preview(pis.parse_workbook(data))
    token = preview[0].token
    results = pis.confirm_rows([token], [token])
    summary = pis.summarize(results, total_rows=1, invalid_count=0)
    assert summary["CREATED"] == 1
    assert summary["total_rows"] == 1