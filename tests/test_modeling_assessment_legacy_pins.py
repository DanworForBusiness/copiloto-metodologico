"""PINS del comportamiento ACTUAL del evaluador legacy (Fase 1, hito M0).

Esto es una *characterization test suite*, igual que tests/test_routing_corpus_regression.py:
significa "sabemos EXACTAMENTE que hace el legacy hoy", NO "este comportamiento es
metodologicamente correcto". Los defectos conocidos quedan DIFERIDOS (decision del owner,
Modeling Self-Assessment v2 — NOT FIXED IN THIS INITIATIVE) y se rastrean en:

    LEGACY-1  B1  scope de clientes de 1 cubo         _handoffs/LEGACY-1-single-cube-assessment-scope.md
    LEGACY-2  B2+B3  centralidad raw + texto del finding  _handoffs/LEGACY-2-raw-centrality-and-finding-text.md
    LEGACY-3  B4  rama default 'buena' sin evidencia   _handoffs/LEGACY-3-default-buena-without-evidence.md
    (B5, B6: diferidos y documentados en el handoff; el mapeo aprobado no les asigna ticket)

Si alguno de estos tests se pone rojo, el legacy CAMBIO su comportamiento: eso es una decision
de producto (cambia veredictos visibles a consumidores), no un arreglo de rutina.
No se usa el evaluador nuevo ni se modifica nada del legacy.
"""
from __future__ import annotations

import pytest

from tools.catalog import generate_catalog as gc
from tools.catalog.generate_catalog import Entity, evaluate_compliance


def _ent(name, remote_entity="", conn="S4PREM", *, cube=False, upstreams=(), assoc=(), pattern=""):
    return Entity(name=name, file=f"{name}.json", data_category=("CUBE" if cube else ""),
                  modeling_pattern=pattern or ("ANALYTICAL_CUBE" if cube else ("DATA_STRUCTURE" if remote_entity else "")),
                  remote_connection=conn, remote_entity=remote_entity,
                  upstreams=list(upstreams), assoc_upstreams=list(assoc))


def _pool(*ents):
    return {e.name: e for e in ents}


def _findings(entities):
    findings, verdict, apto = evaluate_compliance(entities)
    return {f.id: f for f in findings}, verdict, apto


def _reaches(entities, start, goal):
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for e in entities.values():
            if cur in e.upstreams + e.assoc_upstreams and e.name not in seen:
                seen.add(e.name)
                stack.append(e.name)
    return goal in seen


# ── LEGACY-1 · B1 · scope de clientes de 1 cubo ────────────────────────────
def _capture_flows(monkeypatch, tmp_path, entities):
    seen = []
    monkeypatch.setattr(gc, "load_folder", lambda *a, **k: (entities, {}))
    monkeypatch.setattr(gc, "_write_flow", lambda out_dir, cliente, dom, sub, folder: seen.append(set(sub)) or "ok")
    gc.process_folder(tmp_path / "in", tmp_path / "out", "Cli", "General", json_files=[], txt_files=[])
    return seen


def test_pin_legacy1_single_cube_client_evaluates_the_WHOLE_pool(monkeypatch, tmp_path):
    ents = _pool(_ent("AM_X", cube=True, upstreams=["VF_X"]), _ent("VF_X"),
                 _ent("ORPHAN_RAW", "ABAPTABLES.ORPH"))              # no pertenece a la clausura de AM_X
    (flow,) = _capture_flows(monkeypatch, tmp_path, ents)
    assert flow == {"AM_X", "VF_X", "ORPHAN_RAW"}                    # el huerfano entra al flujo/veredicto


def test_pin_legacy1_multi_cube_client_segments_by_cube_closure(monkeypatch, tmp_path):
    ents = _pool(_ent("AM_X", cube=True, upstreams=["VF_X"]), _ent("VF_X"),
                 _ent("AM_Y", cube=True, upstreams=["VF_Y"]), _ent("VF_Y"),
                 _ent("ORPHAN_RAW", "ABAPTABLES.ORPH"))
    flows = _capture_flows(monkeypatch, tmp_path, ents)
    assert sorted(map(sorted, flows)) == [["AM_X", "VF_X"], ["AM_Y", "VF_Y"]]   # el huerfano queda fuera


# ── LEGACY-2 · B2 + B3 · centralidad raw y texto del finding ───────────────
def _spine_with_raw():
    """AM ← VF ← VR ← RAW (cadena de upstreams) + 3 CDS: la raw ALIMENTA el cubo por la espina."""
    return _pool(
        _ent("AM_X", cube=True, upstreams=["VF_X"]), _ent("VF_X", upstreams=["VR_X"]),
        _ent("VR_X", upstreams=["RAW_X", "CDS_1", "CDS_2", "CDS_3"]),
        _ent("RAW_X", "ABAPTABLES.MBEW"),
        _ent("CDS_1", "ABAP_CDS.ZA$E"), _ent("CDS_2", "ABAP_CDS.ZB$E"), _ent("CDS_3", "ABAP_CDS.ZC$E"))


def test_pin_legacy2_is_cube_is_false_for_every_realistic_remote_entity():
    raw = _ent("RAW_X", "ABAPTABLES.MBEW")
    assert gc._is_cube(raw) is False                                  # raw_on_fact solo se activa si la RAW ES un cubo


def test_pin_legacy2_raw_that_feeds_the_cube_is_still_classified_peripheral():
    ents = _spine_with_raw()
    assert _reaches(ents, "RAW_X", "AM_X") is True                    # estructuralmente alimenta el AM
    f, verdict, apto = _findings(ents)
    assert f["abap_direct"].severidad == "medio"                      # 1/4 = 25 % < 1/3 y raw_on_fact=False
    assert verdict == "buena"
    assert apto == "Reutilizable con reserva puntual — origen crudo minoritario y periférico"


def test_pin_legacy2_finding_text_claims_it_does_not_feed_the_fact_even_when_it_does():
    ents = _spine_with_raw()
    f, _v, _a = _findings(ents)
    assert "periférica (no alimenta el hecho)" in f["abap_direct"].evidencia
    assert _reaches(ents, "RAW_X", "AM_X") is True                    # B3: el texto contradice el linaje


def test_pin_legacy2_raw_on_fact_only_fires_when_the_raw_entity_itself_is_the_cube():
    f, verdict, _a = _findings(_pool(_ent("FACT", "ABAPTABLES.MBEW", cube=True)))
    assert f["abap_direct"].severidad == "alto" and verdict == "parcial"


# ── LEGACY-3 · B4 · rama default sin evidencia de origen ───────────────────
def test_pin_legacy3_no_remote_evidence_defaults_to_buena_revisar_origen():
    ents = _pool(_ent("AM_X", cube=True, upstreams=["VF_X"]), _ent("VF_X"))
    f, verdict, apto = _findings(ents)
    assert verdict == "buena" and apto == "Reutilizable — revisar origen"
    assert set(f) <= {"naming"}                                       # ningun finding de origen: ausencia de evidencia = "buena"


# ── B5 · SAPI queda fuera del denominador de la rubrica proporcional ───────
def _raw_cds_sapi(n_cds, n_sapi):
    ents = [_ent("RAW_X", "ABAPTABLES.MBEW")]
    ents += [_ent(f"CDS_{i}", f"ABAP_CDS.Z{i}$E") for i in range(n_cds)]
    ents += [_ent(f"SAPI_{i}", f"SAPI.0X{i}") for i in range(n_sapi)]
    return _pool(*ents)


def test_pin_b5_sapi_volume_never_dilutes_the_raw_ratio():
    for n_sapi in (0, 3, 30):
        f, verdict, _a = _findings(_raw_cds_sapi(n_cds=2, n_sapi=n_sapi))
        assert "2 de 3 orígenes ABAP" in f["abap_direct"].evidencia  # denominador = ideal+raw (SAPI fuera)
        assert verdict == "buena"                                     # 1/3 no supera 1/3
    _f, verdict, _a = _findings(_raw_cds_sapi(n_cds=1, n_sapi=30))
    assert verdict == "parcial"                                       # 1/2 > 1/3 sin importar los 30 SAPI


# ── B6 · naming es una unica 'nota', ciega a magnitud, y omite remote-bearing ──
def _plena_base():
    return [_ent("ZCDS_A", "ABAP_CDS.ZA$E"), _ent("ZCDS_B", "ABAP_CDS.ZB$E")]


def test_pin_b6_naming_is_one_nota_regardless_of_how_many_violations():
    verdicts, severities, counts = set(), set(), []
    for n_bad in (1, 30):
        ents = _pool(*_plena_base(), *[_ent(f"bad_name_{i}") for i in range(n_bad)])
        f, verdict, _a = _findings(ents)
        verdicts.add(verdict)
        severities.add(f["naming"].severidad)
        counts.append(len(f["naming"].evidencia.split(" | ")))
    assert verdicts == {"plena"} and severities == {"nota"}           # nunca gatea el veredicto
    assert counts == [1, 30]                                          # la magnitud solo aparece como largo del texto


def test_pin_b6_naming_check_skips_remote_bearing_entities():
    ents = _pool(*_plena_base(), _ent("nombre_malo_remoto", "ABAP_CDS.ZX$E"))
    f, verdict, _a = _findings(ents)
    assert "naming" not in f and verdict == "plena"
