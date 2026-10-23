"""scoring_engine.py — motor PURO del scoring de candidatos por Job Description (sin I/O, sin HTTP).

Fuente de dominio: la app de referencia (`app-seleccion`, `lib/scoring.ts`): bloques de criterios, nota 1-5 (la UI de CCHH ofrece 1/3/5 en palabras), pesos que suman 100, puntaje 0-100, tiers y alerta de
dispersión entre evaluadores. ÚNICO lugar con las constantes (umbrales, bloques, escala): las vistas y el JavaScript nunca las repiten.

Alcance de esta etapa: **scoring CCHH**. El modelo ya distingue `fase` (`cchh` | `tecnica`) para que la fase técnica entre sin migrar datos, pero solo `cchh` está habilitada y NINGÚN resultado es un
consolidado CCHH/técnico (los pesos entre fases todavía no están definidos).

Modelo de evaluación (contrato; la persistencia llega en el siguiente incremento):
  - una evaluación = (postulación, fase, `evaluator`): `evaluator` es QUIÉN evalúa y `recorded_by` QUIÉN la registra; ambos son identidades verificadas (sesión) y por defecto coinciden. Registrar a nombre de
    otra persona exige una delegación explícita que hoy NO existe (sin suplantación);
  - cada evaluación guarda el `grid_id` y la `grid_version` contra la que se hizo: si la grilla cambia (nueva versión), la evaluación queda DESACTUALIZADA y no cuenta en el resultado hasta re-evaluar."""
from __future__ import annotations

from datetime import datetime, timezone

FASE_CCHH, FASE_TECNICA = "cchh", "tecnica"
FASES = (FASE_CCHH, FASE_TECNICA)
FASES_HABILITADAS = (FASE_CCHH,)                  # la fase técnica se habilita con la plantilla de Arquitectura (incremento 3)
FASE_LABELS = {FASE_CCHH: "Entrevista CCHH", FASE_TECNICA: "Entrevista técnica"}

# Umbrales CENTRALIZADOS (primera versión: fijos; más adelante pueden volverse configurables sin tocar vistas).
TIER_A_MIN = 75
TIER_B_MIN = 60
DISPERSION_ALERTA = 20                            # puntos de diferencia entre evaluadores que disparan la alerta

PESO_TOTAL = 100
_PESO_TOLERANCIA = 0.01
NOTA_MIN, NOTA_MAX = 1, 5
TEXTO_CORTO_MAX, TEXTO_LARGO_MAX = 200, 2000

BLOQUES = (
    {"key": "excluyente", "label": "Preguntas filtro (excluyentes)",
     "ayuda": "Cumple / No cumple. Se validan en la entrevista CCHH (ej. «¿Usaste Datasphere en un proyecto real?»). Si no cumple, queda en «No aplica». No suman puntos."},
    {"key": "tecnico", "label": "Validación técnica básica",
     "ayuda": "Lo técnico que CCHH puede validar sin ser técnica: experiencia de uso, proyectos, rol, tiempo. La validación en profundidad queda para la entrevista técnica."},
    {"key": "deseable", "label": "Deseables", "ayuda": "Suman pero no descartan (certificaciones, industria, herramientas extra)."},
    {"key": "competencia", "label": "Competencias soft", "ayuda": "Comunicación, autonomía, trabajo en equipo, fit cultural, seniority esperado."},
)
BLOQUE_KEYS = tuple(b["key"] for b in BLOQUES)

# Escala de la entrevista CCHH: 3 opciones en palabras por bloque; se guardan como nota 1 / 3 / 5 (= 0 / 50 / 100 pts).
OPCIONES_POR_BLOQUE = {
    "tecnico": ({"nota": 1, "label": "Sin experiencia", "ayuda": "Solo cursos o nunca lo usó"},
                {"nota": 3, "label": "Experiencia puntual", "ayuda": "1 proyecto o menos de 1 año"},
                {"nota": 5, "label": "Experiencia sólida", "ayuda": "Varios proyectos o más de 1 año, con rol activo"}),
    "deseable": ({"nota": 1, "label": "No tiene", "ayuda": "No lo tiene ni lo usó"},
                 {"nota": 3, "label": "Parcial", "ayuda": "Conocimiento básico o en curso"},
                 {"nota": 5, "label": "Tiene", "ayuda": "Lo tiene o lo usa con fluidez"}),
    "competencia": ({"nota": 1, "label": "No lo demuestra", "ayuda": "No aparece en sus respuestas o ejemplos"},
                    {"nota": 3, "label": "Parcialmente", "ayuda": "Aparece con algún ejemplo, sin mucha profundidad"},
                    {"nota": 5, "label": "Claramente", "ayuda": "Lo muestra con ejemplos concretos y consistentes"}),
}


class ScoringIdentityError(ValueError):
    """Identidad de evaluador/registrante inválida o intento de suplantación."""


class ScoringValidationError(ValueError):
    """Grilla o evaluación inválida; `errors` lista los motivos (en español, para mostrar)."""
    def __init__(self, errors: list[str]):
        super().__init__(" ".join(errors))
        self.errors = errors


# ───────────────────────── grilla ─────────────────────────
def es_ponderable(bloque: str) -> bool:
    return bloque != "excluyente"


def _num(value) -> float | None:
    try:
        n = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def suma_pesos(criterios, fase: str = FASE_CCHH) -> float:
    total = sum(float(c.get("peso") or 0) for c in criterios if c.get("fase", FASE_CCHH) == fase and es_ponderable(c.get("bloque")))
    return round(total * 100) / 100


def grilla_valida(criterios, fase: str = FASE_CCHH) -> bool:
    """La fase sirve para evaluar si tiene al menos un criterio ponderable y los pesos de la fase suman 100."""
    ponderables = [c for c in criterios if c.get("fase", FASE_CCHH) == fase and es_ponderable(c.get("bloque"))]
    return bool(ponderables) and abs(suma_pesos(criterios, fase) - PESO_TOTAL) < _PESO_TOLERANCIA


def normalizar_criterios(filas, *, fase: str = FASE_CCHH) -> tuple[list[dict], list[str]]:
    """Filas crudas (de un formulario) -> `(criterios limpios, errores)`. Una fila sin nombre y sin otros datos se descarta; el peso de un excluyente es siempre 0. NO asigna ids ni versión (lo hace el repositorio)."""
    limpios, errores = [], []
    for i, fila in enumerate(filas or [], start=1):
        nombre = str(fila.get("nombre") or "").strip()
        bloque = str(fila.get("bloque") or "").strip()
        resto = [str(fila.get(k) or "").strip() for k in ("descripcion", "ancla_1", "ancla_3", "ancla_5", "preguntas_guia")]
        peso_raw = str(fila.get("peso") or "").strip()
        if not nombre and not any(resto) and (not peso_raw or _num(peso_raw) == 0):
            continue
        if bloque not in BLOQUE_KEYS:
            errores.append(f"El criterio {i} tiene un bloque inválido."); continue
        if not nombre:
            errores.append(f"El criterio {i} necesita un nombre."); continue
        if len(nombre) > TEXTO_CORTO_MAX:
            errores.append(f"El nombre del criterio «{nombre[:30]}…» supera los {TEXTO_CORTO_MAX} caracteres."); continue
        if any(len(t) > TEXTO_LARGO_MAX for t in resto):
            errores.append(f"El criterio «{nombre}» tiene un texto de más de {TEXTO_LARGO_MAX} caracteres."); continue
        if es_ponderable(bloque):
            peso = _num(peso_raw) if peso_raw else 0.0
            if peso is None or peso < 0 or peso > PESO_TOTAL:
                errores.append(f"El peso de «{nombre}» tiene que ser un número entre 0 y {PESO_TOTAL}."); continue
        else:
            peso = 0.0
        limpios.append({"fase": fase, "bloque": bloque, "nombre": nombre, "descripcion": resto[0] or None, "peso": round(peso * 100) / 100,
                        "ancla_1": resto[1] or None, "ancla_3": resto[2] or None, "ancla_5": resto[3] or None, "preguntas_guia": resto[4] or None,
                        "criterio_id": str(fila.get("criterio_id") or "").strip() or None})
    return limpios, errores


def validar_grilla(criterios, fase: str = FASE_CCHH) -> list[str]:
    """Reglas de una grilla guardable de la fase: al menos un criterio ponderable y pesos que suman 100."""
    if fase not in FASES:
        return ["Fase inválida."]
    if fase not in FASES_HABILITADAS:
        return [f"La fase «{FASE_LABELS.get(fase, fase)}» todavía no está habilitada."]
    errores = []
    if not [c for c in criterios if c.get("fase", FASE_CCHH) == fase and es_ponderable(c.get("bloque"))]:
        errores.append("Agregá al menos un criterio con ponderación (validación técnica básica, deseables o competencias).")
    elif abs(suma_pesos(criterios, fase) - PESO_TOTAL) >= _PESO_TOLERANCIA:
        errores.append(f"La ponderación de los criterios técnicos, deseables y de competencias tiene que sumar {PESO_TOTAL}% (hoy suma {suma_pesos(criterios, fase):g}%).")
    return errores


# ───────────────────────── puntaje y tiers ─────────────────────────
def calcular_puntaje(criterios, notas: dict, fase: str = FASE_CCHH) -> float | None:
    """Puntaje 0-100 de UNA evaluación: nota 1 -> 0, nota 5 -> 100, ponderado por peso. `None` si falta la nota de algún criterio ponderable."""
    ponderables = [c for c in criterios if c.get("fase", FASE_CCHH) == fase and es_ponderable(c.get("bloque"))]
    if not ponderables:
        return None
    total = 0.0
    for c in ponderables:
        nota = notas.get(c["criterio_id"])
        if nota is None:
            return None
        total += (float(c["peso"]) / 100) * ((nota - NOTA_MIN) / (NOTA_MAX - NOTA_MIN))
    return round(total * 10000) / 100


def calcular_puntaje_parcial(criterios, notas: dict, fase: str = FASE_CCHH) -> float:
    """Como `calcular_puntaje` pero cuenta las notas faltantes como 0 (avance en vivo mientras se completa el formulario)."""
    total = 0.0
    for c in criterios:
        if c.get("fase", FASE_CCHH) == fase and es_ponderable(c.get("bloque")) and notas.get(c["criterio_id"]) is not None:
            total += (float(c["peso"]) / 100) * ((notas[c["criterio_id"]] - NOTA_MIN) / (NOTA_MAX - NOTA_MIN))
    return round(total * 10000) / 100


def tier_desde_puntaje(puntaje: float | None, cumple_excluyentes: bool) -> str | None:
    if not cumple_excluyentes:
        return "No aplica"
    if puntaje is None:
        return None
    if puntaje >= TIER_A_MIN:
        return "A"
    if puntaje >= TIER_B_MIN:
        return "B"
    return "C"


# ───────────────────────── identidades (sin suplantación) ─────────────────────────
def _identidad(valor) -> str:
    return str(valor or "").strip().lower()


def resolver_identidades(recorded_by, evaluator=None, *, delegacion_permitida: bool = False) -> tuple[str, str]:
    """`(evaluator, recorded_by)` verificados. `recorded_by` es la identidad AUTENTICADA de quien registra (obligatoria). `evaluator` por defecto es el mismo; registrar a nombre de otra persona solo se admite con una
    delegación explícita (hoy ningún rol la concede: `delegacion_permitida=False`)."""
    registrante = _identidad(recorded_by)
    if not registrante:
        raise ScoringIdentityError("No se pudo identificar a quien registra la evaluación.")
    evaluador = _identidad(evaluator) or registrante
    if evaluador != registrante and not delegacion_permitida:
        raise ScoringIdentityError("No se puede registrar una evaluación a nombre de otra persona.")
    return evaluador, registrante


# ───────────────────────── evaluaciones ─────────────────────────
def clave_evaluacion(application_id: str, fase: str, evaluator: str) -> str:
    return f"{application_id}:{fase}:{_identidad(evaluator)}"


def construir_evaluacion(grilla: dict, *, application_id: str, fase: str, recorded_by, evaluator=None, detalles: dict, comentario_general=None, ahora: str | None = None,
                         delegacion_permitida: bool = False) -> dict:
    """Registro de evaluación (sin persistir) contra la VERSIÓN VIGENTE de la grilla. `detalles`: `{criterio_id: {"nota": 1-5 | "cumple": bool, "comentario": str}}`; todos los criterios de la fase son obligatorios
    (excluyente: `cumple`; ponderable: `nota`). Guarda `grid_id` + `grid_version` para detectar después los resultados desactualizados."""
    if fase not in FASES_HABILITADAS:
        raise ScoringValidationError([f"La fase «{FASE_LABELS.get(fase, fase)}» todavía no está habilitada."])
    evaluador, registrante = resolver_identidades(recorded_by, evaluator, delegacion_permitida=delegacion_permitida)
    criterios = [c for c in grilla.get("criterios", []) if c.get("fase", FASE_CCHH) == fase]
    if not grilla_valida(criterios, fase):
        raise ScoringValidationError(["La grilla de esta fase todavía no está completa (la ponderación tiene que sumar 100%)."])
    errores, filas, notas = [], [], {}
    for c in criterios:
        d = (detalles or {}).get(c["criterio_id"]) or {}
        if c["bloque"] == "excluyente":
            if not isinstance(d.get("cumple"), bool):
                errores.append(f"Marcá si cumple el excluyente «{c['nombre']}».")
            filas.append({"criterio_id": c["criterio_id"], "nota": None, "cumple": d.get("cumple"), "comentario": (str(d.get("comentario") or "").strip() or None)})
        else:
            nota = d.get("nota")
            if isinstance(nota, bool) or not isinstance(nota, int) or not NOTA_MIN <= nota <= NOTA_MAX:
                errores.append(f"Falta la nota de «{c['nombre']}».")
            else:
                notas[c["criterio_id"]] = nota
            filas.append({"criterio_id": c["criterio_id"], "nota": nota if isinstance(nota, int) and not isinstance(nota, bool) else None, "cumple": None,
                          "comentario": (str(d.get("comentario") or "").strip() or None)})
    if errores:
        raise ScoringValidationError(errores)
    cumple = all(f["cumple"] is True for f, c in zip(filas, criterios) if c["bloque"] == "excluyente")
    return {
        "evaluation_key": clave_evaluacion(application_id, fase, evaluador), "application_id": application_id, "busqueda_id": grilla.get("busqueda_id"), "fase": fase,
        "evaluator": evaluador, "recorded_by": registrante, "grid_id": grilla.get("grid_id"), "grid_version": grilla.get("version"),
        "puntaje": calcular_puntaje(criterios, notas, fase), "cumple_excluyentes": cumple, "comentario_general": (str(comentario_general or "").strip() or None),
        "detalles": filas, "recorded_at": ahora or datetime.now(timezone.utc).isoformat(),
    }


def esta_vigente(grilla: dict | None, evaluacion: dict) -> bool:
    """Una evaluación vale solo contra la MISMA grilla (identidad) y la MISMA versión con la que se hizo."""
    return bool(grilla) and evaluacion.get("grid_id") == grilla.get("grid_id") and evaluacion.get("grid_version") == grilla.get("version")


def resultado_candidato(application_id: str, evaluaciones, grilla: dict | None, fase: str = FASE_CCHH) -> dict:
    """Resultado de scoring CCHH de una postulación: promedio de las evaluaciones VIGENTES (misma versión de grilla), dispersión, excluyentes, tier y cuántas quedaron desactualizadas. No es un consolidado entre fases."""
    propias = [e for e in evaluaciones if e.get("application_id") == application_id and e.get("fase") == fase]
    vigentes = [e for e in propias if esta_vigente(grilla, e)]
    puntajes = [float(e["puntaje"]) for e in vigentes if e.get("puntaje") is not None]
    puntaje = round(sum(puntajes) / len(puntajes) * 100) / 100 if puntajes else None
    dispersion = round((max(puntajes) - min(puntajes)) * 100) / 100 if len(puntajes) > 1 else None
    cumple = all(e.get("cumple_excluyentes") for e in vigentes)
    return {"application_id": application_id, "fase": fase, "alcance": "scoring_cchh" if fase == FASE_CCHH else "scoring_tecnico", "puntaje": puntaje, "dispersion": dispersion,
            "alerta_dispersion": dispersion is not None and dispersion > DISPERSION_ALERTA, "cumple_excluyentes": cumple,
            "tier": tier_desde_puntaje(puntaje, cumple) if vigentes else None, "evaluaciones_vigentes": len(vigentes), "evaluaciones_desactualizadas": len(propias) - len(vigentes)}


def cobertura(grilla: dict | None, application_ids, evaluaciones, fase: str = FASE_CCHH) -> dict:
    """Estado explícito por postulación: `sin_evaluar` (sin ninguna evaluación de la fase), `desactualizada` (solo evaluaciones de versiones anteriores de la grilla) o `vigente`."""
    estados = {}
    for app_id in application_ids:
        propias = [e for e in evaluaciones if e.get("application_id") == app_id and e.get("fase") == fase]
        if not propias:
            estados[app_id] = "sin_evaluar"
        elif any(esta_vigente(grilla, e) for e in propias):
            estados[app_id] = "vigente"
        else:
            estados[app_id] = "desactualizada"
    return estados
