"""scoring_service.py — casos de uso del Scoring (JD) de un proceso de selección (alcance de esta etapa: scoring CCHH: JD + grilla).

Capa fina entre el router y los repositorios: valida contra el motor puro (`scoring_engine`), exige un proceso GESTIONADO y ACTIVO para escribir y delega la persistencia en `scoring_grid_repository` (grilla) y
`busqueda_repository` (JD: sigue siendo `busqueda.jd_texto`/`palabras_clave`, sin copias)."""
from __future__ import annotations

from webapp.services import busqueda_repository, profile_repository, scoring_engine, scoring_evaluation_repository, scoring_grid_repository

JD_MAX = 30000
KEYWORDS_MAX = 500


def _writable_busqueda(busqueda_id: str) -> dict:
    busqueda = busqueda_repository.get(busqueda_id)
    if busqueda is None:
        raise LookupError("Búsqueda no encontrada.")
    if not busqueda_repository.es_gestionada(busqueda):
        raise ValueError("El proceso todavía no está en gestión de Capital Humano.")
    if busqueda.get("estado") != "activo":
        raise ValueError("El proceso está cerrado: el scoring queda en solo lectura.")
    return busqueda


def save_jd(busqueda_id: str, jd_texto: str, palabras_clave: str) -> dict:
    """Guarda la Job Description del proceso (la MISMA que muestra el formulario de edición)."""
    _writable_busqueda(busqueda_id)
    jd = (jd_texto or "").strip()
    kw = (palabras_clave or "").strip()
    if len(jd) > JD_MAX:
        raise ValueError(f"La Job Description supera los {JD_MAX} caracteres.")
    if len(kw) > KEYWORDS_MAX:
        raise ValueError(f"Las palabras clave superan los {KEYWORDS_MAX} caracteres.")
    return busqueda_repository.update_jd(busqueda_id, jd or None, kw or None)


def save_grid(busqueda_id: str, expected_revision, rows, *, actor: str | None) -> tuple[dict, bool]:
    """Valida y guarda la grilla CCHH del proceso -> `(grilla, cambió)`. `scoring_engine.ScoringValidationError` con los motivos si no es guardable."""
    _writable_busqueda(busqueda_id)
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        raise ValueError("Revisión de grilla inválida.") from None
    criterios, errores = scoring_engine.normalizar_criterios(rows, fase=scoring_engine.FASE_CCHH)
    errores = errores + ([] if errores else scoring_engine.validar_grilla(criterios, scoring_engine.FASE_CCHH))
    if errores:
        raise scoring_engine.ScoringValidationError(errores)
    return scoring_grid_repository.save(busqueda_id, revision, criterios, actor=actor)


def panel(busqueda: dict) -> dict:
    """Datos del panel «Scoring CCHH» para la vista: JD, grilla vigente agrupada por bloque, suma de pesos y si está lista (JD cargada + grilla válida)."""
    grid = scoring_grid_repository.get(busqueda["busqueda_id"])
    criterios = [c for c in (grid or {}).get("criterios", []) if c.get("fase") == scoring_engine.FASE_CCHH]
    por_bloque = {b["key"]: [c for c in criterios if c["bloque"] == b["key"]] for b in scoring_engine.BLOQUES}
    jd = (busqueda.get("jd_texto") or "").strip()
    return {
        "grid": grid, "grid_revision": (grid or {}).get("revision", 0), "grid_version": (grid or {}).get("version"),
        "criterios_por_bloque": por_bloque, "suma_pesos": scoring_engine.suma_pesos(criterios), "grilla_valida": scoring_engine.grilla_valida(criterios),
        "jd_texto": busqueda.get("jd_texto") or "", "palabras_clave": busqueda.get("palabras_clave") or "", "jd_cargada": bool(jd),
        "editable": busqueda.get("estado") == "activo", "bloques": scoring_engine.BLOQUES, "peso_total": scoring_engine.PESO_TOTAL,
        "tier_a_min": scoring_engine.TIER_A_MIN, "tier_b_min": scoring_engine.TIER_B_MIN,
    }


# ───────────────────────── evaluación CCHH por candidato y ranking ─────────────────────────
def _application(busqueda: dict, application_id: str) -> dict:
    app = next((a for a in busqueda.get("candidatos", []) if a.get("application_id") == application_id), None)
    if app is None:
        raise LookupError("Postulación no encontrada.")
    return app


def _display_name(profile: dict | None) -> str:
    if not profile or profile.get("deleted_at"):
        return "Candidato eliminado del Banco"
    return " ".join(p for p in (str(profile.get("first_name") or "").strip(), str(profile.get("last_name") or "").strip()) if p) or "Candidato"


def evaluation_form(busqueda: dict, application_id: str, evaluator: str | None) -> dict:
    """Datos del formulario de evaluación CCHH de UNA postulación para el evaluador autenticado (solo lectura: navegar NO crea nada). Si ya evaluó, se precargan sus respuestas por criterio (también si quedaron
    desactualizadas por un cambio de grilla, para que revise y vuelva a guardar)."""
    app = _application(busqueda, application_id)
    grid = scoring_grid_repository.get(busqueda["busqueda_id"])
    criterios = [c for c in (grid or {}).get("criterios", []) if c.get("fase") == scoring_engine.FASE_CCHH]
    ready = bool(grid) and scoring_engine.grilla_valida(criterios)
    who = scoring_engine._identidad(evaluator)
    existing = scoring_evaluation_repository.get(busqueda["busqueda_id"], scoring_engine.clave_evaluacion(application_id, scoring_engine.FASE_CCHH, who)) if (ready and who) else None
    previous = {d["criterio_id"]: d for d in (existing or {}).get("detalles", [])}
    profile = profile_repository.get(app.get("profile_id")) if app.get("profile_id") else None
    return {
        "application": app, "profile": profile, "nombre": _display_name(profile), "grid": grid, "ready": ready, "grid_version": (grid or {}).get("version"),
        "criterios_por_bloque": {b["key"]: [c for c in criterios if c["bloque"] == b["key"]] for b in scoring_engine.BLOQUES}, "bloques": scoring_engine.BLOQUES,
        "opciones": scoring_engine.OPCIONES_POR_BLOQUE, "evaluation": existing, "revision": (existing or {}).get("revision", 0), "previous": previous,
        "outdated": bool(existing) and not scoring_engine.esta_vigente(grid, existing), "comentario_general": (existing or {}).get("comentario_general") or "",
        "editable": busqueda.get("estado") == "activo", "evaluator": who,
    }


def save_evaluation(busqueda_id: str, application_id: str, *, recorded_by: str | None, campos, expected_revision) -> tuple[dict, bool]:
    """Guarda (upsert) la evaluación CCHH del evaluador autenticado sobre la VERSIÓN VIGENTE de la grilla. El evaluador es siempre quien registra (identidad de sesión): no se acepta un evaluador enviado por el cliente."""
    busqueda = _writable_busqueda(busqueda_id)
    app = _application(busqueda, application_id)
    profile = profile_repository.get(app.get("profile_id")) if app.get("profile_id") else None
    if profile is None or profile.get("deleted_at"):
        raise LookupError("El candidato ya no está disponible en el Banco de CVs.")
    grid = scoring_grid_repository.get(busqueda_id)
    if not grid:
        raise scoring_engine.ScoringValidationError(["Todavía no hay una grilla de evaluación para este proceso."])
    try:
        revision = int(expected_revision)
    except (TypeError, ValueError):
        raise ValueError("Revisión de la evaluación inválida.") from None
    criterios = [c for c in grid.get("criterios", []) if c.get("fase") == scoring_engine.FASE_CCHH]
    record = scoring_engine.construir_evaluacion(
        grid, application_id=application_id, fase=scoring_engine.FASE_CCHH, recorded_by=recorded_by,
        detalles=scoring_engine.parsear_formulario_evaluacion(criterios, campos), comentario_general=campos.get("comentario_general"))
    return scoring_evaluation_repository.save(record, revision)


def ranking_view(busqueda: dict, current_user: str | None) -> dict:
    """Ranking de scoring CCHH del proceso: una fila por postulación con puntaje (promedio de evaluaciones VIGENTES), tier, dispersión, excluyentes no cumplidos y cobertura explícita (sin evaluar / desactualizada /
    vigente). Alcance: SOLO scoring CCHH — no es un consolidado con la fase técnica."""
    bid = busqueda["busqueda_id"]
    grid = scoring_grid_repository.get(bid)
    criterios = [c for c in (grid or {}).get("criterios", []) if c.get("fase") == scoring_engine.FASE_CCHH]
    nombres = {c["criterio_id"]: c["nombre"] for c in criterios}
    evaluaciones = [e for e in scoring_evaluation_repository.list_for_busqueda(bid) if e.get("fase") == scoring_engine.FASE_CCHH]
    apps = busqueda.get("candidatos", [])
    estados = scoring_engine.cobertura(grid, [a["application_id"] for a in apps], evaluaciones)
    me = scoring_engine._identidad(current_user)
    items = []
    for app in apps:
        aid = app["application_id"]
        profile = profile_repository.get(app.get("profile_id")) if app.get("profile_id") else None
        res = scoring_engine.resultado_candidato(aid, evaluaciones, grid)
        propias = [e for e in evaluaciones if e.get("application_id") == aid]
        incumplidos = sorted({nombres.get(d["criterio_id"], "(criterio eliminado)") for e in propias if scoring_engine.esta_vigente(grid, e) for d in e.get("detalles", []) if d.get("cumple") is False})
        detalle = [{"evaluator": e["evaluator"], "recorded_by": e.get("recorded_by"), "puntaje": e.get("puntaje"), "grid_version": e.get("grid_version"), "vigente": scoring_engine.esta_vigente(grid, e),
                    "recorded_at": e.get("recorded_at"), "comentario_general": e.get("comentario_general"), "cumple": e.get("cumple_excluyentes"),
                    "respuestas": [{"criterio": nombres.get(d["criterio_id"], "(criterio eliminado)"), "nota": d.get("nota"), "cumple": d.get("cumple"), "comentario": d.get("comentario")} for d in e.get("detalles", [])]}
                   for e in propias]
        items.append({**res, "application_id": aid, "profile_id": app.get("profile_id"), "nombre": _display_name(profile), "persona_eliminada": not profile or bool(profile.get("deleted_at")),
                      "etapa": app.get("etapa"), "estado": estados[aid], "incumplidos": incumplidos, "evaluaciones": detalle,
                      "mi_evaluacion": any(d["evaluator"] == me for d in detalle) if me else False})
    ordered = scoring_engine.ordenar_ranking(items)
    return {"filas": ordered, "grid_version": (grid or {}).get("version"), "ready": bool(grid) and scoring_engine.grilla_valida(criterios), "dispersion_alerta": scoring_engine.DISPERSION_ALERTA,
            "conteos": {k: sum(1 for i in ordered if i["estado"] == k) for k in ("sin_evaluar", "desactualizada", "vigente")}}
