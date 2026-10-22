"""test_hc_sensitive_remuneration.py — «Remuneración pretendida» (CCHH / Entrevistas) es un dato SENSIBLE: `HC_SENSITIVE_DATA_ALLOWED_USERS`, denegación por defecto.

Autorizados (figuran en la variable Y tienen admisión a Capital Humano): ven y editan el importe. El resto ve `****` y no puede modificarlo ni borrarlo, ni siquiera con un POST manipulado.
El valor real no sale en HTML, inputs, búsqueda ni respuestas de error; el persistido se conserva al editar otros campos; el owner NO tiene excepción implícita.
"""
from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access, human_capital_access
from webapp.main import app
from webapp.services import auth_service, busqueda_repository, entrevista_repository, human_capital_service, onboarding_repository, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo

SECRET = "9.876.543"
CHANGED = "1.234.567"
AUTH = "hc-sensible@danwor.com"
PLAIN = "hc-comun@danwor.com"
OWNER = "duenia@danwor.com"
AREA = "Consultoria"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{AUTH}, {PLAIN}")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", AUTH.upper())              # mayúsculas a propósito: se normaliza igual que las demás allowlists
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    for mod in (entrevista_repository, busqueda_repository, profile_repository, onboarding_repository):
        monkeypatch.setattr(mod.remote_store, "push_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "delete_path", lambda path: True)
        monkeypatch.setattr(mod.remote_store, "enabled", lambda: False)
    snap = catalog_repo.build_snapshot({"area_consultora": [{"valor": AREA, "orden": 1}]}, refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


def _client(email, follow=False) -> TestClient:
    c = TestClient(app, follow_redirects=follow)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


_N = iter(range(1, 1000))


def _seed(**over):
    profile = human_capital_service.create_profile({"first_name": f"Zeta{next(_N):03d}", "last_name": "Prueba"})
    fields = {"area": AREA, "observaciones": "obs original", "remuneracion_pretendida": SECRET, **over}
    return human_capital_service.create_entrevista_manual(profile["profile_id"], fields, allow_compensation=True)


def _stored(eid):
    return entrevista_repository.get(eid)


def _form(eid, **over):
    e = _stored(eid)
    data = {"fecha_entrevista": "", "puesto_evaluado": "", "area": AREA, "modalidad_contratacion": "", "remuneracion_pretendida": SECRET, "observaciones": e["observaciones"] or "",
            "resumen_entrevista_tecnica": "", "expected_revision": str(e["revision"])}
    data.update(over)
    return data


# ═══ política central ═══════════════════════════════════════════════════════════════════════════════════════════
def _claims(email):
    return {"sub": email}


def test_the_policy_requires_a_validated_session_workspace_admission_and_membership(monkeypatch):
    assert human_capital_access.can_view_sensitive_data(_claims(AUTH)) is True
    assert human_capital_access.can_view_sensitive_data(_claims(PLAIN)) is False                          # en Capital Humano, pero no en la lista
    assert human_capital_access.can_view_sensitive_data(None) is False and human_capital_access.can_view_sensitive_data({}) is False       # sin autenticar
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", "fuera-de-cchh@danwor.com")
    assert human_capital_access.can_view_sensitive_data(_claims("fuera-de-cchh@danwor.com")) is False       # en la lista pero SIN admisión a Capital Humano


@pytest.mark.parametrize("value", ["", "   ", None, "ALL", "all"])
def test_a_missing_empty_or_wildcard_variable_denies_everybody_by_default(monkeypatch, value):
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", value)
    assert not any(human_capital_access.can_view_sensitive_data(_claims(u)) for u in (AUTH, PLAIN, OWNER))


def test_identities_are_normalized_like_the_other_allowlists(monkeypatch):
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", "  HC-Sensible , otra.persona@DANWOR.com ")      # espacios, mayúsculas y alias compacto `usuario`
    assert human_capital_access.can_view_sensitive_data(_claims("hc-sensible@danwor.com")) is True
    assert human_capital_access.can_view_sensitive_data(_claims("HC-SENSIBLE@Danwor.com")) is True
    assert human_capital_access.can_view_sensitive_data(_claims(PLAIN)) is False


def test_the_owner_has_no_implicit_exception(monkeypatch):
    assert human_capital_access.is_human_capital_authorized_user(_claims(OWNER)) is True                      # el owner SÍ entra a Capital Humano...
    assert human_capital_access.can_view_sensitive_data(_claims(OWNER)) is False                              # ...pero NO ve el dato salvo que figure en la variable
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", OWNER)
    assert human_capital_access.can_view_sensitive_data(_claims(OWNER)) is True


# ═══ lectura: listado, formulario, búsqueda ═════════════════════════════════════════════════════════════════════
def test_an_authorized_user_sees_the_real_amount_in_the_list_and_the_edit_form():
    e = _seed()
    listing = _client(AUTH).get("/cchh/entrevistas").text
    form = _client(AUTH).get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    assert SECRET in listing and 'name="remuneracion_pretendida"' in form and f'value="{SECRET}"' in form


def test_an_unauthorized_user_sees_asterisks_and_the_real_amount_is_nowhere_in_the_responses():
    e = _seed()
    for url in ("/cchh/entrevistas", f"/cchh/entrevistas/{e['entrevista_id']}/editar", "/cchh/entrevistas/nueva", "/cchh/indicadores", "/cchh/onboarding", "/cchh/candidatos", "/cchh/busquedas"):
        for email in (PLAIN, OWNER):
            body = _client(email).get(url, headers={"HX-Request": "true"} if url == "/cchh/entrevistas" else {}).text
            assert SECRET not in body and "9876543" not in body, (url, email)                                 # ni HTML, ni atributos, ni scripts embebidos
    listing = _client(PLAIN).get("/cchh/entrevistas").text
    assert re.search(r'data-column-key="remuneracion">\*\*\*\*</td>', listing)
    form = _client(PLAIN).get(f"/cchh/entrevistas/{e['entrevista_id']}/editar").text
    assert 'name="remuneracion_pretendida"' not in form and 'value="****"' in form and "readonly" in form and "disabled" in form
    assert not re.search(r'<input[^>]+type="hidden"[^>]+remuneraci', form)                                    # sin inputs ocultos con el dato


def test_the_mask_is_shown_even_when_there_is_no_amount_so_presence_is_not_revealed():
    e = _seed(remuneracion_pretendida="")
    assert "****" in _client(PLAIN).get("/cchh/entrevistas").text
    assert human_capital_service.entrevista_view(_stored(e["entrevista_id"]))["remuneracion_pretendida"] == "****"


def test_text_search_cannot_be_used_to_probe_the_hidden_amount():
    _seed()
    html = lambda email, q: _client(email).get("/cchh/entrevistas", params={"q": q}, headers={"HX-Request": "true"}).text
    assert "Zeta" in html(AUTH, "9.876.543") and "Zeta" not in html(PLAIN, "9.876.543") and "Zeta" not in html(OWNER, "9.876")      # el no autorizado NO encuentra nada por importe
    assert "Zeta" in html(PLAIN, "prueba")                                                                      # y la búsqueda por otros campos sigue funcionando


def test_unauthenticated_requests_get_neither_the_amount_nor_the_form():
    e = _seed()
    anon = TestClient(app, follow_redirects=False)
    for url in ("/cchh/entrevistas", f"/cchh/entrevistas/{e['entrevista_id']}/editar"):
        r = anon.get(url)
        assert r.status_code in (302, 303, 307) and SECRET not in r.text


# ═══ escritura: autorizado edita; no autorizado NO puede, ni manipulando el POST ════════════════════════════════
def test_an_authorized_user_can_change_and_clear_the_amount():
    e = _seed()
    r = _client(AUTH).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], remuneracion_pretendida=CHANGED))
    assert r.status_code == 303 and _stored(e["entrevista_id"])["remuneracion_pretendida"] == CHANGED
    r = _client(AUTH).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], remuneracion_pretendida=""))
    assert r.status_code == 303 and _stored(e["entrevista_id"])["remuneracion_pretendida"] is None


@pytest.mark.parametrize("sent", [CHANGED, "", "0", "   "])
def test_an_unauthorized_user_cannot_modify_or_erase_the_amount_even_with_a_manipulated_post(sent):
    e = _seed()
    for email in (PLAIN, OWNER):
        r = _client(email).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], remuneracion_pretendida=sent, observaciones=f"editado por {email}"))
        assert r.status_code == 303
        stored = _stored(e["entrevista_id"])
        assert stored["remuneracion_pretendida"] == SECRET and stored["observaciones"] == f"editado por {email}"     # el importe se conserva; los demás campos SÍ se editan


def test_editing_other_fields_never_overwrites_the_stored_amount_for_anyone_who_cannot_see_it():
    e = _seed()
    data = _form(e["entrevista_id"], observaciones="solo cambié esto")
    data.pop("remuneracion_pretendida")                                                                        # el formulario legítimo del no autorizado ni siquiera envía el campo
    _client(PLAIN).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=data)
    assert _stored(e["entrevista_id"])["remuneracion_pretendida"] == SECRET and _stored(e["entrevista_id"])["observaciones"] == "solo cambié esto"


def test_identity_comes_only_from_the_server_session_never_from_the_request():
    e = _seed()
    c = _client(PLAIN)
    r = c.post(f"/cchh/entrevistas/{e['entrevista_id']}/editar?user={AUTH}&email={AUTH}&sub={AUTH}", data={**_form(e["entrevista_id"], remuneracion_pretendida=CHANGED), "email": AUTH, "sub": AUTH,
                                                                                                             "can_see_compensation": "1", "sensitive": "1"},
               headers={"X-User": AUTH, "X-Forwarded-User": AUTH, "X-Sensitive": "1"})
    assert r.status_code == 303 and _stored(e["entrevista_id"])["remuneracion_pretendida"] == SECRET
    assert SECRET not in c.get(f"/cchh/entrevistas/{e['entrevista_id']}/editar?user={AUTH}&sensitive=1", headers={"X-User": AUTH}).text


def test_an_unauthorized_create_stores_no_amount():
    profile = human_capital_service.create_profile({"first_name": "Nueva", "last_name": "Persona"})
    r = _client(PLAIN).post("/cchh/entrevistas/nueva", data={"profile_id": profile["profile_id"], "area": AREA, "remuneracion_pretendida": CHANGED, "observaciones": "alta"})
    assert r.status_code == 303
    created = [x for x in entrevista_repository.list() if x["profile_id"] == profile["profile_id"]][0]
    assert created["remuneracion_pretendida"] is None and created["observaciones"] == "alta"
    r = _client(AUTH).post("/cchh/entrevistas/nueva", data={"profile_id": profile["profile_id"], "area": AREA, "remuneracion_pretendida": CHANGED})
    assert r.status_code == 303 and [x for x in entrevista_repository.list() if x["profile_id"] == profile["profile_id"] and x["remuneracion_pretendida"] == CHANGED]


def test_the_not_concretada_action_cannot_modify_the_amount_for_an_unauthorized_user():
    e = _seed()
    r = _client(PLAIN).post(f"/cchh/entrevistas/{e['entrevista_id']}/no-concretada", data=_form(e["entrevista_id"], remuneracion_pretendida=CHANGED))
    assert r.status_code == 303
    stored = _stored(e["entrevista_id"])
    assert stored["concretada"] is False and stored["remuneracion_pretendida"] == SECRET
    e2 = _seed()
    _client(AUTH).post(f"/cchh/entrevistas/{e2['entrevista_id']}/no-concretada", data=_form(e2["entrevista_id"], remuneracion_pretendida=CHANGED))
    assert _stored(e2["entrevista_id"])["remuneracion_pretendida"] == CHANGED and _stored(e2["entrevista_id"])["concretada"] is False


# ═══ errores, concurrencia y logs: tampoco filtran ══════════════════════════════════════════════════════════════
def test_error_and_conflict_pages_do_not_echo_the_amount_to_an_unauthorized_user(caplog):
    e = _seed()
    with caplog.at_level(logging.DEBUG):
        bad = _client(PLAIN).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], area="Área inexistente", remuneracion_pretendida="7.777.777"))
        stale = _client(PLAIN).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], expected_revision="999", remuneracion_pretendida="7.777.777"))
    for r in (bad, stale):
        assert r.status_code == 200 and SECRET not in r.text and "7.777.777" not in r.text and 'name="remuneracion_pretendida"' not in r.text
    assert SECRET not in caplog.text and "7.777.777" not in caplog.text                                       # nada de remuneraciones en logs
    assert _stored(e["entrevista_id"])["remuneracion_pretendida"] == SECRET
    ok_error = _client(AUTH).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], area="Área inexistente"))
    assert ok_error.status_code == 200 and f'value="{SECRET}"' in ok_error.text                                 # el autorizado conserva su experiencia previa (el form re-renderizado mantiene lo que cargó)


# ═══ el servicio también es fail-closed ═════════════════════════════════════════════════════════════════════════
def test_the_service_defaults_deny_writes_and_mask_reads():
    e = _seed()
    updated = human_capital_service.update_entrevista(e["entrevista_id"], e["revision"], {"remuneracion_pretendida": CHANGED, "observaciones": "x"})          # sin allow_compensation
    assert updated["remuneracion_pretendida"] == SECRET and updated["observaciones"] == "x"
    profile = human_capital_service.create_profile({"first_name": "S", "last_name": "V"})
    assert human_capital_service.create_entrevista_manual(profile["profile_id"], {"remuneracion_pretendida": CHANGED})["remuneracion_pretendida"] is None
    assert {r["remuneracion_pretendida"] for r in human_capital_service.list_entrevistas_view()} == {"****"}
    assert human_capital_service.entrevista_view(_stored(e["entrevista_id"]))["remuneracion_pretendida"] == "****"
    assert _stored(e["entrevista_id"])["remuneracion_pretendida"] == SECRET                                     # enmascarar NO modifica lo persistido
    assert human_capital_service.entrevista_view(_stored(e["entrevista_id"]), True)["remuneracion_pretendida"] == SECRET


def test_masking_never_mutates_the_persisted_record_nor_the_input_row():
    row = {"entrevista_id": "x", "remuneracion_pretendida": SECRET}
    masked = human_capital_service.mask_entrevista_compensation(row, False)
    assert masked["remuneracion_pretendida"] == "****" and row["remuneracion_pretendida"] == SECRET and human_capital_service.mask_entrevista_compensation(row, True) is row


# ═══ superficie documentada: /admin/runtime/export.zip ══════════════════════════════════════════════════════════
def test_the_runtime_export_does_not_include_capital_humano_data_and_is_owner_diagnostic_only(monkeypatch):
    """`/admin/runtime/export.zip` empaqueta SOLO `feedback_log/`, `activity_log/` y `case_library/` (no `human-capital/`), y exige modo diagnóstico: únicamente el owner con la cookie `dw-diagnostic=on`.
    Se verifica que, tras guardar un importe real, el ZIP no lo contiene; el exportador NO se modifica."""
    import io
    import zipfile
    from webapp.services import admin_tools
    assert [label for label, _ in admin_tools._RUNTIME_EXPORT_DIRS] == ["feedback_log", "activity_log", "case_library"]
    e = _seed()
    _client(AUTH).post(f"/cchh/entrevistas/{e['entrevista_id']}/editar", data=_form(e["entrevista_id"], remuneracion_pretendida=CHANGED))
    _client(AUTH).get("/cchh/entrevistas")
    blob = admin_tools.runtime_export_zip()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        everything = b"".join(z.read(n) for n in z.namelist())
    assert SECRET.encode() not in everything and CHANGED.encode() not in everything
    monkeypatch.setattr(access, "DIAGNOSTIC_MODE", False)
    for email in (AUTH, PLAIN, OWNER):
        assert _client(email).get("/admin/runtime/export.zip").status_code == 403                  # sin la cookie de diagnóstico nadie lo descarga, ni el owner
    owner = _client(OWNER)
    owner.cookies.set("dw-diagnostic", "on")
    assert owner.get("/admin/runtime/export.zip").status_code == 200
    plain = _client(PLAIN)
    plain.cookies.set("dw-diagnostic", "on")
    assert plain.get("/admin/runtime/export.zip").status_code == 403                                 # la cookie sola no alcanza para no-owners
