"""«Históricos sin validación» (Entrega A, solo lectura): presentación de las excepciones de la importación histórica con categoría de recuperabilidad, evidencia legible y «Ver CV original» por Microsoft Graph con el token
delegado del usuario. Lotes SINTÉTICOS (motor real de `build_plan`) y Graph simulado: nada contra DigitalOcean, SharePoint ni datos reales. La entrega no escribe nada: se verifica con el digest del disco."""
from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365
from tests.test_cv_history_recoverability import RUN, World, _corroborated, _ext
from tests.webapp.test_cv_history_pilot import BASE, HC_USER, OWNER, client_for
from webapp.main import app
from webapp.services import cv_history_import_exec as ex, cv_history_manifest as mf, cv_history_recoverability as rec, cv_history_review as review, profile_repository

TOKEN = "tok-lectura-sintetico-123"
SP_URL = "https://danwor.sharepoint.com/sites/rrhh/CVs/historico.pdf"
PRIVATE = ("Valeria", "Ibarra", "Laura", "Mendez", "sintetico.test")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", False)


def digest() -> dict:
    return {p.relative_to(config.BASE_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(config.BASE_DIR).rglob("*")) if p.is_file()}


def locate(keys=None, *, drive="drv-1", with_member=False):
    """Agrega al staging los identificadores de SharePoint del representante (lo que hoy guarda `build_groups`)."""
    for n, group in enumerate(mf.list_groups(RUN), start=1):
        if keys and group["content_key"] not in keys:
            continue
        group["representative"] = {**group["representative"], "item_id": f"item-{n}", "drive_id": drive}
        if with_member:
            group["members"] = [{"item_ref": "r1", "item_id": f"item-{n}", "drive_id": drive, "specialty": "Contador"}, {"item_ref": "r2", "item_id": f"copy-{n}", "drive_id": drive, "specialty": "Contador"}]
        mf.put_group(RUN, group["content_key"], group)


def mixed_world() -> World:
    w = World()
    w.profile("Valeria", "Ibarra", email="ibarra.v@sintetico.test", phone="5491155550001")
    w.doc(_ext(failure="NO_TEXT", rule="R6", state="MISSING"))                                                                                                        # problema de lectura
    w.doc(_ext(rule="R6", state="MISSING", flags=["NAME_MISSING"]))                                                                                                   # información insuficiente
    w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))                                # alta a confirmar
    w.doc(_ext(emails=["ibarra.v@sintetico.test", "otro@sintetico.test"], phone="5491155550001", alts=[("Valeria Ibarra", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))   # posible vinculación
    w.doc(_ext(phone="+54 9 11 5555-0401", rule="R4", state="CONFLICT", alts=[("Pablo Rios", "DECLARED"), ("Mario Vega", "DECLARED")], flags=["NAME_CONFLICT", "EMAIL_MISSING"]))   # conflicto
    return w


# ═══════════════════ página «Históricos sin validación» ═══════════════════
def test_the_page_is_owner_only_no_store_and_reads_as_historicos_sin_validacion(monkeypatch):
    mixed_world().build()
    for method, url, kw in (("get", f"{BASE}/{RUN}/excepciones", {}), ("post", f"{BASE}/{RUN}/cv-fuente/{'0' * 16}", {"data": {"graph_token": TOKEN}})):
        assert getattr(TestClient(app, follow_redirects=False), method)(url, **kw).status_code in (302, 303, 307), url
        assert getattr(client_for("fuera-de-cchh@danwor.com", monkeypatch), method)(url, **kw).status_code == 403, url                  # autenticado pero sin autorización de Capital Humano
    assert client_for(HC_USER, monkeypatch).get(f"{BASE}/{RUN}/excepciones").status_code == 200                                         # un usuario autorizado de CCHH entra (ver test_cv_history_access)
    page = client_for(OWNER, monkeypatch).get(f"{BASE}/{RUN}/excepciones")
    html = page.text
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "<h1>Históricos sin validación</h1>" in html and "<title>Históricos sin validación" in html and "data-historicos-intro" in html
    assert "todavía no son perfiles del Banco" in html and "no son identidades verificadas" in html and "no se copia ni se guarda el enlace" in html
    assert html.count("data-exception-row") == 5 and html.count("data-original-cv-form") == 5 and 'scope="col"' in html


def test_every_row_shows_its_recoverability_category_with_text_and_consulting_writes_nothing(monkeypatch):
    mixed_world().build()
    before = digest()
    html = client_for(OWNER, monkeypatch).get(f"{BASE}/{RUN}/excepciones").text
    assert digest() == before                                                                                                                                         # consultar no escribe en el manifiesto, staging, libro ni Banco
    seen = re.findall(r'data-recoverability="([A-Z_]+)">([^<]+)<', html)
    assert sorted(seen) == sorted([(rec.EXTRACTION, "Problema de lectura"), (rec.INSUFFICIENT, "Información insuficiente"), (rec.ASSISTED, "Alta a confirmar"), (rec.LINK, "Posible vinculación"), (rec.CONFLICT, "Conflicto de identidad")])
    assert "Por qué hay conflicto" in html and "El documento no tiene texto legible" in html                                                                         # el texto explica; el color no es lo único que distingue


def test_the_evidence_panel_explains_names_sources_warnings_and_what_to_confirm(monkeypatch):
    w = World()
    for i, level in enumerate("ABCD"):
        e, *_ = _corroborated(i, level)
        w.doc(e)
    w.build()
    html = client_for(OWNER, monkeypatch).get(f"{BASE}/{RUN}/excepciones").text
    assert html.count("data-exception-evidence") == 4 and html.count("Propuesta automática: no es una identidad verificada.") == 4
    assert "Corroborada por evidencia independiente" in html and "(es la propuesta)" in html and "Nombre propuesto:" in html
    assert "coincide entre el texto del CV y el nombre del archivo" in html and "el email contiene partes distintivas del nombre" in html
    assert "No se sabe dónde termina el nombre y empieza el apellido." in html and "No se sabe el orden nombre/apellido" in html and "no se pudo explicar" in html
    assert "Para incorporarlo al Banco hay que confirmar" in html and "nombre y apellido" in html and "orden nombre/apellido" in html
    assert html.count("identidad verificada") == html.count("no es una identidad verificada") + html.count("identidades verificadas") * 0 and "ya verificada" not in html          # siempre negada: nunca se afirma


def test_review_view_covers_conflict_phone_only_edge_token_and_normalization():
    extraction = _ext(first="Ana", last="Soto", rule="R1", state="CORROBORATED", alts=[("Ana Soto", "CORROBORATED"), ("Pedro Gil", "DECLARED")], flags=["NAME_ALTERNATIVE", "PHONE_MULTIPLE"])
    extraction["name_decision"]["proposed"] = 0
    extraction["name_decision"]["alternatives"][0].update(sources=["filename"], evidence=["EMAIL_STRONG"], edge_token={"token": "RC", "kind": "AMBIGUOUS"}, normalization={"original": "Lic. Ana Soto", "stripped": ["Lic."]})
    detail = {"categoria": rec.CONFLICT, "evidencia": ["IDENTIDADES_CONTRADICTORIAS", "ALGO_NUEVO"], "riesgos": ["SOLO_CELULAR", "TOKEN_DE_BORDE"], "confirmar": ["nombre y apellido"]}
    v = review._review({}, {"extraction": extraction}, detail)
    assert v["label"] == "Conflicto de identidad" and v["badge"] == "status-badge--estancada" and v["proposal"] == "Ana Soto"
    assert v["conflicts"] == ["El documento reclama identidades distintas.", "ALGO_NUEVO"]                                                                             # un código desconocido no se pierde ni rompe
    assert v["risks"] == ["La coincidencia con un perfil es solo por celular: no confirma identidad."] and v["warnings"] == ["Hay otros nombres posibles en el documento.", "El documento trae varios celulares."]
    first, second = v["identities"]
    assert first["proposed"] and first["found_in"] == ["el nombre del archivo"] and any("Se quitó «RC»" in n for n in first["notes"]) and any("Texto original: «Lic. Ana Soto»" in n for n in first["notes"])
    assert not second["proposed"] and second["state"] == "Declarada en el documento, sin corroboración independiente"
    assert review._review({}, None, detail) is None and review._review({}, {"extraction": extraction}, None) is None                                                   # sin datos temporales o sin clasificación: sin revisión


def test_only_the_rows_of_the_visible_page_are_classified(monkeypatch):
    w = World()
    for i in range(55):
        w.doc(_ext(emails=[f"cv{i}@sintetico.test"], alts=[(f"Nombre{chr(97 + i % 26)}{chr(97 + i // 26)} Apellido{chr(97 + i % 26)}", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    calls = []
    real = rec.classify
    monkeypatch.setattr(rec, "classify", lambda *a, **k: (calls.append(a[0]["content_key"]), real(*a, **k))[1])
    owner = client_for(OWNER, monkeypatch)
    first = owner.get(f"{BASE}/{RUN}/excepciones")
    assert first.text.count("data-exception-row") == 50 and len(calls) == 50                                                                                         # 55 excepciones, 50 clasificaciones: nada se recalcula para el lote entero
    calls.clear()
    second = owner.get(f"{BASE}/{RUN}/excepciones?page=2", headers={"HX-Request": "true"})
    assert second.text.count("data-exception-row") == 5 and len(calls) == 5 and second.headers["cache-control"] == "no-store"
    calls.clear()
    owner.get(f"{BASE}/{RUN}/excepciones?estado=resolved")
    assert calls == []                                                                                                                                                # sin filas abiertas no hay nada que clasificar


def test_resolved_and_unavailable_rows_are_not_classified_and_a_failing_row_does_not_break_the_page(monkeypatch):
    w = World()
    for i in range(2):
        w.doc(_ext(emails=[f"cv{i}@sintetico.test"], alts=[(f"Nombre Apellido{chr(97 + i)}", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    rows = ex.exceptions_overview(RUN)["rows"]
    details = ex.exception_details(RUN, rows)
    details[0]["status"] = "resolved"
    details[1]["available"] = False
    assert [r.get("review") for r in review.annotate(RUN, details)] == [None, None]
    details = ex.exception_details(RUN, rows)
    monkeypatch.setattr(rec, "classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert [r["review"] for r in review.annotate(RUN, details)] == [None, None]
    page = client_for(OWNER, monkeypatch).get(f"{BASE}/{RUN}/excepciones")
    assert page.status_code == 200 and page.text.count("data-exception-row") == 2 and "data-recoverability" not in page.text and page.text.count("data-original-cv-form") == 2


def test_existing_filters_pager_and_actions_keep_working_with_the_new_row(monkeypatch):
    mixed_world().build()
    owner = client_for(OWNER, monkeypatch)
    only = owner.get(f"{BASE}/{RUN}/excepciones?motivo=NO_EMAIL", headers={"HX-Request": "true"})
    assert only.text.lstrip().startswith('<div id="cvexc-panel">') and "data-exceptions-filters" in only.text and "data-exceptions-total" in only.text
    key = mf.list_groups(RUN)[2]["content_key"]
    row = owner.post(f"{BASE}/{RUN}/excepciones/{key}/descartar", data={"reason": "OTHER"})                                                                           # antes de iniciar la importación la acción responde con aviso
    assert row.status_code == 200 and f'id="exc-{key}"' in row.text and 'data-error-kind="exception_action"' in row.text and row.headers["cache-control"] == "no-store"
    assert "data-recoverability=" in row.text and "data-original-cv-form" in row.text and f'id="cvorig-{key}"' in row.text                                           # la fila de la acción conserva categoría y «Ver CV original»


def test_row_markup_is_accessible_and_escapes_untrusted_text(monkeypatch):
    w = World()
    w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    group = mf.list_groups(RUN)[0]
    group["representative"] = {**group["representative"], "name": "<script>alert(1)</script>.pdf"}
    mf.put_group(RUN, group["content_key"], group)
    html = client_for(OWNER, monkeypatch).get(f"{BASE}/{RUN}/excepciones").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;.pdf" in html
    key = group["content_key"]
    assert f'<div id="cvorig-{key}" role="status" aria-live="polite">' in html and f'aria-describedby="cvorig-{key}"' in html and ">Ver CV original</button>" in html
    assert "<summary>Evidencia de identidad</summary>" in html and re.search(r'<form[^>]*data-original-cv-form[^>]*data-dw-graph-token', html) and "data-hc-write-token" not in re.search(r"<form[^>]*data-original-cv-form[^>]*>", html).group(0)


# ═══════════════════ «Ver CV original» ═══════════════════
def graph_stub(monkeypatch, reply):
    calls = []

    def fake(url, token, **kw):
        calls.append({"url": url, "token": token, **kw})
        value = reply(url) if callable(reply) else reply
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(microsoft365, "graph_get_json", fake)
    for name in ("graph_post_json", "graph_put_bytes", "graph_delete", "graph_get_bytes"):
        monkeypatch.setattr(microsoft365, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("«Ver CV original» es de SOLO LECTURA: no descarga ni escribe")))
    return calls


def http(code):
    return urllib.error.HTTPError("https://graph.microsoft.com/v1.0/x", code, "msg", {}, None)


def post_original(client, key, token=TOKEN, run=RUN):
    return client.post(f"{BASE}/{run}/cv-fuente/{key}", data={"graph_token": token})


def one_doc_world() -> str:
    w = World()
    key = w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    locate()
    return key


def test_view_original_cv_returns_the_sharepoint_link_with_the_delegated_token_and_stores_nothing(monkeypatch):
    key = one_doc_world()
    calls = graph_stub(monkeypatch, {"webUrl": SP_URL, "name": "x"})
    before = digest()
    res = post_original(client_for(OWNER, monkeypatch), key)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert f'<a href="{SP_URL}" target="_blank" rel="noopener noreferrer" data-original-cv-link>Abrir «cv1.pdf» en SharePoint</a>' in res.text and "tus propios permisos" in res.text
    assert len(calls) == 1 and calls[0]["token"] == TOKEN and calls[0]["url"] == "https://graph.microsoft.com/v1.0/drives/drv-1/items/item-1?$select=webUrl"
    assert digest() == before and SP_URL not in "".join(p.read_text(encoding="utf-8", errors="ignore") for p in Path(config.BASE_DIR).rglob("*.json"))                      # no se guarda la URL ni se crea ninguna copia
    assert TOKEN not in res.text


def test_without_a_token_it_asks_to_sign_in_again_and_never_calls_graph(monkeypatch):
    key = one_doc_world()
    calls = graph_stub(monkeypatch, {"webUrl": SP_URL})
    res = post_original(client_for(OWNER, monkeypatch), key, token="")
    assert 'data-error-code="NO_TOKEN"' in res.text and "Volvé a iniciar sesión" in res.text and calls == [] and "data-original-cv-link" not in res.text


@pytest.mark.parametrize("failure, code, text", [
    (http(401), "TOKEN_EXPIRED", "venció"), (http(403), "ACCESS_DENIED", "no tiene permiso"), (http(404), "NOT_FOUND", "ya no está en SharePoint"), (http(429), "GRAPH_UNAVAILABLE", "no respondió bien"),
    (http(503), "GRAPH_UNAVAILABLE", "no respondió bien"), (urllib.error.URLError("sin red"), "NETWORK", "No se pudo conectar"), (TimeoutError(), "NETWORK", "No se pudo conectar"),
    (ValueError("json roto"), "BAD_RESPONSE", "no se pudo interpretar"), ([], "INVALID_LINK", "enlace de SharePoint válido"), ({"name": "sin webUrl"}, "INVALID_LINK", "enlace de SharePoint válido"),
])
def test_graph_errors_become_a_clear_message_with_a_stable_code(monkeypatch, failure, code, text):
    key = one_doc_world()
    graph_stub(monkeypatch, failure)
    res = post_original(client_for(OWNER, monkeypatch), key)
    assert res.status_code == 200 and f'data-error-code="{code}"' in res.text and text in res.text and "data-original-cv-link" not in res.text and 'data-error-kind="original_cv"' in res.text


@pytest.mark.parametrize("url", ["http://danwor.sharepoint.com/x.pdf", "https://evil.example.com/x.pdf", "https://danwor.sharepoint.com.evil.com/x.pdf", "javascript:alert(1)", "https://u:p@danwor.sharepoint.com/x.pdf", "//danwor.sharepoint.com/x"])
def test_only_https_sharepoint_links_are_ever_rendered(monkeypatch, url):
    key = one_doc_world()
    graph_stub(monkeypatch, {"webUrl": url})
    res = post_original(client_for(OWNER, monkeypatch), key)
    assert 'data-error-code="INVALID_LINK"' in res.text and "href=" not in res.text.replace('href="/', "")


def test_an_identical_copy_is_tried_when_the_representative_is_gone_and_a_permission_error_wins_over_not_found(monkeypatch):
    w = World()
    key = w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    locate(with_member=True)
    owner = client_for(OWNER, monkeypatch)
    calls = graph_stub(monkeypatch, lambda url: http(404) if "/items/item-1" in url else {"webUrl": SP_URL})
    assert "data-original-cv-link" in post_original(owner, key).text and [c["url"].split("/items/")[1].split("?")[0] for c in calls] == ["item-1", "copy-1"]
    graph_stub(monkeypatch, lambda url: http(404) if "/items/item-1" in url else http(403))
    assert 'data-error-code="ACCESS_DENIED"' in post_original(owner, key).text
    calls = graph_stub(monkeypatch, http(401))
    assert 'data-error-code="TOKEN_EXPIRED"' in post_original(owner, key).text and len(calls) == 1                                                                   # con el token vencido no insiste con las demás copias


def test_without_staging_or_location_it_explains_why_and_unknown_runs_or_malformed_ids_are_404(monkeypatch):
    w = World()
    key = w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    owner = client_for(OWNER, monkeypatch)
    calls = graph_stub(monkeypatch, {"webUrl": SP_URL})
    assert 'data-error-code="NO_LOCATION"' in post_original(owner, key).text                                                                                          # el contenido no guarda drive/item
    assert 'data-error-code="NO_LOCATION"' in post_original(owner, "f" * 16).text
    mf.update_run(RUN, lambda d: d.update({"staging": {"state": "DISCARDED"}}))
    assert 'data-error-code="NO_STAGING"' in post_original(owner, key).text and calls == []
    assert post_original(owner, key, run="ch-20200101T000000Z-aaaa").status_code == 404
    assert post_original(owner, "no-es-una-clave").status_code == 404 and post_original(owner, key, run="../../etc").status_code == 404


def test_logs_carry_only_codes_never_the_token_the_url_names_or_emails(monkeypatch, caplog):
    key = one_doc_world()
    owner = client_for(OWNER, monkeypatch)
    caplog.set_level(logging.DEBUG)
    graph_stub(monkeypatch, {"webUrl": SP_URL})
    post_original(owner, key)
    graph_stub(monkeypatch, http(403))
    post_original(owner, key)
    graph_stub(monkeypatch, urllib.error.URLError("fallo con Laura Mendez en laura.mendez@sintetico.test"))
    post_original(owner, key)
    mine = "\n".join(r.getMessage() for r in caplog.records if r.name == review.__name__)
    assert "resultado=OK" in mine and "resultado=ACCESS_DENIED" in mine and "resultado=NETWORK" in mine
    for secret in (TOKEN, SP_URL, "sharepoint.com", "cv1.pdf", *PRIVATE):
        assert secret not in mine and secret not in caplog.text, secret


def test_the_review_module_has_no_write_path_and_no_download():
    source = Path(review.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    for forbidden in ("put_group", "put_plan", "put_ledger", "put_index", "update_run", "create_run", "append_", "delete_", "write_through", "_write(", "profile_repository.create", "profile_repository.update", "complete_identity", "link_existing",
                      "dismiss(", "retry(", "reopen(", "open(", ".write_text", "unlink", "push_path", "graph_get_bytes", "graph_post_json", "graph_put_bytes", "graph_delete", "/content"):
        assert forbidden not in code, forbidden

def test_rows_carry_labels_for_the_stacked_mobile_layout_and_the_css_lets_the_table_wrap(monkeypatch):
    mixed_world().build()
    html = client_for(OWNER, monkeypatch).get(f"{BASE}/{RUN}/excepciones").text
    assert html.count("data-label=") == 25 and all(f'data-label="{x}"' in html for x in ("Contenido", "Estado y categoría", "Datos extraídos y evidencia", "Perfiles sugeridos", "Acciones"))
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    assert "table.dashboard-table[data-exceptions-rows] :is(th, td).left { white-space: normal" in css                                                                  # gana a `table.dashboard-table td.left { nowrap }` de tools.css
    stacked = css.split("Móvil: cada excepción se apila", 1)[1].split(chr(10) + "}" + chr(10), 1)[0]
    assert "[data-exceptions-rows] { min-width: 0; }" in stacked and "attr(data-label)" in stacked and "clip: rect(0 0 0 0)" in stacked                                        # ficha apilada; el encabezado queda para lectores de pantalla
