"""
test_catalog_delta_pair_collapse.py — ingesta local `X` + su compañera técnica `X_Delta` = UN objeto lógico de ingesta delta-capable.

El grafo de linaje cuenta la historia funcional (un solo nodo); el detalle técnico de la base conserva la verdad (Delta capable: Sí + la tabla técnica).
Regla determinista; ante ambigüedad NO colapsa (un artefacto que solo «termina en Delta» no es un par canónico). Fixtures SINTÉTICAS en tmp.
"""
from __future__ import annotations

import json
import re

import pytest

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import catalog_reads


def _vista(name, capa, *, tecnico="Tabla local", up=(), assoc=(), label=None, remote=False, pattern="DATA_STRUCTURE", cat="null"):
    return ("---\nauto:\n  tipo: vista\n" f"  artefacto_tecnico: {tecnico}\n  nombre: {name}\n  label: {label or name}\n  capa: {capa}\n  modeling_pattern: {pattern}\n"
            f"  data_category: {cat}\n  origen_remoto: {'S4@SAP' if remote else 'null'}\n"
            + (("  upstreams:\n" + "".join(f"  - {u}\n" for u in up)) if up else "  upstreams: []\n")
            + (("  upstreams_asociacion:\n" + "".join(f"  - {u}\n" for u in assoc)) if assoc else "  upstreams_asociacion: []\n")
            + f"---\n\n# {name}\n\n| Propiedad | Valor |\n|---|---|\n| Capa | {capa} |\n| Artefacto | {tecnico} |\n| Patrón de modelado | {pattern} |\n\n#### Campos (2)\n\n| Campo | Tipo |\n|---|---|\n| `A` | String |\n")


def _flow(tmp_path, monkeypatch, vistas: dict, order: list[str], cliente="Cli"):
    d = tmp_path / cliente / "Dom"
    (d / "vistas").mkdir(parents=True)
    (d / "flujo_Dom.md").write_text("---\nauto:\n  tipo: flujo\n  dominio: Dom\n  artefactos:\n" + "".join(f"  - {a}\n" for a in order)
                                    + f"manual:\n  cliente: {cliente}\n---\n\n# Flujo\n", encoding="utf-8")
    for name, body in vistas.items():
        (d / "vistas" / f"{name}.md").write_text(body, encoding="utf-8")
    entries = cs.load_catalog(str(tmp_path))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    return entries[0]


def _chain(*, delta_reads=True, with_delta=True, delta_label=None):
    """1TR_X(+_Delta) → 2VR_X → 3VF_X → 4AM_X."""
    v = {"1TR_X": _vista("1TR_X", "Ingesta", up=["1TR_X_Delta"] if (with_delta and delta_reads) else ()),
         "2VR_X": _vista("2VR_X", "Armonización", up=["1TR_X"], pattern="RELATIONAL_DATASET", cat="null"),
         "3VF_X": _vista("3VF_X", "Propagación", up=["2VR_X"], pattern="FACT", cat="FACT"),
         "4AM_X": _vista("4AM_X", "Consumo", up=["3VF_X"], pattern="ANALYTICAL_CUBE", cat="CUBE")}
    order = ["1TR_X", "2VR_X", "3VF_X", "4AM_X"]
    if with_delta:
        v["1TR_X_Delta"] = _vista("1TR_X_Delta", "Ingesta", label=delta_label or "1TR_X")
        order = ["1TR_X_Delta", "1TR_X"] + order[1:]
    return v, order


def _view(e):
    sections, svg, _assoc, _ctx = main._catalog_flow_view(e, False, "datasphere_core")
    return sections, svg


def _node_order(svg):
    return re.findall(r'<a [^>]*href="#([^"]+)"', svg)


# 1. un objeto lógico, un nodo
@pytest.mark.parametrize("delta_reads,label", [(True, None), (False, "1TR_X")])
def test_local_ingest_with_its_delta_companion_renders_as_one_logical_node(tmp_path, monkeypatch, delta_reads, label):
    v, order = _chain(delta_reads=delta_reads, delta_label=label)
    sections, svg = _view(_flow(tmp_path, monkeypatch, v, order))
    slugs = [s.slug for s in sections]
    assert not any("delta" in s for s in slugs) and len(slugs) == 4                                     # sin sección/nodo para la compañera técnica
    assert "1TR_X_Delta" not in svg and svg.count("1TR_X") >= 1
    assert [s.number for s in sections] == [1, 2, 3, 4]                                                 # numeración contigua, sin hueco
    assert len(_node_order(svg)) == 4 and svg.count("<a ") == 4


# 2. el detalle técnico conserva la verdad
def test_the_technical_detail_still_says_the_ingest_is_delta_capable(tmp_path, monkeypatch):
    v, order = _chain()
    sections, _svg = _view(_flow(tmp_path, monkeypatch, v, order))
    base = next(s for s in sections if s.slug.endswith("1tr-x") or "1TR_X" in s.title)
    text = re.sub(r"<[^>]+>", " ", base.html)
    assert re.search(r"Delta capture\s+No determinado", text)                                           # sin evidencia CSN: nunca un OFF falso
    assert re.search(r"Tabla técnica de cambios\s+1TR_X_Delta", text)                                   # trazabilidad: la tabla técnica sigue identificada
    assert "1TR_X_Delta" not in [s.title for s in sections]


# 3. un objeto normal no cambia
def test_a_normal_flow_without_delta_is_unchanged(tmp_path, monkeypatch):
    v, order = _chain(with_delta=False)
    sections, svg = _view(_flow(tmp_path, monkeypatch, v, order))
    assert [s.number for s in sections] == [1, 2, 3, 4] and len(_node_order(svg)) == 4
    assert all("Tabla técnica de cambios" not in s.html for s in sections)


# 4. «termina en Delta» no basta
@pytest.mark.parametrize("case", ["no_base", "base_is_a_view", "consumed_by_another", "no_underscore", "remote_source"])
def test_an_artifact_that_merely_ends_in_delta_is_not_collapsed(tmp_path, monkeypatch, case):
    v, order = _chain(with_delta=False)
    if case == "no_base":                                                      # `_Delta` sin su base en el flujo
        v["1TR_Y_Delta"] = _vista("1TR_Y_Delta", "Ingesta")
        order.insert(0, "1TR_Y_Delta")
    elif case == "base_is_a_view":                                             # la «base» no es una tabla local de ingesta
        v["MARGIN"] = _vista("MARGIN", "Armonización", tecnico="Vista", up=["1TR_X"], pattern="RELATIONAL_DATASET")
        v["MARGIN_Delta"] = _vista("MARGIN_Delta", "Ingesta", label="MARGIN")
        order += ["MARGIN", "MARGIN_Delta"]
    elif case == "consumed_by_another":                                        # otro artefacto usa la `_Delta` como fuente ordinaria
        v["1TR_Z"] = _vista("1TR_Z", "Ingesta", up=["1TR_Z_Delta"])
        v["1TR_Z_Delta"] = _vista("1TR_Z_Delta", "Ingesta", label="1TR_Z")
        v["2VR_Z"] = _vista("2VR_Z", "Armonización", up=["1TR_Z_Delta"], pattern="RELATIONAL_DATASET")
        order += ["1TR_Z_Delta", "1TR_Z", "2VR_Z"]
    elif case == "no_underscore":                                              # sufijo `Delta` sin guion bajo
        v["1TR_QDelta"] = _vista("1TR_QDelta", "Ingesta")
        v["1TR_Q"] = _vista("1TR_Q", "Ingesta", up=["1TR_QDelta"])
        order += ["1TR_QDelta", "1TR_Q"]
    elif case == "remote_source":                                              # la compañera trae origen remoto: no es una sombra local
        v["1TR_R"] = _vista("1TR_R", "Ingesta", up=["1TR_R_Delta"])
        v["1TR_R_Delta"] = _vista("1TR_R_Delta", "Ingesta", label="1TR_R", remote=True)
        order += ["1TR_R_Delta", "1TR_R"]
    extra = [n for n in order if n not in ("1TR_X", "2VR_X", "3VF_X", "4AM_X")]
    v["2VR_X"] = _vista("2VR_X", "Armonización", up=["1TR_X", *extra], pattern="RELATIONAL_DATASET")            # los extras entran al linaje del output (si no, ni se muestran)
    e = _flow(tmp_path, monkeypatch, v, order)
    sections, svg = _view(e)
    names = [n for n in order if n in v]
    assert len(sections) == len(names)                                                                   # ningún artefacto desapareció
    assert all("Tabla técnica de cambios" not in s.html for s in sections)


# 5. el resto del orden se mantiene
def test_the_rest_of_the_lineage_order_is_stable(tmp_path, monkeypatch):
    v, order = _chain()
    sections, svg = _view(_flow(tmp_path, monkeypatch, v, order))
    v0, order0 = _chain(with_delta=False)
    sections0, svg0 = _view(_flow(tmp_path / "plain", monkeypatch, v0, order0, cliente="Cli"))
    assert [s.title for s in sections] == [s.title for s in sections0]                                  # mismo orden visible que el flujo sin compañera
    assert _node_order(svg) == _node_order(svg0)


def test_focus_on_the_delta_companion_points_to_its_logical_object(tmp_path, monkeypatch):
    v, order = _chain()
    e = _flow(tmp_path, monkeypatch, v, order)
    out: dict = {}
    main._catalog_flow_view(e, False, "datasphere_core", focus=["1TR_X_Delta"], focus_out=out)
    assert [p["name"] for p in out["present"]] == ["1TR_X"] and out["missing"] == []


# ── conteo visible: LÓGICO (mismo conjunto normalizado del linaje); el físico queda para Assessment y detalle técnico ──────────────────────
def test_user_facing_artifact_count_follows_the_logical_lineage_objects(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.services import auth_service
    v, order = _chain()
    e = _flow(tmp_path, monkeypatch, v, order)
    sections, svg = _view(e)
    assert len(e.artefactos) == 5 and len(sections) == 4 == len(_node_order(svg))                      # físico 5; objetos lógicos del linaje 4
    assert main._catalog_logical_artifact_count(e) == 4                                                # el helper deriva del MISMO conjunto normalizado
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    hero = c.get(f"/catalog/flow?flow={e.entry_id}&pack=datasphere_core", headers={"HX-Request": "true"}).text
    tile = c.get("/catalog/domain?dominio=Dom").text
    assert "<span>4 artefactos</span>" in hero and '<p class="catalog-card__meta">4 artefactos' in tile      # hero y tile = lógico
    assert "5 artefactos" not in hero + tile
    assert len(e.artefactos) == 5                                                                      # el conteo físico NO se toca (Assessment / evidencia / fingerprints)


def test_logical_count_equals_the_physical_one_when_nothing_collapses(tmp_path, monkeypatch):
    v, order = _chain(with_delta=False)
    e = _flow(tmp_path, monkeypatch, v, order)
    assert main._catalog_logical_artifact_count(e) == len(e.artefactos) == 4
    v2, order2 = _chain(with_delta=False)
    v2["1TR_Y_Delta"] = _vista("1TR_Y_Delta", "Ingesta")                                               # `_Delta` sin base: no es par → cuenta como artefacto
    e2 = _flow(tmp_path / "b", monkeypatch, v2, order2 + ["1TR_Y_Delta"], cliente="Cli2")
    assert main._catalog_logical_artifact_count(e2) == 5


# ── «Delta capture»: evidencia CANÓNICA (definición CSN `@DataWarehouse.delta`), la MISMA que gobierna el colapso ──────────────────────────────
_LOCAL = {"@DataWarehouse.repository.technicalType": {"#": "DWC_LOCAL_TABLE"}, "kind": "entity", "elements": {"A": {"type": "cds.String"}}}


def _pool(tmp_path, monkeypatch, defs: dict):
    root = tmp_path / "pool"
    for name, extra in defs.items():
        f = root / "Cli" / "local-tables" / f"{name}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"definitions": {name: {**_LOCAL, **extra}}}), encoding="utf-8")
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(root))
    return root


def _delta(companions=("1TR_X_Delta",), type_="ACTIVE"):
    return {"@DataWarehouse.delta": {"type": {"#": type_}, "deltaFromEntities": list(companions)}}


def _detail_text(sections, title_part):
    sec = next(s for s in sections if title_part in s.title)
    return re.sub(r"<[^>]+>", " ", sec.html)


def test_explicit_delta_metadata_with_a_proven_companion_is_on_with_the_technical_table_and_one_graph_node(tmp_path, monkeypatch):
    _pool(tmp_path, monkeypatch, {"1TR_X": _delta()})
    v, order = _chain(delta_reads=False, delta_label="otra etiqueta")                               # sin corroboración estructural: lo prueba la evidencia canónica
    sections, svg = _view(_flow(tmp_path / "cat", monkeypatch, v, order))
    text = _detail_text(sections, "1TR_X")
    assert re.search(r"Delta capture\s+ON", text) and re.search(r"Tabla técnica de cambios\s+1TR_X_Delta", text)
    assert len(sections) == 4 and "1TR_X_Delta" not in svg and len(_node_order(svg)) == 4           # el par probado = UN nodo lógico
    props = re.search(r"Capa\s+Ingesta\s+Artefacto\s+Tabla local\s+Delta capture\s+ON\s+Tabla técnica de cambios\s+1TR_X_Delta\s+", text)
    assert props                                                                                    # orden: Capa · Artefacto · Delta capture · Tabla técnica de cambios


def test_an_authoritative_local_table_without_delta_configuration_is_off_and_fabricates_no_technical_table(tmp_path, monkeypatch):
    _pool(tmp_path, monkeypatch, {"1TR_X": {}})
    v, order = _chain(with_delta=False)
    sections, svg = _view(_flow(tmp_path / "cat", monkeypatch, v, order))
    text = _detail_text(sections, "1TR_X")
    assert re.search(r"Delta capture\s+OFF", text) and "Tabla técnica de cambios" not in text and "_Delta" not in text


def test_authoritative_off_vetoes_a_look_alike_pair(tmp_path, monkeypatch):
    _pool(tmp_path, monkeypatch, {"1TR_X": {}})                                                     # la definición dice: sin delta → la «_Delta» es solo un nombre parecido
    v, order = _chain()
    sections, svg = _view(_flow(tmp_path / "cat", monkeypatch, v, order))
    assert len(sections) == 5 and "1TR_X_Delta" in svg                                              # no se pliega


def test_insufficient_metadata_is_undetermined_never_a_false_off(tmp_path, monkeypatch):
    from webapp.services import catalog_delta_evidence as cde
    assert cde.classify_definition(None)["state"] == cde.UNDETERMINED
    assert cde.classify_definition({"kind": "entity"})["state"] == cde.UNDETERMINED                 # ni siquiera es una tabla local autoritativa
    assert cde.classify_definition({**_LOCAL, "@DataWarehouse.delta": {"type": {"#": "WEIRD"}}})["state"] == cde.UNDETERMINED   # anotación ambigua
    assert cde.classify_definition({**_LOCAL})["state"] == cde.OFF and cde.classify_definition({**_LOCAL, **_delta()})["state"] == cde.ON
    _pool(tmp_path, monkeypatch, {"OTRA": {}})                                                      # el pool existe pero no trae la definición de 1TR_X
    v, order = _chain(with_delta=False)
    sections, _svg = _view(_flow(tmp_path / "cat", monkeypatch, v, order))
    text = _detail_text(sections, "1TR_X")
    assert re.search(r"Delta capture\s+No determinado", text) and not re.search(r"Delta capture\s+OFF", text)
    assert cde.delta_capture("NoExiste", "Dom", "1TR_X")["state"] == cde.UNDETERMINED               # sin pool para el cliente: ND


def test_on_without_the_companion_in_the_closure_still_reports_capture_and_the_technical_table(tmp_path, monkeypatch):
    _pool(tmp_path, monkeypatch, {"1TR_X": _delta()})
    v, order = _chain(with_delta=False)                                                             # `1TR_X_Delta` NO está en el flujo del Catálogo
    sections, svg = _view(_flow(tmp_path / "cat", monkeypatch, v, order))
    text = _detail_text(sections, "1TR_X")
    assert re.search(r"Delta capture\s+ON", text) and re.search(r"Tabla técnica de cambios\s+1TR_X_Delta", text)
    assert len(sections) == 4 and len(_node_order(svg)) == 4                                        # el grafo no cambia (un solo objeto lógico)
