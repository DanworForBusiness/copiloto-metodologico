"""Circuito PROGRESIVO de la actualización gobernada: fases derivadas del estado REAL del servidor, acciones por fase, refresco único (Seguimiento +
acciones + próximo paso + encabezado), plan REVISADO exigido en la sección crítica de ``register`` y exclusión mutua recálculo ↔ aprobación.
Las transiciones C1–C6 no cambian; los GET siguen siendo de solo lectura."""
from __future__ import annotations

import pytest

from webapp.services import governed_update_approval as gua
from webapp.services import governed_update_requests as gur
from tests.webapp.test_governed_update_approval import OWNER, READY, _ready_request
from tests.webapp.test_governed_update_requests import ORIGIN, _client, _create, routes, world  # noqa: F401  (fixtures compartidas)


@pytest.fixture
def spaces(monkeypatch):
    from webapp.routers import governed_updates as gu_router
    monkeypatch.setattr(gu_router, "_known_spaces", lambda rec: ["DEV", "PRD"])


def _page(rid):
    return _client("owner@danwor.com").get(f"/copilot/governed-updates/{rid}").text


def _bytes(rid):
    return gur._path("Contreras", "AM_Workforce", rid).read_bytes()


def _set(rid, **fields):
    with gur._lock:
        fresh = gur.load(rid)
        fresh.update(fields)
        gur._write(fresh)
    return gur.load(rid)


# ── fases ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_declare_phase_offers_space_selection_and_dry_run_but_never_approval(routes, spaces):
    rec, _ = _create()
    html = _page(rec["request_id"])
    assert 'data-gu-phase="DECLARE"' in html and "data-gu-origin-form" in html and "data-gu-origin-submit data-gu-once>Calcular dry-run<" in html
    assert "data-gu-approve" not in html and "gu-approve-dialog" not in html and "data-gu-recalc" not in html


def test_computing_phase_shows_real_activity_without_forms_or_approval(routes, spaces):
    rec = _ready_request(routes)
    gur._previewing.add(rec["request_id"])
    try:
        html = _page(rec["request_id"])
    finally:
        gur._previewing.discard(rec["request_id"])
    assert 'data-gu-phase="COMPUTING"' in html and 'data-gu-busy="dry_run"' in html and "section-analysis-spinner" in html
    assert "data-gu-origin-form" not in html and "data-gu-approve" not in html and 'hx-trigger="every 4s"' in html
    assert "%" not in html.split('data-gu-busy="dry_run"')[1].split("</span>")[1]                                    # sin progresos inventados


def test_ready_phase_hides_the_form_promotes_approval_and_offers_a_secondary_recalculation(routes, spaces):
    rec = _ready_request(routes)
    html = _page(rec["request_id"])
    assert 'data-gu-phase="READY"' in html and "data-gu-approve>Aprobar y actualizar<" in html
    assert "data-gu-origin-submit data-gu-once>Calcular dry-run<" not in html                                       # el botón principal desaparece
    recalc = html.split("data-gu-recalc")[1].split("</details>")[0]
    assert '<summary class="btn-secondary gu-btn-compact">Recalcular</summary>' in recalc and "Recalcular dry-run" in recalc
    assert 'value="DEV" checked' in recalc and 'value="PRD">' in recalc                                             # precarga el último Space declarado
    assert f'name="plan_digest" value="{READY["plan_digest"]}"' in html                                              # el diálogo envía el plan mostrado


@pytest.mark.parametrize("state,label", [("REGISTERED", "Aprobación registrada"), ("VERIFYING", "Verificando"), ("STARTED", "Aplicando")])
def test_applying_phase_shows_the_real_backend_stage_and_disables_everything_else(routes, spaces, state, label):
    rec = _ready_request(routes)
    _set(rec["request_id"], approval={"approval_id": "a" * 32, "state": state, "history": [{"state": state}]})
    html = _page(rec["request_id"])
    assert 'data-gu-phase="APPLYING"' in html and 'data-gu-busy="application"' in html and f"{label} en el servidor" in html
    assert "data-gu-origin-form" not in html and "data-gu-approve" not in html and "data-gu-recalc" not in html
    assert f'data-gu-application-state="{state}">Aplicación · {label}<' in html


@pytest.mark.parametrize("state", ["RECONCILIATION_REQUIRED", "INTERRUPTED"])
def test_blocked_phase_offers_no_action(routes, spaces, state):
    rec = _ready_request(routes)
    _set(rec["request_id"], approval={"approval_id": "a" * 32, "state": state, "history": [{"state": state}]})
    html = _page(rec["request_id"])
    assert 'data-gu-phase="BLOCKED"' in html and "data-gu-origin-form" not in html and "data-gu-approve" not in html


@pytest.mark.parametrize("state", ["DENIED", "ABSTAINED", "FAILED"])
def test_ended_with_cause_shows_the_cause_and_only_allowed_actions(routes, spaces, state):
    rec = _ready_request(routes)
    _set(rec["request_id"], preview={**READY, "state": gur.PREVIEW_ABSTAINED, "abstentions": ["EXPECTATION_NOT_MET"], "plan_digest": None},
         approval={"approval_id": "a" * 32, "state": state, "reason": "PLAN_CHANGED", "history": [{"state": state}]})
    html = _page(rec["request_id"])
    assert 'data-gu-phase="ENDED"' in html and "Causa:" in html and "data-gu-origin-form" in html and "data-gu-approve" not in html


def test_applied_phase_shows_only_result_and_traceability(routes, spaces):
    rec = _ready_request(routes)
    _set(rec["request_id"], status="APPLIED", approval={"approval_id": "a" * 32, "state": "APPLIED", "outcome": "APPLIED", "history": [{"state": "APPLIED"}]})
    html = _page(rec["request_id"])
    assert 'data-gu-phase="APPLIED"' in html and "data-gu-origin-form" not in html and "data-gu-recalc" not in html and "data-gu-approve" not in html
    assert "data-gu-preview-submit" not in html and 'data-gu-state="APPLIED">Solicitud cerrada<' in html and "Aplicación · Aplicada" in html


# ── sincronización: un único refresco trae Seguimiento, acciones, próximo paso y encabezado ───────────────────────────────────────────────────
def test_the_poll_fragment_also_carries_the_header_state_outside_the_status_block(routes, spaces):
    rec = _ready_request(routes)
    _set(rec["request_id"], approval={"approval_id": "a" * 32, "state": "VERIFYING", "history": [{"state": "VERIFYING"}]})
    page = _page(rec["request_id"])
    assert page.count('id="gu-header-state"') == 1 and 'hx-select-oob="#gu-header-state"' in page                   # sin ids duplicados en la página
    assert "data-gu-poll-error hidden" in page and "hx-on::response-error" in page                                  # fallo VISUAL, no de ejecución
    _set(rec["request_id"], status="APPLIED", approval={"approval_id": "a" * 32, "state": "APPLIED", "history": [{"state": "APPLIED"}]})
    frag = _client("owner@danwor.com").get(f"/copilot/governed-updates/{rec['request_id']}/preview").text
    status_block, header = frag.split('<span id="gu-header-state"')
    assert 'data-gu-phase="APPLIED"' in status_block and "Próximo paso" in status_block
    assert 'data-gu-application-state="APPLIED">Aplicación · Aplicada<' in header                                  # el encabezado nunca queda en «Verificando»


def test_gets_and_reloads_never_write_or_start_anything(routes, spaces, monkeypatch):
    rec = _ready_request(routes)
    _set(rec["request_id"], approval={"approval_id": "a" * 32, "state": "VERIFYING", "history": [{"state": "VERIFYING"}]})
    before = _bytes(rec["request_id"])
    monkeypatch.setattr(gur, "start_preview", lambda *a, **k: pytest.fail("un GET no lanza dry-runs"))
    monkeypatch.setattr(gua, "start", lambda *a, **k: pytest.fail("un GET no ejecuta"))
    c = _client("owner@danwor.com")
    for _ in range(3):
        assert c.get(f"/copilot/governed-updates/{rec['request_id']}").status_code == 200
        assert c.get(f"/copilot/governed-updates/{rec['request_id']}/preview").status_code == 200
    assert _bytes(rec["request_id"]) == before


# ── plan REVISADO y exclusión mutua recálculo ↔ aprobación (sección crítica) ───────────────────────────────────────────────────────────────────
def test_register_requires_the_reviewed_plan_to_be_the_current_one(world):
    rec = _ready_request(world)
    with pytest.raises(gua.ApprovalRefused, match="REVIEWED_PLAN_MISSING"):
        gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest="")
    with pytest.raises(gua.ApprovalRefused, match="PLAN_CHANGED_SINCE_REVIEW"):
        gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest="x" * 64)           # pestaña obsoleta
    appr, created = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest=READY["plan_digest"])
    assert created and appr["expected"]["plan_digest"] == READY["plan_digest"]


def test_a_legitimate_retry_with_the_same_key_returns_the_same_approval_even_if_the_plan_changed_since(world):
    rec = _ready_request(world)
    appr, created = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest=READY["plan_digest"])
    _set(rec["request_id"], preview={**READY, "plan_digest": "n" * 64})
    again, created2 = gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest=READY["plan_digest"])
    assert created and not created2 and again["approval_id"] == appr["approval_id"]                                  # idempotencia: nada nuevo


def test_no_approval_while_a_recalculation_is_running(world):
    rec = _ready_request(world)
    gur._previewing.add(rec["request_id"])
    try:
        with pytest.raises(gua.ApprovalRefused, match="PREVIEW_RUNNING"):
            gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest=READY["plan_digest"])
    finally:
        gur._previewing.discard(rec["request_id"])
    assert (gur.load(rec["request_id"]) or {}).get("approval") is None


def test_no_recalculation_while_an_approval_is_in_progress(routes, spaces):
    rec = _ready_request(routes)
    _set(rec["request_id"], approval={"approval_id": "a" * 32, "state": "REGISTERED", "history": [{"state": "REGISTERED"}]})
    assert gur.start_preview(gur.load(rec["request_id"]), OWNER, "APP_OWNER", run=lambda r, **k: pytest.fail("no recalcula")) == "APPROVAL_IN_PROGRESS"
    r = _client("owner@danwor.com").post(f"/copilot/governed-updates/{rec['request_id']}/preview", data={"spaces": ["DEV"]}, headers=ORIGIN,
                                         follow_redirects=False)
    assert r.status_code == 409


def test_concurrent_recalculation_and_approval_are_mutually_exclusive(world):
    import threading
    rec = _ready_request(world)
    gate, out = threading.Event(), {}

    def slow(r, **k):
        gate.wait(2)
        return dict(READY)
    assert gur.start_preview(rec, OWNER, "APP_OWNER", run=slow, spaces=["DEV"]) == "STARTED"                         # recálculo en curso
    try:
        gua.register(rec, OWNER, "APP_OWNER", ["DEV"], idempotency_key="k", reviewed_plan_digest=READY["plan_digest"])
    except gua.ApprovalRefused as exc:
        out["refused"] = exc.code
    gate.set()
    assert out["refused"] == "PREVIEW_RUNNING" and (gur.load(rec["request_id"]) or {}).get("approval") is None


def test_stale_tab_and_double_click_through_the_route(routes, spaces, monkeypatch):
    rec = _ready_request(routes)
    started = []
    monkeypatch.setattr(gua, "start", lambda r, a, on_event=None: started.append(a["approval_id"]) or "STARTED_THREAD")
    url, owner = f"/copilot/governed-updates/{rec['request_id']}/approve", _client("owner@danwor.com")
    base = {"confirm": "1", "spaces": ["DEV"]}
    assert owner.post(url, data={**base, "idempotency_key": "s"}, headers=ORIGIN, follow_redirects=False).status_code == 409       # sin digest
    assert owner.post(url, data={**base, "idempotency_key": "s", "plan_digest": "x" * 64}, headers=ORIGIN, follow_redirects=False).status_code == 409   # pestaña obsoleta
    ok = {**base, "idempotency_key": "k", "plan_digest": READY["plan_digest"]}
    assert owner.post(url, data=ok, headers=ORIGIN, follow_redirects=False).status_code == 303
    assert owner.post(url, data=ok, headers=ORIGIN, follow_redirects=False).status_code == 303                       # doble clic: misma aprobación
    assert len(started) == 1


def test_double_submit_of_the_dry_run_starts_a_single_computation(routes, spaces, monkeypatch):
    rec, _ = _create()
    calls = []
    real = gur.start_preview

    def counting(r, user, role, **kw):
        out = real(r, user, role, run=lambda rr, **k: (calls.append(1), __import__("time").sleep(0.3), {"state": gur.PREVIEW_NO_CHANGE})[2], **kw)
        return out
    monkeypatch.setattr(gur, "start_preview", counting)
    url, owner = f"/copilot/governed-updates/{rec['request_id']}/preview", _client("owner@danwor.com")
    for _ in range(2):
        assert owner.post(url, data={"spaces": ["DEV"]}, headers=ORIGIN, follow_redirects=False).status_code == 303
    __import__("time").sleep(0.5)
    assert len(calls) == 1                                                                                            # ALREADY_RUNNING en el segundo
