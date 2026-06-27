"""
prompts.py — Constructores de prompts para el LLM.

ARQUITECTURA DE ENRIQUECIMIENTO (dos niveles):

  Nivel A — IMPLEMENTADO (PoC)
  ─────────────────────────────
  Los prompts se enriquecen con contexto de la case_library/ en cada
  invocación. Filtro por tree_id (árbol) o module (módulo).
  Fuente: case_library/*.yaml leídos en tiempo real.

  Nivel B — DISEÑADO, pendiente de implementar
  ─────────────────────────────────────────────
  Filtro por similitud de ruta (route signature).
  Migración A → B: reemplazar _load_relevant_cases() con versión
  que reciba la route del run actual y filtre por similitud.
  El resto del código no cambia.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

# Tags Markdown propios:
# - section_meta: keywords invisibles para routing/detección.
# - semantic_summary: texto invisible en UI pero útil para el LLM cuando el
#   conocimiento está representado visualmente por html_embed o imágenes.
# - section_mode: clasificación editorial; por ejemplo visual_only.
_SECTION_META_RE = re.compile(r"^\s*\{\{\s*section_meta\s*:.*?\}\}\s*$", re.I | re.M)
_SECTION_MODE_RE = re.compile(r"^\s*\{\{\s*section_mode\s*:.*?\}\}\s*$", re.I | re.M)
_SECTION_ID_RE = re.compile(r"^\s*\{\{\s*section_id\s*:\s*[0-9]+\s*\}\}\s*$", re.I | re.M)
_SEMANTIC_SUMMARY_RE = re.compile(
    r"^\s*\{\{\s*semantic_summary\s*\}\}\s*$\n?(.*?)^\s*\{\{\s*/\s*semantic_summary\s*\}\}\s*$",
    re.I | re.M | re.S,
)
_HTML_EMBED_RE = re.compile(r"^\s*\{\{\s*html_embed\s*:.*?\}\}\s*$", re.I | re.M)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_HEADING_TONE_RE = re.compile(r"\{dw-(?:black|gold|muted|accent|danger)\}", re.I)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _clean_heading_label(text: str) -> str:
    """Quita tags visuales del heading antes de usarlo en prompts."""
    text = _HEADING_TONE_RE.sub("", text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()

# ── Acciones disponibles en el panel de sección ───────────────────────────────
ACTION_FULL       = "Salida completa (operativa)"
ACTION_CHECKLIST  = "Checklist ejecutable"
ACTION_DECISIONS  = "Decisiones si/entonces"
ACTION_ANTIPATTERNS = "Anti-patterns y riesgos"
ACTION_REVIEW     = "Mirada Danwor"

ALL_ACTIONS = [
    ACTION_FULL,
    ACTION_CHECKLIST,
    ACTION_DECISIONS,
    ACTION_ANTIPATTERNS,
    ACTION_REVIEW,
]

# ── Quick actions (sidebar) ────────────────────────────────────────────────────
QUICK_ACTIONS: dict[str, str] = {
    "Guía operativa del módulo": """\
Actuá como Arquitecto Senior Danwor revisando el módulo activo bajo presión real de proyecto.

Usá el markdown activo como contrato de contenido: `scope`, `guardrails`, `semantic_summary`,
`quick_action_guidance`, secciones, assets, árboles y apéndices. El foco editorial, prioridades,
ejemplos y exclusiones específicas deben venir del módulo. `prompts.py` solo define la estructura.

Estructura la respuesta así:
- Una introducción breve de 2-3 líneas, sin numerar, explicando cómo usar esta guía bajo presión de proyecto.
- Una línea en blanco.
- Un título en negrita: **Guía operativa del módulo**
- Luego la lista numerada.

Entregá en la lista numerada:
1. Decisión arquitectónica dominante que este módulo obliga a tomar.
2. Tres decisiones donde el consultor típicamente se equivoca.
3. Qué priorizarías si el tiempo es limitado.
4. Riesgos reales si se toma la decisión incorrecta.
5. Recomendación Danwor clara (una postura final).

Usá lista numerada Markdown válida: `1. `, `2. `, `3. ` con punto y espacio.
Usá terminología Danwor. No describas: decidí.
Si depende de contexto no provisto, indicalo explícitamente.
""",
    "Checklist de implementación": """\
Actuá como Arquitecto Senior Danwor preparando un checklist operativo para una reunión técnica crítica.

Usá el markdown activo como contrato de contenido: `scope`, `guardrails`, `semantic_summary`,
`quick_action_guidance`, secciones, assets, árboles y apéndices. Los puntos obligatorios,
dependencias, exclusiones y foco técnico deben venir del módulo. `prompts.py` solo define la estructura.

Entregá:
1. Checklist de 10-12 ítems ordenados por dependencia (lo que bloquea va primero).
2. Para cada ítem: qué validar, con quién, y qué pasa si no está resuelto.

Usá terminología Danwor. Sé específico.
Si un ítem depende de contexto no provisto, marcalo con [VALIDAR CON CLIENTE].
No agregues una sección final de "ítems que se saltean", "errores frecuentes" ni recomendaciones genéricas.
No cierres con una frase de coaching al consultor ni hables en tercera persona. Terminá con el último ítem del checklist.
""",
    "Errores frecuentes": """\
Actuá como Arquitecto Senior Danwor haciendo una revisión post-mortem de proyectos anteriores.

Usá el markdown activo como contrato de contenido: `scope`, `guardrails`, `semantic_summary`,
`quick_action_guidance`, secciones, assets, árboles y apéndices. Los errores prioritarios,
causas raíz y correcciones Danwor deben venir del módulo. `prompts.py` solo define la estructura.

Entregá:
1. 8 errores frecuentes, ordenados de mayor a menor impacto en el proyecto.
2. Para cada error: cómo se manifiesta, por qué ocurre, y cómo lo corrige el marco Danwor.
3. El error que más cuesta dinero o tiempo si no se detecta a tiempo.

Usá terminología Danwor. No describas síntomas solamente: describí causas raíz.
Si el error depende de contexto no provisto, indicalo explícitamente.
""",
    "Ejemplos prácticos": """\
Actuá como Arquitecto Senior Danwor explicándole a un consultor cómo se ve el módulo activo en un proyecto real.

Usá el markdown activo como contrato de contenido: `scope`, `guardrails`, `semantic_summary`,
`quick_action_guidance`, secciones, assets, árboles y apéndices. Los casos, fuentes internas y límites
editoriales deben venir del módulo. Si completás algo que no está explícitamente documentado, marcá esa parte con [INFERENCIA].

Entregá una introducción breve de 1-2 líneas indicando de qué fuentes internas te apoyaste.
Dejá siempre una línea en blanco entre la introducción y el primer ejemplo.

Luego entregá 2 o 3 ejemplos, no más, con este formato:

**Ejemplo N: <nombre del caso o escenario>**
- **Fuente interna usada:** apéndice, sección, checklist o asset utilizado.
- **Contexto:** tipo de cliente/sistema/restricción real.
- **Decisión Danwor:** postura o recomendación concreta.
- **Por qué esa y no otra:** criterio arquitectónico.
- **Riesgo si se elige mal:** consecuencia práctica.

Cerrá con:
**Cuándo aplicarlo sin dudar:** 2 bullets.
**Cuándo pedir más contexto:** 2 bullets.

Usá terminología Danwor. Nombrá tecnologías reales del módulo activo.
Si no encontrás material interno suficiente, decilo explícitamente antes de usar ejemplos hipotéticos.
""",
}

# Acciones a nivel módulo (cabecera): mismo orden de QUICK_ACTIONS. Se ofrecen en
# la pestaña "Sección" del panel derecho cuando el target es "Módulo (general)".
MODULE_ACTIONS = list(QUICK_ACTIONS.keys())

MODULE_ACTION_DESCRIPTIONS = {
    "Guía operativa del módulo": "Decisión arquitectónica dominante del módulo, dónde se equivoca el consultor y la postura Danwor.",
    "Checklist de implementación": "Checklist ordenado por dependencia para una reunión técnica: qué validar, con quién y qué bloquea.",
    "Errores frecuentes": "Post-mortem: los errores más caros del módulo, su causa raíz y cómo los corrige el marco Danwor.",
    "Ejemplos prácticos": "2-3 casos reales (apoyados en fuentes internas) con contexto, decisión Danwor y riesgo si se elige mal.",
}

# =========================================================
# Case library — carga y filtrado
# =========================================================

def _case_library_dir() -> Path:
    """Directorio de casos. Fuente de verdad: config.CASE_LIBRARY_DIR."""
    from config import CASE_LIBRARY_DIR
    return CASE_LIBRARY_DIR


def _load_relevant_cases(
    tree_id: str | None = None,
    module: str | None = None,
    max_cases: int = 5,
) -> list[dict]:
    """
    Nivel A: carga casos filtrando por tree_id (prioridad) o module (fallback).
    Liviano en PoC (<50 casos). Con >200 casos considerar st.cache_data(ttl=300).
    """
    case_dir = _case_library_dir()
    if not case_dir.exists():
        return []

    candidates: list[tuple[int, dict]] = []
    for p in sorted(case_dir.glob("*.yaml")):
        try:
            c = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(c, dict) or not c.get("case_id"):
                continue
            case_tree   = (c.get("source") or {}).get("tree_id", "")
            case_module = (c.get("source") or {}).get("module", "")
            if tree_id and case_tree == tree_id:
                candidates.append((0, c))
            elif module and case_module == module:
                candidates.append((1, c))
        except Exception:
            continue

    candidates.sort(key=lambda x: (x[0], -(len(x[1].get("route") or []))))
    return [c for _, c in candidates[:max_cases]]


def _format_cases_for_prompt(cases: list[dict]) -> str:
    """Serializa casos en formato compacto para inyectar en el prompt."""
    if not cases:
        return ""

    lines = ["── CASOS REALES DANWOR (base de conocimiento) ──"]
    for i, c in enumerate(cases, 1):
        tree      = (c.get("source") or {}).get("tree_id", "")
        outcome   = c.get("outcome", "")
        ctx       = (c.get("context_description") or "").strip()
        dec       = (c.get("decision_taken") or "").strip()
        lessons   = [l for l in (c.get("lessons") or []) if l]
        notes     = [n for n in (c.get("leaf_notes") or []) if n]
        route     = c.get("route") or []
        uncharted = c.get("uncharted", False)

        route_str = " → ".join(
            f"{r['node']}:{r['answer']}" for r in route if isinstance(r, dict)
        ) if route else "camino no contemplado"

        lines.append(
            f"\nCaso {i}"
            + (" [no contemplado]" if uncharted else "")
            + f" | árbol: {tree} | outcome: {outcome}"
        )
        lines.append(f"  Ruta: {route_str}")
        if ctx:
            lines.append(f"  Contexto: {ctx[:200]}")
        if dec:
            lines.append(f"  Decisión tomada: {dec[:200]}")
        for l in (lessons + notes)[:3]:
            lines.append(f"  Lección: {l}")

    lines.append("─" * 48)
    return "\n".join(lines)


def cases_available(tree_id: str | None = None, module: str | None = None) -> bool:
    """Helper para que tools_panel sepa si hay casos antes de armar el spinner."""
    return len(_load_relevant_cases(tree_id=tree_id, module=module, max_cases=1)) > 0


def get_loading_message(action: str, has_cases: bool) -> str:
    base = {
        "Guía operativa del módulo":   "Preparando guía operativa",
        "Checklist de implementación":  "Generando checklist",
        "Errores frecuentes":           "Relevando errores frecuentes",
        "Ejemplos prácticos":           "Preparando ejemplos",
        ACTION_FULL:          "Preparando salida operativa",
        ACTION_CHECKLIST:     "Generando checklist ejecutable",
        ACTION_DECISIONS:     "Elaborando árbol de decisiones",
        ACTION_ANTIPATTERNS:  "Relevando anti-patterns y riesgos",
        ACTION_REVIEW:        "Consultando postura metodológica Danwor",
        "Explicación":        "Consultando al Arquitecto Senior Danwor",
        "Pregunta libre":     "Obteniendo la mejor respuesta",
        "Valor agregado":     "Enriqueciendo con inteligencia del árbol",
    }.get(action, "Obteniendo la mejor respuesta")

    suffix = " · enriqueciendo con casos reales..." if has_cases else "..."
    return base + suffix

# ── Clasificador de ambigüedad de profundidad ──────────────────────────────────
DEPTH_MODES = {
    "metodologico": {
        "label": "📋 Contexto metodológico",
        "desc":  "Qué dice el marco Danwor sobre esto",
        "instruction": (
            "Respondé desde el marco metodológico Danwor. "
            "Enfocate en conceptos, criterios de decisión y postura del marco. "
            "Sin código ni pasos técnicos detallados."
        ),
    },
    "tecnico": {
        "label": "🔧 Detalle técnico",
        "desc":  "Cómo implementarlo concretamente",
        "instruction": (
            "Respondé con foco técnico e implementación concreta. "
            "Incluí código, anotaciones, configuraciones o pasos específicos según corresponda. "
            "Asumí que el consultor tiene conocimiento técnico."
        ),
    },
    "ejemplos": {
        "label": "💡 Con ejemplos prácticos",
        "desc":  "Casos aplicados y situaciones reales",
        "instruction": (
            "Respondé con ejemplos concretos y casos prácticos. "
            "Mostrá cómo se aplica en situaciones reales de proyectos SAP/Datasphere. "
            "Combiná contexto metodológico con ilustraciones tangibles."
        ),
    },
    "decision": {
        "label": "⚖️ Decisión guiada",
        "desc":  "Qué haría Danwor en este caso",
        "instruction": (
            "Respondé como Arquitecto Senior Danwor tomando una decisión. "
            "Analizá el caso, identificá variables clave y recomendá el camino con justificación. "
            "Formato: situación → análisis → recomendación Danwor."
        ),
    },
}

AMBIGUITY_SYSTEM = (
    "Sos un clasificador de consultas técnicas. "
    "Respondé SOLO con JSON válido, sin texto adicional ni backticks."
)

AMBIGUITY_PROMPT = """\
Analizá esta consulta y determiná si puede responderse de forma útil tanto a nivel conceptual/metodológico \
como a nivel técnico/implementación.

Consulta: "{question}"

Respondé EXACTAMENTE con este JSON:
{{"ambiguous": true}}   ← si la consulta tiene ambigüedad de profundidad
{{"ambiguous": false}}  ← si la consulta es claramente conceptual O claramente técnica

Ejemplos de ambiguas: "cómo integrar con Datasphere", "qué es ODP y cómo usarlo", "cómo modelar ventas"
Ejemplos de no ambiguas: "qué significa clean core" (conceptual), "cómo escribo @Analytics.dataExtraction" (técnica)
"""

def build_ambiguity_prompt(question: str) -> str:
    return AMBIGUITY_PROMPT.format(question=question)


# ── Module scope helpers ───────────────────────────────────────────────────────
def _load_module_meta(module_stem: str | None, pack_name: str | None = None) -> dict:
    """Lee el frontmatter del .md del módulo activo. Retorna {} si no hay."""
    if not module_stem:
        return {}
    try:
        from core.content_registry import resolve_content_path
        md_path = resolve_content_path(module_stem, pack_name)
        if not md_path.exists():
            return {}
        text = md_path.read_text(encoding="utf-8")
        m = _FRONT_MATTER_RE.match(text)
        if not m:
            return {}
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def _format_guardrails(meta: dict) -> str:
    """
    Serializa guardrails del frontmatter.

    Contrato editorial:
    - scope define el alcance temático del módulo.
    - out_of_scope_redirect deriva consultas a otro módulo.
    - intent_assets ofrece activos concretos.
    - guardrails define posturas normativas Danwor que el LLM debe respetar
      cuando genera recomendaciones, decisiones o anti-patterns.
    """
    guardrails = meta.get("guardrails") or []
    if isinstance(guardrails, str):
        guardrails = [guardrails]
    if not isinstance(guardrails, list):
        return ""

    lines: list[str] = []
    for item in guardrails:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            rule = str(item.get("rule") or item.get("text") or "").strip()
            text = f"{title}: {rule}" if title and rule else (rule or title)
        else:
            text = ""
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def get_out_of_scope_message(
    prompt: str, module_stem: str | None, pack_name: str | None = None,
) -> str | None:
    """
    Retorna mensaje de redirección si el prompt cae fuera del scope del módulo.
    Retorna None si está dentro del scope o no hay reglas definidas.
    """
    meta = _load_module_meta(module_stem, pack_name)
    redirects = meta.get("out_of_scope_redirect") or []
    prompt_lower = prompt.lower()
    for rule in redirects:
        keywords = rule.get("keywords") or []
        if any(kw.lower() in prompt_lower for kw in keywords):
            return rule.get("message", "Esa consulta corresponde a otro módulo.")
    return None


def get_out_of_scope_redirect(
    prompt: str, module_stem: str | None, pack_name: str | None = None,
) -> dict | None:
    """
    Igual que get_out_of_scope_message pero devuelve el objeto completo de la regla:
    { "message": ..., "module": "04", "label": "Modelado en SAP Datasphere", "keywords": [...] }
    Devuelve None si está dentro del scope.
    """
    meta = _load_module_meta(module_stem, pack_name)
    redirects = meta.get("out_of_scope_redirect") or []
    prompt_lower = prompt.lower()
    for rule in redirects:
        keywords = rule.get("keywords") or []
        if any(kw.lower() in prompt_lower for kw in keywords):
            return rule
    return None


# ── System prompt ──────────────────────────────────────────────────────────────
def build_system_prompt(
    module_stem: str | None = None,
    *,
    pack_name: str | None = None,
    user_context: dict | None = None,
) -> str:
    """
    Construye el system prompt inyectando scope del módulo activo si está disponible.
    module_stem: nombre del archivo .md sin extensión
                 (ej: "03_Integracion_y_Estrategia_de_Datos")
    """
    _user_ctx = ""
    try:
        from config import TITLE_CONTEXT
        if user_context is None:
            from auth import get_current_user as _get_auth_user
            _auth = _get_auth_user()
        else:
            _auth = user_context
        _first = _auth.get("first_name", "")
        _title = _auth.get("job_title", "") if TITLE_CONTEXT == "ON" else ""
        if _first:
            _user_ctx = f"\nEstás hablando con {_first}"
            if _title:
                _user_ctx += f", cuyo cargo en la empresa es \"{_title}\"."
                _user_ctx += (
                    " Usá ese contexto para calibrar el nivel técnico de tus respuestas"
                    " si lo considerás relevante."
                )
            else:
                _user_ctx += "."
            _user_ctx += (
                f" Cada tanto (no en cada mensaje) podés incluir el nombre {_first} en tu respuesta"
                " para que la conversación se sienta más personal y directa."
            )
    except ImportError:
        pass

    # Scope del módulo activo (leído del frontmatter del .md)
    _scope_block = ""
    meta = _load_module_meta(module_stem, pack_name)
    scope = (meta.get("scope") or "").strip()
    guardrails = _format_guardrails(meta)
    if scope:
        _scope_block = (
            f"\nScope de este módulo:\n{scope}\n"
            "Si una consulta cae fuera de este scope, indicalo claramente y "
            "sugerí el módulo correspondiente. "
            "No intentes responder temas que no están cubiertos en este módulo."
        )
    if guardrails:
        _scope_block += (
            f"\n\nGuardrails Danwor de este módulo:\n{guardrails}\n"
            "Estos guardrails son postura metodológica obligatoria. "
            "No los suavices como alternativas viables cuando describen anti-patterns."
        )

    return f"""\
Actuás como "Arquitecto Senior Danwor" (interno). Respondés SIEMPRE en español, lenguaje Danwor.
{_user_ctx}
Reglas:
0) Estilo: tono rioplatense/profesional. Usá español natural de Argentina/Uruguay sin exceso de informalidad.
   Preferí formas como "Encontré", "podés", "tenés", "querés" y evitá giros peninsulares o demasiado neutros
   como "he encontrado", "puedes", "tienes", "deseas" o "aquí", salvo que estés citando una fuente.
   Usá "acá" en lugar de "aquí" cuando corresponda.
1) No inventés decisiones de arquitectura, patrones ni recomendaciones que no estén en el módulo.
   Si algo no está en el módulo, decilo explícitamente.
2) SÍ podés y debés materializar técnicamente lo que el módulo describe: si el marco menciona
   una vista CDS, un objeto ABAP, una configuración — escribí el código o la estructura concreta.
   El consultor necesita poder usar lo que explicás, no solo leerlo.
3) Cuando escribas código técnico (ABAP, CDS, SQL, YAML, JSON), usá las anotaciones y sintaxis
   correctas y mínimas para el caso. No agregues anotaciones innecesarias ni genéricas.
4) Terminología: "solución híbrida", "federación vs persistencia", "clean core".
5) Evitá teoría genérica. Foco: decisiones, pasos, código accionable, riesgos.
6) Cuando tengas casos reales disponibles, priorizalos sobre generalidades.
{_scope_block}
Conocimiento técnico de referencia — CDS Views para SAP Datasphere (ODP/ABAP_CDS):
- Hay dos generaciones relevantes de ABAP CDS: CDS DDIC-based (`define view` con
  `@AbapCatalog.sqlViewName`) y CDS view entity (`define view entity`).
- NO afirmes que DDIC/Data Dictionary no soporta ODP o anotaciones @Analytics. Las CDS DDIC-based
  sí pueden exponer extracción ODP/ABAP_CDS con `@Analytics.dataExtraction.enabled`.
- Para extractores ODP-CDS productivos y compatibilidad amplia, Danwor prefiere CDS DDIC-based
  salvo que el release, la nota SAP aplicable y el conector confirmen soporte para view entity.
- Muchas CDS estándar liberadas por SAP para extracción siguen siendo DDIC-based por compatibilidad.
- Si el usuario pregunta DDIC vs view entity, respondé comparando generaciones CDS, compatibilidad ODP,
  release/soporte y postura Danwor. No lo redirijas a un template salvo que pida explícitamente código.
- La anotación mínima para habilitar extracción es @Analytics.dataExtraction.enabled: true
- @AccessControl.authorizationCheck: #NOT_REQUIRED es el estándar para vistas de extracción
- @Analytics.dataCategory: #FACT para tablas de hechos, #DIMENSION para maestros
- La vista DEBE tener clave primaria estable para soportar delta
- Para delta por campo de timestamp/fecha: @Analytics.dataExtraction.delta.byElement.name: '<campo>'
  con opcionales @Analytics.dataExtraction.delta.byElement.maxDelayInSeconds y detectDeletedRecords
- Para vistas con joins complejos usar @Analytics.dataExtraction.delta.changeDataCapture.mapping
  con role: #MAIN y mapeo de campos clave tabla→vista
- Para CDC automático en vistas simples: changeDataCapture.automatic: true
- Cuando el usuario pregunte por "delta", "incremental", "puntero delta" o "mantener datos actualizados",
  no respondas solo con `delta.byElement` salvo que el usuario haya dicho que la vista es simple y tiene
  un timestamp confiable. Presentá también `delta.changeDataCapture.mapping` como patrón moderno/robusto
  para joins, múltiples tablas, necesidad de detectar cambios en tablas relacionadas o mayor control.
- Si falta contexto para elegir el patrón delta, pedí release de S/4HANA/ABAP, si hay joins y si deben
  detectarse borrados antes de cerrar la recomendación.
- NO usar @OData.publish: true para integración con Datasphere — esa anotación es para servicios OData,
  no para extracción vía ABAP_CDS/ODP
- Siempre que el usuario pida un ejemplo, entregá al menos dos variantes: FULL y FULL+DELTA
"""


# ── Instrucción por acción ─────────────────────────────────────────────────────
def topic_instruction(action: str) -> str:
    instructions = {

        ACTION_FULL: (
            "Actuá como Arquitecto Senior Danwor preparando una síntesis ejecutiva para presentar al cliente.\n"
            "El cliente NO es técnico: evitá jerga SAP innecesaria, priorizá claridad y decisión.\n"
            "Entregá SOLO en este formato, sin secciones adicionales:\n\n"
            "**¿Qué resuelve esta sección?** (2-3 líneas: el problema que aborda y por qué importa al proyecto)\n\n"
            "**Lo que el cliente necesita saber:**\n"
            "(4-6 bullets concisos con el conocimiento clave para tomar decisiones)\n\n"
            "**Decisiones que se derivan:**\n"
            "(3-5 bullets con decisiones concretas que el cliente debe tomar o validar)\n\n"
            "**Riesgos si no se aborda correctamente:**\n"
            "(3-4 bullets con consecuencias reales de no seguir el marco)\n\n"
            "Tono: directo, ejecutivo, orientado a acción. Sin rodeos."
        ),

        ACTION_CHECKLIST: (
            "Actuá como Arquitecto Senior Danwor preparando un checklist de relevamiento técnico.\n"
            "El consultor va a usar esto en campo, con el cliente, para verificar que nada quede sin validar.\n"
            "Entregá SOLO un checklist ejecutable, sin secciones adicionales:\n\n"
            "**Checklist de relevamiento — {topic}**\n\n"
            "Cada ítem debe:\n"
            "- Ser verificable (respuesta Sí/No o valor concreto)\n"
            "- Estar redactado como pregunta o acción directa\n"
            "- Incluir entre paréntesis el riesgo si no se valida (breve)\n\n"
            "Formato por ítem: ☐ [Acción/pregunta] *(riesgo si se omite)*\n"
            "Entre 8 y 14 ítems. Ordenados por prioridad de relevamiento."
        ),

        ACTION_DECISIONS: (
            "Actuá como Arquitecto Senior Danwor extrayendo la lógica de decisión de esta sección.\n"
            "El objetivo es preparar al consultor para anticipar preguntas y escenarios en la reunión.\n"
            "Entregá SOLO decisiones en formato SI / ENTONCES, sin secciones adicionales:\n\n"
            "**Mapa de decisiones — {topic}**\n\n"
            "Formato estricto por ítem:\n"
            "**SI** [condición técnica o de negocio] → **ENTONCES** [acción o decisión recomendada] "
            "*(justificación en 1 línea)*\n\n"
            "Si la condición representa una expectativa del cliente contraria al marco Danwor, "
            "NO la presentes como camino viable. Redactá el ENTONCES como postura negativa: "
            "explicar por qué no conviene, qué riesgo evita el marco y qué alternativa gobernada ofrecer.\n\n"
            "Entre 5 y 9 reglas. Cubrí los escenarios más frecuentes y los bordes críticos."
        ),

        ACTION_ANTIPATTERNS: (
            "Actuá como Arquitecto Senior Danwor identificando errores frecuentes en proyectos reales.\n"
            "Muchos anti-patterns están implícitos en el marco — tu trabajo es hacerlos explícitos.\n"
            "Entregá SOLO la siguiente lista, sin secciones adicionales:\n\n"
            "**Anti-patterns y riesgos — {topic}**\n\n"
            "Formato estricto por ítem:\n"
            "❌ **[Qué se hace mal]**\n"
            "↳ Por qué está mal: [explicación breve]\n"
            "↳ Riesgo concreto: [consecuencia real en el proyecto]\n\n"
            "Entre 4 y 6 ítems. Priorizá los más frecuentes en proyectos SAP Datasphere reales."
        ),

        ACTION_REVIEW: (
            "Actuá como Arquitecto Senior Danwor revisando si una decisión o entregable respeta el marco.\n"
            "Esta acción va en dirección inversa: no explicás el marco, sino que lo usás como estándar "
            "para evaluar si algo está bien hecho.\n"
            "Entregá SOLO en este formato, sin secciones adicionales:\n\n"
            "**Postura Danwor sobre {topic}:**\n"
            "(2-4 líneas: qué exige el marco en este punto y por qué)\n\n"
            "**Evidencia mínima que debe estar presente:**\n"
            "(4-7 bullets: qué debería poder mostrarse en un entregable correcto)\n\n"
            "**Señales de desvío del marco:**\n"
            "(3-5 bullets: qué indica que algo NO está alineado — para usar como checklist de revisión)\n\n"
            "**Veredicto rápido:**\n"
            "(1 línea: si el consultor no puede mostrar la evidencia mínima, ¿qué implica?)\n\n"
            "Tono: criterioso pero constructivo. Como un peer review técnico senior."
        ),
    }
    return instructions.get(action, instructions[ACTION_REVIEW])


# ── Prompt helpers (comunes a todas las acciones) ──────────────────────────────

def _resolve_module_header(module_context: str, module: str | None) -> str:
    """Extrae solo el título del módulo desde el frontmatter."""
    fm_match = _FRONT_MATTER_RE.match(module_context or "")
    if fm_match:
        try:
            fm_data = yaml.safe_load(fm_match.group(1)) or {}
        except Exception:
            fm_data = {}
        return f"Módulo: {fm_data.get('title') or module or '?'}"
    return f"Módulo: {module or '?'}"


def _module_scope_for_topic_prompt(module: str | None, pack_name: str | None = None) -> str:
    """
    Devuelve el scope editorial del módulo para acciones de sección.

    El system prompt ya recibe este alcance, pero las acciones como "Salida
    completa" se construyen alrededor de un fragmento puntual. Reinyectarlo acá
    evita que una sección visual o breve opaque las reglas metodológicas del
    módulo.
    """
    meta = _load_module_meta(module, pack_name)
    scope = (meta.get("scope") or "").strip()
    guardrails = _format_guardrails(meta)
    if not scope and not guardrails:
        return ""
    parts: list[str] = []
    if scope:
        parts.append(f"Alcance metodológico del módulo:\n{scope}")
    if guardrails:
        parts.append(
            "Guardrails Danwor del módulo:\n"
            f"{guardrails}\n"
            "Estos guardrails son postura obligatoria. Si una condición los contradice, "
            "tratala como riesgo o anti-pattern, no como recomendación viable."
        )
    parts.append(
        "Usá este alcance como marco obligatorio de interpretación. "
        "El fragmento seleccionado es el foco local, pero no reemplaza los "
        "criterios, restricciones y postura técnica definidos para el módulo."
    )
    return "\n\n".join(parts)


def _clean_fragment(fragment: str, display_heading: str | None = None) -> tuple[str, list[str]]:
    """
    Limpia el fragmento de sección para el LLM.

    Contrato editorial:
    - section_meta aporta keywords para encontrar la sección; no se muestra al LLM
      como markup, pero sus keywords pueden completar fragmentos demasiado pobres.
    - semantic_summary sí queda como contenido de conocimiento, sin sus tags.
    - html_embed es una vista visual; no se manda como markup al LLM.
    """
    sm_keywords: list[str] = []
    for sm in re.finditer(r'\{\{\s*section_meta\s*:.*?keywords\s*=\s*"([^"]+)"', fragment or "", re.I):
        sm_keywords = [k.strip() for k in sm.group(1).split(",") if k.strip()]

    has_visual_asset = bool(_HTML_EMBED_RE.search(fragment or "") or _IMAGE_RE.search(fragment or ""))

    semantic_parts = [
        m.group(1).strip()
        for m in _SEMANTIC_SUMMARY_RE.finditer(fragment or "")
        if m.group(1).strip()
    ]

    clean = _SEMANTIC_SUMMARY_RE.sub("", fragment or "")
    clean = _SECTION_META_RE.sub("", clean).strip()
    clean = _SECTION_MODE_RE.sub("", clean).strip()
    clean = _SECTION_ID_RE.sub("", clean).strip()
    clean = _HEADING_TONE_RE.sub("", clean)

    clean = re.sub(r"\{\{tree_button:.*?\}\}", "", clean, flags=re.I)
    clean = re.sub(r"\{\{support:.*?\}\}", "", clean, flags=re.I)
    clean = _HTML_EMBED_RE.sub("", clean)
    clean = _IMAGE_RE.sub("", clean)
    clean = re.sub(r"\{\{callout_row\}\}|\{\{/callout_row\}\}", "", clean, flags=re.I)
    clean = re.sub(r"\n{2,}", "\n\n", clean).strip()

    heading_for_prompt = _clean_heading_label(display_heading or "")
    if heading_for_prompt:
        clean = re.sub(
            r"^(#{1,6}\s+).+$",
            lambda m: f"{m.group(1)}{heading_for_prompt}",
            clean,
            count=1,
            flags=re.M,
        )

    if semantic_parts:
        clean += "\n\nResumen semántico para el asistente:\n" + "\n\n".join(semantic_parts)

    if sm_keywords and (has_visual_asset or len(clean.split()) < 30):
        clean = re.sub(r"\n{2,}", "\n", clean).strip()
        clean += "\n\nConceptos clave que cubre esta sección: " + ", ".join(sm_keywords[:40])

    return clean.strip(), sm_keywords


def _filter_relevant_cases(module: str | None, topic: str, sm_keywords: list[str]) -> list:
    """Filtra casos de la case_library por relevancia a la sección seleccionada."""
    all_cases = _load_relevant_cases(module=module, max_cases=10)
    topic_words = set(w.lower() for w in re.split(r'\W+', topic) if len(w) > 3)
    section_words = topic_words | set(k.lower() for k in sm_keywords[:20])

    relevant = []
    for case in all_cases:
        case_text = " ".join([
            case.get("context", ""),
            case.get("lesson", ""),
            case.get("outcome", ""),
            " ".join(case.get("route", {}).values() if isinstance(case.get("route"), dict) else []),
        ]).lower()
        if any(w in case_text for w in section_words):
            relevant.append(case)
        if len(relevant) >= 3:
            break
    return relevant


# ── Prompt constructors ────────────────────────────────────────────────────────
def build_topic_prompt(
    module_context: str,
    selected_topic: str,
    fragment: str,
    action: str,
    instruction: str | None = None,
    module: str | None = None,
    pack_name: str | None = None,
) -> str:
    display_topic  = _clean_heading_label(selected_topic)
    instr          = (instruction or topic_instruction(action)).replace("{topic}", display_topic)
    module_header  = _resolve_module_header(module_context, module)
    clean_frag, sm = _clean_fragment(fragment, display_heading=display_topic)
    relevant_cases = _filter_relevant_cases(module, selected_topic, sm)
    cases_block    = _format_cases_for_prompt(relevant_cases) if relevant_cases else ""

    parts = [
        f"CONTEXTO (módulo):\n{module_header}",
        f"Sección seleccionada: {display_topic}",
    ]
    scope_block = _module_scope_for_topic_prompt(module, pack_name)
    if scope_block:
        parts.append(scope_block)
    parts.append(f"Fragmento:\n{clean_frag}")
    if cases_block:
        parts.append(cases_block)
    parts.append(f"INSTRUCCIÓN ESTRICTA:\n{instr}")

    return "\n\n".join(parts).strip()


def build_tree_value_add_prompt(
    tree_export: dict[str, Any],
    module: str | None = None,
) -> str:
    exp     = tree_export or {}
    tree    = exp.get("tree", {}) or {}
    run     = exp.get("run", {}) or {}
    result  = exp.get("result", {}) or {}
    leaf    = result.get("leaf", {}) or {}
    post    = leaf.get("post_actions", {}) or {}

    tree_id  = tree.get("id", "")
    title    = tree.get("title", "")
    links    = tree.get("links", {})
    tags     = tree.get("tags", [])
    leaf_id  = result.get("leaf_id", "")
    decision = leaf.get("decision", "")
    notes    = leaf.get("notes", []) or []
    route    = run.get("route", []) or []

    questions: list[str] = post.get("questions") or []
    if not isinstance(questions, list) or not questions:
        questions = [
            "Resumen de decisión (2-5 líneas) y por qué aplica en este caso.",
            "Checklist ejecutable (8-12 ítems) para implementarlo.",
            "Anti-patterns y riesgos (separados) + mitigación concreta.",
            "Validaciones mínimas (supuestos / constraints / dependencias) antes de avanzar.",
            "Artefactos Danwor sugeridos (ADR, diagrama, checklist, evidencias).",
        ]

    cases = _load_relevant_cases(tree_id=tree_id, max_cases=3)
    if len(cases) < 2 and module:
        extra = _load_relevant_cases(module=module, max_cases=3)
        seen  = {c.get("case_id") for c in cases}
        cases += [c for c in extra if c.get("case_id") not in seen][:2]
    cases_block = _format_cases_for_prompt(cases)

    compact = {
        "tree":   {"id": tree_id, "title": title, "links": links, "tags": tags},
        "run":    {"route": route},
        "result": {"leaf_id": leaf_id, "decision": decision, "notes": notes, "leaf": leaf},
    }

    parts = [
        f"ÁRBOL DE DECISIÓN (resultado determinístico):\n"
        f"- tree_id: {tree_id}\n"
        f"- title: {title}\n"
        f"- links: {links}\n"
        f"- leaf_id: {leaf_id}",
        f"DECISIÓN:\n{decision}",
        f"NOTAS:\n{notes}",
        f"TRAZA:\n{route}",
    ]
    if cases_block:
        parts.append(cases_block)
    parts.append(
        f"JSON compacto:\n```json\n{json.dumps(compact, ensure_ascii=False, indent=2)}\n```"
    )
    return "\n\n".join(parts).strip()
