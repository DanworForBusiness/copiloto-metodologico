"""Motor puro del scoring CCHH (Scoring (JD), incremento 1): constantes centralizadas, grilla, puntaje, tiers, identidades sin suplantación y versión de grilla en las evaluaciones."""
from __future__ import annotations

import pytest

from webapp.services import scoring_engine as se


def _c(bloque, peso, cid=None, nombre=None, fase="cchh"):
    return {"criterio_id": cid or f"c-{bloque}-{peso}", "fase": fase, "bloque": bloque, "nombre": nombre or bloque, "peso": peso}


GRID = {"grid_id": "g1", "busqueda_id": "b1", "version": 3, "criterios": [
    _c("excluyente", 0, "x1", "Usó Datasphere"), _c("tecnico", 60, "t1"), _c("deseable", 15, "d1"), _c("competencia", 25, "k1")]}


def test_thresholds_are_centralized_constants():
    assert (se.TIER_A_MIN, se.TIER_B_MIN, se.DISPERSION_ALERTA) == (75, 60, 20)
    assert se.tier_desde_puntaje(75, True) == "A" and se.tier_desde_puntaje(74.99, True) == "B" and se.tier_desde_puntaje(60, True) == "B"
    assert se.tier_desde_puntaje(59.99, True) == "C" and se.tier_desde_puntaje(100, False) == "No aplica" and se.tier_desde_puntaje(None, True) is None


def test_only_the_cchh_phase_is_enabled_and_blocks_follow_the_reference():
    assert se.FASES == ("cchh", "tecnica") and se.FASES_HABILITADAS == ("cchh",)
    assert se.BLOQUE_KEYS == ("excluyente", "tecnico", "deseable", "competencia") and not se.es_ponderable("excluyente")
    assert [o["nota"] for o in se.OPCIONES_POR_BLOQUE["tecnico"]] == [1, 3, 5]
    assert se.validar_grilla([_c("tecnico", 100, fase="tecnica")], "tecnica")[0].startswith("La fase «Entrevista técnica» todavía no está habilitada")


def test_weights_must_sum_100_and_need_a_weighted_criterion():
    assert se.suma_pesos(GRID["criterios"]) == 100 and se.grilla_valida(GRID["criterios"]) and se.validar_grilla(GRID["criterios"]) == []
    assert not se.grilla_valida([_c("excluyente", 0)]) and "al menos un criterio con ponderación" in se.validar_grilla([_c("excluyente", 0)])[0]
    off = [_c("tecnico", 60), _c("deseable", 30)]
    assert not se.grilla_valida(off) and "hoy suma 90%" in se.validar_grilla(off)[0]
    assert se.grilla_valida([_c("tecnico", 33.33), _c("deseable", 33.33), _c("competencia", 33.34)])


def test_normalization_drops_blank_rows_zeroes_excluding_weights_and_reports_errors():
    rows = [{"bloque": "excluyente", "nombre": " Filtro ", "peso": "50"}, {"bloque": "tecnico", "nombre": "SQL", "peso": "40,5", "preguntas_guia": "¿Cuánto SQL?"},
            {"bloque": "deseable", "nombre": "", "peso": ""}, {"bloque": "competencia", "nombre": "Comunica", "peso": "59.5"}]
    limpios, errores = se.normalizar_criterios(rows)
    assert errores == [] and [c["nombre"] for c in limpios] == ["Filtro", "SQL", "Comunica"]
    assert limpios[0]["peso"] == 0.0 and limpios[1]["peso"] == 40.5 and limpios[1]["preguntas_guia"] == "¿Cuánto SQL?" and all(c["fase"] == "cchh" for c in limpios)
    assert se.grilla_valida(limpios)
    _, errs = se.normalizar_criterios([{"bloque": "raro", "nombre": "X"}, {"bloque": "tecnico", "nombre": ""  , "peso": "10"}, {"bloque": "tecnico", "nombre": "Y", "peso": "abc"},
                                       {"bloque": "tecnico", "nombre": "Z", "peso": "101"}, {"bloque": "tecnico", "nombre": "N" * 201, "peso": "1"}])
    assert len(errs) == 5 and "bloque inválido" in errs[0] and "necesita un nombre" in errs[1] and "entre 0 y 100" in errs[2]


def test_score_formula_matches_the_reference():
    cr = GRID["criterios"]
    assert se.calcular_puntaje(cr, {"t1": 5, "d1": 5, "k1": 5}) == 100.0 and se.calcular_puntaje(cr, {"t1": 1, "d1": 1, "k1": 1}) == 0.0
    assert se.calcular_puntaje(cr, {"t1": 3, "d1": 3, "k1": 3}) == 50.0
    assert se.calcular_puntaje(cr, {"t1": 5, "d1": 1, "k1": 3}) == pytest.approx(60 + 0 + 12.5)           # 60*1 + 15*0 + 25*0.5
    assert se.calcular_puntaje(cr, {"t1": 5, "d1": 5}) is None                                              # falta una nota ponderable
    assert se.calcular_puntaje(cr, {"t1": 5}) is None and se.calcular_puntaje_parcial(cr, {"t1": 5}) == 60.0


def test_identities_are_verified_and_cannot_be_impersonated():
    assert se.resolver_identidades(" Ana@Danwor.com ") == ("ana@danwor.com", "ana@danwor.com")           # por defecto evaluator == recorded_by
    assert se.resolver_identidades("ana@danwor.com", "ANA@danwor.com") == ("ana@danwor.com", "ana@danwor.com")
    with pytest.raises(se.ScoringIdentityError):
        se.resolver_identidades("ana@danwor.com", "otro@danwor.com")                                      # no se registra a nombre de otra persona
    with pytest.raises(se.ScoringIdentityError):
        se.resolver_identidades("", "otro@danwor.com")
    with pytest.raises(se.ScoringIdentityError):
        se.resolver_identidades(None)
    assert se.resolver_identidades("rrhh@danwor.com", "lider@danwor.com", delegacion_permitida=True) == ("lider@danwor.com", "rrhh@danwor.com")   # solo con delegación explícita (hoy ningún rol la concede)


def _eval(app="a1", evaluator="ana@danwor.com", grid=GRID, notas=(5, 5, 5), cumple=True, **kw):
    det = {"x1": {"cumple": cumple}, "t1": {"nota": notas[0]}, "d1": {"nota": notas[1]}, "k1": {"nota": notas[2], "comentario": " ok "}}
    return se.construir_evaluacion(grid, application_id=app, fase="cchh", recorded_by=kw.pop("recorded_by", evaluator), evaluator=evaluator, detalles=det, ahora="2026-10-23T10:00:00+00:00", **kw)


def test_an_evaluation_records_evaluator_recorder_and_the_grid_version():
    e = _eval(comentario_general=" buen perfil ")
    assert (e["evaluator"], e["recorded_by"], e["grid_id"], e["grid_version"], e["fase"]) == ("ana@danwor.com", "ana@danwor.com", "g1", 3, "cchh")
    assert e["puntaje"] == 100.0 and e["cumple_excluyentes"] is True and e["comentario_general"] == "buen perfil" and e["evaluation_key"] == "a1:cchh:ana@danwor.com"
    assert [d["criterio_id"] for d in e["detalles"]] == ["x1", "t1", "d1", "k1"] and e["detalles"][3]["comentario"] == "ok" and e["detalles"][0]["nota"] is None
    with pytest.raises(se.ScoringIdentityError):
        _eval(evaluator="lider@danwor.com", recorded_by="rrhh@danwor.com")


def test_an_evaluation_requires_every_criterion_and_the_enabled_phase():
    with pytest.raises(se.ScoringValidationError) as exc:
        se.construir_evaluacion(GRID, application_id="a1", fase="cchh", recorded_by="ana@danwor.com", detalles={"t1": {"nota": 4}})
    assert exc.value.errors == ["Marcá si cumple el excluyente «Usó Datasphere».", "Falta la nota de «deseable».", "Falta la nota de «competencia»."]
    with pytest.raises(se.ScoringValidationError):
        se.construir_evaluacion(GRID, application_id="a1", fase="tecnica", recorded_by="ana@danwor.com", detalles={})
    with pytest.raises(se.ScoringValidationError):
        _eval(notas=(6, 5, 5))
    with pytest.raises(se.ScoringValidationError):
        se.construir_evaluacion({**GRID, "criterios": [_c("tecnico", 50, "t1")]}, application_id="a1", fase="cchh", recorded_by="ana@danwor.com", detalles={"t1": {"nota": 5}})   # grilla inválida (suma 50)


def test_results_only_count_evaluations_of_the_current_grid_version():
    e1, e2 = _eval(evaluator="ana@danwor.com", notas=(5, 5, 5)), _eval(evaluator="beto@danwor.com", notas=(3, 3, 3))
    r = se.resultado_candidato("a1", [e1, e2], GRID)
    assert r["puntaje"] == 75.0 and r["tier"] == "A" and r["dispersion"] == 50.0 and r["alerta_dispersion"] is True and r["evaluaciones_vigentes"] == 2
    assert r["alcance"] == "scoring_cchh" and r["fase"] == "cchh"                                           # nunca se presenta como consolidado CCHH/técnico
    new_grid = {**GRID, "version": 4}                                                                       # la grilla cambió de versión
    r2 = se.resultado_candidato("a1", [e1, e2], new_grid)
    assert r2["puntaje"] is None and r2["tier"] is None and r2["evaluaciones_vigentes"] == 0 and r2["evaluaciones_desactualizadas"] == 2
    other = {**GRID, "grid_id": "otra"}                                                                    # otra identidad de grilla tampoco vale aunque la versión coincida
    assert se.resultado_candidato("a1", [e1], other)["evaluaciones_desactualizadas"] == 1
    failed = _eval(app="a2", cumple=False)
    assert se.resultado_candidato("a2", [failed], GRID)["tier"] == "No aplica"
    assert se.resultado_candidato("a3", [e1], GRID)["tier"] is None


def test_coverage_flags_missing_and_outdated_explicitly():
    e1 = _eval(app="a1")
    old = {**_eval(app="a2"), "grid_version": 2}
    estados = se.cobertura(GRID, ["a1", "a2", "a3"], [e1, old])
    assert estados == {"a1": "vigente", "a2": "desactualizada", "a3": "sin_evaluar"}
    assert se.cobertura(None, ["a1"], [e1]) == {"a1": "desactualizada"}
