"""
test_feedback_auth_hardening.py — REACTIONS AUTH HARDENING (owner AUDIT
"REACTIONS — TRACE MISSING USER IDENTITY" + ticket "REACTIONS — FINAL AUTH
HARDENING").

The 4 Copiloto feedback families (módulo, sección, catálogo, sección de
catálogo — `webapp/routers/feedback.py`) used to silently coerce
`auth_service.session_user(request) or {}` into `{}` and persist anyway.
That's exactly what let a request without a valid session (expired,
missing, or blocked by a third-party-cookie restriction) write a feedback
record AND emit an Activity event with an empty identity — the root cause
traced in the AUDIT. A valid Hub reaction now requires an authenticated
session, enforced BEFORE any persistence (never inside the fail-soft
try/except blocks, which would otherwise swallow the rejection and render a
false "Gracias").

Also covers a second, distinct defect found WHILE hardening this file: the
FEEDBACK_LOG YAML `user` field used `user.get("username") or
user.get("email")` — but `auth_service.session_user` (via
`create_session`/`decode_session`) NEVER populates either key, only `sub`
(see auth_service.py:224-232). That meant even a fully authenticated,
valid-session submission persisted `user=""` in the YAML — independent of
this ticket's 401 gate. Fixed by adding the missing `.get("sub")` fallback,
the SAME 3-key chain already used everywhere else in this codebase
(reporting.py/staffing.py/commercial.py/academy.py).

MISMO patrón de test que test_feedback_activity_reactions.py
(`monkeypatch.setattr(feedback_router, "log_activity", ...)`) — determinístico,
sin depender de que la aggregación de activity_analytics vuelva a recorrer el
evento.
"""
from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import feedback as feedback_router
from webapp.services import auth_service

anon_client = TestClient(app)  # SIN cookie de sesión -- a propósito.

auth_client = TestClient(app)
auth_client.cookies.set(
    auth_service.COOKIE_NAME,
    auth_service.create_session({
        "email": "hardening@danwor.com", "name": "Hardening User",
        "first_name": "Hardening", "family_name": "User",
    }),
)

# (label, post_url, form_data, feedback_glob, event_name, area) por familia --
# MISMOS 4 endpoints "up" nombrados por el ticket, uno por familia.
_FAMILIES = [
    (
        "module",
        "/modules/01_Arquitectura_y_Conectividad/feedback",
        {"rating": "up", "pack": "datasphere_core"},
        "module_feedback_*.yaml",
        "module.feedback",
        "methodology",
    ),
    (
        "section",
        "/modules/01_Arquitectura_y_Conectividad/sections/espacios-transportes/feedback",
        {"rating": "up", "pack": "datasphere_core"},
        "section_feedback_*.yaml",
        "section.feedback",
        "methodology",
    ),
    (
        "catalog",
        "/catalog/feedback",
        {"rating": "up", "catalog_kind": "dominio", "catalog_ref": "ventas"},
        "catalog_feedback_*.yaml",
        "catalog.feedback",
        "catalog",
    ),
    (
        "catalog_section",
        "/catalog/section-feedback",
        {"rating": "up", "cliente": "AySA", "dominio": "Ventas", "slug": "resumen"},
        "catalog_section_*.yaml",
        "catalog_section.feedback",
        "catalog",
    ),
]
_FAMILY_IDS = [f[0] for f in _FAMILIES]


# ── A/B/C: comportamiento AUTENTICADO (sin cambios de fondo, solo ahora
# realmente persiste identidad -- ver el segundo hallazgo en el docstring) ──

@pytest.mark.parametrize("label,url,data,glob,event,area", _FAMILIES, ids=_FAMILY_IDS)
def test_authenticated_request_succeeds_and_persists_identity(monkeypatch, tmp_path, label, url, data, glob, event, area):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda ev, **kw: events.append((ev, kw)))

    # A: request autenticado -- éxito (200, "Gracias").
    r = auth_client.post(url, data=data)
    assert r.status_code == 200
    assert "Gracias" in r.text or "gracias" in r.text.lower()

    # B: el YAML persistido tiene usuario NO vacío -- el segundo hallazgo
    # (falta ".get(\"sub\")") corregido, no solo el gate de 401.
    saved = list(tmp_path.glob(glob))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["user"] == "hardening@danwor.com"

    # C: el evento de Activity emitido lleva el MISMO usuario autenticado
    # (el `user` crudo que log_activity recibe -- de ahí deriva "sub").
    assert events and events[0][0] == event
    kw = events[0][1]
    assert kw["scope"] == "copilot" and kw["area"] == area
    assert kw["user"].get("sub") == "hardening@danwor.com"
    assert kw["feedback_sentiment"] == "positive"


# ── D/E/F: comportamiento NO AUTENTICADO -- rechazado, sin persistir nada ──

@pytest.mark.parametrize("label,url,data,glob,event,area", _FAMILIES, ids=_FAMILY_IDS)
def test_unauthenticated_request_is_rejected_and_persists_nothing(monkeypatch, tmp_path, label, url, data, glob, event, area):
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    events = []
    monkeypatch.setattr(feedback_router, "log_activity", lambda ev, **kw: events.append((ev, kw)))

    r = anon_client.post(url, data=data)

    # D: rechazado -- MISMO código/mensaje que las páginas GET equivalentes
    # ya usan (webapp/main.py, webapp/routers/modules.py).
    assert r.status_code == 401
    assert "Sin sesión" in r.text

    # E: ningún registro de feedback se creó.
    assert list(tmp_path.glob(glob)) == []

    # F: ningún evento de Activity se emitió.
    assert events == []


# ── G (SUPERSEDED — owner ticket "REACTIONS — EXCLUDE LEGACY UNATTRIBUTED
# FEEDBACK"): las reacciones históricas sin usuario (backfilleadas, no
# recuperables — ver el AUDIT) ya NO se muestran como "Sin usuario
# identificado" -- Metrics las excluye de Reacciones por completo (owner
# §1). Este test sigue probando lo que realmente le importa a ESTE
# archivo (auth hardening): el gate solo afecta ESCRITURAS nuevas, nunca
# reescribe/oculta el evento histórico persistido -- el evento SIGUE ahí
# (línea intacta en el .jsonl), simplemente la agregación de Reacciones ya
# no lo cuenta (ver test_activity_routes.py para la cobertura A-G completa
# de esa exclusión). ───────────────────────────────────────────────────

def test_historical_unattributed_reaction_is_untouched_but_excluded_from_reacciones(monkeypatch, tmp_path):
    import json
    from webapp.services import activity_analytics
    from tests.webapp.test_activity_routes import _owner_client, _hx

    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback_dir = tmp_path / "_no_feedback_yaml"
    empty_feedback_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback_dir)
    # MISMA forma que backfill_copilot_reactions.py produce para un registro
    # SIN usuario -- el caso real reportado por el owner (catálogo=1).
    activity_path = tmp_path / "activity_20260701.jsonl"
    historical = [
        {"ts_utc": "2026-07-01T10:00:00+00:00", "event": "catalog.feedback", "user": "", "name": "",
         "scope": "copilot", "area": "catalog",
         "payload": {"feedback_sentiment": "positive", "backfill_marker": "abc", "backfill_source_type": "catalog"}},
    ]
    activity_path.write_text("\n".join(json.dumps(r) for r in historical) + "\n", encoding="utf-8")
    before = activity_path.read_text(encoding="utf-8")

    t = _hx(_owner_client(monkeypatch), "/admin/activity").text

    # El evento de Activity Log NUNCA se tocó -- este ticket es agregación
    # de lectura solamente, jamás una reescritura de origen.
    assert activity_path.read_text(encoding="utf-8") == before
    # Pero Reacciones ya no lo cuenta -- sin usuario, sin área "catalog" en
    # el panel (era su ÚNICA reacción).
    assert 'data-event="catalog"' not in t
    assert "Sin usuario identificado" not in t


# ── H: sin regresión en la agregación existente -- una reacción AUTENTICADA
# real (vía POST, sin mockear log_activity esta vez) sigue apareciendo tal
# cual en "Reacciones" -- total de área + fila por usuario con su email. ──

def test_authenticated_reaction_still_aggregates_correctly_in_reacciones(monkeypatch, tmp_path):
    from webapp.services import activity_analytics, activity_service
    from tests.webapp.test_activity_routes import _owner_client, _hx, _panel_html

    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path / "_feedback_yaml")
    # log_activity real (SIN mockear) -- prueba el pipeline end-to-end,
    # router -> sink -> agregación, no solo la llamada. `_isolate_runtime_logs`
    # (conftest.py, autouse) apaga LOG_ACTIVITY y redirige su dir por defecto
    # -- este test gana sobre eso a propósito, MISMO criterio documentado ahí.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path, raising=False)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", tmp_path / "_no_feedback_yaml")
    (tmp_path / "_no_feedback_yaml").mkdir(exist_ok=True)

    r = auth_client.post("/catalog/feedback", data={"rating": "up", "catalog_kind": "dominio", "catalog_ref": "ventas"})
    assert r.status_code == 200

    t = _hx(_owner_client(monkeypatch), "/admin/activity").text
    assert 'title="Catálogo: 1 👍 · 0 👎 de 1"' in t
    catalog_panel = _panel_html(t, "catalog")
    assert 'title="hardening@danwor.com"' in catalog_panel
