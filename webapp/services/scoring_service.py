"""scoring_service.py — casos de uso del Scoring (JD) de un proceso de selección (alcance de esta etapa: scoring CCHH: JD + grilla).

Capa fina entre el router y los repositorios: valida contra el motor puro (`scoring_engine`), exige un proceso GESTIONADO y ACTIVO para escribir y delega la persistencia en `scoring_grid_repository` (grilla) y
`busqueda_repository` (JD: sigue siendo `busqueda.jd_texto`/`palabras_clave`, sin copias)."""
from __future__ import annotations

from webapp.services import busqueda_repository, scoring_engine, scoring_grid_repository

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
