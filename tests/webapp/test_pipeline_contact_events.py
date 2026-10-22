"""Ajuste 17 — Trazabilidad de comunicaciones con candidatos en el historial EXISTENTE del proceso.

Se registra solo lo confirmado por el navegador: mensaje copiado, canal abierto (WhatsApp verificable) o apertura solicitada (correo, no verificable). Datos estructurados (candidato/postulación, situación, canal, acción, ejecutor,
fecha): nunca el cuerpo del mensaje ni datos de contacto, y nunca «enviado/entregado». Idempotente ante doble clic y reintentos. Fixtures de I1 (persistencia aislada)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import busqueda_repository as brepo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual  # noqa: F401

PHONE, EMAIL = "+54 9 11 5555-1234", "mateo@danwor.com"


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _candidate(bid, first="Mateo", **extra) -> tuple[str, str]:
    pid = profile_repository.create({"first_name": first, "last_name": "Test", "email": EMAIL if first == "Mateo" else f"{first.lower()}@danwor.com", "phone": PHONE, **extra})["profile_id"]
    return pid, svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]


def _url(bid, app_id) -> str:
    return f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto/evento"


def _post(c, bid, app_id, accion="mensaje_copiado", situacion="primer_contacto", canal="whatsapp", event_id=None, **kw):
    return c.post(_url(bid, app_id), data={"accion": accion, "situacion": situacion, "canal": canal, **({"event_id": event_id} if event_id else {})}, **kw)


def _events(bid) -> list[dict]:
    return [h for h in brepo.historial_proceso_of(brepo.get(bid)) if h["transicion"] == "candidato_contacto"]


# ═══════════════════ registro y presentación ═══════════════════
def test_a_copied_message_is_recorded_with_structured_data_and_shown_in_the_history(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    r = _post(c, bid, app_id, "mensaje_copiado", "primer_contacto", "whatsapp", "evt-1")
    assert r.status_code == 200 and r.headers["x-contact-recorded"] == "1" and r.headers["cache-control"] == "no-store"
    [e] = _events(bid)
    assert (e["accion"], e["situacion"], e["canal"], e["event_id"]) == ("mensaje_copiado", "primer_contacto", "whatsapp", "evt-1")
    assert (e["profile_id"], e["application_id"], e["actor"], e["actor_tipo"]) == (pid, app_id, CCHH_EMAIL, "cchh") and e["candidato"] == "Mateo Test" and e["fecha"]   # candidato/postulación/ejecutor/timestamp
    assert "Primer contacto por WhatsApp" in r.text and "Mateo Test — Mensaje copiado" in r.text and CCHH_EMAIL in r.text and 'id="busqueda-historial"' in r.text and "hx-swap-oob" not in r.text
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert "Primer contacto por WhatsApp" in page and "Mensaje copiado" in page                             # y la página de detalle lo muestra


def test_opening_whatsapp_and_mail_are_distinguished_and_never_say_sent(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    _post(c, bid, app_id, "canal_abierto", "primer_contacto", "whatsapp", "e1")
    _post(c, bid, app_id, "apertura_solicitada", "actualizacion", "mail", "e2")
    _post(c, bid, app_id, "canal_abierto", "cierre", "mail", "e3")
    last = _post(c, bid, app_id, "mensaje_copiado", "pausa", "mail", "e4")
    html = last.text                                                                                        # tabla de Historial devuelta por el endpoint
    for texto in ("Primer contacto por WhatsApp", "Mateo Test — Apertura de WhatsApp", "Actualización del proceso por mail", "Mateo Test — Apertura solicitada (correo)",
                  "Cierre del proceso por mail", "Mateo Test — Apertura de correo", "Pausa del proceso por mail", "Mateo Test — Mensaje copiado"):
        assert texto in html, texto
    low = html.lower()
    assert "enviado" not in low and "entregado" not in low and "recibido" not in low                           # el historial nunca afirma envío/entrega/recepción
    assert [(e["accion"], e["canal"]) for e in _events(bid)] == [("canal_abierto", "whatsapp"), ("apertura_solicitada", "mail"), ("canal_abierto", "mail"), ("mensaje_copiado", "mail")]


def test_changing_situation_and_channel_between_actions_is_reflected_in_each_event(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    for sit, canal in (("primer_contacto", "whatsapp"), ("primer_contacto", "mail"), ("pausa", "whatsapp")):
        assert _post(c, bid, app_id, "mensaje_copiado", sit, canal).status_code == 200
    assert [(e["situacion"], e["canal"]) for e in _events(bid)] == [("primer_contacto", "whatsapp"), ("primer_contacto", "mail"), ("pausa", "whatsapp")]


def test_the_executor_is_the_authenticated_session_user(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    # el ejecutor sale de la sesión (claim `sub`), nunca de un campo del formulario
    r = c.post(_url(bid, app_id), data={"accion": "mensaje_copiado", "situacion": "pausa", "canal": "mail", "actor": "falso@danwor.com", "event_id": "x"})
    assert r.status_code == 200 and _events(bid)[0]["actor"] == CCHH_EMAIL and "falso@danwor.com" not in json.dumps(_events(bid))


# ═══════════════════ qué NO se persiste ═══════════════════
def test_no_message_body_contact_data_or_urls_are_persisted(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    c.post(_url(bid, app_id), data={"accion": "canal_abierto", "situacion": "primer_contacto", "canal": "whatsapp", "event_id": "e",
                                    "cuerpo": "TEXTO SECRETO DEL MENSAJE", "mensaje": "TEXTO SECRETO", "url": "https://wa.me/5491155551234?text=x", "token": "tok", "telefono": PHONE})
    [e] = _events(bid)
    assert set(e) == {"transicion", "desde", "hasta", "actor", "actor_tipo", "fecha", "profile_id", "candidato", "application_id", "accion", "situacion", "canal", "event_id"}   # lista blanca exacta
    stored = json.dumps(brepo.get(bid))
    for leak in ("TEXTO SECRETO", "wa.me", "5491155551234", PHONE, "tok", "mailto:"):
        assert leak not in stored.replace(EMAIL, "")                                                       # (el email del perfil vive en el Banco, no en el historial)
    assert EMAIL not in json.dumps(e)


# ═══════════════════ duplicados ═══════════════════
def test_double_click_and_http_retries_do_not_duplicate_events(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    first = _post(c, bid, app_id, "mensaje_copiado", event_id="same-id")
    retry = _post(c, bid, app_id, "mensaje_copiado", event_id="same-id")                                     # reintento HTTP con el mismo event_id
    assert first.headers["x-contact-recorded"] == "1" and retry.status_code == 200 and retry.headers["x-contact-recorded"] == "0" and len(_events(bid)) == 1
    dbl = _post(c, bid, app_id, "mensaje_copiado", event_id="otro-id")                                       # doble clic: misma acción/situación/canal/ejecutor en pocos segundos
    assert dbl.headers["x-contact-recorded"] == "0" and len(_events(bid)) == 1
    revision = brepo.get(bid)["revision"]
    _post(c, bid, app_id, "mensaje_copiado", event_id="y")
    assert brepo.get(bid)["revision"] == revision                                                            # un duplicado ni siquiera reescribe el proceso
    assert _post(c, bid, app_id, "canal_abierto", event_id="z").headers["x-contact-recorded"] == "1"        # otra acción sí cuenta
    assert _post(c, bid, app_id, "mensaje_copiado", canal="mail", event_id="w").headers["x-contact-recorded"] == "1"   # otro canal sí cuenta
    assert len(_events(bid)) == 3


# ═══════════════════ errores y acciones canceladas ═══════════════════
@pytest.mark.parametrize("data", [
    {"accion": "enviado", "situacion": "primer_contacto", "canal": "whatsapp"},                              # nunca existe «enviado»
    {"accion": "mensaje_copiado", "situacion": "inventada", "canal": "whatsapp"},
    {"accion": "mensaje_copiado", "situacion": "primer_contacto", "canal": "sms"},
    {"accion": "", "situacion": "", "canal": ""},
])
def test_invalid_requests_register_nothing(c, data):
    bid = _manual()
    _, app_id = _candidate(bid)
    before = brepo.get(bid)
    assert c.post(_url(bid, app_id), data=data).status_code == 400
    assert brepo.get(bid) == before and _events(bid) == []


def test_unknown_process_or_application_and_foreign_application_register_nothing(c):
    bid, other = _manual(), _manual(nombre_puesto="Otro")
    _, foreign = _candidate(other)
    assert _post(c, bid, foreign).status_code == 404 and _post(c, "no-existe", foreign).status_code == 404 and _post(c, bid, "no-existe").status_code == 404
    assert _events(bid) == [] and _events(other) == []


def test_opening_a_channel_without_that_contact_data_is_rejected_but_copying_is_allowed(c):
    bid = _manual()
    _, no_phone = _candidate(bid, "Sinfono", phone="")
    _, no_mail = _candidate(bid, "Sinmail", email="")
    assert _post(c, bid, no_phone, "canal_abierto", canal="whatsapp").status_code == 409
    assert _post(c, bid, no_mail, "apertura_solicitada", canal="mail").status_code == 409
    assert _events(bid) == []
    assert _post(c, bid, no_phone, "mensaje_copiado", canal="whatsapp").status_code == 200 and len(_events(bid)) == 1       # copiar el texto no necesita el canal


def test_a_closed_process_and_a_removed_candidate_register_nothing(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    gone_pid, gone_app = _candidate(bid, "Baja")
    profile_repository.soft_delete(gone_pid, actor=CCHH_EMAIL)
    assert _post(c, bid, gone_app).status_code == 404
    svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
    assert _post(c, bid, app_id).status_code == 409 and _events(bid) == []


def test_a_storage_failure_returns_an_error_and_leaves_no_event(c, monkeypatch):
    bid = _manual()
    _, app_id = _candidate(bid)
    def boom(*a, **k):
        raise brepo.BusquedaStorageError("disco")
    with monkeypatch.context() as m:
        m.setattr(brepo, "_persist", boom)
        r = _post(c, bid, app_id)
    assert r.status_code == 503 and "No se pudo registrar" in r.text
    assert _events(bid) == []


def test_requires_the_cchh_session():
    bid = _manual()
    _, app_id = _candidate(bid)
    r = TestClient(app, follow_redirects=False).post(_url(bid, app_id), data={"accion": "mensaje_copiado", "situacion": "pausa", "canal": "mail"})
    assert r.status_code in (302, 303, 307) and _events(bid) == []


def test_opening_the_dialog_and_changing_selects_registers_nothing_and_the_dialog_posts_only_on_explicit_actions(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    before = brepo.get(bid)
    frag = c.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto").text                                  # abrir el diálogo = solo lectura
    assert brepo.get(bid) == before and _events(bid) == []
    assert f'data-event-url="/cchh/busquedas/{bid}/candidatos/{app_id}/contacto/evento"' in frag and "data-contact-log-note" in frag
    body = c.get(f"/cchh/busquedas/{bid}").text
    script = body.split("__hcContactWired", 1)[1].split("</script>")[0]
    assert script.count('logEvent("') == 3 and 'logEvent("mensaje_copiado")' in script and 'logEvent("canal_abierto")' in script and 'logEvent("apertura_solicitada")' in script
    assert 'if (what === "mensaje"' in script.replace("what === \"mensaje\" ?", 'if (what === "mensaje"')                                      # solo «Copiar mensaje» registra (no email/teléfono/asunto)
    assert "if (w) {" in script and "bloqueó la apertura" in script                                          # apertura bloqueada: no se registra
    assert "event_id: newId()" in script and "recent[key]" in script                                         # id de evento + guarda anti doble clic en el navegador


# ═══════════════════ integración con el historial / seguridad ═══════════════════
def test_contact_events_do_not_alter_the_pipeline_staleness_or_leak_to_operations(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    app_before = brepo.get(bid)["candidatos"][0]
    dias = svc.busqueda_dias_sin_movimiento(brepo.get(bid))
    _post(c, bid, app_id)
    after = brepo.get(bid)
    assert after["candidatos"][0] == app_before and svc.busqueda_dias_sin_movimiento(after) == dias           # ni etapa ni «último movimiento»
    assert all(r["transicion"] != "candidato_contacto" for r in svc.solicitud_view(after, include_cchh_actors=False)["historial"]) if svc.solicitud_view(after) else True


def test_contact_events_on_a_solicitud_process_live_in_the_same_history_and_stay_internal_to_cchh(c):
    from tests.webapp.test_busqueda_solicitud_i1 import OPS_A, SID, req
    svc.create_solicitud(SID, req(), OPS_A)
    svc.take_solicitud(SID, 1, CCHH_EMAIL)
    _, app_id = _candidate(SID)
    assert _post(c, SID, app_id, "canal_abierto", "primer_contacto", "whatsapp").status_code == 200
    doc = brepo.get(SID)
    assert any(h["transicion"] == "candidato_contacto" for h in doc["solicitud"]["historial"]) and not doc.get("historial")   # MISMA lista de la solicitud, no una paralela
    assert all(r["transicion"] != "candidato_contacto" for r in svc.solicitud_view(doc, include_cchh_actors=False)["historial"])                  # Operaciones no ve comunicaciones con candidatos
    assert "Primer contacto por WhatsApp" in c.get(f"/cchh/busquedas/{SID}").text


# ═══════════════════ endurecimiento: la auditoría no invalida la revisión del proceso ═══════════════════
def _form_revision(c, bid) -> int:
    import re
    return int(re.search(r'name="expected_revision" value="(\d+)"', c.get(f"/cchh/busquedas/{bid}/editar").text).group(1))


def _edit(c, bid, revision, **over):
    return c.post(f"/cchh/busquedas/{bid}/editar", data={"nombre_puesto": "Solution Architect", "fecha_apertura": "2026-10-01", "expected_revision": str(revision), **over})


def test_a_contact_event_does_not_bump_the_revision_but_functional_changes_still_do(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    rev = brepo.get(bid)["revision"]
    assert _post(c, bid, app_id, "mensaje_copiado", event_id="a").headers["x-contact-recorded"] == "1"
    assert brepo.get(bid)["revision"] == rev and len(_events(bid)) == 1                                       # evento escrito, revisión intacta
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    assert brepo.get(bid)["revision"] == rev + 1                                                              # el contrato del pipeline no cambia


def test_an_open_edit_form_survives_a_contact_event_and_the_event_is_not_lost(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    rev = _form_revision(c, bid)                                                                              # el usuario abre la edición...
    _post(c, bid, app_id, "canal_abierto", "primer_contacto", "whatsapp", "e1")                               # ...mientras otro registra un contacto
    r = _edit(c, bid, rev, descripcion="Descripción editada")
    assert r.status_code == 303                                                                               # sin falso conflicto de edición
    doc = brepo.get(bid)
    assert doc["descripcion"] == "Descripción editada" and [e["event_id"] for e in _events(bid)] == ["e1"]    # la edición se aplicó y el evento sigue en el historial
    assert doc["revision"] == rev + 1                                                                         # la edición sí avanza la revisión


def test_a_real_concurrent_edit_is_still_detected_and_contacts_after_it_do_not_disturb(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    stale = _form_revision(c, bid)
    assert _edit(c, bid, stale, descripcion="primera").status_code == 303                                     # otro usuario guarda primero
    _post(c, bid, app_id, "mensaje_copiado", event_id="e2")
    after = brepo.get(bid)["revision"]
    r = _edit(c, bid, stale, descripcion="segunda")                                                           # el formulario viejo SIGUE chocando (cambio funcional real)
    assert r.status_code == 200 and "cambió desde que abriste" in r.text and brepo.get(bid)["descripcion"] == "primera" and brepo.get(bid)["revision"] == after
    assert _edit(c, bid, after, descripcion="tercera").status_code == 303                                     # con la revisión vigente (que los contactos no movieron) se guarda


def test_contacts_do_not_move_the_revision_that_operations_sees_on_a_solicitud(c):
    from tests.webapp.test_busqueda_solicitud_i1 import OPS_A, SID, req
    svc.create_solicitud(SID, req(), OPS_A)
    svc.take_solicitud(SID, 1, CCHH_EMAIL)
    _, app_id = _candidate(SID)
    rev = svc.solicitud_view(brepo.get(SID))["revision"]
    _post(c, SID, app_id, "mensaje_copiado", event_id="s1")
    assert svc.solicitud_view(brepo.get(SID))["revision"] == rev


def test_concurrent_contacts_and_an_edit_lose_nothing_and_never_conflict(c):
    from concurrent.futures import ThreadPoolExecutor
    bid = _manual()
    _, app_id = _candidate(bid)
    rev = _form_revision(c, bid)
    combos = [(a, s, k) for a in ("mensaje_copiado", "canal_abierto") for s in ("primer_contacto", "actualizacion", "cierre") for k in ("whatsapp",)]   # 6 eventos distintos (sin ventana de duplicado)

    def contact(i_combo):
        i, (a, s, k) = i_combo
        return _post(session_client(CCHH_EMAIL), bid, app_id, a, s, k, f"c{i}").status_code

    def edit():
        return _edit(session_client(CCHH_EMAIL), bid, rev, descripcion="editada en paralelo").status_code

    with ThreadPoolExecutor(max_workers=7) as ex:
        futures = [ex.submit(contact, x) for x in enumerate(combos)] + [ex.submit(edit)]
        codes = [f.result() for f in futures]
    assert codes[:-1] == [200] * 6 and codes[-1] == 303                                                       # ningún contacto falla y la edición no choca por ellos
    doc = brepo.get(bid)
    assert doc["descripcion"] == "editada en paralelo" and sorted(e["event_id"] for e in _events(bid)) == sorted(f"c{i}" for i in range(6))   # ni un evento ni la edición se pierden
    assert doc["revision"] == rev + 1


def test_a_failed_write_leaves_neither_event_nor_revision_change(c, monkeypatch):
    bid = _manual()
    _, app_id = _candidate(bid)
    before = brepo.get(bid)
    def boom(*a, **k):
        raise brepo.BusquedaStorageError("disco")
    with monkeypatch.context() as m:
        m.setattr(brepo, "_persist", boom)
        assert _post(c, bid, app_id).status_code == 503
    assert brepo.get(bid) == before                                                                           # documento idéntico: sin evento, sin revisión nueva, sin updated_at nuevo
