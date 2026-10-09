"""
test_lob_surface_consistency.py — la Línea de negocio EFECTIVA en las superficies transversales del Catálogo.

Causa de las dos omisiones que fija:
  * Informe de Cliente (tabla Artefactos): ``client_assessment_portfolio._identity`` agregaba ``entry.dominio`` (agrupación LEGACY: «Ventas», «Stock / Inventario»,
    «General»…) bajo el nombre del artefacto como si fuera su clasificación. Ahora bajo el nombre va la Línea de negocio efectiva.
  * Cabecera del detalle del flujo: ``catalog_flow_content.html`` imprimía ``Catálogo Danwor · {cliente}`` sin ninguna dimensión de negocio.
    Ahora: ``Catálogo Danwor · {Línea de negocio efectiva} · {cliente}``.
Ambas leen el MISMO resolver efectivo que la navegación (``dcs.effective_lob_by_entry``: manual > automática HIGH > Sin clasificar, de la v2 PERSISTIDA); una sugerencia
MEDIUM jamás se muestra como asignación; ``entry.dominio`` jamás es fallback; el informe se arma en cada pedido (no hay snapshot persistido que quede obsoleto tras un override).
Business Classification es contexto de negocio: no altera adopción, cobertura, confianza ni Juicio. Fixtures SINTÉTICAS.
"""
from __future__ import annotations

import html as _html
import re
import types

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import business_classification as BC
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as pf
from webapp.services import domain_classification_service as dcs

HX = {"HX-Request": "true"}
PACK = "datasphere_core"
V2 = BC.BUSINESS_CLASSIFICATION_VERSION
RAW_GL = "4AM_FNC_GLACCOUNTLINEITRAWDATA"


def _flow(root, dom_dir, dominio, am, *, manual_lob=None, extra_manual=""):
    d = root / "Kensing" / dom_dir
    d.mkdir(parents=True)
    manual = "manual:\n  cliente: Kensing\n" + (f"  linea_de_negocio: {manual_lob}\n" if manual_lob else "") + extra_manual
    (d / f"flujo_{dom_dir}.md").write_text(f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n  - {am}\n{manual}---\nCuerpo.\n", encoding="utf-8")


def _bc(lob=None, *, status=BC.CLASSIFIED, confidence="HIGH"):
    return {"contract_version": V2, "status": status, "confidence": confidence,
            "line_of_business": ({"id": lob, "label": BC.lob_label(lob)} if lob else None), "domain": None}


def _status(entry, business, am):
    return svc.AssessmentStatus("NOT_YET_ASSESSED", entry.entry_id, entry.cliente, am, business_classification=business)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    # El dominio LEGACY de cada flujo es deliberadamente distinto de su línea de negocio efectiva (y no es una etiqueta de la taxonomía, salvo «Ventas»).
    _flow(tmp_path, "RawGL", "Stock / Inventario", RAW_GL)                                                 # HIGH FINANCE
    _flow(tmp_path, "Medio", "General", "4AM_MEDIO")                                                       # MEDIUM SALES (sugerencia)
    _flow(tmp_path, "Indet", "Compras", "4AM_INDET")                                                       # UNDETERMINED
    _flow(tmp_path, "Manual", "Ventas", "4AM_MANUAL", manual_lob="SUPPLY_CHAIN",                           # manual sobre HIGH FINANCE + resto del bloque manual
          extra_manual="  intent_negocio: Conciliar mayor\n  veredicto: plena\n")
    entries = cs.load_catalog(str(tmp_path))
    by = {e.path.split("flujo_")[1][:-3]: e for e in entries}
    businesses = {"RawGL": _bc("FINANCE"), "Medio": _bc("SALES", status=BC.SUGGESTED, confidence="MEDIUM"),
                  "Indet": _bc(None, status=BC.UNDETERMINED, confidence="LOW"), "Manual": _bc("FINANCE")}
    ams = {"RawGL": RAW_GL, "Medio": "4AM_MEDIO", "Indet": "4AM_INDET", "Manual": "4AM_MANUAL"}
    statuses = {by[k].entry_id: _status(by[k], businesses[k], ams[k]) for k in by}
    reads = []

    def fake_statuses(batch):
        batch = list(batch)
        reads.append([x.entry_id for x in batch])
        return {x.entry_id: statuses[x.entry_id] for x in batch}
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(svc, "statuses_for_entries", fake_statuses)
    return types.SimpleNamespace(by=by, statuses=statuses, reads=reads, root=tmp_path)


def _client():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _report(world, key="RawGL"):
    return _client().get(f"/catalog/client/assessment?flow={world.by[key].entry_id}&pack={PACK}", headers=HX).text


def _rows(html):
    """entry_id → (texto de la línea de negocio bajo el artefacto, fuente) del Informe de Cliente."""
    out = {}
    for m in re.finditer(r'<tr data-entry="([^"]+)".*?</tr>', html, re.S):
        lob = re.search(r'<span class="catalog-note__ev" data-lob-source="([^"]+)">([^<]*)</span>', m.group(0))
        out[m.group(1)] = (_html.unescape(lob.group(2)), lob.group(1)) if lob else None
    return out


def _eyebrow(world, key):
    html = _client().get(f"/catalog/flow?flow={world.by[key].entry_id}&pack={PACK}", headers=HX).text
    return _html.unescape(re.search(r'<p class="eyebrow">(Catálogo Danwor[^<]*)</p>', html).group(1)).strip()


# ── 1. Informe de Cliente ───────────────────────────────────────────────────
def test_client_report_shows_the_effective_lob_under_each_artifact_and_no_legacy_grouping(world):
    html = _report(world)
    rows = _rows(html)
    got = {k: rows[world.by[k].entry_id] for k in world.by}
    assert got == {"RawGL": ("Finanzas", "AUTOMATIC"), "Medio": ("Sin clasificar", "UNCLASSIFIED"), "Indet": ("Sin clasificar", "UNCLASSIFIED"),
                   "Manual": ("Cadena de suministro", "MANUAL")}
    text = re.sub(r"<[^>]+>", " ", html)
    for legacy in ("Stock / Inventario", "General"):                      # agrupaciones legacy: ya no representan la clasificación
        assert legacy not in text, legacy
    assert world.by["Manual"].dominio == "Ventas" and got["Manual"][0] != "Ventas"      # «Ventas» legacy no se cuela aunque coincida con una etiqueta de la taxonomía


def test_medium_suggestion_is_never_presented_as_the_effective_lob(world):
    html = _report(world)
    assert _rows(html)[world.by["Medio"].entry_id] == ("Sin clasificar", "UNCLASSIFIED")
    assert "Ventas" not in re.sub(r"<[^>]+>", " ", html).replace("Ventas</a>", "")       # la sugerencia SALES de «Medio» no aparece como clasificación


def test_exports_come_from_the_same_snapshot_and_carry_the_effective_lob(world):
    c, fid = _client(), world.by["RawGL"].entry_id
    snap = c.get(f"/catalog/client/assessment.json?flow={fid}").json()
    by_id = {r["entry_id"]: r for r in snap["assets"]}
    assert by_id[fid]["line_of_business"] == {"label": "Finanzas", "source": "AUTOMATIC"}
    assert by_id[world.by["Medio"].entry_id]["line_of_business"] == {"label": "Sin clasificar", "source": "UNCLASSIFIED"}
    assert all("artifact_scope" not in r for r in snap["assets"])           # el dominio legacy dejó de viajar como «scope» del artefacto
    md = c.get(f"/catalog/client/assessment.md?flow={fid}").text
    assert f"{RAW_GL} (Finanzas)" in md and "(Sin clasificar)" in md and "Stock / Inventario" not in md


def test_a_resolver_batch_failure_is_reported_as_unavailable_never_as_unclassified(world, monkeypatch):
    monkeypatch.setattr(dcs, "effective_lob_by_entry", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rows = _rows(_report(world))
    assert set(rows.values()) == {("Clasificación no disponible", "UNAVAILABLE")}


def test_the_report_reads_statuses_once_and_does_not_derive_or_write(world, monkeypatch):
    monkeypatch.setattr(dcs, "derive_business_for_entry", lambda *a, **k: pytest.fail("el informe NO deriva la clasificación"))
    before = {k: v.business_classification for k, v in world.statuses.items()}
    _report(world)
    assert len(world.reads) == 1                                             # la misma lectura por lote alimenta estados y línea de negocio
    assert {k: v.business_classification for k, v in world.statuses.items()} == before


# ── 2. Cabecera del detalle ─────────────────────────────────────────────────
def test_flow_header_is_catalogo_danwor_lob_client_for_kensing_raw_gl(world):
    assert _eyebrow(world, "RawGL") == "Catálogo Danwor · Finanzas · Kensing"


def test_flow_header_without_effective_classification_says_sin_clasificar_and_ignores_the_suggestion(world):
    assert _eyebrow(world, "Medio") == "Catálogo Danwor · Sin clasificar · Kensing"
    assert _eyebrow(world, "Indet") == "Catálogo Danwor · Sin clasificar · Kensing"


def test_flow_header_uses_labels_not_technical_codes(world):
    for key in world.by:
        h = _eyebrow(world, key)
        assert not re.search(r"\b(FINANCE|SALES|SUPPLY_CHAIN|UNCLASSIFIED|MANUFACTURING|HUMAN_RESOURCES)\b", h), h


def test_header_falls_back_to_unavailable_when_the_assessment_header_cannot_be_built(world, monkeypatch):
    monkeypatch.setattr(presenter, "header_for", lambda e, p: None)
    assert _eyebrow(world, "RawGL") == "Catálogo Danwor · Clasificación no disponible · Kensing"


# ── 3. Override manual: ambas superficies, sin tocar el assessment ─────────
def test_manual_override_is_reflected_on_both_surfaces_and_preserves_the_rest_of_the_manual_block(world):
    assert _eyebrow(world, "Manual") == "Catálogo Danwor · Cadena de suministro · Kensing"
    assert _rows(_report(world, "Manual"))[world.by["Manual"].entry_id][0] == "Cadena de suministro"
    text = world.by["Manual"].path
    body = open(text, encoding="utf-8").read()
    assert "intent_negocio: Conciliar mayor" in body and "veredicto: plena" in body        # el bloque manual no se toca (solo lectura)


def test_changing_the_manual_override_changes_both_surfaces_without_any_cache_and_leaves_adoption_untouched(world):
    adoption_before = re.findall(r'catalog-card__verdict[^>]*>([^<]*)<', _report(world, "RawGL"))
    path = world.by["RawGL"].path
    src = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(src.replace("manual:\n  cliente: Kensing\n", "manual:\n  cliente: Kensing\n  linea_de_negocio: SALES\n"))
    assert _eyebrow(world, "RawGL") == "Catálogo Danwor · Ventas · Kensing"
    after = _report(world, "RawGL")
    assert _rows(after)[world.by["RawGL"].entry_id] == ("Ventas", "MANUAL")                # sin snapshot persistido: el override se ve en el próximo pedido
    assert re.findall(r'catalog-card__verdict[^>]*>([^<]*)<', after) == adoption_before    # Business Classification no es adopción metodológica


def test_business_classification_does_not_change_methodological_columns(world):
    html = _report(world)
    row = re.search(rf'<tr data-entry="{re.escape(world.by["RawGL"].entry_id)}".*?</tr>', html, re.S).group(0)
    cells = re.findall(r"<td>(.*?)</td>", row, re.S)
    assert "Finanzas" not in " ".join(cells[1:])                              # la línea de negocio vive solo bajo el nombre; estado/cobertura/confianza/adopción no la mezclan


# ── presentación: traducción única ─────────────────────────────────────────
@pytest.mark.parametrize("effective,expected", [
    ({"available": True, "source": "AUTOMATIC", "label": "Finanzas"}, {"label": "Finanzas", "source": "AUTOMATIC"}),
    ({"available": True, "source": "MANUAL", "label": "Ventas"}, {"label": "Ventas", "source": "MANUAL"}),
    ({"available": True, "source": "UNCLASSIFIED", "label": None, "suggestion": {"id": "SALES", "label": "Ventas"}}, {"label": "Sin clasificar", "source": "UNCLASSIFIED"}),
    ({"available": False, "source": dcs.UNAVAILABLE, "label": None}, {"label": "Clasificación no disponible", "source": dcs.UNAVAILABLE}),
    (None, {"label": "Clasificación no disponible", "source": dcs.UNAVAILABLE}),
])
def test_lob_display_is_the_single_effective_to_text_translation(effective, expected):
    assert presenter.lob_display(effective) == expected


def test_entry_dominio_is_never_a_lob_fallback_in_the_new_code():
    import inspect
    for fn in (presenter.lob_display, presenter.lob_for):
        assert "dominio" not in inspect.getsource(fn)


def test_header_reuses_the_status_already_read_and_adds_no_extra_read(world, monkeypatch):
    c, fid = _client(), world.by["RawGL"].entry_id
    c.get(f"/catalog/flow?flow={fid}&pack={PACK}", headers=HX)
    with_lob = len(world.reads)
    world.reads.clear()
    monkeypatch.setattr(presenter, "lob_for", lambda entry, status: presenter.lob_display(None))
    c.get(f"/catalog/flow?flow={fid}&pack={PACK}", headers=HX)
    assert with_lob == len(world.reads)                                       # la línea de negocio sale del MISMO estado que la cabecera de assessment
