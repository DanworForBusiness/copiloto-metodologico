"""
test_catalog_lines_of_business.py — Línea de negocio como TERCERA modalidad de navegación del Catálogo.

Contratos que fija:
  * la agrupación sale SOLO de la clasificación v2 PERSISTIDA (``status.business_classification``), con precedencia manual > automática HIGH > sin clasificar;
    la MEDIUM es solo sugerencia y no agrupa; una v1/otro contrato no cuenta; jamás se deriva ni se clasifica durante la navegación (GET sin efectos);
  * partición COMPLETA y EXCLUSIVA del universo navegable del Catálogo; las entradas sin AM terminal van a "Sin clasificar" con motivo propio (≠ UNDETERMINED);
  * un error real de lectura se informa ("Clasificación no disponible"), nunca se disfraza de una clasificación ficticia;
  * INCOMPATIBLE_SCHEMA no descarta una v2 válida (eje independiente del Juicio/perfil);
  * UNA lectura por lote (``statuses_for_entries``), reusada por los grados de las tarjetas;
  * Dominios y Data Products conservan su contrato; sin reglas por cliente.
Fixtures SINTÉTICAS (catálogo en tmp, estados simulados); nunca datos reales.
"""
from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tools.catalog import business_classification as BC
from tools.catalog import catalog_service as cs
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service
from webapp.services import catalog_navigation as nav
from webapp.services import domain_classification_service as dcs

V2 = BC.BUSINESS_CLASSIFICATION_VERSION


# ── fixtures ────────────────────────────────────────────────────────────────
def _flow(root, client, dom_dir, dominio, *, manual_lob=None, verdict=None, eid_suffix=""):
    d = root / client / dom_dir
    d.mkdir(parents=True)
    manual = f"manual:\n  cliente: {client}\n" + (f"  linea_de_negocio: {manual_lob}\n" if manual_lob else "")
    manual += f"  veredicto: {verdict}\n" if verdict else ""
    (d / f"flujo_{dom_dir}.md").write_text(
        f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n  - 4AM_{dom_dir}\n{manual}---\nCuerpo.\n", encoding="utf-8")
    return d


def bc(lob=None, *, status=BC.CLASSIFIED, confidence="HIGH", contract=V2, label=None):
    return {"contract_version": contract, "status": status, "confidence": confidence,
            "line_of_business": ({"id": lob, "label": label or BC.lob_label(lob)} if lob else None), "domain": None}


def status(entry, *, state="CURRENT", business=None, am="4AM_X"):
    return svc.AssessmentStatus(state, entry.entry_id, entry.cliente, None if state == "NOT_APPLICABLE" else am, business_classification=business)


@pytest.fixture()
def catalog(tmp_path):
    """Catálogo sintético que cubre cada caso de agrupación."""
    _flow(tmp_path, "Alfa", "Ventas1", "Ventas")                         # automático HIGH → SALES
    _flow(tmp_path, "Alfa", "Gastos", "Finanzas")                        # automático HIGH → FINANCE
    _flow(tmp_path, "Beta", "Stock", "Stock")                            # automático HIGH → SUPPLY_CHAIN
    _flow(tmp_path, "Beta", "Manual", "Ventas", manual_lob="FINANCE")    # manual FINANCE sobre automática SALES (difiere)
    _flow(tmp_path, "Beta", "Medio", "Varios")                           # MEDIUM → sin clasificar + sugerencia
    _flow(tmp_path, "Gama", "Indet", "Varios")                           # UNDETERMINED → sin clasificar
    _flow(tmp_path, "Gama", "SinAM", "Stock")                            # NOT_APPLICABLE (sin AM terminal)
    _flow(tmp_path, "Gama", "Historico", "Varios")                       # sin v2 persistida (sobre histórico)
    _flow(tmp_path, "Gama", "Sinsobre", "Varios")                        # NOT_YET_ASSESSED
    _flow(tmp_path, "Delta", "Sistema", "Seguridad")                     # dominio RESERVADO: no es un flujo navegable
    entries = cs.load_catalog(str(tmp_path))
    by = {e.path.split("flujo_")[1][:-3]: e for e in entries}
    st = {
        "Ventas1": status(by["Ventas1"], business=bc("SALES")),
        "Gastos": status(by["Gastos"], business=bc("FINANCE")),
        "Stock": status(by["Stock"], business=bc("SUPPLY_CHAIN")),
        "Manual": status(by["Manual"], business=bc("SALES")),
        "Medio": status(by["Medio"], business=bc("SALES", status=BC.SUGGESTED, confidence="MEDIUM")),
        "Indet": status(by["Indet"], business=bc(None, status=BC.UNDETERMINED, confidence="LOW")),
        "SinAM": status(by["SinAM"], state="NOT_APPLICABLE"),
        "Historico": status(by["Historico"], business=None),
        "Sinsobre": status(by["Sinsobre"], state="NOT_YET_ASSESSED", am=None),
        "Sistema": status(by["Sistema"], business=bc("SALES")),
    }
    return SimpleNamespace(entries=entries, by=by, statuses={by[k].entry_id: v for k, v in st.items()}, root=tmp_path)


def universe(catalog):
    return nav.browsable_flows(cs, catalog.entries)


def ids(catalog, names):
    return {catalog.by[n].entry_id for n in names}


# ── lógica de agrupación ────────────────────────────────────────────────────
def test_grouping_follows_manual_then_automatic_high_then_unclassified(catalog):
    u = universe(catalog)
    eff = dcs.effective_lob_by_entry(u, catalog.statuses)
    g = {k: {e.entry_id for e in v} for k, v in dcs.partition_by_lob(u, eff).items()}
    assert g["SALES"] == ids(catalog, ["Ventas1"])                       # la Manual (FINANCE) NO está en SALES aunque la automática diga SALES
    assert g["FINANCE"] == ids(catalog, ["Gastos", "Manual"])
    assert g["SUPPLY_CHAIN"] == ids(catalog, ["Stock"])
    assert g["MANUFACTURING"] == g["HUMAN_RESOURCES"] == set() and g[dcs.UNAVAILABLE] == set()
    assert g[dcs.UNCLASSIFIED] == ids(catalog, ["Medio", "Indet", "SinAM", "Historico", "Sinsobre"])      # MEDIUM no agrupa
    m = eff[catalog.by["Manual"].entry_id]
    assert (m["source"], m["line_of_business"], m["alignment"], m["automatic_label"]) == ("MANUAL", "FINANCE", "DISAGREES", "Ventas")


def test_medium_is_only_a_suggestion_and_never_moves_the_flow(catalog):
    eff = dcs.effective_lob_by_entry(universe(catalog), catalog.statuses)[catalog.by["Medio"].entry_id]
    assert eff["group"] == dcs.UNCLASSIFIED and eff["line_of_business"] is None
    assert eff["reason"] == dcs.WHY_SUGGESTED and eff["suggestion"] == {"id": "SALES", "label": "Ventas", "confidence": "MEDIUM"}


def test_unclassified_reasons_distinguish_no_model_not_classified_and_undetermined(catalog):
    eff = dcs.effective_lob_by_entry(universe(catalog), catalog.statuses)
    reason = {n: eff[catalog.by[n].entry_id]["reason"] for n in ("SinAM", "Historico", "Sinsobre", "Indet", "Medio")}
    assert reason == {"SinAM": dcs.WHY_NO_MODEL, "Historico": dcs.WHY_NOT_CLASSIFIED, "Sinsobre": dcs.WHY_NOT_CLASSIFIED,
                      "Indet": dcs.WHY_UNDETERMINED, "Medio": dcs.WHY_SUGGESTED}
    assert dcs.WHY_NO_MODEL != dcs.WHY_UNDETERMINED                      # una entrada sin AM NO se presenta como un resultado del clasificador


def test_manual_assignment_applies_even_without_a_terminal_model(catalog, tmp_path):
    e = catalog.by["SinAM"]
    text = open(e.path, encoding="utf-8").read().replace("manual:\n", "manual:\n  linea_de_negocio: sales\n")
    open(e.path, "w", encoding="utf-8").write(text)
    eff = dcs.effective_lob_by_entry([e], {e.entry_id: catalog.statuses[e.entry_id]})[e.entry_id]
    assert (eff["group"], eff["source"]) == ("SALES", "MANUAL")


@pytest.mark.parametrize("contract", ["domain-classification/v1", "business-classification/v1", None, ""])
def test_only_v2_persisted_counts_as_classification(catalog, contract):
    e = catalog.by["Ventas1"]
    st = status(e, business=bc("SALES", contract=contract))
    eff = dcs.effective_lob_by_entry([e], {e.entry_id: st})[e.entry_id]
    assert eff["group"] == dcs.UNCLASSIFIED and eff["reason"] == dcs.WHY_NOT_CLASSIFIED


def test_navigation_never_derives_or_classifies(catalog, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("la navegación no deriva ni clasifica")
    for obj, name in ((dcs, "derive_business_for_entry"), (dcs, "resolve_business"), (dcs, "classify_business"), (dcs, "resolve"),
                      (BC, "classify_business"), (svc, "_derive_business_classification"), (svc, "_derive_domain_classification")):
        monkeypatch.setattr(obj, name, boom)
    eff = dcs.effective_lob_by_entry(universe(catalog), catalog.statuses)
    assert len(eff) == 9                                                  # (el flujo de dominio reservado no es navegable)


def test_partition_is_complete_and_exclusive_over_the_browsable_universe(catalog):
    u = universe(catalog)
    assert catalog.by["Sistema"] not in u and len(u) == 9                 # dominio reservado fuera; el resto (incluso con dominio genérico) dentro
    eff = dcs.effective_lob_by_entry(u, catalog.statuses)
    parts = dcs.partition_by_lob(u, eff)
    flat = [e.entry_id for v in parts.values() for e in v]
    assert sorted(flat) == sorted(e.entry_id for e in u) and len(flat) == len(set(flat))
    assert set(parts) == {o["id"] for o in dcs.lob_options()} | {dcs.UNAVAILABLE}


def test_entries_without_domain_are_still_navigable_by_line_of_business(tmp_path):
    _flow(tmp_path, "Alfa", "Sin_dominio", "")
    entries = cs.load_catalog(str(tmp_path))
    assert cs.domains(entries) == [] and nav.browsable_flows(cs, entries) == entries      # Dominios no la alcanza; la partición por LoB sí


def test_options_follow_the_governed_taxonomy_order_with_unclassified_last():
    opts = dcs.lob_options()
    assert [o["id"] for o in opts] == [x["id"] for x in BC.LINES_OF_BUSINESS] + ["UNCLASSIFIED"]
    assert [o["label"] for o in opts] == ["Finanzas", "Ventas", "Cadena de suministro", "Manufactura", "Recursos Humanos", "Sin clasificar"]


# ── compatibilidad: INCOMPATIBLE_SCHEMA conserva la v2 ──────────────────────
def test_incompatible_schema_keeps_a_valid_persisted_v2_but_not_profile_or_judgment(monkeypatch, catalog):
    entry = SimpleNamespace(entry_id="e1", cliente="Alfa")
    env = {"assessment_schema_version": "modeling_assessment.assessment/v0", "profile_json": {"x": 1}, "judgment": {"level": "BUENA"},
           "business_classification": bc("SALES"), "domain_classification": {"classification_status": "ALIGNED"}, "generated_at": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr(svc, "_load_envelope", lambda client, am: env)
    res = svc.Resolution("OK", "Alfa", terminal_am="AM_X", pool_signature="sig")
    st = svc._status_from(entry, res, svc.current_assessment_versions())
    assert st.state == svc.INCOMPATIBLE_SCHEMA and st.profile is None and st.judgment is None
    assert st.business_classification == bc("SALES")
    real = catalog.by["Ventas1"]                                         # flujo real sin asignación manual: la v2 persistida agrupa aunque el perfil sea incompatible
    eff = dcs.effective_lob_by_entry([real], {real.entry_id: st})
    assert eff[real.entry_id]["group"] == "SALES" and eff[real.entry_id]["available"]


# ── errores reales de lectura ───────────────────────────────────────────────
def test_manual_lob_memo_is_keyed_by_file_content_never_stale_and_never_caches_errors(catalog, monkeypatch):
    import webapp.services.content_service as content_service
    e = catalog.by["Ventas1"]
    parses = []
    real = content_service.split_front_matter
    monkeypatch.setattr(content_service, "split_front_matter", lambda t: (parses.append(1), real(t))[1])
    dcs._MANUAL_LOB_MEMO.clear()
    assert dcs.manual_lob(e, strict=True) is None and dcs.manual_lob(e, strict=True) is None and len(parses) == 1      # 2da lectura: sin re-parsear
    for value in ("SALES", "FINANCE", "SALEZ"):                                  # ediciones CONSECUTIVAS del mismo tamaño: jamás un valor viejo (aunque coincida el mtime)
        text = open(e.path, encoding="utf-8").read()
        text = text.split("manual:\n")[0] + "manual:\n  linea_de_negocio: " + value + "\n" + "\n".join(text.split("manual:\n")[1].splitlines()[:0]) + "---\nCuerpo.\n"
        open(e.path, "w", encoding="utf-8").write(text)
        assert dcs.manual_lob(e, strict=True) == (value if value in ("SALES", "FINANCE") else None)
    bad = SimpleNamespace(path=e.path + ".missing")
    for _ in range(2):
        with pytest.raises(OSError):
            dcs.manual_lob(bad, strict=True)                                                                            # los errores no se memoizan ni se tragan


def test_the_legacy_catalog_service_stays_unaware_of_line_of_business():
    assert "manual_lob" not in open("tools/catalog/catalog_service.py", encoding="utf-8").read()


def test_manual_lob_strict_propagates_read_errors_but_missing_key_is_not_an_error(tmp_path, catalog):
    e = SimpleNamespace(path=catalog.by["Ventas1"].path)
    assert dcs.manual_lob(e, strict=True) is None                                     # sin asignación: normal
    bad = SimpleNamespace(path=str(tmp_path / "no-existe.md"))
    assert dcs.manual_lob(bad) is None                                                # compat: lectura laxa de siempre
    with pytest.raises(OSError):
        dcs.manual_lob(bad, strict=True)


def test_a_single_unreadable_entry_is_marked_unavailable_and_never_unclassified(catalog):
    u = universe(catalog)
    victim = catalog.by["Ventas1"]
    victim.path = victim.path + ".missing"                              # error REAL de lectura del flujo (≠ "sin asignación manual")
    eff = dcs.effective_lob_by_entry(u, catalog.statuses)
    assert eff[victim.entry_id]["available"] is False and eff[victim.entry_id]["group"] == dcs.UNAVAILABLE
    assert all(v["available"] for k, v in eff.items() if k != victim.entry_id)
    parts = dcs.partition_by_lob(u, eff)
    assert [e.entry_id for e in parts[dcs.UNAVAILABLE]] == [victim.entry_id] and victim.entry_id not in {e.entry_id for e in parts["UNCLASSIFIED"]}


# ── ruta /catalog/lob ───────────────────────────────────────────────────────
@pytest.fixture()
def client(catalog, monkeypatch):
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, catalog.entries))
    monkeypatch.setattr(svc, "statuses_for_entries", lambda entries: {e.entry_id: catalog.statuses[e.entry_id] for e in entries})
    session = auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"})
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def flow_ids(html):
    return re.findall(r"flow=([a-z0-9-]+)&amp;pack|flow=([a-z0-9-]+)&pack", html.replace("&amp;", "&"))


def listed(html):
    return {a or b for a, b in re.findall(r"flow=([a-z0-9-]+)&pack|flow=([a-z0-9-]+)&amp;pack", html)}


def test_route_lists_only_the_flows_of_the_selected_line(client, catalog):
    r = client.get("/catalog/lob?lob=FINANCE&pack=datasphere_core")
    assert r.status_code == 200 and listed(r.text) == ids(catalog, ["Gastos", "Manual"])
    assert "Asignación manual" in r.text and "la clasificación automática sugiere Ventas" in r.text
    assert listed(client.get("/catalog/lob?lob=SALES").text) == ids(catalog, ["Ventas1"])
    assert listed(client.get("/catalog/lob?lob=supply_chain").text) == ids(catalog, ["Stock"])        # tolera minúsculas


def test_route_unclassified_view_shows_discrete_reasons_and_medium_suggestion(client, catalog):
    r = client.get("/catalog/lob?lob=UNCLASSIFIED")
    assert listed(r.text) == ids(catalog, ["Medio", "Indet", "SinAM", "Historico", "Sinsobre"])
    assert "Sugerencia: Ventas (confianza media) · requiere confirmación; no agrupa." in r.text
    assert "Sin modelo analítico terminal: no hay raíz que clasificar." in r.text
    assert "Evidencia insuficiente para determinar la línea de negocio." in r.text
    assert "Aún sin clasificación de línea de negocio." in r.text


def test_route_empty_line_and_prompt_and_unknown_and_auth(client, catalog):
    assert "Aún no hay modelos en «Manufactura»." in client.get("/catalog/lob?lob=MANUFACTURING").text
    assert "Elegí una línea de negocio." in client.get("/catalog/lob").text
    assert client.get("/catalog/lob?lob=NOPE").status_code == 404
    assert TestClient(main.app).get("/catalog/lob?lob=SALES").status_code == 401


def test_route_reads_statuses_in_one_batch_and_reuses_them_for_the_tile_grades(client, catalog, monkeypatch):
    calls = []
    real = svc.statuses_for_entries
    monkeypatch.setattr(svc, "statuses_for_entries", lambda entries: (calls.append(len(list(entries))), real(entries))[1])
    assert client.get("/catalog/lob?lob=SALES").status_code == 200
    assert calls == [9]                                                   # UNA lectura de todo el universo; ni N+1 ni segunda lectura para los grados


def test_route_get_is_side_effect_free_and_never_derives(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("efecto no permitido durante la navegación")
    for name in ("_engine_assess", "generate", "_store_envelope", "_derive_business_classification", "_derive_domain_classification"):
        monkeypatch.setattr(svc, name, boom)
    for obj, name in ((dcs, "derive_business_for_entry"), (dcs, "resolve_business"), (BC, "classify_business")):
        monkeypatch.setattr(obj, name, boom)
    for lob in ("SALES", "UNCLASSIFIED", "FINANCE", "MANUFACTURING"):
        assert client.get(f"/catalog/lob?lob={lob}").status_code == 200


def test_route_batch_read_failure_says_unavailable_and_shows_no_fictitious_grouping(client, monkeypatch):
    monkeypatch.setattr(svc, "statuses_for_entries", lambda e: (_ for _ in ()).throw(RuntimeError("fallo de lectura")))
    r = client.get("/catalog/lob?lob=UNCLASSIFIED")
    assert r.status_code == 200 and "Clasificación no disponible" in r.text and "catalog-card " not in r.text and "fallo de lectura" not in r.text


def test_route_single_entry_read_error_is_reported_not_hidden(client, catalog):
    victim = catalog.by["Gastos"]
    victim.path = victim.path + ".missing"
    r = client.get("/catalog/lob?lob=FINANCE")
    assert "Clasificación no disponible para 1 modelo (error de lectura)." in r.text and catalog.by["Gastos"].entry_id not in listed(r.text)
    only = client.get("/catalog/lob?lob=UNAVAILABLE")
    assert listed(only.text) == {victim.entry_id} and "Clasificación no disponible (error de lectura)." in only.text
    assert victim.entry_id not in listed(client.get("/catalog/lob?lob=UNCLASSIFIED").text)


def test_route_cards_keep_the_methodological_grade_untouched(client, catalog):
    r = client.get("/catalog/lob?lob=SALES")
    g = presenter.tile_grade(catalog.statuses[catalog.by["Ventas1"].entry_id])
    assert f'catalog-card__verdict catalog-card__verdict--{g["tone"]}' in r.text and g["label"] in r.text


# ── no regresión: Dominios y Data Products ──────────────────────────────────
def test_domain_route_contract_is_unchanged(client, catalog):
    r = client.get("/catalog/domain?dominio=Ventas&pack=datasphere_core")
    assert r.status_code == 200 and listed(r.text) == ids(catalog, ["Ventas1", "Manual"])           # agrupa por DOMINIO, no por LoB
    assert "catalog-card__lob" not in r.text and "Asignación manual" not in r.text and "Sugerencia:" not in r.text


def test_flows_template_is_identical_without_the_new_optional_context(client, catalog):
    tpl = main.templates.get_template("partials/catalog_flows.html")
    base = {"flows": catalog.entries[:3], "dominio": "Ventas", "foco": None, "pack": "datasphere_core", "grades": {}, "logical_counts": {}}
    assert tpl.render(**base) == tpl.render(**base, lob_info=None, empty_message=None, lob_unavailable=0)
    assert "catalog-card__lob" not in tpl.render(**base)
    empty = tpl.render(**{**base, "flows": []})
    assert 'Sin flujos implementados para "Ventas".' in empty


def _render_tab(**over):
    ctx = dict(document=SimpleNamespace(summary=SimpleNamespace(number="04", stem="04_Modelado")), pack="datasphere_core", request=None,
               catalog_models=["Despachos", "Compras"], catalog_domains=["Despachos", "Compras", "Ventas", "General"], catalog_lobs=dcs.lob_options(), catalog_data_products=[])
    ctx.update(over)
    return main.templates.get_template("partials/catalog_tab.html").render(**ctx)


def test_selector_labels_follow_the_chosen_mode_and_the_first_mode_is_renamed_to_models():
    html = _render_tab()
    kinds = re.findall(r'<option value="([a-z_]*)">([^<]*)</option>', html.split('id="catalog-kind"')[1].split("</select>")[0])
    assert kinds[:4] == [("", "Elegí un catálogo..."), ("domains", "Modelos"), ("data_products", "Data Products"), ("lob", "Líneas de negocio")]    # el valor técnico `domains` no cambia
    assert '<label for="catalog-dominio">Modelo</label>' in html and "Elegí un modelo..." in html and "Dominio</label>" not in html
    assert '<label for="catalog-lob">Línea de negocio</label>' in html and "Elegí una línea de negocio..." in html            # título propio de cada modalidad
    assert "2 modelos disponibles" in html and "dominios disponibles" not in html and "¿Falta un Modelo?" in html and "¿Falta un Dominio?" not in html


def test_models_selector_lists_only_the_genuine_models_not_the_legacy_groupings():
    html = _render_tab(catalog_models=["Despachos", "Compras"], catalog_domains=["Despachos", "Compras", "Ventas", "General", "Stock / Inventario"])
    box = html.split('id="catalog-dominio"')[1].split("</select>")[0]
    assert re.findall(r'<option value="([^"]+)"', box) == ["Despachos", "Compras"]
    for legacy in ("Ventas", "General", "Stock / Inventario"):
        assert legacy not in box
    assert "1 modelo disponible" in _render_tab(catalog_models=["Solo"])


def test_model_domains_keeps_only_unshared_non_generic_domains(tmp_path):
    for client, d, dom in (("A", "m1", "Despachos"), ("A", "m2", "Compras"), ("B", "m3", "Ventas"), ("B", "m4", "ventas"), ("C", "m5", "Ventas"),
                           ("C", "m6", "General"), ("D", "m8", ""), ("E", "m9", "Seguridad")):
        _flow(tmp_path, client, d, dom)
    entries = cs.load_catalog(str(tmp_path))
    assert nav.model_domains(cs, entries) == ["Compras", "Despachos"]                       # compartido (aun con otra capitalización), genérico, vacío y reservado: fuera
    assert "Ventas" in cs.domains(entries) and "General" in cs.domains(entries)        # el contrato de ``domains`` NO cambia (General es único acá: solo la regla genérica lo excluye)
    assert sorted(e.dominio for e in cs.flows_for_domain(entries, "Ventas")) == ["Ventas", "Ventas", "ventas"]
    src = inspect.getsource(nav.model_domains)
    for client_name in ("Biogenesis", "Kensing", "Illolay", "Loma", "Pampa", "Danone", "Ventas", "Stock"):
        assert client_name not in src, client_name                                     # criterio de datos puro, sin nombres propios


@pytest.mark.parametrize("generic", ["General", "general", "GENERAL", " General "])
def test_the_generators_generic_fallback_label_never_names_a_model(tmp_path, generic):
    _flow(tmp_path, "A", "solo", generic)
    _flow(tmp_path, "A", "otro", "Despachos")
    assert nav.model_domains(cs, cs.load_catalog(str(tmp_path))) == ["Despachos"]


# ── cuarta modalidad: Clientes ──────────────────────────────────────────────
def test_clients_lists_each_client_once_alphabetically_excluding_reserved_and_blank(catalog, tmp_path):
    assert nav.clients(cs, catalog.entries) == ["Alfa", "Beta", "Gama"]                        # Delta solo tiene un flujo de dominio reservado → no figura
    extra = tmp_path / "extra"
    extra.mkdir()
    _flow(extra, "zeta", "z1", "X")
    _flow(extra, "Zeta", "z2", "X")
    _flow(extra, "alfa", "a1", "X")
    got = nav.clients(cs, cs.load_catalog(str(extra)))
    assert [c.lower() for c in got] == ["alfa", "zeta"]                                   # mayúsculas/minúsculas no duplican; orden alfabético sin distinguirlas


def test_flows_for_client_returns_every_model_of_that_client_only(catalog):
    assert {e.entry_id for e in nav.flows_for_client(cs, catalog.entries, "Gama")} == ids(catalog, ["Indet", "SinAM", "Historico", "Sinsobre"])
    assert {e.entry_id for e in nav.flows_for_client(cs, catalog.entries, " beta ")} == ids(catalog, ["Stock", "Manual", "Medio"])
    assert nav.flows_for_client(cs, catalog.entries, "Delta") == [] and nav.flows_for_client(cs, catalog.entries, "") == [] and nav.flows_for_client(cs, catalog.entries, "Nadie") == []


def test_client_route_lists_all_the_models_of_the_selected_client(client, catalog):
    r = client.get("/catalog/client?cliente=Beta&pack=datasphere_core")
    assert r.status_code == 200 and listed(r.text) == ids(catalog, ["Stock", "Manual", "Medio"])
    assert listed(client.get("/catalog/client?cliente=alfa").text) == ids(catalog, ["Ventas1", "Gastos"])        # sin distinguir mayúsculas
    assert "catalog-card__lob" not in r.text                                                                       # no mezcla la clasificación de línea de negocio


def test_client_route_prompt_unknown_and_auth(client, catalog):
    assert "Elegí un cliente." in client.get("/catalog/client").text
    assert "Sin modelos para «Nadie»." in client.get("/catalog/client?cliente=Nadie").text
    assert "Sin modelos para «Delta»." in client.get("/catalog/client?cliente=Delta").text                         # su único flujo es de dominio reservado
    assert TestClient(main.app).get("/catalog/client?cliente=Alfa").status_code == 401


def test_client_route_keeps_cards_navigation_and_grades_and_reads_statuses_once(client, catalog, monkeypatch):
    calls = []
    real = svc.statuses_for_entries
    monkeypatch.setattr(svc, "statuses_for_entries", lambda entries: (calls.append(len(list(entries))), real(entries))[1])
    r = client.get("/catalog/client?cliente=Alfa")
    g = presenter.tile_grade(catalog.statuses[catalog.by["Ventas1"].entry_id])
    assert f'catalog-card__verdict--{g["tone"]}' in r.text and "/catalog/flow?" in r.text and 'hx-target="#catalog-detail-slot"' in r.text
    assert calls == [2]                                                                                           # UNA lectura por lote (solo los 2 flujos del cliente)


def test_client_route_is_read_only_and_has_no_client_specific_rules(client):
    src = inspect.getsource(main.catalog_client)
    for forbidden in ("classify", "derive", "business", "_store", "generate", "Biogenesis", "Kensing", "Illolay", "Loma", "Pampa", "Danone"):
        assert forbidden not in src, forbidden
    for obj in (nav.clients, nav.flows_for_client, nav.browsable_flows, nav.model_domains):
        for name in ("Biogenesis", "Kensing", "Illolay", "Loma", "Pampa", "Danone"):
            assert name not in inspect.getsource(obj)


def test_fourth_mode_is_wired_in_the_selector_with_its_own_dynamic_title():
    html = _render_tab(catalog_clients=["Alfa", "Beta"])
    kinds = re.findall(r'<option value="([a-z_]*)">([^<]*)</option>', html.split('id="catalog-kind"')[1].split("</select>")[0])
    assert kinds == [("", "Elegí un catálogo..."), ("domains", "Modelos"), ("data_products", "Data Products"), ("lob", "Líneas de negocio"), ("clients", "Clientes")]
    assert '<label for="catalog-client">Cliente</label>' in html and "Elegí el cliente..." in html and "2 clientes disponibles" in html
    box = html.split('id="catalog-client"')[1].split("</select>")[0]
    assert re.findall(r'<option value="([^"]+)"', box) == ["Alfa", "Beta"] and "4 catálogos disponibles" in html and 'id="catalog-client-flows"' in html
    assert "1 cliente disponible" in _render_tab(catalog_clients=["Solo"])
    src = open("webapp/routers/modules.py", encoding="utf-8").read()
    assert '"catalog_clients": catalog_reads._catalog_clients()' in src


def test_domain_route_still_serves_any_domain_including_legacy_groupings(client, catalog):
    assert listed(client.get("/catalog/domain?dominio=Ventas&pack=datasphere_core").text) == ids(catalog, ["Ventas1", "Manual"])


def test_module_context_exposes_models_without_changing_catalog_domains():
    src = open("webapp/routers/modules.py", encoding="utf-8").read()
    assert '"catalog_domains": catalog_reads._catalog_domains()' in src and '"catalog_models": catalog_reads._catalog_model_domains()' in src


def test_catalog_tab_adds_the_third_mode_and_keeps_the_existing_ones():
    html = open("webapp/templates/partials/catalog_tab.html", encoding="utf-8").read()
    options = re.findall(r'<option value="([a-z_]*)">([^<]*)</option>', html.split('id="catalog-kind"')[1].split("</select>")[0])
    assert options == [("", "Elegí un catálogo..."), ("domains", "Modelos"), ("data_products", "Data Products"), ("lob", "Líneas de negocio"), ("clients", "Clientes")]
    assert 'id="catalog-dominio"' in html and 'hx-get="/catalog/domain?' in html and "/catalog/data-product?" in html
    assert 'hx-get="/catalog/lob?' in html and 'id="catalog-lob-flows"' in html and "4 catálogos disponibles" in html


def test_module_context_exposes_only_the_static_taxonomy_for_the_selector():
    src = open("webapp/routers/modules.py", encoding="utf-8").read()
    assert '"catalog_lobs": dcs.lob_options()' in src
    assert "effective_lob_by_entry" not in src and "statuses_for_entries" not in src      # el render de la pestaña no paga lecturas por lote


def test_navigation_verdict_order_is_the_legacy_one_and_the_legacy_modules_stay_untouched():
    assert nav._VERDICT_RANK == cs._VERDICT_RANK                               # el orden de las tarjetas no puede divergir del de `flows_for_domain`
    src = open("tools/catalog/catalog_service.py", encoding="utf-8").read()
    for name in ("def browsable_flows(", "def model_domains(", "def flows_for_client(", "def sort_flows_by_verdict(", "def clients(", "_GENERIC_DOMAIN", "manual_lob"):
        assert name not in src                                                 # los módulos legacy NO aprenden de la navegación nueva


def test_sort_and_flows_for_domain_keep_their_order(catalog):
    flows = cs.flows_for_domain(catalog.entries, "Ventas")
    assert flows == nav.sort_flows_by_verdict([e for e in catalog.entries if e.dominio == "Ventas"])
    rank = {"plena": 0, "buena": 1, "parcial": 2}
    assert [rank.get(e.veredicto, 9) for e in nav.sort_flows_by_verdict(catalog.entries)] == sorted(rank.get(e.veredicto, 9) for e in catalog.entries)


# ── guardas estáticas ───────────────────────────────────────────────────────
def _src(*objs):
    return "\n".join(inspect.getsource(o) for o in objs)


def test_new_navigation_code_has_no_classification_logic_and_no_client_rules():
    code = _src(dcs._effective_for, dcs.effective_lob_by_entry, dcs.partition_by_lob, dcs._persisted_business, dcs.lob_options, main.catalog_lob, main._lob_note)
    for forbidden in ("classify_business", "derive_business_for_entry", "resolve_business", "classify_domain", "_signals", "LOB_BY_CODE", "codes", "startswith", "prefix",
                      "judgment", "evidence", "score", "portab"):
        assert forbidden not in code, forbidden
    for client_name in ("Biogenesis", "Kensing", "Illolay", "Loma", "Pampa", "Danone"):
        assert client_name not in code, client_name
    assert "effective_classification" in code and "LINES_OF_BUSINESS" in code


def test_new_code_only_reuses_the_public_effective_classification_api():
    tree_src = open("webapp/services/domain_classification_service.py", encoding="utf-8").read()
    start = tree_src.index("# ── Navegación por LÍNEA DE NEGOCIO")
    block = tree_src[start:]
    used = set(re.findall(r"BC\.(\w+)", block)) | set(re.findall(r"from tools\.catalog\.business_classification import ([^\n]+)", block))
    assert used == {"effective_classification", "LINES_OF_BUSINESS"}


def test_route_surface_snapshot_has_exactly_the_two_new_routes():
    snap = set(json.load(open("tests/route_surface.json", encoding="utf-8")))
    assert {"GET /catalog/lob", "GET /catalog/client"} <= snap
