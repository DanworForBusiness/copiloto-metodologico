"""Hotfix transversal de UX de Capital Humano previo al ingreso del director: (1) hojas de la vertical en <head> (sin desfasaje visual en la carga), (2) íconos de evaluación de los tiles → edición de la entrevista con contexto,
(3) foco / bloqueo contextual de los resúmenes con contrato en el BACKEND, (4) pestaña Evaluación solo para APP_OWNER (server-side), (5) orden por columna del Banco de CVs antes de paginar y (6) autocompletado desactivado en
los campos de búsqueda libre. Datos sintéticos; persistencia aislada (fixtures de I1)."""
from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pytest

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401  (env es un fixture autouse)
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _candidate, _card, _manual
from tests.webapp.test_pipeline_return_context import _card_link
from webapp import access
from webapp.services import auth_service, busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, profile_repository

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "webapp" / "templates"


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def interview_stage(stage="Entrevista CCHH"):
    bid = _manual()
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, stage, None, actor=CCHH_EMAIL)
    return bid, pid, app_id, erepo.find_by_profile_and_busqueda(pid, bid)


def tile_href(html: str, name: str, label: str) -> str:
    card = _card(html, name)
    return html_lib.unescape(re.search(r'<a href="([^"]+)" class="table-action-btn chart-tooltip-anchor busqueda-cand__interview" aria-label="' + re.escape(label) + '"', card).group(1))


def textarea(html: str, field: str) -> str:
    return re.search(r"<textarea[^>]*id=\"" + field + r"\"[^>]*>", html).group(0)


# ═══════════════════ 1. hojas de la vertical en <head> (causa raíz del desfasaje de carga) ═══════════════════
def test_the_vertical_stylesheet_is_linked_in_the_head_not_after_the_content(c):
    for url in ("/cchh/candidatos", "/cchh/entrevistas", "/cchh/onboarding", "/cchh/busquedas"):
        page = c.get(url).text
        head, body = page.split("</head>", 1)
        assert "human_capital.css" in head and "human_capital.css" not in body, url                                      # antes: al final del body (el contenido se pintaba sin sus estilos)
        assert head.index("app.css") < head.index("human_capital.css")                                                  # después de las hojas globales (misma cascada que antes)


def test_base_declares_the_head_block_and_every_template_that_links_the_vertical_sheet_uses_it():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "{% block head_styles %}{% endblock %}" in base and base.index("{% block head_styles %}") < base.index("</head>")
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "<link rel=\"stylesheet\"" in line and ("human_capital.css" in line or "operations_busquedas.css" in line):
                if "{% block head_styles %}" not in line:
                    offenders.append(f"{path.relative_to(TEMPLATES)}: {line.strip()[:80]}")
    assert offenders == []                                                                                                  # ninguna hoja de la vertical queda suelta al final del body


# ═══════════════════ 2. íconos de evaluación → edición de la entrevista, con contexto ═══════════════════
def test_the_evaluation_icons_open_the_interview_edit_with_the_origin_context_and_create_nothing(c):
    bid = _manual()
    pid, app_id = _candidate(bid, "Lucia", "Prueba")
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    existing = erepo.find_by_profile_and_busqueda(pid, bid)
    before = len(erepo.list())
    ctx = f"return_to=%2Fcchh%2Fbusquedas%2F{bid}"
    cchh = tile_href(c.get(f"/cchh/busquedas/{bid}").text, "Lucia Prueba", "Evaluación CCHH")
    assert cchh == f"/cchh/entrevistas/{existing['entrevista_id']}/editar?foco=cchh&{ctx}"                              # candidato + entrevista + contexto de origen + regreso al Pipeline
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)
    tecnica = tile_href(c.get(f"/cchh/busquedas/{bid}").text, "Lucia Prueba", "Evaluación técnica")
    assert tecnica == f"/cchh/entrevistas/{existing['entrevista_id']}/editar?foco=tecnica&{ctx}"
    assert "evaluacion-cchh" not in c.get(f"/cchh/busquedas/{bid}").text and len(erepo.list()) == before                 # ya no abre la pantalla de Evaluación; navegar no crea ni duplica entrevistas
    assert c.get(tecnica).status_code == 200 and c.get(cchh).status_code == 200 and len(erepo.list()) == before


def test_without_an_interview_the_icon_opens_the_prefilled_create_form_and_a_stale_link_redirects_to_the_existing_one(c):
    bid, pid, app_id, existing = interview_stage()
    erepo.delete(existing["entrevista_id"])
    href = tile_href(c.get(f"/cchh/busquedas/{bid}").text, "Mateo Biasotto", "Evaluación CCHH")
    q = parse_qs(urlparse(href).query)
    assert urlparse(href).path == "/cchh/entrevistas/nueva" and q["profile_id"] == [pid] and q["busqueda_id"] == [bid] and q["foco"] == ["cchh"] and q["return_to"] == [f"/cchh/busquedas/{bid}"]
    assert len(erepo.list()) == 0 and c.get(href).status_code == 200 and len(erepo.list()) == 0                           # abrir el alta no crea nada
    created = svc.create_entrevista_for_application(pid, bid, {})[0]
    redirect = c.get(href)
    assert redirect.status_code == 303 and redirect.headers["location"].startswith(f"/cchh/entrevistas/{created['entrevista_id']}/editar") and "foco=cchh" in redirect.headers["location"]       # una entrevista por (candidato, proceso)


# ═══════════════════ 3. foco, etiquetas y bloqueo contextual ═══════════════════
def edit_page(c, ent, **qs):
    q = "&".join(f"{k}={v}" for k, v in qs.items())
    return c.get(f"/cchh/entrevistas/{ent['entrevista_id']}/editar" + (f"?{q}" if q else "")).text


def test_labels_focus_and_read_only_fields_per_access_context(c):
    bid, pid, app_id, ent = interview_stage()
    svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "Resumen CCHH original", "resumen_entrevista_tecnica": "Resumen técnico original"})
    ctx = f"return_to=%2Fcchh%2Fbusquedas%2F{bid}"
    cchh = edit_page(c, ent, foco="cchh", return_to=f"%2Fcchh%2Fbusquedas%2F{bid}")
    assert "<label for=\"ent-obs\">Resumen de la entrevista CCHH</label>" in cchh and "Observaciones</label>" not in cchh and "Resumen de la entrevista técnica</label>" in cchh
    obs, tec = textarea(cchh, "ent-obs"), textarea(cchh, "ent-resumen")
    assert "autofocus" in obs and "disabled" not in obs and "disabled" in tec and "aria-readonly=\"true\"" in tec and "autofocus" not in tec        # foco en el CCHH; el técnico, grisado y solo lectura
    assert "Resumen técnico original" in cchh and 'name="foco" value="cchh"' in cchh and "data-locked-hint" in cchh and "técnica se muestra solo para lectura" in cchh
    assert f'href="/cchh/busquedas/{bid}"' in cchh and "Volver a Pipeline" in cchh or "← Volver" in cchh                              # regreso al contexto de origen
    tecnica = edit_page(c, ent, foco="tecnica", return_to=f"%2Fcchh%2Fbusquedas%2F{bid}")
    obs, tec = textarea(tecnica, "ent-obs"), textarea(tecnica, "ent-resumen")
    assert "autofocus" in tec and "disabled" not in tec and "disabled" in obs and "autofocus" not in obs and "Resumen CCHH original" in tecnica and 'name="foco" value="tecnica"' in tecnica
    plain = edit_page(c, ent)                                                                                           # Entrevistas → Editar: comportamiento general
    assert "disabled" not in textarea(plain, "ent-obs") and "disabled" not in textarea(plain, "ent-resumen") and "autofocus" not in plain.split("ent-obs")[1].split("</section>")[0] and 'name="foco"' not in plain and "data-locked-hint" not in plain
    assert "Resumen de la entrevista CCHH" in plain                                                                      # el rótulo nuevo rige siempre
    bogus = edit_page(c, ent, foco="otro")
    assert 'name="foco"' not in bogus and "disabled" not in textarea(bogus, "ent-obs") and "disabled" not in textarea(bogus, "ent-resumen")                    # un contexto desconocido se ignora


def save(c, ent, **data):
    ctx = data.pop("_ctx", "")
    return c.post(f"/cchh/entrevistas/{ent['entrevista_id']}/editar{ctx}", data={"expected_revision": str(ent["revision"]), **data})


def stored(ent_id):
    return erepo.get(ent_id)


def test_saving_one_summary_never_overwrites_clears_or_modifies_the_other_even_if_the_request_is_tampered(c):
    bid, pid, app_id, ent = interview_stage()
    ent = svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "CCHH original", "resumen_entrevista_tecnica": "Técnico original"})
    ctx = f"?return_to=%2Fcchh%2Fbusquedas%2F{bid}"
    # contexto CCHH, como lo envía el navegador: el campo deshabilitado NO viaja ⇒ no se vacía el técnico
    r = save(c, ent, foco="cchh", observaciones="CCHH nuevo", _ctx=ctx)
    assert r.status_code == 303 or r.status_code == 200
    s = stored(ent["entrevista_id"])
    assert (s["observaciones"], s["resumen_entrevista_tecnica"]) == ("CCHH nuevo", "Técnico original")
    # manipulado: se envía igual el campo bloqueado con otro valor ⇒ el servidor lo descarta
    s2 = stored(ent["entrevista_id"])
    save(c, s2, foco="cchh", observaciones="CCHH otra vez", resumen_entrevista_tecnica="HACK", _ctx=ctx)
    s3 = stored(ent["entrevista_id"])
    assert (s3["observaciones"], s3["resumen_entrevista_tecnica"]) == ("CCHH otra vez", "Técnico original")
    # contexto técnico: simétrico
    save(c, s3, foco="tecnica", resumen_entrevista_tecnica="Técnico nuevo", observaciones="HACK", _ctx=ctx)
    s4 = stored(ent["entrevista_id"])
    assert (s4["observaciones"], s4["resumen_entrevista_tecnica"]) == ("CCHH otra vez", "Técnico nuevo")
    save(c, s4, foco="tecnica", resumen_entrevista_tecnica="Técnico final", _ctx=ctx)                                    # el CCHH ausente tampoco se vacía
    s5 = stored(ent["entrevista_id"])
    assert (s5["observaciones"], s5["resumen_entrevista_tecnica"]) == ("CCHH otra vez", "Técnico final")
    assert s5["revision"] == ent["revision"] + 4


def test_the_other_summary_survives_a_concurrent_change_and_a_stale_save_is_rejected(c):
    bid, pid, app_id, ent = interview_stage()
    ent = svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "CCHH", "resumen_entrevista_tecnica": "Técnico v1"})
    opened = stored(ent["entrevista_id"])                                                                              # el usuario CCHH abre el formulario (revisión N)
    svc.update_entrevista(ent["entrevista_id"], opened["revision"], {"observaciones": "CCHH", "resumen_entrevista_tecnica": "Técnico v2 (otro usuario)"})
    r = save(c, opened, foco="cchh", observaciones="CCHH editado por el usuario CCHH")
    assert r.status_code == 200 and "cambió" in r.text.lower()                                                         # revisión vieja: no pisa nada
    assert stored(ent["entrevista_id"])["resumen_entrevista_tecnica"] == "Técnico v2 (otro usuario)" and stored(ent["entrevista_id"])["observaciones"] == "CCHH"
    fresh = stored(ent["entrevista_id"])
    save(c, fresh, foco="cchh", observaciones="CCHH editado")
    assert stored(ent["entrevista_id"])["resumen_entrevista_tecnica"] == "Técnico v2 (otro usuario)"


def test_without_context_the_general_behavior_is_unchanged(c):
    bid, pid, app_id, ent = interview_stage()
    ent = svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "A", "resumen_entrevista_tecnica": "B"})
    save(c, ent, observaciones="A2", resumen_entrevista_tecnica="B2")
    s = stored(ent["entrevista_id"])
    assert (s["observaciones"], s["resumen_entrevista_tecnica"]) == ("A2", "B2")
    save(c, s, observaciones="A3")                                                                                      # sin contexto, un campo ausente se vacía como siempre (no hay bloqueo)
    assert stored(ent["entrevista_id"])["resumen_entrevista_tecnica"] is None


def test_the_no_concretada_action_and_a_failed_save_also_respect_the_lock(c):
    bid, pid, app_id, ent = interview_stage()
    ent = svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "CCHH", "resumen_entrevista_tecnica": "Técnico"})
    r = c.post(f"/cchh/entrevistas/{ent['entrevista_id']}/no-concretada", data={"expected_revision": str(ent["revision"]), "foco": "cchh", "observaciones": "No se concretó: motivo"})
    s = stored(ent["entrevista_id"])
    assert s["concretada"] is False and s["observaciones"] == "No se concretó: motivo" and s["resumen_entrevista_tecnica"] == "Técnico"
    bad = c.post(f"/cchh/entrevistas/{ent['entrevista_id']}/editar", data={"expected_revision": str(s["revision"]), "foco": "cchh", "observaciones": "x", "area": "Área inexistente"})
    assert bad.status_code == 200 and "Técnico" in bad.text and "disabled" in textarea(bad.text, "ent-resumen")           # el re-render muestra lo PERSISTIDO en el campo bloqueado
    assert stored(ent["entrevista_id"])["resumen_entrevista_tecnica"] == "Técnico"


def test_the_service_contract_locks_fields_and_a_create_with_context_discards_the_blocked_field(c):
    bid, pid, app_id, ent = interview_stage()
    ent = svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "CCHH", "resumen_entrevista_tecnica": "Técnico"})
    out = svc.update_entrevista(ent["entrevista_id"], ent["revision"], {"observaciones": "nuevo", "resumen_entrevista_tecnica": "HACK"}, locked_fields=("resumen_entrevista_tecnica",))
    assert (out["observaciones"], out["resumen_entrevista_tecnica"]) == ("nuevo", "Técnico")
    bid2 = _manual()
    pid2, _ = _candidate(bid2, "Nora", "Alta")
    created = c.post("/cchh/entrevistas/nueva?return_to=%2Fcchh%2Fbusquedas%2F" + bid2, data={"profile_id": pid2, "busqueda_id": bid2, "foco": "cchh", "observaciones": "Solo CCHH", "resumen_entrevista_tecnica": "HACK"})
    assert created.status_code == 303 and "foco=cchh" in created.headers["location"]
    s = erepo.find_by_profile_and_busqueda(pid2, bid2)
    assert (s["observaciones"], s["resumen_entrevista_tecnica"]) == ("Solo CCHH", None)


# ═══════════════════ 4. pestaña Evaluación solo para APP_OWNER ═══════════════════
OTHER_HC = "otra.persona@danwor.com"


@pytest.fixture()
def two_hc_users(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{CCHH_EMAIL},{OTHER_HC}")
    monkeypatch.setattr(access, "APP_OWNER", CCHH_EMAIL)


def managed_process():
    bid, pid, app_id, ent = interview_stage()
    return bid, app_id


def test_non_owners_see_the_evaluation_tab_disabled_with_no_navigation(two_hc_users):
    bid, app_id = managed_process()
    page = session_client(OTHER_HC).get(f"/cchh/busquedas/{bid}").text
    assert re.search(r'<button type="button" disabled title="[^"]*" data-evaluation-locked>Evaluación</button>', page) and f"?vista=evaluacion" not in page.split("busqueda-views", 1)[1].split("</div>", 1)[0]
    assert '<button type="button" disabled title="Próximamente">Sugeridos</button>' in page                           # igual que las demás funcionalidades deshabilitadas
    owner = session_client(CCHH_EMAIL).get(f"/cchh/busquedas/{bid}").text
    assert f'<a href="/cchh/busquedas/{bid}?vista=evaluacion"' in owner and "data-evaluation-locked" not in owner


def test_every_evaluation_endpoint_is_protected_server_side_for_non_owners(two_hc_users):
    bid, app_id = managed_process()
    other, owner = session_client(OTHER_HC), session_client(CCHH_EMAIL)
    assert other.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh").status_code == 403
    assert other.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh", data={"expected_revision": "0"}).status_code == 403
    assert other.post(f"/cchh/busquedas/{bid}/scoring/jd", data={"jd_texto": "x", "palabras_clave": "y"}).status_code == 403
    assert other.post(f"/cchh/busquedas/{bid}/scoring/grilla", data={"expected_revision": "0"}).status_code == 403
    assert brepo.get(bid).get("jd_texto") in (None, "") and owner.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh").status_code == 200       # el dueño sí accede
    forced = other.get(f"/cchh/busquedas/{bid}?vista=evaluacion")                                                        # forzar la vista por URL: el servidor muestra el Pipeline, no la Evaluación
    assert forced.status_code == 200 and "scoring-step" not in forced.text and "busqueda-detail-body" in forced.text
    assert "scoring-step" in owner.get(f"/cchh/busquedas/{bid}?vista=evaluacion").text


def test_app_owner_is_not_content_owner_nor_the_sensitive_data_allowlist(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{CCHH_EMAIL},{OTHER_HC}")
    monkeypatch.setattr(access, "APP_OWNER", "el.dueno@danwor.com")
    monkeypatch.setattr(auth_service, "HC_SENSITIVE_DATA_ALLOWED_USERS", OTHER_HC)                                    # autorizado para datos sensibles, pero NO es APP_OWNER
    bid, app_id = managed_process()
    assert session_client(OTHER_HC).get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh").status_code == 403
    assert session_client(CCHH_EMAIL).get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh").status_code == 403


def test_the_general_interview_flow_is_not_blocked_for_non_owners(two_hc_users):
    bid, pid, app_id, ent = interview_stage()
    other = session_client(OTHER_HC)
    assert other.get("/cchh/entrevistas").status_code == 200 and other.get(f"/cchh/entrevistas/{ent['entrevista_id']}/editar?foco=cchh&return_to=%2Fcchh%2Fbusquedas%2F{bid}").status_code == 200
    assert other.get(f"/cchh/busquedas/{bid}").status_code == 200 and "busqueda-cand__interview" in _card(other.get(f"/cchh/busquedas/{bid}").text, "Mateo Biasotto")


# ═══════════════════ 5. orden por columna del Banco de CVs ═══════════════════
def seed_profiles(n=130):
    first = ["Zoe", "Ana", "Bruno", "Carla", "Diego", "Elena", "Fede", "Gaby", "Hugo", "Iris"]
    for i in range(n):
        profile_repository.create({"first_name": f"{first[i % 10]}{i:03d}", "last_name": f"Apellido{(n - i):03d}", "email": f"p{i}@danwor.com", "profile_skills": ("" if i % 7 == 0 else f"Skill {chr(65 + (i % 9))}"),
                                   "location": ("" if i % 11 == 0 else f"Ciudad {chr(75 - (i % 5))}"), "seniority": ""})


def names(html: str) -> list[str]:
    return [html_lib.unescape(m) for m in re.findall(r'<span class="hc-candidate-name dw-idcell__name">([^<]+)</span>', html)]


def test_the_sort_service_is_stable_puts_empties_last_in_both_directions_and_ignores_unknown_columns():
    rows = [{"first_name": "B", "last_name": "x", "location": "Zeta"}, {"first_name": "A", "last_name": "y", "location": ""}, {"first_name": "C", "last_name": "z", "location": "alfa"}, {"first_name": "D", "last_name": "w", "location": "Zeta"}]
    asc = [p["first_name"] for p in svc.sort_profiles(rows, "ubicacion", "asc")]
    desc = [p["first_name"] for p in svc.sort_profiles(rows, "ubicacion", "desc")]
    assert asc == ["C", "B", "D", "A"] and desc == ["B", "D", "C", "A"]                                                 # alfa < zeta; vacíos al final; empates en el orden original (estable)
    assert [p["first_name"] for p in svc.sort_profiles(rows, "nombre", "desc")] == ["D", "C", "B", "A"]
    assert svc.sort_profiles(rows, "no-existe", "asc") == rows and svc.sort_profiles(rows, "", "desc") == rows and svc.sort_profiles(rows, "nombre", "asc") is not rows


def test_sorting_applies_to_the_full_result_before_paginating(c):
    seed_profiles(130)
    asc_1 = names(c.get("/cchh/candidatos?sort=nombre&dir=asc&page_size=50").text)
    asc_2 = names(c.get("/cchh/candidatos?sort=nombre&dir=asc&page_size=50&page=2").text)
    asc_3 = names(c.get("/cchh/candidatos?sort=nombre&dir=asc&page_size=50&page=3").text)
    everything = asc_1 + asc_2 + asc_3
    assert len(everything) == 130 and everything == sorted(everything, key=lambda s: s.lower())                           # la última fila de la página 1 precede a la primera de la 2: se ordenó TODO
    d_1 = names(c.get("/cchh/candidatos?sort=nombre&dir=desc&page_size=50").text)
    assert d_1 == sorted(everything, key=lambda s: s.lower(), reverse=True)[:50]
    default = names(c.get("/cchh/candidatos?page_size=50").text)
    assert default != asc_1 and len(default) == 50                                                                      # sin orden: el de origen, sin cambios
    assert names(c.get("/cchh/candidatos?sort=inexistente&dir=asc&page_size=50").text) == default                         # un orden inválido se ignora


def test_sorting_keeps_search_filters_page_size_and_the_pager_state(c):
    seed_profiles(130)
    page = c.get("/cchh/candidatos?q=ciudad&seniority=&sort=ubicacion&dir=desc&page_size=50&page=2").text
    sort_urls = {k: html_lib.unescape(v) for k, v in re.findall(r'data-sort-key="([a-z]+)" hx-get="([^"]+)"', page)}
    assert set(sort_urls) == {"nombre", "skills", "seniority", "ubicacion", "fuente"}
    q_ub = parse_qs(urlparse(sort_urls["ubicacion"]).query)
    assert q_ub == {"q": ["ciudad"], "page_size": ["50"], "sort": ["ubicacion"], "dir": ["asc"]}                           # la columna activa invierte la dirección (desc → asc); conserva búsqueda y tamaño; vuelve a la página 1
    q_nombre = parse_qs(urlparse(sort_urls["nombre"]).query)
    assert q_nombre["dir"] == ["asc"] and q_nombre["sort"] == ["nombre"] and q_nombre["q"] == ["ciudad"] and "page" not in q_nombre   # otra columna vuelve a ascendente
    assert 'aria-sort="descending"' in page and page.count('aria-sort="none"') == 4 and re.search(r"Ubicación ▼</th>", page) and "dashboard-table-th--sortable" in page                  # señal discreta: columna y dirección activas
    nav = page.split('<nav class="activity-pager"', 1)[1].split("</nav>", 1)[0]
    links = [html_lib.unescape(u) for u in re.findall(r'hx-get="([^"]+)"', nav)]
    assert links and all("sort=ubicacion" in u and "dir=desc" in u and "q=ciudad" in u for u in links)                        # cada enlace del paginador conserva el orden
    assert 'name="sort" value="ubicacion"' in page and 'name="dir" value="desc"' in page                                  # y los filtros reactivos lo reenvían
    fragment = c.get("/cchh/candidatos?sort=ubicacion&dir=asc&page_size=50", headers={"HX-Request": "true"}).text
    assert "<html" not in fragment and 'id="hc-profiles-panel"' in fragment and 'aria-sort="ascending"' in fragment and "Ubicación ▲" in fragment
    loc = [names(c.get(f"/cchh/candidatos?sort=ubicacion&dir=asc&page_size=50&page={p}").text) for p in (1, 2, 3)]
    assert sum(len(x) for x in loc) == 130


def test_sorted_results_respect_the_filters_and_the_total_counter(c):
    seed_profiles(130)
    html = c.get("/cchh/candidatos?q=skill+a&sort=nombre&dir=asc&page_size=50").text
    total = int(re.search(r'dashboard-section__count">(\d+) candidatos?<', html).group(1))
    all_names = []
    for p in range(1, (total // 50) + 2):
        all_names += names(c.get(f"/cchh/candidatos?q=skill+a&sort=nombre&dir=asc&page_size=50&page={p}").text)
    assert len(all_names) == total and total < 130 and all_names == sorted(all_names, key=lambda s: s.lower())


# ═══════════════════ 6. autocompletado desactivado en la búsqueda libre ═══════════════════
SEARCH_INPUT = re.compile(r"<input\b[^>]*(?:name=\"q\"|type=\"search\"|id=\"hc-filter-location\")[^>]*>")


def test_free_search_inputs_disable_the_browser_autocomplete_on_the_rendered_pages(c, two_hc_users):
    pages = {"/cchh/candidatos": 2, "/cchh/entrevistas": 1, "/cchh/onboarding": 1, "/cchh/publicaciones": 1}
    for url, expected in pages.items():
        html = c.get(url).text
        found = SEARCH_INPUT.findall(html)
        assert len(found) >= expected, url
        assert all('autocomplete="off"' in tag for tag in found), (url, [t for t in found if 'autocomplete="off"' not in t])
    ops = session_client(CCHH_EMAIL).get("/operaciones/busquedas")
    assert ops.status_code in (200, 303, 403)


def test_every_free_search_input_in_the_sources_declares_autocomplete_off_and_data_entry_fields_do_not():
    offenders = []
    sources = [*TEMPLATES.rglob("*.html"), ROOT / "webapp" / "static" / "commercial.js"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for tag in SEARCH_INPUT.findall(text):
            if "autocomplete" not in tag:
                offenders.append(f"{path.name}: {tag[:90]}")
    assert offenders == []
    for rel in ("partials/human_capital_profile_fields.html",):                                                       # captura de datos: NO se toca
        assert 'autocomplete="off"' not in (TEMPLATES / rel).read_text(encoding="utf-8")
    login = "\n".join(p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*login*.html"))
    assert 'type="password"' not in login or 'autocomplete="off"' not in login.split('type="password"')[1].split(">")[0]


# ═══════════════════ retorno al Pipeline desde ambas evaluaciones ═══════════════════
@pytest.mark.parametrize("stage", ["Entrevista CCHH", "Entrevista Líder"])
@pytest.mark.parametrize("readded", [False, True])
def test_back_from_either_interview_tile_returns_to_the_pipeline_even_if_the_candidate_was_removed_and_readded(c, stage, readded):
    """La entrevista es única por (candidato, proceso) y NO se borra al quitar la postulación: si el candidato vuelve a agregarse conserva el `application_id` anterior. «Volver» y el redirect de
    Guardar siguen yendo al Pipeline de origen (nunca al listado de Entrevistas)."""
    bid = _manual()
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, stage, None, actor=CCHH_EMAIL)
    if readded:
        svc.remove_application(bid, app_id, actor=CCHH_EMAIL)
        app_id = svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]
        svc.move_application_stage(bid, app_id, stage, None, actor=CCHH_EMAIL)
        assert [e["application_id"] for e in erepo.list()] != [app_id]                    # la entrevista conserva la postulación anterior (el caso que antes rompía el retorno)
    ctx = f"/cchh/busquedas/{bid}"
    link = _card_link(c.get(ctx).text, "Mateo Biasotto", "busqueda-cand__interview")
    page = c.get(link).text
    assert f'<a class="catalog-back" href="{ctx}">← Volver a Pipeline de la búsqueda</a>' in page
    assert re.search(r'formaction="/cchh/entrevistas/[^"]+/no-concretada\?return_to=' + re.escape(quote(ctx, safe="")) + '"', page) or "No se concretó" not in page      # la edición no tiene «Cancelar»; «No se concretó» conserva el contexto
    entrevista = erepo.list()[0]
    save = c.post(re.search(r'<form method="post" action="([^"]+)"', page).group(1), data={"fecha_entrevista": "2026-10-21", "puesto_evaluado": "SA", "area": "SAP", "expected_revision": str(entrevista["revision"])})
    assert save.status_code == 303 and save.headers["location"] == ctx


def test_a_return_context_naming_another_candidate_is_still_rejected(c):
    """La relajación es por candidato: un `return_to` de OTRA búsqueda sigue cayendo al listado independiente."""
    bid = _manual()
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    other = _manual()
    entrevista = erepo.list()[0]
    page = c.get(f"/cchh/entrevistas/{entrevista['entrevista_id']}/editar?return_to=%2Fcchh%2Fbusquedas%2F{other}").text
    assert '<a class="catalog-back" href="/cchh/entrevistas">← Volver a Entrevistas</a>' in page
