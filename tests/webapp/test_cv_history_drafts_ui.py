"""«Históricos sin validación» — Entrega B (borradores editables y reanudables): rutas y pantalla. Lotes SINTÉTICOS (motor real de `build_plan`), Spaces simulado y Graph/SharePoint prohibidos: guardar un borrador no puede tocar nada
fuera de `drafts/`. Criterio de aceptación: abrir un CV histórico, completar o rectificar parcialmente sus datos, guardar, salir y volver a encontrar exactamente lo guardado, sin afectar el Banco operativo."""
from __future__ import annotations

import hashlib
import html as htmllib
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365, remote_store
from tests.test_cv_history_recoverability import RUN, World, _corroborated, _ext
from tests.webapp.test_cv_history_pilot import BASE, HC_USER, OWNER, client_for
from webapp.main import app
from webapp.services import cv_history_drafts as drafts, cv_history_import_exec as ex, cv_history_manifest as mf, profile_repository

WRITE_TOKEN = "tok-escritura-sintetico-456"
SECRETS = ("Zulema", "Draftona", "zulema.draftona@sintetico.test", "5555-9999", "observacion secreta")
DRAFT = {"first_name": "Zulema", "last_name": "Draftona", "email": "zulema.draftona@sintetico.test", "phone": "+54 9 11 5555-9999", "notes": "observacion secreta\nsegunda línea"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", False)


def digest(skip="/drafts/") -> dict:
    root = Path(config.BASE_DIR)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file() and skip not in p.as_posix()}


def two_docs() -> list[str]:
    w = World()
    w.profile("Valeria", "Ibarra", email="ibarra.v@sintetico.test")
    keys = [w.doc(_ext(emails=[f"cv{i}@sintetico.test", f"alt{i}@sintetico.test"], phone=f"54912000000{i}", alts=[(f"Persona{chr(97 + i)} Apellido{chr(97 + i)}", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"])) for i in range(2)]
    w.build()
    return keys


def post_draft(client, key, *, token=WRITE_TOKEN, revision="0", **fields):
    return client.post(f"{BASE}/{RUN}/excepciones/{key}/borrador", data={"graph_token": token, "revision": revision, **{**DRAFT, **fields}})


def row_of(html: str, key: str) -> str:
    chunk = next(c for c in html.split('<tr id="exc-')[1:] if c.startswith(key))
    return chunk.split("</tr>", 1)[0]


def form_values(html: str, key: str) -> dict:
    form = re.search(r"<form[^>]*data-exc-draft[^>]*>(.*?)</form>", row_of(html, key) if "<tr id=" in html else html, flags=re.S).group(1)
    out = {n: htmllib.unescape(re.search(rf'<input name="{n}"[^>]*value="([^"]*)"', form).group(1)) for n in ("first_name", "last_name", "email", "phone")}
    out["notes"] = htmllib.unescape(re.search(r'<textarea name="notes"[^>]*>(.*?)</textarea>', form, flags=re.S).group(1))
    out["revision"] = re.search(r'<input type="hidden" name="revision" value="(\d+)"', form).group(1)
    return out


def page(client, query="") -> str:
    return client.get(f"{BASE}/{RUN}/excepciones{query}").text


# ═══════════════════ acceso y cabeceras ═══════════════════
def test_saving_a_draft_is_owner_only_requires_the_write_token_and_is_never_cached(monkeypatch):
    key, _ = two_docs()
    url = f"{BASE}/{RUN}/excepciones/{key}/borrador"
    assert TestClient(app, follow_redirects=False).post(url, data={"graph_token": WRITE_TOKEN}).status_code in (302, 303, 307)
    assert client_for("fuera-de-cchh@danwor.com", monkeypatch).post(url, data={"graph_token": WRITE_TOKEN, **DRAFT}).status_code == 403          # autenticado pero sin autorización de Capital Humano
    assert mf.get_draft(RUN, key) is None
    owner = client_for(OWNER, monkeypatch)
    no_token = post_draft(owner, key, token="")
    assert no_token.status_code == 200 and "Sin token con permiso de escritura" in no_token.text and mf.get_draft(RUN, key) is None
    assert form_values(no_token.text, key)["first_name"] == "Zulema" and form_values(no_token.text, key)["notes"] == DRAFT["notes"]       # sin token no se pierde lo escrito
    ok = post_draft(owner, key)
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store" and mf.get_draft(RUN, key)["revision"] == 1
    assert owner.post(f"{BASE}/{RUN}/excepciones/{key}/otra-cosa", data={"graph_token": WRITE_TOKEN}).status_code == 404
    assert owner.post(f"{BASE}/ch-20200101T000000Z-aaaa/excepciones/{key}/borrador", data={"graph_token": WRITE_TOKEN, **DRAFT}).status_code == 404


def test_the_draft_form_uses_the_write_token_hook_and_never_the_read_hook(monkeypatch):
    two_docs()
    html = page(client_for(OWNER, monkeypatch))
    forms = re.findall(r"<form[^>]*data-exc-draft[^>]*>", html)
    assert len(forms) == 2 and all("data-hc-write-token" in f and "data-dw-graph-token" not in f and "/borrador" in f for f in forms)
    assert html.count("Guardar borrador") == 2 and "human_capital_import.js" in html and WRITE_TOKEN not in html


# ═══════════════════ criterio de aceptación: guardar, salir y volver ═══════════════════
def test_acceptance_save_leave_and_find_exactly_what_was_saved_without_touching_the_bank(monkeypatch):
    key, other = two_docs()
    owner = client_for(OWNER, monkeypatch)
    before_page = page(owner)
    assert 'data-draft-state="none"' in row_of(before_page, key) and "Sin revisar" in row_of(before_page, key) and "Con borrador guardado: 0." in before_page
    profiles = profile_repository.list()
    untouched = digest()
    for fn in ("complete_identity", "link_existing", "dismiss", "reopen", "retry", "execute_batch"):
        monkeypatch.setattr(ex, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError("un borrador no ejecuta acciones de importación")))
    for fn in ("graph_get_json", "graph_get_bytes", "graph_post_json", "graph_put_bytes", "graph_delete"):
        monkeypatch.setattr(microsoft365, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError("guardar un borrador no habla con Microsoft Graph")))
    res = post_draft(owner, key)                                                                                                     # completar parcialmente y guardar
    assert 'data-draft-done' in res.text and "Borrador guardado (revisión 1)." in res.text and "No se creó ningún perfil ni se tocó el Banco." in res.text
    assert 'data-draft-state="saved"' in res.text and "Sin revisar" not in res.text and f"Rev. 1 · {OWNER}" in res.text and "«Zulema Draftona»" in res.text
    assert form_values(res.text, key) == {**DRAFT, "revision": "1"}                                                                  # la fila responde con lo vigente
    again = page(client_for(OWNER, monkeypatch))                                                                                     # «salir y volver»: otra petición, otro cliente
    assert form_values(again, key) == {**DRAFT, "revision": "1"}                                                                     # exactamente lo guardado
    assert 'data-draft-state="saved"' in row_of(again, key) and 'data-draft-state="none"' in row_of(again, other) and "Con borrador guardado: 1." in again
    assert digest() == untouched and profile_repository.list() == profiles                                                           # nada fuera de drafts/ cambió: ni perfiles, ni libro, ni staging, ni run.json
    rect = post_draft(owner, key, revision="1", phone="", last_name="Draftoni")                                                      # rectificar parcialmente
    assert "revisión 2" in rect.text and form_values(rect.text, key)["last_name"] == "Draftoni" and form_values(rect.text, key)["phone"] == "" and form_values(page(owner), key)["revision"] == "2"
    assert "Historial del borrador (2)" in rect.text and "antes, apellido: «Draftona»" in rect.text and "antes, teléfono: «+54 9 11 5555-9999»" in rect.text and f"Rev. 2 · {OWNER}" in rect.text


def test_saving_without_changes_says_so_and_does_not_advance_the_revision(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key)
    res = post_draft(owner, key, revision="1")
    assert "Sin cambios: el borrador ya tenía estos datos." in res.text and mf.get_draft(RUN, key)["revision"] == 1 and len(mf.get_draft(RUN, key)["history"]) == 1


# ═══════════════════ errores: nada se pierde en silencio ═══════════════════
def test_validation_errors_keep_everything_typed_and_save_nothing(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    res = post_draft(owner, key, email="esto-no-es-un-email", notes="texto <b>largo</b> & más")
    assert 'data-error-kind="exception_action"' in res.text and "email no parece válido" in res.text and mf.get_draft(RUN, key) is None
    values = form_values(res.text, key)
    assert values["email"] == "esto-no-es-un-email" and values["notes"] == "texto <b>largo</b> & más" and values["first_name"] == "Zulema" and "<b>largo</b>" not in res.text      # se conserva lo escrito, escapado
    assert "<details class=\"hc-exc-actions\" open>" in res.text.replace("\r", "").replace("\n", "") or 'class="hc-exc-actions" open' in res.text                           # el formulario queda abierto para corregir


def test_a_conflict_shows_what_is_saved_keeps_what_was_typed_and_resubmitting_after_seeing_it_works(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key)
    drafts.save(RUN, key, {**DRAFT, "last_name": "Draftoni"}, actor="otra-persona@danwor.com", expected_revision=1)               # otra sesión guardó la revisión 2
    res = post_draft(owner, key, revision="1", first_name="Zulema Ana")
    assert "data-draft-conflict" in res.text and "ya está en la revisión 2 (por otra-persona@danwor.com" in res.text and "No se guardó nada y tus datos siguen en el formulario" in res.text
    assert "apellido guardado: «Draftoni»" in res.text and "nombre guardado: «Zulema»" in res.text
    values = form_values(res.text, key)
    assert values["first_name"] == "Zulema Ana" and values["revision"] == "2" and mf.get_draft(RUN, key)["revision"] == 2 and mf.get_draft(RUN, key)["fields"]["first_name"] == "Zulema"
    ok = post_draft(owner, key, revision=values["revision"], first_name="Zulema Ana")
    assert "revisión 3" in ok.text and mf.get_draft(RUN, key)["fields"]["first_name"] == "Zulema Ana" and mf.get_draft(RUN, key)["updated_by"] == OWNER


def test_when_spaces_does_not_confirm_the_owner_is_told_nothing_was_saved_and_keeps_the_form_and_the_revision(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key)
    state = {"result": remote_store.DURABLE_LOCAL}

    def fake(path, text, **kw):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text if isinstance(text, str) else text.decode("utf-8"), encoding="utf-8")
        return state["result"]
    monkeypatch.setattr(remote_store, "write_through", fake)
    monkeypatch.setattr(remote_store, "local_only", lambda: False)
    monkeypatch.setattr(remote_store, "enabled", lambda: False)
    res = post_draft(owner, key, revision="1", last_name="Draftoni")
    assert "No se pudo guardar el borrador (persistencia no confirmada). No se guardó nada" in res.text and form_values(res.text, key)["last_name"] == "Draftoni" and form_values(res.text, key)["revision"] == "1"
    assert mf.get_draft(RUN, key)["fields"]["last_name"] == "Draftona" and mf.get_draft(RUN, key)["revision"] == 1                  # lo anterior sigue íntegro
    state["result"] = remote_store.DURABLE_REMOTE
    assert "revisión 2" in post_draft(owner, key, revision="1", last_name="Draftoni").text and mf.get_draft(RUN, key)["fields"]["last_name"] == "Draftoni"


def test_an_exception_that_was_resolved_in_the_meantime_cannot_receive_a_draft(monkeypatch):
    key, _ = two_docs()
    mf.put_ledger(RUN, key, {"source": "manual", "action": "MANUAL_CREATE", "profile_id": "p", "document_id": "d", "attempts": 0, "history": [], "state": "DONE"})
    res = post_draft(client_for(OWNER, monkeypatch), key)
    assert "ya no es una excepción por resolver" in res.text and mf.get_draft(RUN, key) is None


# ═══════════════════ precarga solo con evidencia segura ═══════════════════
def test_names_are_prefilled_only_with_strong_evidence_and_otherwise_alternatives_are_shown_without_choosing(monkeypatch):
    w = World()
    keys = {}
    for i, level in enumerate("ABCD"):
        e, first, last, _email = _corroborated(i, level)
        keys[level] = (w.doc(e), first, last)
    w.build()
    html = page(client_for(OWNER, monkeypatch))
    a_key, first, last = keys["A"]
    a = form_values(html, a_key)
    assert (a["first_name"], a["last_name"]) == (first, last) and "data-draft-prefilled" in row_of(html, a_key) and "data-draft-noprefill" not in row_of(html, a_key)
    for level, why in (("B", "partición nombre/apellido incierta"), ("C", "orden incierto"), ("D", "token de borde sin explicar o sin nombre y apellido")):
        key, first, last = keys[level]
        values, row = form_values(html, key), row_of(html, key)
        assert (values["first_name"], values["last_name"]) == ("", "") and "data-draft-noprefill" in row and why in row and f"«{first} {last}»" in row, level               # no se elige: se muestra la alternativa


def test_email_and_phone_are_prefilled_only_when_unambiguous_and_a_saved_draft_always_wins(monkeypatch):
    w = World()
    one = w.doc(_ext(emails=["unico@sintetico.test"], phone="+54 9 11 5555-0101", alts=[("Elena Rojas", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    many = w.doc(_ext(emails=["uno@sintetico.test", "dos@sintetico.test"], alts=[("Pablo Rios", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    w.build()
    owner = client_for(OWNER, monkeypatch)
    html = page(owner)
    single, multiple = form_values(html, one), form_values(html, many)
    assert single["email"] == "unico@sintetico.test" and single["phone"] == "+54 9 11 5555-0101" and single["first_name"] == ""
    assert multiple["email"] == "" and "data-draft-emails" in row_of(html, many) and "uno@sintetico.test, dos@sintetico.test" in row_of(html, many)
    post_draft(owner, one, email="", phone="", first_name="Elena", last_name="Rojas", notes="")
    saved = form_values(page(owner), one)
    assert saved["email"] == "" and saved["phone"] == "" and saved["first_name"] == "Elena"                                          # lo guardado manda sobre la propuesta del extractor


# ═══════════════════ el Banco y el resto de CCHH no ven los borradores ═══════════════════
def test_drafts_never_appear_in_search_interviews_selectors_or_indicators(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key)
    assert mf.get_draft(RUN, key)["fields"]["first_name"] == "Zulema"
    for url in ("/cchh/candidatos", "/cchh/candidatos?q=Zulema", "/cchh/busquedas", "/cchh/entrevistas", "/cchh/onboarding", "/cchh/indicadores", f"{BASE}/{RUN}"):
        res = owner.get(url)
        assert res.status_code == 200, url
        text = res.text.replace('value="Zulema"', "")                                                                                 # el buscador devuelve en su cuadro lo que se escribió: no cuenta como resultado
        assert not any(secret in text for secret in SECRETS[:3]), url
    assert all("Zulema" not in str(p) for p in profile_repository.list())


# ═══════════════════ lo existente sigue funcionando ═══════════════════
def test_filters_pager_original_cv_and_the_other_actions_keep_working_next_to_the_draft(monkeypatch):
    key, other = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key)
    html = page(owner)
    row = row_of(html, key)
    for marker in ("data-exc-complete", "data-exc-link", "data-exc-dismiss", "data-original-cv-form", "data-exc-draft", "data-recoverability"):
        assert marker in row, marker
    fragment = owner.get(f"{BASE}/{RUN}/excepciones?motivo=MULTIPLE_EMAILS", headers={"HX-Request": "true"})
    assert fragment.text.lstrip().startswith('<div id="cvexc-panel">') and "data-exceptions-filters" in fragment.text and "Con borrador guardado: 1." in fragment.text and fragment.headers["cache-control"] == "no-store"
    only_resolved = owner.get(f"{BASE}/{RUN}/excepciones?estado=resolved").text
    assert "data-exceptions-empty" in only_resolved and 'data-draft-state' not in only_resolved
    dismiss = owner.post(f"{BASE}/{RUN}/excepciones/{other}/descartar", data={"reason": "OTHER"})                                    # las acciones existentes responden igual (antes de iniciar la importación, con aviso)
    assert dismiss.status_code == 200 and f'id="exc-{other}"' in dismiss.text and 'data-draft-state="none"' in dismiss.text and "data-exc-draft" in dismiss.text


def test_the_draft_form_markup_is_accessible_and_the_notes_are_escaped_everywhere(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key, notes="</textarea><script>alert(1)</script>", first_name="<i>Z</i>")
    html = page(owner)
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<i>Z</i>" not in html
    form = re.search(r"<form[^>]*data-exc-draft[^>]*>(.*?)</form>", row_of(html, key), flags=re.S).group(0)
    for label in ("Nombre del borrador", "Apellido del borrador", "Email del borrador", "Teléfono del borrador", "Observaciones del borrador"):
        assert f'aria-label="{label}"' in form, label
    assert ">Guardar borrador</button>" in form and "<summary>Historial del borrador" in form and 'maxlength="2000"' in form
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
    assert ".hc-exc-form :is(input, select, textarea)" in css and "pager" not in css.lower()


def test_logs_of_the_draft_flow_carry_no_personal_data(monkeypatch, caplog):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    caplog.set_level(logging.DEBUG)
    post_draft(owner, key)
    post_draft(owner, key, revision="0", first_name="Otro")                                                                           # conflicto
    post_draft(owner, key, revision="1", email="malo")                                                                                # validación
    page(owner)
    assert not any(secret in caplog.text for secret in (*SECRETS, "Otro", WRITE_TOKEN))


def test_saving_tells_the_panel_the_new_draft_count_and_errors_do_not(monkeypatch):
    key, other = two_docs()
    owner = client_for(OWNER, monkeypatch)
    assert "hx-trigger" not in post_draft(owner, key, email="malo").headers and "hx-trigger" not in post_draft(owner, key, token="").headers
    assert post_draft(owner, key).headers["hx-trigger"] == '{"cvexc-drafts": 1}'
    assert post_draft(owner, other).headers["hx-trigger"] == '{"cvexc-drafts": 2}'
    js = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "human_capital_import.js").read_text(encoding="utf-8")
    assert 'addEventListener("cvexc-drafts"' in js and "[data-drafts-count]" in js and 'typeof total === "number"' in js                # el oyente solo escribe un número


def test_a_draft_never_feeds_the_complete_identity_form_that_creates_profiles(monkeypatch):
    key, _ = two_docs()
    owner = client_for(OWNER, monkeypatch)
    for res in (post_draft(owner, key), post_draft(owner, key, email="malo"), page(owner)):
        text = res if isinstance(res, str) else res.text
        form = re.search(r"<form[^>]*data-exc-complete[^>]*>(.*?)</form>", row_of(text, key) if "<tr id=" in text else text, flags=re.S).group(1)
        assert re.search(r'name="first_name"[^>]*value=""', form) and re.search(r'name="last_name"[^>]*value=""', form)             # «Completar identidad» no hereda nada del borrador: la aceptación llega en otra entrega


# ═══════════════════ ciclo de vida desde la interfaz ═══════════════════
def test_the_run_page_blocks_discarding_the_temporary_area_while_drafts_are_active_and_the_route_refuses(monkeypatch):
    from webapp.services import cv_history_pilot as pilot
    key, other = two_docs()
    owner = client_for(OWNER, monkeypatch)
    free = owner.get(f"{BASE}/{RUN}").text
    assert "data-discard-blocked" not in free and "data-pilot-discard-form" in free                                                  # sin borradores el descarte sigue disponible (explicito)
    post_draft(owner, key)
    blocked = owner.get(f"{BASE}/{RUN}").text
    assert "data-discard-blocked" in blocked and 'data-discard-drafts>1<' in blocked.replace("\r", "").replace("\n", "") and "no se borran solos" in blocked and "política de retención está pendiente" in blocked
    assert "data-pilot-discard-form" not in blocked
    before = digest(skip="__none__")
    staging = mf.staging_files_present(RUN)
    res = owner.post(f"{BASE}/{RUN}/descartar", data={"confirm": pilot.discard_phrase(RUN)})                                         # aunque se fuerce el POST con la frase correcta
    assert res.status_code == 200 and "borradores guardados de excepciones sin resolver" in res.text and "Datos personales del lote eliminados" not in res.text
    assert digest(skip="__none__") == before and mf.staging_files_present(RUN) == staging and mf.get_draft(RUN, key)["fields"]["first_name"] == "Zulema"     # no se perdio nada
    assert res.headers["cache-control"] == "no-store" and mf.get_run(RUN)["staging"]["state"] == "PRESENT"


def test_after_resolving_the_exception_the_owner_can_discard_and_the_draft_with_its_corrections_remains(monkeypatch):
    from webapp.services import cv_history_pilot as pilot
    key, other = two_docs()
    owner = client_for(OWNER, monkeypatch)
    post_draft(owner, key)
    post_draft(owner, key, revision="1", notes="correccion que no se debe perder")
    saved = mf.get_draft(RUN, key)
    for k in (key, other):
        mf.put_ledger(RUN, k, {"source": "manual", "action": "MANUAL_CREATE", "profile_id": "p", "document_id": "d", "attempts": 0, "history": [], "state": "DONE"})       # ambas resueltas
    ok = owner.post(f"{BASE}/{RUN}/descartar", data={"confirm": pilot.discard_phrase(RUN)})
    assert "Datos personales del lote eliminados" in ok.text and mf.staging_files_present(RUN) == 0 and mf.get_run(RUN)["staging"]["state"] == "DISCARDED"
    assert mf.get_draft(RUN, key) == saved and mf.get_draft(RUN, key)["fields"]["notes"] == "correccion que no se debe perder" and len(saved["history"]) == 2          # el borrador y su historial siguen intactos
    refused = post_draft(owner, key, revision="2", notes="otra")
    assert mf.get_draft(RUN, key) == saved and mf.get_draft(RUN, other) is None
