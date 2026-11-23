"""PR-1 — Solicitudes de actualización del Assessment gobernado: registro, deduplicación, obsolescencia, procedencia, autorización,
dry-run de SOLO LECTURA, notificación, telemetría y el contenedor «Catálogos y Assessments». Nada de esta superficie aprueba ni escribe sobres."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from webapp import access
from webapp.services import assessment_service as svc
from webapp.services import auth_service
from webapp.services import catalog_activity
from webapp.services import datasphere_assessment_service as dsa
from webapp.services import governed_update_requests as gur

STEM = "04_Modelado_y_Desarrollo"
ORIGIN = {"origin": "http://testserver"}
RID = "f" * 32


def _leaves(cls):
    return {"leaves": [{"leaf": f"1TR_SSF_{i}", "contract_class": cls} for i in range(15)]}


def _env(*, space=None, level=None, status="NOT_DETERMINABLE"):
    return {"client": "Contreras", "terminal_am": "AM_Workforce", "judgment": {"status": status, "level": level}, "generated_at": "2026-11-01T00:00:00+00:00",
            "profile_json": {"scope": ({"space": space} if space else {}), "canonical_evidence": {"source_contract_classes": _leaves("EXTERNAL_CONTRACT_UNDETERMINED")}}}


CAND_PROFILE = {"canonical_evidence": {"source_contract_classes": _leaves("GOVERNED_SAP_APPLICATION_API")}}
CAND_J = {"status": "DETERMINED", "level": "BUENA"}


@pytest.fixture
def world(monkeypatch):
    state = {"env": _env(), "sha": "a" * 64, "published": None}
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: state["env"])
    monkeypatch.setattr(gur, "envelope_sha", lambda c, a: state["sha"])
    monkeypatch.setattr(svc, "published_space", lambda c, a: state["published"])
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "content@danwor.com", raising=False)
    from webapp.services import governed_update_approval as gua
    monkeypatch.setattr(gua, "_readiness", {"state": gua.READINESS_READY, "reason": None})   # C5: proceso ya recuperado (los tests de compuerta la cierran)
    monkeypatch.setattr(gur, "_review_seen", {})                                             # C6: deduplicación de revisiones aislada por test
    return state


def _create(**kw):
    args = dict(client_dir="Contreras", client_display="Contreras", terminal_am="AM_Workforce", space="DEV", result_id=RID,
                candidate_judgment=CAND_J, candidate_profile=CAND_PROFILE, requested_by="consultor@danwor.com")
    args.update(kw)
    return gur.create(**args)


# ── registro ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_create_registers_a_summary_without_sensitive_payloads(world):
    rec, created = _create()
    assert created and rec["status"] == gur.OPEN and rec["schema"] == gur.SCHEMA
    assert rec["governed"]["sha256"] == "a" * 64 and rec["governed"]["judgment"]["level"] is None
    src = dict(rec["source"])
    fp = src.pop("fingerprint")
    assert src == {"result_id": RID, "space": "DEV", "judgment": {"status": "DETERMINED", "level": "BUENA"},
                   "source_contracts": {"GOVERNED_SAP_APPLICATION_API": 15}}
    assert fp["schema"] == "governed-evidence-fingerprint/v1" and len(fp["equivalence_digest"]) == 64 and len(fp["identity_digest"]) == 64
    on_disk = json.loads(next(config.governed_update_requests_dir().glob("*/*/*.json")).read_text(encoding="utf-8"))
    assert on_disk["request_id"] == rec["request_id"]
    blob = json.dumps(on_disk)
    assert "profile_json" not in blob and "canonical_evidence" not in blob and "leaves" not in blob     # solo el resumen
    assert gur.load(rec["request_id"])["request_id"] == rec["request_id"]


def test_without_a_relevant_difference_nothing_is_registered(world):
    world["env"] = {**_env(level="BUENA", status="DETERMINED"),
                    "profile_json": {"scope": {}, "canonical_evidence": {"source_contract_classes": _leaves("GOVERNED_SAP_APPLICATION_API")}}}
    with pytest.raises(gur.NoOpportunity):
        _create()
    assert not list(config.governed_update_requests_dir().glob("*/*/*.json"))


def test_one_open_request_per_model_is_reused(world):
    first, created = _create()
    again, created_again = _create(requested_by="otro@danwor.com")
    assert created and not created_again and again["request_id"] == first["request_id"]


def test_a_changed_governed_envelope_makes_the_request_obsolete_and_allows_a_new_one(world):
    first, _ = _create()
    world["sha"] = "b" * 64                                                        # el Assessment gobernado cambió
    assert gur.current_status(first) == gur.OBSOLETE and gur.find_open("Contreras", "AM_Workforce") is None
    second, created = _create()
    assert created and second["request_id"] != first["request_id"]


# ── procedencia ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cand,gov,pub,expected,verified", [
    ("DEV", "DEV", None, gur.PROV_VERIFIED, True),
    ("DEV", "PRD", "DEV", gur.PROV_CONFLICT, False),                               # el sobre manda: el ledger no lo pisa
    ("DEV", None, "DEV", gur.PROV_AUXILIARY, False),                               # published_space es AUXILIAR: nunca verifica
    ("DEV", None, "PRD", gur.PROV_CONFLICT, False),
    ("DEV", None, None, gur.PROV_UNVERIFIED, False),
])
def test_provenance_only_the_envelope_space_verifies(cand, gov, pub, expected, verified):
    p = gur.assess_provenance(cand, gov, pub)
    assert p["status"] == expected and p["verified"] is verified


def test_request_records_auxiliary_provenance_without_verifying(world):
    world["published"] = "DEV"
    rec, _ = _create()
    assert rec["provenance"]["status"] == gur.PROV_AUXILIARY and rec["provenance"]["verified"] is False


# ── autorización ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_approver_role_is_app_owner_or_content_owner_only(world):
    assert gur.approver_role({"email": "owner@danwor.com", "sub": "owner@danwor.com"}) == "APP_OWNER"
    assert gur.approver_role({"email": "content@danwor.com", "sub": "content@danwor.com"}) == "CONTENT_OWNER"
    assert gur.approver_role({"email": "consultor@danwor.com", "sub": "consultor@danwor.com"}) is None
    rec, _ = _create()
    assert gur.can_view({"sub": "consultor@danwor.com"}, rec) and not gur.can_view({"sub": "otro@danwor.com"}, rec)


def test_review_is_deduplicated_per_person_without_writing_the_request(world):
    rec, _ = _create()
    path = gur._path("Contreras", "AM_Workforce", rec["request_id"])
    before = path.read_bytes()
    owner, content = {"sub": "owner@danwor.com", "email": "owner@danwor.com"}, {"sub": "content@danwor.com", "email": "content@danwor.com"}
    assert gur.should_log_review(rec["request_id"], owner, now=1000.0)
    assert not gur.should_log_review(rec["request_id"], owner, now=1000.0 + 60)                    # recarga: no es otra revisión (misma métrica que antes)
    assert gur.should_log_review(rec["request_id"], content, now=1000.0 + 60)                       # otro revisor sí cuenta
    assert gur.should_log_review(rec["request_id"], owner, now=1000.0 + gur.REVIEW_DEDUP_S + 1)
    assert path.read_bytes() == before                                                              # C6: el registro transaccional no se toca


def test_the_review_get_never_writes_the_request_and_audits_once_with_reviewer_and_role(routes):
    rec, _ = _create()
    path = gur._path("Contreras", "AM_Workforce", rec["request_id"])
    before = path.read_bytes()
    url = f"/copilot/governed-updates/{rec['request_id']}"
    owner = _client("owner@danwor.com")
    for _ in range(3):
        assert owner.get(url).status_code == 200
    assert path.read_bytes() == before                                                              # ni la primera visita escribe el registro
    reviewed = [kw for e, kw in routes if e == "governed_update.reviewed"]
    assert len(reviewed) == 1 and reviewed[0]["role"] == "APP_OWNER" and reviewed[0]["request_id"] == rec["request_id"]   # auditoría funcional
    assert (reviewed[0]["user"] or {}).get("sub") == "owner@danwor.com"                             # identidad del revisor (sesión)


def test_a_concurrent_review_cannot_overwrite_an_approval_state(routes, monkeypatch):
    from webapp.services import governed_update_approval as gua
    rec, _ = _create()
    with gur._lock:
        fresh = gur.load(rec["request_id"])
        fresh["approval"] = {"approval_id": "a" * 32, "state": gua.STARTED, "history": [{"state": gua.STARTED}]}
        gur._write(fresh)
    real = gur.should_log_review

    def meanwhile(request_id, user, **kw):                                                          # mientras el GET está en curso, la ejecución termina
        gua._transition(request_id, "a" * 32, gua.APPLIED, outcome="APPLIED")
        return real(request_id, user, **kw)
    monkeypatch.setattr(gur, "should_log_review", meanwhile)
    assert _client("owner@danwor.com").get(f"/copilot/governed-updates/{rec['request_id']}").status_code == 200
    stored = gur.load(rec["request_id"])
    assert stored["approval"]["state"] == gua.APPLIED and stored["status"] == "APPLIED"            # el GET no reescribió una copia vieja


# ── dry-run (solo lectura) ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _preview_world(monkeypatch, *, pending="evidence_source_application_api_v6"):
    from webapp.services import assessment_backfill as ab
    calls = []
    monkeypatch.setattr(gur, "_entries_for", lambda c, a: [SimpleNamespace(entry_id="e1", cliente=c)])
    monkeypatch.setattr(svc, "status_for_entry", lambda e: SimpleNamespace(state=svc.CURRENT, pending_migration=pending, stale_targeted=None))
    plan = {"digest": "d" * 64, "items": [{"client": "Contreras", "root": "AM_Workforce", "judgment_action": "judgment_rederived:ND/LVL-06->BUENA/LVL-04"}]}

    def migrate(entries, migration, *, apply=False, **kw):
        calls.append(("migrate", migration, apply))
        return {"plan": plan, "records": [{"action": "WOULD_REGENERATE"}]}

    def selective(entries, roots, *, client="", **kw):
        calls.append(("selective", tuple(roots), client))
        return {"plan": {"digest": "e" * 64, "items": []}, "records": [{"action": ab.ABSTAINED, "reason": "EXPECTATION_NOT_MET"}]}
    monkeypatch.setattr(ab, "migrate_stale_evidence", migrate)
    monkeypatch.setattr(ab, "plan_selective_regeneration", selective)
    return calls


def test_preview_uses_the_pending_migration_in_preview_mode_and_writes_nothing(world, monkeypatch):
    calls = _preview_world(monkeypatch)
    rec, _ = _create()
    out = gur.compute_preview(rec)
    assert calls == [("migrate", "evidence_source_application_api_v6", False)]     # apply=False: CERO escrituras
    assert out["state"] == gur.PREVIEW_READY and out["plan_digest"] == "d" * 64 and out["zero_writes"] is True
    assert out["judgment"]["to"] == "BUENA" and out["judgment"]["from"] is None and out["path"] == "MIGRATION:evidence_source_application_api_v6"


def test_preview_without_pending_migration_uses_selective_plan_and_reports_abstention(world, monkeypatch):
    calls = _preview_world(monkeypatch, pending=None)
    rec, _ = _create()
    out = gur.compute_preview(rec)
    assert calls and calls[0][0] == "selective" and out["state"] == gur.PREVIEW_ABSTAINED and out["abstentions"] == ["EXPECTATION_NOT_MET"]
    assert out["plan_digest"] is None


def test_preview_of_an_obsolete_request_does_not_run_the_engine(world, monkeypatch):
    calls = _preview_world(monkeypatch)
    rec, _ = _create()
    world["sha"] = "c" * 64
    assert gur.compute_preview(rec)["state"] == gur.PREVIEW_OBSOLETE and calls == []
    assert gur.start_preview(rec, {"sub": "owner@danwor.com"}, "APP_OWNER") == "OBSOLETE"


def test_start_preview_stores_the_result_in_the_request(world, monkeypatch):
    rec, _ = _create()
    done = []
    out = gur.start_preview(rec, {"sub": "owner@danwor.com"}, "APP_OWNER", run=lambda r: {"computed_at": "x", "state": gur.PREVIEW_NO_CHANGE, "zero_writes": True},
                            on_done=lambda fresh, result: done.append(result))
    assert out == "STARTED"
    for _ in range(50):
        if done:
            break
        time.sleep(0.05)
    stored = gur.load(rec["request_id"])
    assert stored["preview"]["state"] == gur.PREVIEW_NO_CHANGE and stored["preview"]["by"] == "owner@danwor.com" and len(stored["previews"]) == 1


# ── rutas ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _client(email):
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture
def routes(world, monkeypatch):
    record = {"result_id": RID, "owner": "consultor@danwor.com", "client_dir": "Contreras", "client_display": "Contreras", "terminal_am": "AM_Workforce",
              "space": "DEV", "created_at": time.time(), "profile": CAND_PROFILE}
    monkeypatch.setattr(dsa, "tool_enabled", lambda stem: stem == STEM)
    monkeypatch.setattr(dsa, "can_execute", lambda request: True)
    monkeypatch.setattr(dsa, "load_result", lambda rid: record if rid == RID else None)
    monkeypatch.setattr(dsa, "result_status", lambda rec: SimpleNamespace(client="Contreras", terminal_am="AM_Workforce", profile=CAND_PROFILE, judgment=CAND_J))
    logged = []
    from webapp.routers import governed_updates as gu
    monkeypatch.setattr(gu, "log_activity", lambda event, **kw: logged.append((event, kw)))
    return logged


def test_request_route_registers_notifies_and_redirects_to_the_review(routes):
    r = _client("consultor@danwor.com").post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/copilot/governed-updates/")
    rec = gur.find_open("Contreras", "AM_Workforce")
    assert rec and rec["requested_by"] == "consultor@danwor.com" and rec["notification"]["dispatch"] == "disabled"
    events = [e for e, _ in routes]
    assert events == ["governed_update.requested", "governed_update.notified"]
    assert routes[0][1]["created"] is True and routes[0][1]["request_id"] == rec["request_id"]


def test_request_route_requires_same_origin_and_the_owners_result(routes):
    c = _client("consultor@danwor.com")
    assert c.post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, follow_redirects=False).status_code == 403
    assert _client("otro@danwor.com").post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, headers=ORIGIN,
                                            follow_redirects=False).status_code == 404
    assert not routes


def test_review_is_visible_to_requester_and_approvers_only_and_offers_no_approval(routes):
    rec, _ = _create()
    url = f"/copilot/governed-updates/{rec['request_id']}"
    assert _client("otro@danwor.com").get(url).status_code == 404
    mine = _client("consultor@danwor.com").get(url)
    assert mine.status_code == 200 and "data-gu-preview-submit" not in mine.text                   # quien solicita no calcula el dry-run
    html = _client("owner@danwor.com").get(url).text
    assert "data-gu-preview-submit" in html and "Calcular dry-run" in html
    assert "data-gu-approve" not in html and "/apply" not in html and "/execute" not in html            # sin origen acreditado no hay «Aprobar»
    assert [e for e, _ in routes] == ["governed_update.reviewed"]                                   # solo la del aprobador cuenta como revisión
    owner = _client("owner@danwor.com")
    assert owner.post(f"{url}/approve", headers=ORIGIN, follow_redirects=False).status_code == 400        # sin confirmación explícita
    r = owner.post(f"{url}/approve", data={"confirm": "1", "idempotency_key": "k1", "spaces": ["DEV"]}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 409 and "No se puede aprobar todavía" in r.text                         # nada aprobable todavía


def test_preview_route_is_for_approvers_only(routes, monkeypatch):
    rec, _ = _create()
    url = f"/copilot/governed-updates/{rec['request_id']}/preview"
    assert _client("consultor@danwor.com").post(url, headers=ORIGIN, follow_redirects=False).status_code == 403
    started = []
    monkeypatch.setattr(gur, "start_preview", lambda r, u, role, on_done=None, spaces=None: started.append(role) or "STARTED")
    assert _client("owner@danwor.com").post(url, headers=ORIGIN, follow_redirects=False).status_code == 303 and started == ["APP_OWNER"]


# ── revisión final de PR-1 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_concurrent_requests_for_the_same_model_create_exactly_one(world):
    import threading
    out, barrier = [], threading.Barrier(8)

    def go(i):
        barrier.wait()
        out.append(_create(requested_by=f"u{i}@danwor.com"))
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for _r, created in out if created) == 1 and len({r["request_id"] for r, _c in out}) == 1
    assert len(list(config.governed_update_requests_dir().glob("*/*/*.json"))) == 1


def test_requests_survive_a_restart_because_they_live_on_disk(world, monkeypatch):
    rec, _ = _create()
    monkeypatch.setattr(gur, "_previewing", set())                                   # proceso nuevo: sin estado en memoria
    assert gur.load(rec["request_id"])["request_id"] == rec["request_id"]
    assert gur.find_open("Contreras", "AM_Workforce")["request_id"] == rec["request_id"]


def test_mail_failure_keeps_the_request_and_records_the_error(routes, monkeypatch):
    from webapp.services import notifications
    monkeypatch.setattr(notifications, "enabled", lambda: True)
    monkeypatch.setattr(notifications, "emit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("graph down")))
    r = _client("consultor@danwor.com").post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    rec = gur.find_open("Contreras", "AM_Workforce")
    assert rec["notification"]["dispatch"] == "error"
    assert [kw.get("dispatch") for e, kw in routes if e == "governed_update.notified"] == ["error"]
    view = catalog_activity.build([{"event": "governed_update.notified", "ts_utc": "x", "user": "u", "payload": {"dispatch": "error"}}], {})
    assert {k["key"]: k["value"] for k in view["kpis"]}["notificaciones"] == 0                     # error ≠ emitida


def test_preview_cannot_persist_even_if_the_engine_tried(world, monkeypatch):
    from webapp.services import assessment_backfill as ab
    _preview_world(monkeypatch)

    def sneaky(entries, migration, *, apply=False, commit=None, retry_remote=None, **kw):
        commit(object())                                                          # un intento de escribir
    monkeypatch.setattr(ab, "migrate_stale_evidence", sneaky)
    rec, _ = _create()
    with pytest.raises(gur.WriteForbidden):
        gur.compute_preview(rec)


@pytest.mark.parametrize("preview,verified,obsolete,expected", [
    ({"state": "READY", "governed_sha256": "a" * 64}, True, False, "ELIGIBLE"),
    ({"state": "READY", "governed_sha256": "a" * 64}, False, False, "NOT_ELIGIBLE"),     # procedencia no verificada
    ({"state": "ABSTAINED", "governed_sha256": "a" * 64}, True, False, "NOT_ELIGIBLE"),
    (None, True, False, "NOT_ELIGIBLE"),
    ({"state": "READY", "governed_sha256": "a" * 64}, True, True, "NOT_ELIGIBLE"),       # obsoleta
])
def test_eligibility_requires_open_verified_and_a_current_ready_plan(world, preview, verified, obsolete, expected):
    rec, _ = _create()
    rec["preview"], rec["provenance"]["verified"] = preview, verified
    if obsolete:
        world["sha"] = "z" * 64
    assert gur.eligibility(rec)["state"] == expected


def test_review_requires_a_session_and_metrics_require_metrics_access(routes):
    rec, _ = _create()
    anon = TestClient(main.app)
    assert anon.get(f"/copilot/governed-updates/{rec['request_id']}", follow_redirects=False).status_code == 401
    assert anon.post("/copilot/governed-updates/request", data={"result_id": RID, "stem": STEM}, headers=ORIGIN, follow_redirects=False).status_code == 401
    r = _client("consultor@danwor.com").get("/admin/activity", follow_redirects=False)
    assert r.status_code in (302, 303, 307, 401, 403, 404) and "Catálogos y Assessments" not in r.text


def test_review_separates_authorization_from_provenance_dry_run_and_application(routes):
    """El APP_OWNER ve que ESTÁ autorizado; la procedencia histórica, el origen autorizado, el dry-run y la aplicación son etapas aparte. No aparece
    «No elegible» como titular ni ninguna acción de aprobar/aplicar."""
    rec, _ = _create()
    html = _client("owner@danwor.com").get(f"/copilot/governed-updates/{rec['request_id']}").text
    assert 'data-gu-role="APP_OWNER"' in html and "Autorizado · APP_OWNER" in html
    for key in ("authorization", "historical_provenance", "authorized_origin", "dry_run", "application", "notification"):
        assert f'data-gu-stage="{key}"' in html
    assert "Pendiente de declaración" in html and "Todavía no" in html and "Desconocida" in html
    assert "Próximo paso:" in html and "Calcular el dry-run gobernado" in html
    assert "No elegible" not in html and "Aprobar" not in html
    assert 'data-gu-vigente' in html and 'data-gu-candidato' in html and "Evaluación ad hoc" in html   # comparación siempre visible
    reader = _client("consultor@danwor.com").get(f"/copilot/governed-updates/{rec['request_id']}").text
    assert "Solo lectura" in reader and "data-gu-preview-submit" not in reader


@pytest.mark.parametrize("dispatch,delivery,label,terminal", [
    ("queued", {"state": "accepted"}, "Aceptada por el servidor de correo", True),
    ("queued", {"state": "sending"}, "En envío", False),
    ("queued", {"state": "uncertain"}, "Incierta", True),
    ("disabled", None, "Notificaciones apagadas", True),
    ("error", None, "No se pudo emitir", True),
])
def test_notification_state_is_readable_and_never_claims_delivery(dispatch, delivery, label, terminal):
    from webapp.routers import governed_updates as gu
    v = gu._notification_view({"notification": {"dispatch": dispatch}}, delivery)
    assert v["label"] == label and v["terminal"] is terminal
    assert "entregad" not in (v["label"] + v["detail"]).lower()                            # aceptada por Graph ≠ entrega confirmada


def test_status_refresh_only_reads_and_stops_at_a_terminal_state(routes, monkeypatch):
    from webapp.services import notifications
    rec, _ = _create()
    gur.record_notification(rec, "queued")
    calls = []
    monkeypatch.setattr(notifications, "emit", lambda *a, **k: calls.append("emit"))
    monkeypatch.setattr(notifications, "delivery_status", lambda event, key: {"state": "sending"})
    url = f"/copilot/governed-updates/{rec['request_id']}/preview"
    owner = _client("owner@danwor.com")
    assert 'hx-trigger="every 4s"' in owner.get(url).text                                 # mientras está en envío, se refresca
    monkeypatch.setattr(notifications, "delivery_status", lambda event, key: {"state": "accepted"})
    html = owner.get(url).text
    assert 'hx-trigger' not in html and "Aceptada por el servidor de correo" in html       # terminal: deja de refrescar
    assert calls == []                                                                     # el refresco jamás reenvía


# ── ventana de restauración tras un redeploy ────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def restoring(monkeypatch):
    """Persistencia remota habilitada y una restauración de arranque cuyo estado controla el test (sin Spaces real)."""
    from core import remote_store
    state = {"reason": "restore en curso", "waited": []}
    monkeypatch.setattr(remote_store, "enabled", lambda: True)
    monkeypatch.setattr(remote_store, "await_restore", lambda timeout: state["waited"].append(timeout) or "in_progress")
    monkeypatch.setattr(remote_store, "sync_blocked_reason", lambda: state["reason"])
    monkeypatch.setattr(remote_store, "write_through", lambda path, data, **kw: (Path(path).write_text(data, encoding="utf-8"), "local")[1])
    return state


from pathlib import Path  # noqa: E402


@pytest.mark.parametrize("reason", ["restore en curso", "el restore falló", "el restore quedó incompleto (fallos de listado o descarga)"])
def test_no_read_or_create_on_an_incomplete_registry(world, restoring, reason):
    restoring["reason"] = reason
    with pytest.raises(gur.RegistryUnavailable):
        _create()
    with pytest.raises(gur.RegistryUnavailable):
        gur.load("a" * 32)
    with pytest.raises(gur.RegistryUnavailable):
        gur.find_open("Contreras", "AM_Workforce")
    assert restoring["waited"] and restoring["waited"][0] == gur.RESTORE_WAIT_S          # reutiliza await_restore con espera acotada
    assert not list(config.governed_update_requests_dir().glob("*/*/*.json"))


def test_an_existing_request_restored_late_is_not_duplicated(world, restoring):
    restoring["reason"] = None                                                          # registro completo: se crea la primera
    first, created = _create()
    assert created
    restoring["reason"] = "restore en curso"                                            # redeploy: el registro vuelve a estar en restauración
    with pytest.raises(gur.RegistryUnavailable):
        _create(requested_by="otro@danwor.com")                                        # NO se crea otra sobre un estado parcial
    restoring["reason"] = None                                                          # restauración terminada: se reutiliza la existente
    again, created_again = _create(requested_by="otro@danwor.com")
    assert not created_again and again["request_id"] == first["request_id"]
    assert len(list(config.governed_update_requests_dir().glob("*/*/*.json"))) == 1


def test_routes_answer_503_retry_after_while_the_registry_is_restoring(routes, restoring):
    restoring["reason"] = None
    rec, _ = _create()
    restoring["reason"] = "restore en curso"
    for method, url in (("get", f"/copilot/governed-updates/{rec['request_id']}"), ("post", "/copilot/governed-updates/request"),
                        ("post", f"/copilot/governed-updates/{rec['request_id']}/preview")):
        kw = {"headers": ORIGIN, "follow_redirects": False}
        if url.endswith("/request"):
            kw["data"] = {"result_id": RID, "stem": STEM}
        r = getattr(_client("owner@danwor.com" if "preview" in url or method == "get" else "consultor@danwor.com"), method)(url, **kw)
        assert r.status_code == 503 and r.headers["Retry-After"] == str(gur.RETRY_AFTER_S) and "Reintentá" in r.text, url
    assert not [e for e, _ in routes if e == "governed_update.requested"]                 # nada registrado mientras restaura


def test_without_remote_persistence_there_is_nothing_to_wait_for(world, monkeypatch):
    from core import remote_store
    monkeypatch.setattr(remote_store, "enabled", lambda: False)
    monkeypatch.setattr(remote_store, "await_restore", lambda timeout: (_ for _ in ()).throw(AssertionError("no debe esperar")))
    rec, created = _create()
    assert created and gur.load(rec["request_id"])["request_id"] == rec["request_id"]
