"""Cabecera del detalle del flujo: el eyebrow muestra SOLO la línea de negocio EFECTIVA y el cliente; el dominio LEGACY nunca la complementa ni la reemplaza.

Tres identidades que no se mezclan: línea de negocio efectiva (eyebrow), dominio legacy («Stock / Inventario», agrupación histórica del Catálogo) y nombre propio del modelo (h1).
Regla uniforme para modelos clasificados, no clasificados y con/sin nombre propio (sin casos por cliente). No toca el motor de clasificación, el scoring, el assessment ni la persistencia."""
from __future__ import annotations

import html as _html
import re
import types

import pytest

import webapp.main as main
from tools.catalog import business_classification as BC
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads
from tests.webapp.test_lob_surface_consistency import HX, PACK, _bc, _client, _flow, _status

DOMINIO = "Stock / Inventario"


def _label_view(root, dom_dir, am, label):
    d = root / "Kensing" / dom_dir / "vistas"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{am}.md").write_text(f"---\nauto:\n  label: {label}\n---\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    _flow(tmp_path, "Clasif", DOMINIO, "AM_CLASIFICADO")                                              # clasificado HIGH + modelo con nombre propio
    _label_view(tmp_path, "Clasif", "AM_CLASIFICADO", "Modelo analítico - Cuentas por Cobrar")
    _flow(tmp_path, "SinClasif", DOMINIO, "AM_OCUPACION_ALMACENES")                                    # sin clasificar + modelo con nombre propio (el caso reportado)
    _label_view(tmp_path, "SinClasif", "AM_OCUPACION_ALMACENES", "Modelo analítico - Ocupación de Almacenes")
    _flow(tmp_path, "SinNombre", "General", "4MA_PEDIDOS_ENTREGAS")                                     # sin clasificar + modelo SIN nombre de negocio (caso Illolay: el dominio legacy es «General»)
    _flow(tmp_path, "SinModelo", "Compras", "3VF_SIN_MODELO_TERMINAL")                                  # sin ningún modelo terminal identificable
    _flow(tmp_path, "Manual", DOMINIO, "AM_MANUAL", manual_lob="SUPPLY_CHAIN")                          # asignación manual sobre una clasificación automática
    _label_view(tmp_path, "Manual", "AM_MANUAL", "Modelo analítico - Despachos")
    entries = cs.load_catalog(str(tmp_path))
    by = {e.path.split("flujo_")[1][:-3]: e for e in entries}
    businesses = {"Clasif": _bc("FINANCE"), "SinClasif": _bc(None, status=BC.UNDETERMINED, confidence="LOW"),
                  "SinNombre": _bc(None, status=BC.UNDETERMINED, confidence="LOW"), "SinModelo": _bc(None, status=BC.UNDETERMINED, confidence="LOW"), "Manual": _bc("FINANCE")}
    ams = {"Clasif": "AM_CLASIFICADO", "SinClasif": "AM_OCUPACION_ALMACENES", "SinNombre": "4MA_PEDIDOS_ENTREGAS", "SinModelo": None, "Manual": "AM_MANUAL"}
    statuses = {by[k].entry_id: _status(by[k], businesses[k], ams[k]) for k in by}
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(svc, "statuses_for_entries", lambda batch: {x.entry_id: statuses[x.entry_id] for x in batch})
    return types.SimpleNamespace(by=by)


def _page(world, key):
    html = _client().get(f"/catalog/flow?flow={world.by[key].entry_id}&pack={PACK}", headers=HX).text
    eyebrow = _html.unescape(re.search(r'<p class="eyebrow">(Catálogo Danwor[^<]*)</p>', html).group(1)).strip()
    h1 = _html.unescape(re.search(r"<h1[^>]*>([^<]*)</h1>", html).group(1)).strip()
    return eyebrow, h1


def test_the_eyebrow_is_only_the_effective_line_of_business_and_the_client(world):
    assert _page(world, "Clasif")[0] == "Catálogo Danwor · Finanzas · Kensing"
    assert _page(world, "SinClasif")[0] == "Catálogo Danwor · Sin clasificar · Kensing"
    assert _page(world, "Manual")[0] == "Catálogo Danwor · Cadena de suministro · Kensing"                 # la asignación manual manda


def test_the_legacy_domain_never_appears_in_the_eyebrow_classified_or_not(world):
    for key in world.by:
        eyebrow, _h1 = _page(world, key)
        assert DOMINIO not in eyebrow and "Stock" not in eyebrow, (key, eyebrow)


def test_the_title_is_the_terminal_models_identity_never_a_legacy_domain_label(world):
    assert _page(world, "SinClasif")[1] == "Ocupación de Almacenes"                                          # 1. nombre de negocio específico
    assert _page(world, "Clasif")[1] == "Cuentas por Cobrar" and _page(world, "Manual")[1] == "Despachos"
    assert _page(world, "SinNombre")[1] == "Pedidos Entregas"                                                # 2. nombre técnico legible (NO «General», el dominio legacy)
    assert "General" not in _page(world, "SinNombre")[0]


def test_without_an_identifiable_terminal_model_the_title_is_a_neutral_fallback_never_the_domain(world):
    eyebrow, h1 = _page(world, "SinModelo")
    assert h1 == "Flujo sin modelo terminal" and "Compras" not in eyebrow and "Compras" not in h1             # el dominio legacy queda como metadato interno
    assert world.by["SinModelo"].dominio == "Compras"                                                        # ...y se conserva intacto en la entrada del Catálogo


def test_the_page_title_follows_the_same_identity(world):
    html = _client().get(f"/catalog/flow?flow={world.by['SinClasif'].entry_id}&pack={PACK}").text
    assert "<title>Kensing · Ocupación de Almacenes · Catálogo Danwor</title>" in _html.unescape(html).replace("\n", "")


def test_unclassified_is_not_complemented_with_the_domain_and_matches_the_classified_structure(world):
    classified, unclassified = _page(world, "Clasif")[0], _page(world, "SinClasif")[0]
    assert classified.split(" · ")[0::2] == ["Catálogo Danwor", "Kensing"] and unclassified.split(" · ")[0::2] == ["Catálogo Danwor", "Kensing"]    # misma estructura: marca · LoB · cliente
    assert len(classified.split(" · ")) == len(unclassified.split(" · ")) == 3


# ── consistencia con el informe de Assessment (mismo resolver; el identificador técnico sigue visible; el documento canónico no se toca) ──────────────────
def _report(world, key):
    return _html.unescape(_client().get(f"/catalog/flow/assessment?flow={world.by[key].entry_id}&pack={PACK}", headers=HX).text)


def test_the_assessment_report_uses_the_same_readable_name_and_keeps_the_technical_id_visible(world):
    html = _report(world, "SinNombre")
    assert re.search(r'<h1 class="asr-title">Pedidos Entregas</h1>', html)
    assert "<code>4MA_PEDIDOS_ENTREGAS</code>" in html                                                       # identificador técnico visible bajo el título
    assert _page(world, "SinNombre")[1] == "Pedidos Entregas"                                                 # idéntico al encabezado del Catálogo


def test_the_assessment_report_prefers_the_business_name_and_never_names_a_flow_after_its_domain(world):
    assert re.search(r'<h1 class="asr-title">Ocupación de Almacenes</h1>', _report(world, "SinClasif"))
    sin_modelo = _report(world, "SinModelo")
    assert re.search(r'<h1 class="asr-title">Flujo sin modelo terminal</h1>', sin_modelo) and "<code>" not in re.search(r'<h1 class="asr-title">.*?</h1>(.{0,120})', sin_modelo, re.S).group(1)


def test_model_title_is_the_single_resolver_and_does_not_touch_the_status_or_profile(world):
    entry = world.by["SinNombre"]
    status = svc.statuses_for_entries([entry])[entry.entry_id]
    before = (status.terminal_am, status.profile, status.judgment)
    assert presenter.model_title(entry, "4MA_PEDIDOS_ENTREGAS") == ("Pedidos Entregas", "4MA_PEDIDOS_ENTREGAS")
    presenter.header_for(entry, PACK)
    assert (status.terminal_am, status.profile, status.judgment) == before                                  # solo presentación: nada del documento canónico cambia
