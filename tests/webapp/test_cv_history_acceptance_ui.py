"""Entrega C — pantalla y rutas de la ACEPTACIÓN CONTROLADA («Revisar para aceptar» y «Aceptar»). Mundo sintético de la ejecución (SharePoint, Graph y Spaces simulados con inyección de fallos). La revisión es de solo lectura; la
confirmación exige el token de escritura, la huella de la revisión y las confirmaciones humanas."""
from __future__ import annotations

import html as htmllib
import re
import threading

import pytest
from fastapi.testclient import TestClient

from tests.webapp.test_cv_history_acceptance import PABLO, digest, draft, ready  # noqa: F401
from tests.webapp.test_cv_history_import_exec import OWNER, TOKEN, banco_profiles, create_in_banco, env, ex, mf, profile_document_repository, profile_repository, seeded_snapshot  # noqa: F401
from tests.webapp.test_cv_history_pilot import BASE, HC_USER, client_for, home, sp  # noqa: F401
from webapp.main import app
from webapp.services import cv_history_acceptance as acc, cv_history_drafts as drafts


def act(client, rid, key, accion, token=TOKEN, **data):
    return client.post(f"{BASE}/{rid}/excepciones/{key}/{accion}", data={"graph_token": token, **data})


def review(client, rid, key):
    return act(client, rid, key, "aceptar-revisar", token="")


def fingerprint_of(html: str) -> str:
    return re.search(r'data-accept-fingerprint="([0-9a-f]{64})"', html).group(1)


def form_of(html: str, kind: str) -> dict:
    """Campos ocultos de un formulario de confirmación del panel (`create`, `link`, `resume`)."""
    block = re.search(rf"<form[^>]*data-accept-{kind}[^>]*>(.*?)</form>", html, flags=re.S).group(1)
    return {m.group(1): htmllib.unescape(m.group(2)) for m in re.finditer(r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', block)}


def acks_of(html: str, kind: str) -> list[str]:
    block = re.search(rf"<form[^>]*data-accept-{kind}[^>]*>(.*?)</form>", html, flags=re.S).group(1)
    return re.findall(r'<input type="checkbox" name="ack" value="([a-z_]+)"', block)


def confirm_create(client, rid, key, panel_html, **over):
    fields = form_of(panel_html, "create")
    return act(client, rid, key, "aceptar", **{**fields, "ack": acks_of(panel_html, "create"), **over})


# ═══════════════════ la revisión es de solo lectura y solo del owner ═══════════════════
def test_review_is_owner_only_no_store_and_changes_nothing(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    url = f"{BASE}/{rid}/excepciones/{pablo}/aceptar-revisar"
    assert TestClient(app, follow_redirects=False).post(url, data={}).status_code in (302, 303, 307)
    assert client_for("fuera-de-cchh@danwor.com", monkeypatch).post(url, data={}).status_code == 403                                      # autenticado pero sin autorización de Capital Humano
    assert client_for(HC_USER, monkeypatch).post(url, data={}).status_code == 200                                                          # CCHH autorizado: puede revisar (solo lectura)
    owner = client_for(OWNER, monkeypatch)
    before, profiles, puts = digest(), seeded_snapshot(), list(store.puts)
    res = review(owner, rid, pablo)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store" and "data-accept-panel" in res.text and not res.text.lstrip().startswith("<html")
    assert digest() == before and seeded_snapshot() == profiles and store.puts == puts and mf.get_ledger(rid, pablo) is None
    assert review(owner, f"ch-20200101T000000Z-aaaa", pablo).status_code == 404 and act(owner, rid, "no-es-clave", "aceptar-revisar").status_code == 404


def test_the_review_panel_shows_the_draft_the_warnings_and_every_match_without_choosing_for_the_owner(env, monkeypatch):
    store, rid, pablo = ready(env)
    create_in_banco("Pablo", "Ruiz", "pablo.ruiz@a.com")
    draft(rid, pablo, email="")
    owner = client_for(OWNER, monkeypatch)
    html = review(owner, rid, pablo).text
    assert "revisión 1" in html and "Las observaciones del borrador no se copian al perfil" in html and "Esto no se puede deshacer desde esta pantalla" in html
    assert 'data-match-strength="PROBABLE"' in html and "mismo nombre y apellido" in html and "data-accept-duplicate" in html and "data-accept-no-email" in html
    assert acks_of(html, "create") == ["irreversible", "no_email", "matches", "duplicate"] and "checked" not in html                                       # ninguna confirmación viene marcada
    assert form_of(html, "create")["op"] == "create" and form_of(html, "link")["op"] == "link" and len(fingerprint_of(html)) == 64
    forms = re.findall(r"<form[^>]*data-accept-(?:create|link|resume)[^>]*>", html)
    assert forms and all("data-hc-write-token" in f and "data-dw-graph-token" not in f and "/aceptar\"" in f for f in forms)                          # confirmar exige el token de escritura


def test_without_a_saved_draft_there_is_nothing_to_review_and_the_row_says_so(env, monkeypatch):
    store, rid, pablo = ready(env)
    owner = client_for(OWNER, monkeypatch)
    assert "Guardá un borrador" in review(owner, rid, pablo).text and "data-accept-create" not in review(owner, rid, pablo).text
    page = owner.get(f"{BASE}/{rid}/excepciones").text
    row = page.split(f'<tr id="exc-{pablo}"')[1].split("</tr>")[0]
    assert "data-accept-needs-draft" in row and "data-accept-review" not in row
    draft(rid, pablo)
    row = owner.get(f"{BASE}/{rid}/excepciones").text.split(f'<tr id="exc-{pablo}"')[1].split("</tr>")[0]
    assert "data-accept-review" in row and ">Revisar para aceptar</button>" in row and "data-accept-needs-draft" not in row


# ═══════════════════ confirmar ═══════════════════
def test_confirming_creates_the_profile_and_the_row_shows_accepted_from_the_ledger(env, monkeypatch):
    store, rid, pablo = ready(env)
    saved = draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    before = len(banco_profiles())
    res = confirm_create(owner, rid, pablo, panel)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store" and 'data-status="resolved"' in res.text and "data-accept-done" in res.text and "se creó el perfil y se subió el CV" in res.text
    assert 'data-accepted-state="done"' in res.text and "Perfil nuevo · borrador rev. 1 ·" in res.text and OWNER in res.text and "Ver perfil" in res.text and len(banco_profiles()) == before + 1
    again = owner.get(f"{BASE}/{rid}/excepciones?estado=resolved").text
    assert 'data-accepted-state="done"' in again and mf.get_draft(rid, pablo) == saved                                                                      # el estado sale del libro; el borrador no se tocó


def test_confirming_requires_the_write_token_and_the_acknowledgements(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    before, puts = digest(), list(store.puts)
    no_token = confirm_create(owner, rid, pablo, panel, graph_token="")
    assert "Sin token con permiso de escritura" in no_token.text and 'data-error-kind="exception_action"' in no_token.text
    missing = confirm_create(owner, rid, pablo, panel, ack=["irreversible"])
    assert 'data-error-kind="exception_action"' in missing.text and "Faltan confirmaciones" in missing.text and "data-accept-panel" in missing.text      # vuelve con la revisión para decidir de nuevo
    assert digest() == before and store.puts == puts and mf.get_ledger(rid, pablo) is None


def test_a_stale_confirmation_writes_nothing_and_comes_back_with_the_current_review(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    create_in_banco("Pablo", "Ruiz", "pablo.ruiz@a.com")                                                                                                        # cambia el Banco entre la revisión y la confirmación
    before, puts = digest(), list(store.puts)
    res = confirm_create(owner, rid, pablo, panel)
    assert "La información cambió desde la revisión" in res.text and "data-accept-panel" in res.text and fingerprint_of(res.text) != fingerprint_of(panel) and "data-accept-duplicate" in res.text
    assert digest() == before and store.puts == puts and mf.get_ledger(rid, pablo) is None
    fresh = res.text
    assert "duplicate" in acks_of(fresh, "create")
    ok = confirm_create(owner, rid, pablo, fresh)
    assert 'data-status="resolved"' in ok.text and "data-accept-done" in ok.text


def test_double_submit_is_idempotent_and_a_different_decision_is_a_visible_conflict(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    first = confirm_create(owner, rid, pablo, panel)
    assert "data-accept-done" in first.text
    puts, profiles = list(store.puts), seeded_snapshot()
    second = confirm_create(owner, rid, pablo, panel)                                                                                                            # doble clic / reenvío
    assert "ya estaba aceptado con esa misma decisión" in second.text and store.puts == puts and seeded_snapshot() == profiles
    zoe = next(p for p in profile_repository.list() if p["first_name"] == "Zoe")
    other = act(owner, rid, pablo, "aceptar", op="link", profile_id=zoe["profile_id"], target_revision=str(zoe["revision"]), revision="1", fingerprint="x", ack=["irreversible", "weak_link"])
    assert "data-accept-conflict" in other.text and "ya fue aceptado con otra decisión" in other.text and "ya estaba aceptado" not in other.text and store.puts == puts


def test_link_through_the_screen_adds_the_cv_without_touching_the_profile(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo, first_name="Pablito", email="otro@x.com")
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    target = next(p for p in profile_repository.list() if p["first_name"] == "Pablo" and not p.get("import_origin"))
    block = re.search(r'<form[^>]*data-accept-link[^>]*>(.*?)</form>', panel, flags=re.S).group(1)
    fields = {m.group(1): htmllib.unescape(m.group(2)) for m in re.finditer(r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', block)}
    assert fields["profile_id"] == target["profile_id"] and fields["target_revision"] == str(target["revision"])
    snapshot = seeded_snapshot()
    res = act(owner, rid, pablo, "aceptar", **fields, ack=re.findall(r'name="ack" value="([a-z_]+)"', block))
    assert "data-accept-done" in res.text and "el CV quedó vinculado al perfil, sin modificarlo" in res.text and "CV vinculado a un perfil existente" in res.text and seeded_snapshot() == snapshot
    assert [d["filename"] for d in profile_document_repository.list_for_profile(target["profile_id"])]


def test_a_partial_failure_is_visible_as_in_progress_and_the_same_decision_can_be_completed(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    store.deny_put = True
    stopped = confirm_create(owner, rid, pablo, panel)
    assert "Aceptación registrada, pero se detuvo (PERMISSION_DENIED)" in stopped.text and 'data-accepted-state="pending"' in stopped.text and "Aceptación en curso" in stopped.text and 'data-status="in_progress"' in stopped.text
    store.deny_put = False
    resume_panel = review(owner, rid, pablo).text
    assert "data-accept-pending" in resume_panel and "data-accept-resume" in resume_panel and "data-accept-create" not in resume_panel and "data-accept-link" not in resume_panel      # solo se puede completar ESA decisión
    done = act(owner, rid, pablo, "aceptar", **form_of(resume_panel, "resume"))
    assert "se creó el perfil y se subió el CV" in done.text and 'data-accepted-state="done"' in done.text and len([p for p in profile_repository.list() if (p.get("import_origin") or {}).get("content_key") == pablo]) == 1


def test_a_busy_content_asks_to_retry_and_writes_nothing(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    panel = review(owner, rid, pablo).text
    held, release = threading.Event(), threading.Event()

    def holder():
        with mf.ledger_lock(rid, pablo):
            held.set()
            release.wait(30)
    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(30)
    monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 0.3)
    res = confirm_create(owner, rid, pablo, panel)
    assert "Otro proceso está trabajando con este contenido" in res.text and mf.get_ledger(rid, pablo) is None
    release.set()
    thread.join(30)


def test_the_existing_forms_filters_and_original_cv_keep_working_next_to_the_acceptance(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    html = owner.get(f"{BASE}/{rid}/excepciones").text
    row = html.split(f'<tr id="exc-{pablo}"')[1].split("</tr>")[0]
    for marker in ("data-exc-draft", "data-exc-accept", "data-exc-complete", "data-exc-link", "data-exc-dismiss", "data-original-cv-form"):
        assert marker in row, marker
    fragment = owner.get(f"{BASE}/{rid}/excepciones?motivo=MULTIPLE_EMAILS", headers={"HX-Request": "true"})
    assert fragment.text.lstrip().startswith('<div id="cvexc-panel">') and "data-exceptions-filters" in fragment.text
    completar = re.search(r"<form[^>]*data-exc-complete[^>]*>(.*?)</form>", row, flags=re.S).group(1)
    assert re.search(r'name="first_name"[^>]*value=""', completar)                                                                                               # «Completar identidad» no hereda nada del borrador ni de la aceptación


def test_the_acceptance_markup_is_accessible_and_escapes_untrusted_text(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo, first_name="<script>alert(1)</script>", notes="x")
    owner = client_for(OWNER, monkeypatch)
    html = review(owner, rid, pablo).text
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    row = owner.get(f"{BASE}/{rid}/excepciones").text.split(f'<tr id="exc-{pablo}"')[1].split("</tr>")[0]
    assert f'<div id="acc-{pablo}" role="region" aria-live="polite" aria-label="Revisión para aceptar">' in row and f'aria-describedby="acc-{pablo}"' in row
    assert re.findall(r'<label class="hc-exc-ack"><input type="checkbox" name="ack" value="[a-z_]+"> <span>[^<]+</span></label>', html)                       # cada casilla es una etiqueta con texto


def test_the_acceptance_logs_carry_no_personal_data(env, monkeypatch, caplog):
    import logging
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    owner = client_for(OWNER, monkeypatch)
    caplog.set_level(logging.DEBUG)
    panel = review(owner, rid, pablo).text
    confirm_create(owner, rid, pablo, panel)
    confirm_create(owner, rid, pablo, panel)
    text = caplog.text
    assert "resultado=OK:DONE" in text and "resultado=IDEMPOTENT" in text
    assert not any(secret in text for secret in ("Pablo", "Ruiz", "pablo.ruiz@a.com", "observación", TOKEN))
