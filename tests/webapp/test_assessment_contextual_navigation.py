"""
test_assessment_contextual_navigation.py — tabla de controles integrada + navegación contextual del informe de Assessment.

Contratos que fija (presentación pura; el algoritmo, el Juicio, la confianza y las exportaciones JSON/Markdown no cambian):
  * cada área muestra UNA tabla de controles (Regla · Resultado · Hallazgos) con la cantidad de hallazgos y «Ver detalle»; la lista redundante
    «Hallazgos en esta área» desaparece cuando sus hallazgos ya están en la tabla;
  * un hallazgo sin regla (p. ej. una señal descriptiva) NO se pierde: queda en «Otros hallazgos del área», compacta y navegable;
  * la relación regla↔hallazgo sale de la identidad canónica (``rule`` == ``id`` del grupo), nunca del título;
  * sin destino renderizado no se emite enlace; ningún ``#asr-…`` queda huérfano; cada detalle trae su enlace de regreso al origen;
  * el mismo contrato vale para el informe del Catálogo y para el ad-hoc (misma plantilla);
  * el cliente (`DwAssessmentNav`, app.js) abre los <details> ancestros, desplaza, resalta y enfoca usando el #hash nativo (ver el arnés de Node).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("assessment_contextual_navigation.node.js")
ORIGIN = {"Origin": "http://testserver"}
HX = {"HX-Request": "true"}
ACME = ["1VR_RAW", "3VF_P", "4AM_ACME", "RAW_T"]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    inbound = write_inbound(tmp_path / "pool")
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "runtime-cache")
    entries = [cs.CatalogEntry(path="x", cliente="Acme", dominio="Dom", veredicto="plena", apto="Referencia reutilizable", entry_id="acme-4am", artefactos=list(ACME))]
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    svc.reset_memory_cache()
    svc.invalidate_pool_index()
    yield entries
    svc.reset_memory_cache()
    svc.invalidate_pool_index()


def _client():
    session = auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"})
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


class _Dom(HTMLParser):
    """Ids y enlaces ``#asr-…`` del HTML renderizado (con su atributo ``data-asr-return``)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag == "a" and (a.get("href") or "").startswith("#asr-"):
            self.links.append({"href": a["href"], "ret": "data-asr-return" in a, "class": a.get("class"), "style": a.get("style")})


def html_escape(text: str) -> str:
    import html as _h
    return _h.escape(text, quote=True)


def _dom(html: str) -> _Dom:
    d = _Dom()
    d.feed(html)
    return d


def _report(world):
    c = _client()
    html = c.post("/catalog/flow/assessment?flow=acme-4am", headers={**ORIGIN, **HX}).text
    status = svc.status_for_entry(world[0])
    return html, status, presenter.report_view(status, world[0], "datasphere_core")


# ── presentador: identidad canónica, cardinalidad, residuales ───────────────────────────────────────────────────────────────────────────────────
def _group(gid, title, area, kind="OBSERVATION", n=2):
    return {"id": gid, "title": title, "area": area, "kind": kind, "occurrence_count": n}


def _area(finding_groups, area="NAMING"):
    return {"id": area, "macro_dimension": "GOVERNANCE", "assessability": "ASSESSED", "reason": None, "highlights": [], "limitations_not_observable": False,
            "required_surfaces": {"assessed": 1, "partially_assessed": 0, "not_assessed": 0, "not_applicable": 0}, "finding_groups": finding_groups}


def _rule(rule, title, area="NAMING", result="PATTERN_FOUND", n=0):
    return {"rule": rule, "title": title, "area": area, "result": result, "assessability": "ASSESSED", "finding_count": n, "reason": None}


def test_rule_to_finding_relation_is_the_canonical_identity_never_the_title():
    groups = {"R_REAL": _group("R_REAL", "Título A", "NAMING"), "OTRO": _group("OTRO", "Mismo título", "NAMING")}
    # el título de la regla coincide con el de OTRO grupo, pero la identidad es R_REAL → enlaza a R_REAL, no a OTRO
    v = presenter._area_view(_area(["R_REAL", "OTRO"]), groups, {"NAMING": [_rule("R_REAL", "Mismo título", n=2)]})
    assert [r["finding"]["dom_id"] for r in v["rules"]] == ["asr-g-R_REAL"]
    # mismo título que un grupo, pero la regla no produce ningún grupo del área → sin hallazgo ni enlace
    v = presenter._area_view(_area(["OTRO"]), groups, {"NAMING": [_rule("SIN_GRUPO", "Mismo título")]})
    assert v["rules"][0]["finding"] is None and [f["dom_id"] for f in v["findings_residual"]] == ["asr-g-OTRO"]


def test_findings_without_a_rule_are_preserved_as_residual_and_the_rest_are_not_repeated():
    groups = {"R1": _group("R1", "Con regla", "NAMING"), "SOURCE_FILTER_PRESENT": _group("SOURCE_FILTER_PRESENT", "Filtro en la fuente", "NAMING", "DESCRIPTIVE", 1)}
    v = presenter._area_view(_area(["R1", "SOURCE_FILTER_PRESENT"]), groups, {"NAMING": [_rule("R1", "Control 1", n=2)]})
    assert [f["dom_id"] for f in v["findings_residual"]] == ["asr-g-SOURCE_FILTER_PRESENT"]                    # el descriptivo NO se pierde
    assert {f["dom_id"] for f in v["findings"]} == {"asr-g-R1", "asr-g-SOURCE_FILTER_PRESENT"}                 # el conteo del encabezado del área no cambia
    assert v["rules"][0]["finding"] == {"dom_id": "asr-g-R1", "count": 2, "kind_text": presenter.P.finding_kind_text("OBSERVATION")}
    assert v["rules"][0]["dom_id"] == "asr-r-R1"


def test_group_origins_point_to_the_control_row_or_the_residual_item_and_only_when_they_exist():
    groups = {"R1": _group("R1", "Con regla", "NAMING"), "DESC": _group("DESC", "Descriptivo", "NAMING", "DESCRIPTIVE", 1)}
    area = presenter._area_view(_area(["R1", "DESC"]), groups, {"NAMING": [_rule("R1", "Control 1", n=2), _rule("LIMPIA", "Sin hallazgos", result="ASSESSED_ABSENCE")]})
    origins = presenter._group_origins([area])
    assert origins["R1"]["dom_id"] == "asr-r-R1" and origins["DESC"]["dom_id"] == "asr-f-DESC"
    assert set(origins) == {"R1", "DESC"} and all(o["label"] == f"Volver a «{area['title']}»" for o in origins.values())   # la regla limpia no origina nada


# ── informe renderizado del Catálogo ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_catalog_report_has_one_integrated_table_per_area_and_no_redundant_findings_list(world):
    html, status, v = _report(world)
    assert "Hallazgos en esta área" not in html
    assert " data-asr-report" in html
    assert any(r["finding"] for a in v["areas"] for r in a["rules"])                                           # el escenario ejercita filas CON hallazgo
    for a in v["areas"]:
        for r in a["rules"]:
            row = re.search(rf'<tr id="{re.escape(r["dom_id"])}">(.*?)</tr>', html, re.S)
            assert row, r["rule"]
            if r["finding"]:
                n = r["finding"]["count"]
                cell = row.group(1)
                assert f'{n} ocurrencia{"s" if n != 1 else ""} ({r["finding"]["kind_text"]})' in cell
                # acción ICÓNICA (patrón CCHH): <a href> semántico + primitivos compartidos + nombre accesible específico + tooltip contextual + SVG decorativo
                act = re.search(r'<a href="#asr-g-[^"]+" class="table-action-btn chart-tooltip-anchor" aria-label="([^"]+)">(.*?)</a>', cell, re.S)
                assert act and f'href="#{r["finding"]["dom_id"]}"' in cell, r["rule"]
                assert act.group(1) == html_escape(f'Ver evidencia del hallazgo «{r["title"]}»')                  # el nombre accesible NO depende del tooltip y es único por fila
                assert '<svg viewBox="0 0 24 24" aria-hidden="true">' in act.group(2)
                assert '<span class="chart-tooltip" aria-hidden="true"><p>Ver evidencia del hallazgo</p></span>' in act.group(2)
                assert "Ver detalle" not in cell                                                                       # la etiqueta de texto genérica ya no se muestra
            else:
                assert "table-action-btn" not in row.group(1)                                                       # sin destino no hay acción
    for t in re.findall(r"<table>\s*<thead><tr><th>Regla</th><th>Resultado</th><th>Hallazgos</th></tr>", html):
        assert t                                                                                               # tabla integrada de 3 columnas
    actions = len(re.findall(r'class="table-action-btn chart-tooltip-anchor"', html))
    assert actions == sum(1 for a in v["areas"] for r in a["rules"] if r["finding"])                            # cardinalidad: una acción por regla con hallazgo
    assert "<code>Ver detalle</code>" not in html and ">Ver detalle<" not in html


def test_every_group_is_reachable_from_its_area_and_nothing_is_orphaned(world):
    html, status, v = _report(world)
    d = _dom(html)
    gids = {g["id"] for g in (*v["observations"], *v["review_required"], *v["descriptive"])}
    from_area = {a_f["dom_id"][len("asr-g-"):] for a in v["areas"] for a_f in a["findings"]}
    inbound = {l["href"][len("#asr-g-"):] for l in d.links if l["href"].startswith("#asr-g-") and not l["ret"]}
    assert from_area <= gids and gids <= inbound                                                                # todo hallazgo del informe se alcanza desde su área
    assert [l["href"] for l in d.links if l["href"][1:] not in d.ids] == []                                     # ni un solo enlace huérfano
    content = [l for l in d.links if l["class"] != "table-action-btn chart-tooltip-anchor"]
    assert content and all(l["class"] is None and l["style"] is None for l in content)                          # enlaces de CONTENIDO (nombre/objeto): sin clase ni estilo propios
    assert all(l["style"] is None for l in d.links)


def test_each_finding_detail_links_back_to_its_origin_and_the_origin_links_forward(world):
    html, status, v = _report(world)
    d = _dom(html)
    groups = [*v["observations"], *v["review_required"], *v["descriptive"]]
    assert groups
    for g in groups:
        if not g["origin"]:
            continue
        assert g["origin"]["dom_id"] in d.ids
        det = re.search(rf'<details class="dashboard-section" id="{re.escape(g["dom_id"])}">(.*?)</details>', html, re.S).group(1)
        assert f'<a href="#{g["origin"]["dom_id"]}" data-asr-return><code>{g["origin"]["label"]}</code></a>' in det
        origin = re.search(rf'id="{re.escape(g["origin"]["dom_id"])}"[^>]*>(.*?)(?:</tr>|</li>)', html, re.S).group(1)
        assert f'href="#{g["dom_id"]}"' in origin                                                              # ida y vuelta son simétricas
    assert sum(1 for g in groups if g["origin"]) == len(re.findall(r"data-asr-return", html))


def test_clean_controls_stay_available_in_the_collapsed_disclosure_with_their_results(world):
    html, status, v = _report(world)
    split = [a for a in v["areas"] if a["rules_split"]]
    assert split
    for a in split:
        assert len(a["rules_open"]) + len(a["rules_clean"]) == len(a["rules"])                                  # nada se pierde al repartir
        for r in a["rules_clean"]:
            row = re.search(rf'<tr id="{re.escape(r["dom_id"])}">(.*?)</tr>', html, re.S).group(1)
            assert r["title"] in row and r["result_text"] in row and "Ver detalle" not in row
        if a["rules_clean"]:
            seg = html[html.index(f'id="{a["dom_id"]}"'):]
            assert '<details class="dashboard-section">' in seg and "Controles sin hallazgos" in seg
    assert '<details class="dashboard-section" open>' not in re.search(r"Controles sin hallazgos.*?</details>", html, re.S).group(0)   # plegado por defecto


def test_every_rule_remains_visible_with_its_result(world):
    html, status, v = _report(world)
    for r in status.profile["rule_results"]:
        assert r["title"] in html
    assert html.count("<th>Resultado</th><th>Hallazgos</th>") >= 1                                              # la tabla técnica de cierre conserva su forma


def test_exports_are_unchanged_by_the_presentation(world):
    c = _client()
    c.post("/catalog/flow/assessment?flow=acme-4am", headers={**ORIGIN, **HX})
    md = c.get("/catalog/flow/assessment.md?flow=acme-4am").text
    assert "Hallazgos en esta área (detalle en *Observaciones* y *Casos a revisar*):" in md or "Hallazgos en esta área" not in md
    assert "data-asr" not in md and "asr-r-" not in md and "asr-f-" not in md
    js = c.get("/catalog/flow/assessment.json?flow=acme-4am").text
    assert "asr-r-" not in js and "data-asr" not in js


def test_every_finding_target_sits_only_inside_native_details_containers(world):
    """Los destinos `asr-g-*` solo pueden estar dentro de contenedores colapsables que `DwAssessmentNav.reveal` sabe abrir (<details>): ni `hidden`, ni
    Alpine `x-show`, ni ARIA `aria-expanded` sobre un ancestro. Si algún día aparece otro tipo de colapsable en el camino, este test lo señala."""
    html, status, v = _report(world)

    class Chain(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.bad, self.targets = [], [], 0

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag in ("br", "img", "input", "meta", "link", "hr", "path", "circle"):
                return
            self.stack.append((tag, a))
            if (a.get("id") or "").startswith("asr-g-"):
                self.targets += 1
                for t, aa in self.stack[:-1]:
                    if t != "details" and ("hidden" in aa or "x-show" in aa or "aria-expanded" in aa or "data-collapsed" in aa):
                        self.bad.append((a["id"], t, sorted(aa)))

        def handle_endtag(self, tag):
            while self.stack:
                if self.stack.pop()[0] == tag:
                    break

    c = Chain()
    c.feed(html)
    assert c.targets and c.bad == []


# ── cliente: arnés de Node (sin jsdom; mismo patrón que catalog_nav_states) ────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(shutil.which("node") is None, reason="node no está disponible")
def test_client_navigation_harness():
    r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
