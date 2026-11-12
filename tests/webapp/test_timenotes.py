"""Operaciones → Carga de Horas (MVP). Cálculo de cumplimiento (timenotes_service), adaptador de envío (timenotes_send) y guardas del router.
Datos SIEMPRE controlados (sin Graph real, sin SharePoint, sin correos reales). Fixtures de sesión de Operaciones reutilizadas."""
from __future__ import annotations

import datetime as dt

import pytest

import config
from webapp.services import timenotes_service as T
from webapp.services import timenotes_send as S
from tests.webapp.test_operations_busquedas_i2 import ORIGIN, operations_users, ops, session_client  # noqa: F401

WEEK = (dt.date(2026, 11, 2), dt.date(2026, 11, 6))          # lunes–viernes
PADRON = {"ana@danwor.com": "Ana Lopez", "beto@danwor.com": "Beto Diaz", "caro@danwor.com": "Caro Ruiz"}
CSV = (b"Email,Date,Duration Decimal,Project\n"
       b"ana@danwor.com,2026-11-03,8,P1\n"
       b"ana@danwor.com,2026-11-04,8,P1\n"            # Ana: 16h
       b"beto@danwor.com,2026-11-03,40,P2\n"          # Beto: 40h (cumple)
       b"ext@otro.com,2026-11-03,5,PX\n"              # no asociable
       b"ana@danwor.com,2026-10-20,8,P1\n")           # fuera de semana


_NOHOL = lambda *a, **k: {"estado": "ok", "feriados": [], "detalle": ""}   # noqa: E731 — calendario válido sin feriados


def _compliance(csv=CSV, padron=PADRON, absences=None):
    return T.build_compliance(token="x", week=WEEK, download=lambda t: csv, meta=lambda t: "2026-11-07T10:00:00Z",
                              email_map_fn=lambda t: padron, absences_fn=lambda t: (absences or []))


# ═══════════════════════ cálculo ═══════════════════════

def test_aggregates_by_email_sums_duration_and_computes_missing():
    r = _compliance()
    assert r["status"] == T.STATUS_OK
    by = {c["email"]: c for c in r["colaboradores"]}
    assert by["ana@danwor.com"]["horas_registradas"] == 16.0 and by["ana@danwor.com"]["faltantes"] == 24.0
    assert by["ana@danwor.com"]["cumple"] is False and by["ana@danwor.com"]["notificable"] is True
    assert by["beto@danwor.com"]["cumple"] is True and by["beto@danwor.com"]["notificable"] is False


def test_padron_members_without_records_are_included_with_zero():
    by = {c["email"]: c for c in _compliance()["colaboradores"]}
    assert by["caro@danwor.com"]["sin_registros"] is True
    assert by["caro@danwor.com"]["horas_registradas"] == 0.0 and by["caro@danwor.com"]["faltantes"] == 40.0


def test_rows_outside_the_week_are_ignored():
    # El registro de Ana del 2026-10-20 no suma (queda en 16h, no 24h).
    by = {c["email"]: c for c in _compliance()["colaboradores"]}
    assert by["ana@danwor.com"]["horas_registradas"] == 16.0


def test_records_not_matching_a_collaborator_are_reported_separately():
    r = _compliance()
    assert {"email": "ext@otro.com", "horas": 5.0} in r["no_asociables"]
    assert all(c["email"] != "ext@otro.com" for c in r["colaboradores"])


def test_source_error_is_not_treated_as_zero_hours():
    from urllib.error import URLError
    def boom(_): raise URLError("down")
    r = T.build_compliance(token="x", week=WEEK, download=boom, meta=lambda t: None,
                           email_map_fn=lambda t: PADRON, absences_fn=lambda t: [])
    assert r["status"] == T.STATUS_READ_ERROR and not r["colaboradores"]


@pytest.mark.parametrize("code,needle", [(404, "no existe"), (403, "permiso"), (401, "Autorización")])
def test_http_errors_are_distinguished_in_the_diagnostic(code, needle):
    from urllib.error import HTTPError
    def boom(_): raise HTTPError("u", code, "m", {}, None)
    r = T.build_compliance(token="x", week=WEEK, download=boom, meta=lambda t: None, diagnose=lambda t: {"ok": True},
                           email_map_fn=lambda t: PADRON, absences_fn=lambda t: [])
    assert r["status"] == T.STATUS_READ_ERROR and r["http_status"] == code and needle in r["detalle"]


def test_404_attaches_folder_diagnostic_only():
    from urllib.error import HTTPError
    def boom(_): raise HTTPError("u", 404, "m", {}, None)
    diag = {"drive": {"name": "Documentos compartidos"}, "carpeta": "General/Template Dashboard",
            "archivos": [{"name": "Time_Logs_Reports.csv", "es_carpeta": False}]}
    r404 = T.build_compliance(token="x", week=WEEK, download=boom, meta=lambda t: None, diagnose=lambda t: diag,
                              email_map_fn=lambda t: PADRON, absences_fn=lambda t: [])
    assert r404["diagnostico"]["archivos"][0]["name"] == "Time_Logs_Reports.csv"
    # El diagnóstico NO se dispara en un 403 (solo 404).
    def boom403(_): raise HTTPError("u", 403, "m", {}, None)
    r403 = T.build_compliance(token="x", week=WEEK, download=boom403, meta=lambda t: None, diagnose=lambda t: diag,
                              email_map_fn=lambda t: PADRON, absences_fn=lambda t: [])
    assert "diagnostico" not in r403


def test_diagnose_source_lists_a_single_known_folder():
    calls = []
    def fetch(url, token, **k):
        calls.append(url)
        if url.endswith("/drive?$select=id,name"):
            return {"id": "d1", "name": "Documentos compartidos"}
        return {"value": [{"name": "Solicitudes.xlsx", "file": {}}, {"name": "Time Logs Reports.csv", "file": {}}]}
    d = T.diagnose_source("tok", site_id="S", folder="General/Template Dashboard", fetch=fetch)
    assert d["drive"]["name"] == "Documentos compartidos"
    assert {a["name"] for a in d["archivos"]} == {"Solicitudes.xlsx", "Time Logs Reports.csv"}
    # Solo consulta el drive y ESA carpeta — nunca un search global.
    assert all("search" not in u.lower() for u in calls) and any("General/Template%20Dashboard:/children" in u for u in calls)


def test_incomplete_csv_is_flagged_and_blocks():
    r = T.build_compliance(token="x", week=WEEK, download=lambda t: b"Mail,Dia,Horas\na,b,c\n", meta=lambda t: None,
                           email_map_fn=lambda t: PADRON, absences_fn=lambda t: [])
    assert r["status"] == T.STATUS_CSV_INCOMPLETE and "Email" in r["detalle"]


def test_padron_unavailable_is_distinguished():
    r = T.build_compliance(token="x", week=WEEK, download=lambda t: CSV, meta=lambda t: None,
                           email_map_fn=lambda t: {}, absences_fn=lambda t: [])
    assert r["status"] == T.STATUS_PADRON_UNAVAILABLE


def test_comma_decimal_duration_is_parsed():
    csv = b"Email,Date,Duration Decimal\nana@danwor.com,2026-11-03,7,5\n"  # 7,5 con coma -> pero CSV separa por coma...
    # Usar punto y coma para no confundir el separador con el decimal coma.
    csv = b"Email;Date;Duration Decimal\nana@danwor.com;2026-11-03;7,5\n"
    r = T.build_compliance(token="x", week=WEEK, download=lambda t: csv, meta=lambda t: None,
                           email_map_fn=lambda t: {"ana@danwor.com": "Ana"}, absences_fn=lambda t: [])
    by = {c["email"]: c for c in r["colaboradores"]}
    assert by["ana@danwor.com"]["horas_registradas"] == 7.5


def test_overlapping_approved_absence_flags_review_and_blocks_notification():
    absences = [{"Nombre": "Ana", "Apellido": "Lopez", "Motivo": "Vacaciones", "Estado": "Aprobada",
                 "Desde": "2026-11-03", "Hasta": "2026-11-05"}]
    by = {c["email"]: c for c in _compliance(absences=absences)["colaboradores"]}
    assert by["ana@danwor.com"]["revisar_excepcion"] is True
    assert by["ana@danwor.com"]["notificable"] is False           # no se auto-notifica a quien tiene excepción a revisar
    assert by["ana@danwor.com"]["horas_exigibles"] == 40.0        # NO se descuentan horas automáticamente


def test_absence_outside_the_week_or_not_approved_does_not_flag():
    out = [{"Nombre": "Ana", "Apellido": "Lopez", "Motivo": "X", "Estado": "Aprobada", "Desde": "2026-10-01", "Hasta": "2026-10-02"}]
    pend = [{"Nombre": "Ana", "Apellido": "Lopez", "Motivo": "X", "Estado": "Pendiente", "Desde": "2026-11-03", "Hasta": "2026-11-05"}]
    assert {c["email"]: c for c in _compliance(absences=out)["colaboradores"]}["ana@danwor.com"]["revisar_excepcion"] is False
    assert {c["email"]: c for c in _compliance(absences=pend)["colaboradores"]}["ana@danwor.com"]["revisar_excepcion"] is False


# ═══════════════════════ envío (adaptador) ═══════════════════════

SEMANA = {"inicio": "2026-11-02", "fin": "2026-11-06", "label": "02/11/2026 al 06/11/2026"}


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    return tmp_path


def _one(transport, *, email="ana@danwor.com"):
    return S.send_one(colaborador_email=email, nombre="Ana", semana=SEMANA, exigibles=40, registradas=16, faltantes=24,
                      actor="op@danwor.com", enabled=lambda: True, missing=lambda: False, transport=transport)


def test_real_send_goes_to_the_collaborator_without_test_prefix(audit_dir):
    seen = {}
    def tx(msg): seen["msg"] = msg; return ("accepted", "HTTP 202")
    r = _one(tx)                                              # actor = op@danwor.com, pero el destinatario es el colaborador
    assert r["status"] == "accepted" and r["recipient"] == "ana@danwor.com"
    assert seen["msg"]["message"]["toRecipients"][0]["emailAddress"]["address"] == "ana@danwor.com"
    assert not seen["msg"]["message"]["subject"].startswith("[PRUEBA]") and "[PRUEBA]" not in seen["msg"]["message"]["subject"]
    assert not seen["msg"]["message"].get("ccRecipients") and not seen["msg"]["message"].get("bccRecipients")   # sin copia oculta al actor
    assert "24 h" in seen["msg"]["message"]["body"]["content"]


def test_no_only_test_mode_attribute_remains():
    # El flujo operativo ya no tiene imposición de modo prueba (se retiró ONLY_TEST_MODE y effective_test_mode).
    assert not hasattr(S, "ONLY_TEST_MODE") and not hasattr(S, "effective_test_mode")


def test_resolve_site_id_reuses_the_absence_operations_site():
    from webapp.services import staffing_repository
    calls = []
    def fake(url, token, **k): calls.append(url); return {"id": "OPS-ID"}
    assert T.resolve_site_id("tok", fetch=fake) == "OPS-ID"
    assert staffing_repository.ABSENCE_SITE_PATH in calls[0]  # reutiliza el contrato de Ausencias (sitio Operaciones), no el sitio Gestión


def test_download_builds_the_operations_site_content_url():
    cap = {}
    def fake_bytes(url, token, **k): cap["url"] = url; return b"x"
    T.download_csv_bytes("tok", site_id="OPS", fetch=fake_bytes)
    assert "/sites/OPS/drive/root:/" in cap["url"] and cap["url"].endswith(":/content")
    assert "Time%20Logs%20Reports.csv" in cap["url"]


def test_duplicate_protection_blocks_resend(audit_dir):
    calls = []
    def tx(msg): calls.append(msg); return ("accepted", "HTTP 202")
    assert _one(tx)["status"] == "accepted"
    assert _one(tx)["status"] == "duplicate" and len(calls) == 1


def test_uncertain_is_not_auto_retried(audit_dir):
    calls = []
    def tx(msg): calls.append(msg); return ("uncertain", "HTTP 500")
    assert _one(tx)["status"] == "uncertain"
    assert _one(tx)["status"] == "duplicate" and len(calls) == 1          # un reintento se reconoce como duplicado, no reenvía


def test_disabled_writes_no_intent(audit_dir):
    r = S.send_one(colaborador_email="ana@danwor.com", nombre="Ana", semana=SEMANA, exigibles=40, registradas=0, faltantes=40,
                   actor="op", enabled=lambda: False, missing=lambda: False, transport=lambda m: ("accepted", ""))
    assert r["status"] == "disabled"
    assert not (S.audit_root() / S.operation_id_for("2026-11-02", "2026-11-06", "ana@danwor.com")).exists()


def test_operation_id_deterministic_per_week_and_email():
    a = S.operation_id_for("2026-11-02", "2026-11-06", "ana@danwor.com")
    assert a == S.operation_id_for("2026-11-02", "2026-11-06", "ana@danwor.com")          # determinístico (anti-duplicado)
    assert a != S.operation_id_for("2026-11-09", "2026-11-13", "ana@danwor.com")          # otra semana, otra clave
    assert a != S.operation_id_for("2026-11-02", "2026-11-06", "beto@danwor.com")         # otro colaborador, otra clave


def test_batch_runs_n_individual_sends(audit_dir):
    def tx(msg): return ("accepted", "HTTP 202")
    out = S.send_batch([{"email": "a@danwor.com", "nombre": "A", "exigibles": 40, "registradas": 0, "faltantes": 40},
                        {"email": "b@danwor.com", "nombre": "B", "exigibles": 40, "registradas": 10, "faltantes": 30}],
                       semana=SEMANA, actor="op", enabled=lambda: True, missing=lambda: False, transport=tx)
    assert out["resumen"]["total"] == 2 and out["resumen"].get("accepted") == 2
    assert len(out["resultados"]) == 2


# ═══════════════════════ router (guardas) ═══════════════════════

NOTIFY = "/operaciones/staffing/carga-horas/notificar"


def _ok_compliance():
    return {"status": T.STATUS_OK, "semana": SEMANA, "horas_base": 40, "csv_actualizado": None, "no_asociables": [], "estados_disponibles": True,
            "colaboradores": [
                {"email": "ana@danwor.com", "nombre": "Ana", "horas_exigibles": 40, "horas_registradas": 16, "faltantes": 24,
                 "cumple": False, "sin_registros": False, "excepciones": [], "revisar_excepcion": False, "notificable": True, "estados": ["Activo"]},
                {"email": "beto@danwor.com", "nombre": "Beto", "horas_exigibles": 40, "horas_registradas": 40, "faltantes": 0,
                 "cumple": True, "sin_registros": False, "excepciones": [], "revisar_excepcion": False, "notificable": False, "estados": ["Activo"]}],
            "resumen": {}}


def test_notify_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    r = ops().post(NOTIFY, data={"graph_token": "t", "emails": ["ana@danwor.com"]}, headers=ORIGIN)   # sin confirm
    assert r.status_code == 400


def test_notify_blocks_when_source_invalid(monkeypatch):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance",
                        lambda **k: {"status": T.STATUS_READ_ERROR, "detalle": "falla"})
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["status"] == "blocked"


def test_notify_skips_non_notificable_and_sends_only_eligible(monkeypatch):
    sent = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    def fake_batch(dest, **k): sent["dest"] = dest; return {"resultados": [], "resumen": {"total": len(dest)}}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", fake_batch)
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes",
                                 "emails": ["ana@danwor.com", "beto@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 200
    assert [d["email"] for d in sent["dest"]] == ["ana@danwor.com"]        # beto (cumple) se omite
    assert any(s["email"] == "beto@danwor.com" and s["reason"] == "sin_faltantes" for s in r.json()["skipped"])


def test_notify_requires_same_origin(monkeypatch):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]})  # sin Origin
    assert r.status_code == 403


# ═══════════════════════ excepciones permanentes (P4) ═══════════════════════

from webapp.services import timenotes_exceptions as X  # noqa: E402

EXC_ADD = "/operaciones/staffing/carga-horas/excepciones/mover"
EXC_REMOVE = "/operaciones/staffing/carga-horas/excepciones/devolver"
DATA = "/operaciones/staffing/carga-horas/data"


def test_exceptions_store_add_remove_keeps_traceability(audit_dir):
    assert X.add([{"email": "Ana@Danwor.com", "nombre": "Ana"}], actor="op@danwor.com") == ["ana@danwor.com"]
    assert X.add([{"email": "ana@danwor.com", "nombre": "Ana"}], actor="otro") == []          # idempotente: no pisa el alta
    assert X.list_emails() == {"ana@danwor.com"} and X.entries()[0]["added_by"] == "op@danwor.com"
    assert X.remove(["ana@danwor.com"], actor="op2@danwor.com") == ["ana@danwor.com"]
    assert X.list_emails() == set()
    assert [(h["op"], h["actor"]) for h in X.history()] == [("add", "op@danwor.com"), ("remove", "op2@danwor.com")]


def test_exceptions_store_refuses_to_overwrite_an_unreadable_register(audit_dir):
    p = audit_dir / "operations" / "timenotes" / "exceptions.json"
    p.parent.mkdir(parents=True)
    p.write_text("{corrupto", encoding="utf-8")
    with pytest.raises(X.ExceptionsUnavailable):
        X.add([{"email": "ana@danwor.com"}], actor="op")
    assert p.read_text(encoding="utf-8") == "{corrupto"


def test_apply_moves_excepted_rows_and_recomputes_summary_without_touching_hours():
    data = _compliance()
    out = X.apply(data, {"ana@danwor.com"})
    assert "ana@danwor.com" not in {c["email"] for c in out["colaboradores"]}
    assert [c["email"] for c in out["excepciones_manuales_rows"]] == ["ana@danwor.com"]
    assert out["excepciones_manuales_rows"][0]["horas_registradas"] == 16
    assert out["resumen"]["esperados"] == len(PADRON) - 1 and out["resumen"]["excepciones_manuales"] == 1


def test_move_and_return_routes_persist_and_require_same_origin(audit_dir):
    assert ops().post(EXC_ADD, data={"email": "ana@danwor.com", "nombre": "Ana"}).status_code == 403          # sin Origin
    r = ops().post(EXC_ADD, data={"email": "ana@danwor.com", "nombre": "Ana"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["entry"]["email"] == "ana@danwor.com"
    assert X.list_emails() == {"ana@danwor.com"}
    assert ops().post(EXC_ADD, data={"email": "no-es-email"}, headers=ORIGIN).status_code == 400
    r = ops().post(EXC_REMOVE, data={"email": "ana@danwor.com"}, headers=ORIGIN)
    assert r.status_code == 200 and X.list_emails() == set()


def test_notify_revalidates_exceptions_server_side(monkeypatch, audit_dir):
    X.add([{"email": "ana@danwor.com", "nombre": "Ana"}], actor="op")
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: called.setdefault("dest", dest))
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and "dest" not in called                                      # nadie elegible: Ana está exceptuada
    assert r.json()["skipped"] == [{"email": "ana@danwor.com", "reason": "en_excepciones"}]


def _corrupt_exceptions(audit_dir):
    p = audit_dir / "operations" / "timenotes" / "exceptions.json"
    p.parent.mkdir(parents=True)
    p.write_text("{corrupto", encoding="utf-8")
    return p


def test_list_emails_strict_raises_on_unreadable_register(audit_dir):
    _corrupt_exceptions(audit_dir)
    assert X.list_emails() == set()                           # lectura tolerante (vista): no rompe la pantalla
    with pytest.raises(X.ExceptionsUnavailable):
        X.list_emails(strict=True)                            # camino de envío: fail-closed, NO «sin excepciones»


def test_notify_blocks_when_exceptions_register_unverifiable(monkeypatch, audit_dir):
    # Registro existente pero ilegible: por seguridad no se envía nada (podría haber un excluido adentro) y el transporte nunca se invoca.
    _corrupt_exceptions(audit_dir)
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: called.setdefault("dest", dest))
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["reason"] == "excepciones_no_verificables"
    assert "dest" not in called                               # no se tocó el transporte


def test_data_panel_flags_and_blocks_when_exceptions_unverifiable(monkeypatch, audit_dir):
    _corrupt_exceptions(audit_dir)
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert 'data-excepciones-ok="1"' not in html              # el botón Notificar queda deshabilitado (fail-closed)
    assert "No se pudo verificar el registro de excepciones" in html


def test_panel_renders_exceptions_container_separately(monkeypatch, audit_dir):
    X.add([{"email": "ana@danwor.com", "nombre": "Ana"}, {"email": "ex@danwor.com", "nombre": "Ex Colaborador"}], actor="op")
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    colab, exc = html.split('data-tn-tbody="colaboradores"')[1].split('data-tn-tbody="excepciones"')
    assert 'data-tn-email="ana@danwor.com"' not in colab and 'data-tn-email="beto@danwor.com"' in colab
    assert 'data-tn-email="ana@danwor.com"' in exc and 'data-tn-fuera-padron="1"' in exc        # Ex no está en el padrón de la semana
    assert 'data-tn-count="excepciones">2 excepciones' in html


# ═══════════════════════ no-colaboradores excluidos por código ═══════════════════════

def test_non_collaborators_are_excluded_from_every_list():
    padron = dict(PADRON, **{"RDiaz@danwor.com": "R Diaz", "administracion@danwor.com": "Administración"})
    csv = CSV + b"rdiaz@danwor.com,2026-11-03,4,PX\nsvines@danwor.com,2026-11-03,6,PX\n"
    out = _compliance(csv=csv, padron=padron)
    emails = {c["email"] for c in out["colaboradores"]}
    assert not emails & T.NON_COLLABORATOR_EMAILS and emails == set(PADRON)
    assert {r["email"] for r in out["no_asociables"]} == {"ext@otro.com"}          # svines (sin padrón) tampoco figura
    assert out["resumen"]["esperados"] == len(PADRON)


def test_non_collaborators_list_is_the_owner_contract():
    assert len(T.NON_COLLABORATOR_EMAILS) == 11 and "administracion@danwor.com" in T.NON_COLLABORATOR_EMAILS
    assert all(e == e.strip().lower() for e in T.NON_COLLABORATOR_EMAILS)


# ═══════════════════════ P6 — badges (contrato visual de Solicitudes) ═══════════════════════

def test_compliance_and_notification_badges_reuse_the_solicitudes_contract(monkeypatch, audit_dir):
    data = _ok_compliance()
    data["colaboradores"].append({"email": "caro@danwor.com", "nombre": "Caro", "horas_exigibles": 40, "horas_registradas": 32, "faltantes": 8,
                                  "cumple": False, "sin_registros": False, "excepciones": [], "revisar_excepcion": False, "notificable": True})
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: data)
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    badge = "dashboard-status-badge solicitudes-status-badge solicitudes-status-badge--"
    assert badge + 'red">Faltan 24 h' in html                # 24 de 40 (≥ 50 %) → rojo
    assert badge + 'amber">Faltan 8 h' in html               # 8 de 40 (< 50 %) → ámbar
    assert badge + 'green">Cumple' in html
    assert badge + 'grey" data-tn-notif-state data-tn-notif-default="Pendiente"' in html
    assert 'class="status-badge' not in html                 # sin el badge genérico anterior


# ═══════════════════════ P7 — Estado de asignación ═══════════════════════

from types import SimpleNamespace as _A  # noqa: E402


def _asig(cid, status, start=None, end=None):
    return _A(consultant_id=cid, status=status, start_date=start, end_date=end)


def test_estados_by_consultant_use_week_overlap_and_open_dates():
    inicio, fin = WEEK
    rows = [_asig("1", "Activo", dt.date(2026, 10, 1), dt.date(2026, 11, 3)),     # termina dentro de la semana → aplica
            _asig("1", "Tentativo", dt.date(2026, 11, 9), None),                 # empieza después → no aplica
            _asig("2", "Activo", dt.date(2026, 1, 1), dt.date(2026, 10, 31)),    # activo vencido antes de la semana → no aplica
            _asig("3", "", None, None),                                          # status vacío, fechas abiertas → «Sin estado»
            _asig("4", "Inactivo", dt.date(2026, 11, 6), dt.date(2026, 12, 1))]
    out = T.estados_por_consultor(rows, inicio, fin)
    assert out == {"1": {"Activo"}, "3": {T.ESTADO_SIN}, "4": {"Inactivo"}}


def test_build_compliance_joins_estados_by_colaboradores_item_id(monkeypatch):
    monkeypatch.setattr(T, "_resolve_padron", lambda token, fn: (dict(PADRON), {"ana@danwor.com": "10", "beto@danwor.com": "20"}))
    seen = {}
    def loader(token, *, include_inactive, cache_scope):
        seen["inactive"] = include_inactive
        return [_asig("10", "Activo"), _asig("10", "Tentativo"), _asig("20", "Inactivo")]
    out = T.build_compliance(token="x", week=WEEK, download=lambda t: CSV, meta=lambda t: None, absences_fn=lambda t: [],
                             assignments_fn=loader, include_inactive=True, availability_fn=lambda *a, **k: {}, holidays_fn=_NOHOL)
    est = {c["email"]: c["estados"] for c in out["colaboradores"]}
    assert est == {"ana@danwor.com": ["Activo", "Tentativo"], "beto@danwor.com": ["Inactivo"], "caro@danwor.com": [T.ESTADO_SIN]}
    assert out["estados_disponibles"] is True and seen["inactive"] is True


def test_unavailable_assignments_are_reported_not_assumed(monkeypatch):
    monkeypatch.setattr(T, "_resolve_padron", lambda token, fn: (dict(PADRON), {}))
    out = T.build_compliance(token="x", week=WEEK, download=lambda t: CSV, meta=lambda t: None, absences_fn=lambda t: [],
                             assignments_fn=lambda *a, **k: None, availability_fn=lambda *a, **k: {}, holidays_fn=_NOHOL)
    assert out["estados_disponibles"] is False and all(c["estados"] == [] for c in out["colaboradores"])


def test_inactive_history_is_needed_for_inactivo_and_sin_estado_only():
    assert not T.estados_requieren_inactivos(["Activo", "Tentativo"])
    assert T.estados_requieren_inactivos(["Inactivo"]) and T.estados_requieren_inactivos([T.ESTADO_SIN])


def test_notify_revalidates_estado_server_side(monkeypatch, audit_dir):
    data = _ok_compliance()
    data["colaboradores"][0]["estados"] = ["Tentativo"]                       # Ana: solo Tentativo
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: data)
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: called.setdefault("dest", dest))
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"],
                                 "estados_presentes": "1", "estados": ["Activo"], "feriados_tipos": ["Inamovible"]}, headers=ORIGIN)
    assert r.status_code == 422 and "dest" not in called
    assert r.json()["skipped"] == [{"email": "ana@danwor.com", "reason": "fuera_del_estado"}]


def test_notify_blocks_when_estados_unavailable(monkeypatch, audit_dir):
    data = dict(_ok_compliance(), estados_disponibles=False)
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: data)
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["reason"] == "estados_no_disponibles"


def test_panel_renders_estado_filter_with_activo_by_default(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert 'name="estados" value="Activo" data-tn-estado-opt aria-label="Estado Activo" checked' in html
    assert 'value="Tentativo" data-tn-estado-opt aria-label="Estado Tentativo">' in html
    assert 'data-tn-estados="Activo"' in html and 'data-estados-ok="1"' in html


# ═══════════════════════ envío REAL (sin modo prueba en el flujo operativo) ═══════════════════════

def test_notify_sends_to_the_collaborator_padron_email(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    sent = {}
    def fake_batch(dest, **k): sent["dest"] = dest; return {"resultados": [], "resumen": {"total": len(dest)}}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", fake_batch)
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 200
    assert [d["email"] for d in sent["dest"]] == ["ana@danwor.com"]         # destinatario = email del padrón (no el actor)
    assert "test_mode" not in r.json()                                      # el flujo operativo ya no expone modo prueba


def test_browser_cannot_inject_an_arbitrary_recipient(monkeypatch, audit_dir):
    # El destinatario sale del padrón recalculado en servidor (by_email), NUNCA de lo que manda el navegador: un email ajeno se omite.
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    sent = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch",
                        lambda dest, **k: (sent.setdefault("dest", dest), {"resultados": [], "resumen": {"total": len(dest)}})[1])
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["atacante@externo.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and sent.get("dest") is None
    assert any(s["email"] == "atacante@externo.com" and s["reason"] == "fuera_del_padron" for s in r.json()["skipped"])


def test_panel_has_no_test_mode_switch(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert "data-tn-test-mode" not in html and "Modo prueba" not in html and "[PRUEBA]" not in html


def test_panel_rerender_keeps_the_requested_estados_checked(monkeypatch, audit_dir):
    seen = {}
    def fake(**k): seen.update(k); return _ok_compliance()
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", fake)
    html = ops().post(DATA, data={"graph_token": "t", "estados_presentes": "1", "estados": ["Activo", "Inactivo"]}, headers=ORIGIN).text
    assert 'value="Inactivo" data-tn-estado-opt aria-label="Estado Inactivo" checked' in html
    assert 'value="Tentativo" data-tn-estado-opt aria-label="Estado Tentativo">' in html
    assert seen["include_inactive"] is True                                   # Inactivo pide el historial (recarga en servidor)
    assert 'hx-sync="closest form:replace"' in html                           # la última consulta reemplaza a la anterior


# ═══════════════════════ Disponibilidad de asignación: obligación solo Full Time ═══════════════════════

def _with_availability(avail, csv=CSV, ok=True):
    import webapp.services.timenotes_service as TS
    ids = {"ana@danwor.com": "1", "beto@danwor.com": "2", "caro@danwor.com": "3"}
    orig = TS._resolve_padron
    TS._resolve_padron = lambda token, fn: (dict(PADRON), ids)
    try:
        return T.build_compliance(token="x", week=WEEK, download=lambda t: csv, meta=lambda t: None, absences_fn=lambda t: [],
                                  assignments_fn=lambda *a, **k: [], availability_fn=(lambda *a, **k: avail) if ok else (lambda *a, **k: None), holidays_fn=_NOHOL)
    finally:
        TS._resolve_padron = orig


def test_full_time_keeps_40h_obligation_and_part_time_or_unknown_never_fail():
    csv = CSV + b"caro@danwor.com,2026-11-04,20,P3\n"                 # Caro (Part Time) 20 h
    out = _with_availability({"1": "Full Time", "2": "Full Time", "3": "Part Time"}, csv=csv)
    by = {c["email"]: c for c in out["colaboradores"]}
    assert by["beto@danwor.com"]["cumple"] and not by["beto@danwor.com"]["notificable"]              # FT con 40 h
    assert by["ana@danwor.com"]["faltantes"] == 24 and by["ana@danwor.com"]["notificable"]           # FT con faltantes
    caro = by["caro@danwor.com"]
    assert caro["disponibilidad"] == "Part Time" and not caro["obligacion"] and caro["faltantes"] == 0 and not caro["notificable"]
    assert out["resumen"]["con_faltantes"] == 1 and out["resumen"]["cumplen"] == 1                  # Caro no cuenta como incumplimiento


def test_unknown_or_unreadable_availability_never_declares_noncompliance():
    out = _with_availability({"1": "Full Time", "2": ""})                         # Beto vacío, Caro sin ítem → «Sin dato»
    by = {c["email"]: c for c in out["colaboradores"]}
    assert by["beto@danwor.com"]["disponibilidad"] == "Sin dato" and not by["beto@danwor.com"]["obligacion"]
    assert by["caro@danwor.com"]["disponibilidad"] == "Sin dato" and not by["caro@danwor.com"]["notificable"]
    down = _with_availability(None, ok=False)
    assert down["disponibilidad_disponible"] is False and not any(c["notificable"] for c in down["colaboradores"])


def test_availability_is_per_collaborator_not_per_assignment():
    import webapp.services.timenotes_service as TS
    rows = [_asig("1", "Activo"), _asig("1", "Tentativo")]                      # varias asignaciones: la disponibilidad NO viene de ellas
    orig = TS._resolve_padron
    TS._resolve_padron = lambda token, fn: (dict(PADRON), {"ana@danwor.com": "1"})
    try:
        out = T.build_compliance(token="x", week=WEEK, download=lambda t: CSV, meta=lambda t: None, absences_fn=lambda t: [],
                                 assignments_fn=lambda *a, **k: rows, availability_fn=lambda *a, **k: {"1": "Part Time"}, holidays_fn=_NOHOL)
    finally:
        TS._resolve_padron = orig
    ana = next(c for c in out["colaboradores"] if c["email"] == "ana@danwor.com")
    assert ana["estados"] == ["Activo", "Tentativo"] and ana["disponibilidad"] == "Part Time" and not ana["notificable"]


def test_notify_revalidates_obligation_server_side(monkeypatch, audit_dir):
    data = _ok_compliance()
    data["colaboradores"][0].update(obligacion=False, disponibilidad="Part Time")          # Ana PT: el cliente la manda igual
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: data)
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: called.setdefault("dest", dest))
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and "dest" not in called
    assert r.json()["skipped"] == [{"email": "ana@danwor.com", "reason": "sin_obligacion_definida"}]


def test_panel_defaults_and_keeps_local_filters_on_reload(monkeypatch, audit_dir):
    data = _ok_compliance()
    data["colaboradores"][1].update(disponibilidad="Part Time", obligacion=False)
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: dict(data))
    first = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert '<option value="Full Time" selected>Full Time</option>' in first and '<option value="faltantes" selected>' in first
    assert 'data-tn-disp="Part Time"' in first and "Sin mínimo definido" in first
    again = ops().post(DATA, data={"graph_token": "t", "estados_presentes": "1", "estados": ["Activo"], "disponibilidad": "",
                                   "cumplimiento": "cumple", "q": "beto"}, headers=ORIGIN).text
    assert '<option value="" selected>Todos</option>' in again and '<option value="cumple" selected>' in again and 'value="beto"' in again


def test_preview_action_hidden_but_endpoint_kept(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert "data-tn-preview=" not in html and 'data-tn-exc-add="ana@danwor.com"' in html     # sin «ojo»; «Mover a excepciones» sigue
    assert 'id="tn-preview-dialog"' in html                                                  # el diálogo se conserva
    r = ops().post("/operaciones/staffing/carga-horas/preview", data={"graph_token": "t", "email": "ana@danwor.com"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["subject"].startswith("Recordatorio de carga de horas")  # endpoint operativo, asunto real (sin [PRUEBA])
    assert "[PRUEBA]" not in r.json()["subject"]


# ═══════════════════════ Horas base de consulta (simulación) ═══════════════════════

def test_horas_base_simulation_recalculates_view_only(monkeypatch, audit_dir):
    seen = {}
    def fake(**k): seen.update(k); return dict(_ok_compliance(), horas_base=k.get("weekly_hours") or 40)
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", fake)
    html = ops().post(DATA, data={"graph_token": "t", "estados_presentes": "1", "estados": ["Activo"], "horas_base": "30",
                                  "feriados_tipos": ["Inamovible"]}, headers=ORIGIN).text
    assert seen["weekly_hours"] == 30 and 'data-simulacion="1"' in html and 'data-horas-base="30"' in html
    assert "Simulación (horas base 30 h)" in html and 'name="horas_base" value="30"' in html
    default = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert seen["weekly_hours"] == 40 and 'data-simulacion=""' in default
    bad = ops().post(DATA, data={"graph_token": "t", "estados_presentes": "1", "horas_base": "500"}, headers=ORIGIN)
    assert seen["weekly_hours"] == 40 and bad.status_code == 200                    # fuera de rango → regla vigente


def test_notify_refuses_simulation_and_always_uses_rule(monkeypatch, audit_dir):
    seen = {}
    def fake(**k): seen.update(k); return _ok_compliance()
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", fake)
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: (called.setdefault("dest", dest), {"resultados": [], "resumen": {"total": 1}})[1])
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"], "horas_base": "30"}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["reason"] == "simulacion" and "dest" not in called
    seen.clear()
    ok = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"], "horas_base": "40"}, headers=ORIGIN)
    assert ok.status_code == 200 and seen.get("weekly_hours") is None                  # el envío calcula con la regla (40 h)


# ═══════════════════════ Feriados nacionales (contrato aprobado 2026-11-12) ═══════════════════════

def _xlsx(rows, sheet="Feriados", headers=("Fecha", "Motivo", "Tipo"), extra_sheet=None):
    import io as _io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if headers:
        ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    if extra_sheet:
        wb.create_sheet(extra_sheet)
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_W = (dt.date(2026, 11, 2), dt.date(2026, 11, 6))                  # lun 02/11 – vie 06/11/2026
_COV = [(dt.date(2026, 1, 1), "Año Nuevo", "Inamovible")]           # cobertura 2026


def _hol(rows, **kw):
    return T.parse_holidays(_xlsx(rows, **kw), *_W)


def test_holidays_real_schema_dates_types_duplicates_and_weekends():
    assert _hol(_COV)["feriados"] == [] and _hol(_COV)["estado"] == "ok"
    out = _hol(_COV + [(dt.date(2026, 11, 3), "Día A", "Inamovible"), ("05/11/2026", "Puente", "No laborable"),
                       (dt.date(2026, 11, 3), "Día A (bis)", "Turístico"), (dt.date(2026, 11, 7), "Sábado", "Inamovible"),
                       (dt.date(2026, 11, 8), "Domingo", "Inamovible")])
    assert [(f["fecha"], f["tipos"]) for f in out["feriados"]] == [("2026-11-03", ["Inamovible", "Turistico"]), ("2026-11-05", ["No laborable"])]
    assert out["feriados"][0]["descripcion"] == "Día A"                                    # Motivo = descripción
    other = T.parse_holidays(_xlsx(_COV + [(dt.date(2026, 11, 4), "X", "inamovible")], sheet="Hoja1"), *_W)   # hoja con otro nombre
    assert other["estado"] == "ok" and other["hoja"] == "Hoja1" and other["feriados"][0]["tipos"] == ["Inamovible"]


def test_holidays_invalid_sources_are_distinguished():
    assert _hol(_COV, headers=("Fecha", "Motivo"))["estado"] == "estructura_invalida"
    assert T.parse_holidays(_xlsx(_COV, sheet="Uno", extra_sheet="Dos"), *_W)["estado"] == "ok"            # única hoja con las columnas
    assert _hol(_COV + [(dt.date(2026, 11, 4), "X", "Optativo")])["estado"] == "estructura_invalida"
    assert _hol(_COV + [("31/02/2026", "Fecha rota", "Inamovible")])["estado"] == "fecha_invalida"
    assert _hol([(dt.date(2025, 12, 25), "Navidad", "Inamovible")])["estado"] == "sin_cobertura"
    assert T.parse_holidays(b"no es excel", *_W)["estado"] == "estructura_invalida"
    from urllib.error import HTTPError
    def missing(token, path=None): raise HTTPError("u", 404, "nf", {}, None)
    assert T.load_holidays("t", *_W, download=missing)["estado"] == "ausente"


def test_holiday_types_drive_the_discount_without_double_counting():
    hol = {"estado": "ok", "detalle": "", "feriados": [{"fecha": "2026-11-03", "descripcion": "A", "tipos": ["Inamovible", "Turistico"]},
                                                     {"fecha": "2026-11-05", "descripcion": "B", "tipos": ["No laborable"]}]}
    def exig(tipos):
        return {c["email"]: c for c in _calc(hol, tipos=tipos)["colaboradores"]}["ana@danwor.com"]["horas_exigibles"]
    assert exig(None) == 32                                   # default operativo: solo Inamovible
    assert exig(["Inamovible", "No laborable"]) == 24
    assert exig(["Inamovible", "Turistico"]) == 32            # misma fecha con dos tipos: un solo descuento
    assert exig(["Inamovible", "No laborable", "Turístico"]) == 24
    assert exig([]) == 40                                     # ninguno seleccionado


def _calc(holidays, base=None, avail=None, tipos=None):
    import webapp.services.timenotes_service as TS
    orig = TS._resolve_padron
    TS._resolve_padron = lambda token, fn: (dict(PADRON), {"ana@danwor.com": "1", "beto@danwor.com": "2", "caro@danwor.com": "3"})
    try:
        return T.build_compliance(token="x", week=WEEK, weekly_hours=base, download=lambda t: CSV, meta=lambda t: None,
                                  absences_fn=lambda t: [], assignments_fn=lambda *a, **k: [],
                                  availability_fn=lambda *a, **k: (avail or {"1": "Full Time", "2": "Full Time", "3": "Part Time"}),
                                  holidays_fn=lambda *a, **k: holidays, holiday_types=tipos)
    finally:
        TS._resolve_padron = orig


def test_full_time_requirement_is_reduced_proportionally_and_part_time_untouched():
    one = {"estado": "ok", "feriados": [{"fecha": "2026-11-04", "descripcion": "X"}], "detalle": ""}
    out = _calc(one)
    by = {c["email"]: c for c in out["colaboradores"]}
    assert by["ana@danwor.com"]["horas_exigibles"] == 32 and by["ana@danwor.com"]["horas_registradas"] == 16 and by["ana@danwor.com"]["faltantes"] == 16
    assert by["beto@danwor.com"]["cumple"]                                                     # 40 registradas ≥ 32
    assert not by["caro@danwor.com"]["obligacion"]                                             # Part Time: sin mínimo
    assert out["feriados"]["cantidad"] == 1 and out["feriados"]["descuento_h"] == 8
    two = {"estado": "ok", "feriados": [{"fecha": "2026-11-03", "descripcion": "A"}, {"fecha": "2026-11-05", "descripcion": "B"}], "detalle": ""}
    assert {c["email"]: c for c in _calc(two)["colaboradores"]}["ana@danwor.com"]["horas_exigibles"] == 24
    sim = {c["email"]: c for c in _calc(one, base=30)["colaboradores"]}                        # simulación proporcional: 30 × 4/5
    assert sim["ana@danwor.com"]["horas_exigibles"] == 24


def test_unverified_calendar_is_provisional_visible_and_blocks_notification(monkeypatch, audit_dir):
    out = _calc({"estado": "sin_cobertura", "feriados": [], "detalle": "sin 2026"})
    by = {c["email"]: c for c in out["colaboradores"]}
    assert out["feriados"]["verificado"] is False and not any(c["notificable"] for c in out["colaboradores"])
    ana = by["ana@danwor.com"]                                                    # provisional: contra 40 h SIN descontar feriados
    assert not ana["calculo_verificado"] and ana["horas_exigibles"] == 40 and ana["horas_registradas"] == 16 and ana["faltantes"] == 24
    assert out["resumen"]["con_faltantes"] == 1 and out["resumen"]["cumplen"] == 1                   # KPIs visibles (provisionales)
    data = dict(_ok_compliance(), feriados={"verificado": False, "estado": "ausente", "detalle": "No se encontró"})
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: data)
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: called.setdefault("dest", dest))
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["reason"] == "feriados_no_verificados" and "dest" not in called


def test_reminder_uses_adjusted_requirement(monkeypatch, audit_dir):
    data = _ok_compliance()
    data["colaboradores"][0].update(horas_exigibles=32, faltantes=16, horas_registradas=16)
    data["feriados"] = {"verificado": True, "estado": "ok", "cantidad": 1, "descuento_h": 8, "lista": []}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: data)
    sent = {}
    def fake_batch(dest, **k): sent["dest"] = dest; return {"resultados": [], "resumen": {"total": len(dest)}}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", fake_batch)
    r = ops().post(NOTIFY, data={"graph_token": "t", "confirm": "yes", "emails": ["ana@danwor.com"]}, headers=ORIGIN)
    assert r.status_code == 200 and sent["dest"][0]["exigibles"] == 32 and sent["dest"][0]["faltantes"] == 16


def test_csv_web_url_only_when_graph_returns_it():
    got = T.csv_metadata("t", site_id="s", fetch=lambda url, tok: {"lastModifiedDateTime": "2026-11-11T12:00:00Z", "webUrl": "https://danwor.sharepoint.com/x.csv"})
    assert got == {"last_modified": "2026-11-11T12:00:00Z", "web_url": "https://danwor.sharepoint.com/x.csv"}
    assert T.csv_metadata("t", site_id="s", fetch=lambda url, tok: {"lastModifiedDateTime": "x"})["web_url"] is None


def test_panel_shows_provisional_compliance_when_calendar_missing_and_recovers(monkeypatch, audit_dir):
    real = T.build_compliance                                                     # el monkeypatch de abajo reemplaza ESTE mismo atributo

    def render(hol):
        monkeypatch.setattr(T, "build_compliance", real)
        data = _calc(hol)
        data.update(estados_disponibles=True)
        monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: dict(data))
        return ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    missing = render({"estado": "ausente", "feriados": [], "detalle": "No se encontró «Feriados Nacionales.xlsx»."})
    assert "Cálculo provisional" in missing and "Colaboradores (provisional)" in missing and "En incumplimiento (provisional)" in missing
    assert "Faltan 24 h" in missing and 'data-tn-estado="faltantes' in missing                    # horas reales y faltantes visibles
    assert ">Provisional</span>" in missing and 'data-tn-notificable=""' in missing                 # visible, no notificable
    invalid = render({"estado": "estructura_invalida", "feriados": [], "detalle": "Falta la hoja «Feriados»."})
    assert "Cálculo provisional" in invalid and "Falta la hoja" in invalid
    ok = render({"estado": "ok", "feriados": [{"fecha": "2026-11-04", "descripcion": "X"}], "detalle": ""})   # el archivo aparece
    assert "Cálculo provisional" not in ok and "(provisional)" not in ok and "Faltan 16 h" in ok and ">Pendiente</span>" in ok
    none = render({"estado": "ok", "feriados": [], "detalle": ""})
    assert "Faltan 24 h" in none and "(provisional)" not in none


def test_exigibles_help_lives_in_the_section_header_not_in_the_table(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    head = html.split('data-section-id="colaboradores"')[1].split("</summary>")[0]
    assert "Horas exigibles (Full Time)" in head and "activity-kpi__info" in head
    assert '<th class="left">Exigibles</th>' in html and "Horas exigibles (Full Time)" not in html.split("<thead>")[1].split("</thead>")[0]


def test_holiday_type_selection_is_simulation_and_blocks_notification(monkeypatch, audit_dir):
    seen = {}
    def fake(**k): seen.update(k); return _ok_compliance()
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", fake)
    base = {"graph_token": "t", "estados_presentes": "1", "estados": ["Activo"]}
    first = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text                 # primer ingreso: solo Inamovible
    assert seen["holiday_types"] == ("Inamovible",) and 'value="Inamovible" data-tn-feriado-opt aria-label="Feriados Inamovible" checked' in first
    assert 'data-simulacion=""' in first
    sim = ops().post(DATA, data=dict(base, feriados_tipos=["Inamovible", "No laborable"]), headers=ORIGIN).text
    assert seen["holiday_types"] == ("Inamovible", "No laborable") and 'data-simulacion="1"' in sim and "feriados: Inamovible, No laborable" in sim
    none = ops().post(DATA, data=dict(base), headers=ORIGIN).text
    assert seen["holiday_types"] == () and "feriados: ninguno" in none
    back = ops().post(DATA, data=dict(base, feriados_tipos=["Inamovible"]), headers=ORIGIN).text      # vuelta a la regla
    assert 'data-simulacion=""' in back
    called = {}
    monkeypatch.setattr("webapp.routers.staffing.timenotes_send.send_batch", lambda dest, **k: (called.setdefault("dest", dest), {"resultados": [], "resumen": {"total": 1}})[1])
    r = ops().post(NOTIFY, data=dict(base, confirm="yes", emails=["ana@danwor.com"], feriados_tipos=["Inamovible", "Turistico"]), headers=ORIGIN)
    assert r.status_code == 409 and r.json()["reason"] == "simulacion" and "dest" not in called
    seen.clear()
    ok = ops().post(NOTIFY, data=dict(base, confirm="yes", emails=["ana@danwor.com"], feriados_tipos=["Inamovible"]), headers=ORIGIN)
    assert ok.status_code == 200 and seen["holiday_types"] is None                         # el envío calcula con la regla operativa


# ═══════════════════ reconciliación del badge Notificación con la auditoría durable (fuente única, #3) ═══════════════════
import json as _json  # noqa: E402
import re as _re      # noqa: E402


def _write_op(*, status=None, intent=True, corrupt=False, email="ana@danwor.com", semana=SEMANA):
    op = S.audit_root() / S.operation_id_for(semana["inicio"], semana["fin"], email)
    op.mkdir(parents=True, exist_ok=True)
    if intent:
        (op / "intent.json").write_text('{"schema": 1}', encoding="utf-8")
    if corrupt:
        (op / "result.json").write_text("{corrupto", encoding="utf-8")
    elif status is not None:
        (op / "result.json").write_text(_json.dumps({"outcome": status}), encoding="utf-8")
    return op


def test_read_week_states_maps_outcomes_and_is_fail_closed(audit_dir):
    _write_op(status="accepted", email="ana@danwor.com")
    _write_op(status="uncertain", email="beto@danwor.com")
    _write_op(status="failed", email="caro@danwor.com")
    _write_op(status=None, intent=True, email="dani@danwor.com")       # intent sin result
    _write_op(corrupt=True, email="eva@danwor.com")                    # auditoría ilegible
    _write_op(status="raro_desconocido", email="fio@danwor.com")       # outcome desconocido: no se asume fallo
    st = S.read_week_states(SEMANA["inicio"], SEMANA["fin"],
                            ["ana@danwor.com", "beto@danwor.com", "caro@danwor.com", "dani@danwor.com", "eva@danwor.com", "fio@danwor.com", "zoe@danwor.com"])
    assert st == {"ana@danwor.com": "enviada", "beto@danwor.com": "incierta", "caro@danwor.com": "fallida",
                  "dani@danwor.com": "sin_confirmar", "eva@danwor.com": "no_verificable", "fio@danwor.com": "no_verificable"}
    assert "zoe@danwor.com" not in st                                  # inexistente (archivo ausente) != no disponible (ilegible)


def test_read_week_states_is_week_scoped(audit_dir):
    _write_op(status="accepted", email="ana@danwor.com", semana=SEMANA)
    assert S.read_week_states("2026-11-09", "2026-11-13", ["ana@danwor.com"]) == {}   # otra semana: estado independiente


def test_panel_badge_reflects_audit_and_survives_refresh(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    _write_op(status="accepted", email="ana@danwor.com")
    h1 = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert _re.search(r'--green"[^>]*>Enviada<', h1)                   # ana: Enviada (verde) desde la auditoría
    h2 = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text   # refrescar
    assert _re.search(r'--green"[^>]*>Enviada<', h2)                   # sigue Enviada (no vuelve a Pendiente)


def test_panel_without_prior_op_keeps_default(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert ">Enviada<" not in html and ">Pendiente<" in html          # cero operaciones: comportamiento actual intacto


def test_panel_corrupt_audit_is_fail_closed_not_pendiente(monkeypatch, audit_dir):
    monkeypatch.setattr("webapp.routers.staffing.timenotes_service.build_compliance", lambda **k: _ok_compliance())
    _write_op(corrupt=True, email="ana@danwor.com")
    html = ops().post(DATA, data={"graph_token": "t"}, headers=ORIGIN).text
    assert ">No verificable<" in html                                 # fail-closed: NO un falso Pendiente para ana
    assert not _re.search(r'data-tn-email="ana@danwor.com".*?>Pendiente<', html, _re.S)


def test_badge_matches_idempotency_same_durable_source(audit_dir):
    _write_op(status="accepted", email="ana@danwor.com")              # operación previa accepted
    assert S.read_week_states(SEMANA["inicio"], SEMANA["fin"], ["ana@danwor.com"]) == {"ana@danwor.com": "enviada"}
    out = _one(lambda m: ("accepted", "HTTP 202"))                    # reintento real: colisiona con el intent previo
    assert out["status"] == "duplicate"                               # misma fuente durable: panel=Enviada y reintento=duplicado


def test_notif_state_is_independent_of_compliance_and_exceptions(audit_dir):
    # La lectura de notificación NO altera cumplimiento/horas: read_week_states es solo lectura y no muta la entrada.
    _write_op(status="accepted", email="ana@danwor.com")
    cols = [{"email": "ana@danwor.com", "horas_registradas": 16, "cumple": False}]
    snapshot = _json.dumps(cols, sort_keys=True)
    S.read_week_states(SEMANA["inicio"], SEMANA["fin"], [c["email"] for c in cols])
    assert _json.dumps(cols, sort_keys=True) == snapshot
