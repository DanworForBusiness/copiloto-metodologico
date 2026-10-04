"""Renderiza el assessment en Markdown LEGIBLE y en ESPANOL (M7) a partir del MISMO documento publico que el JSON (`profile.build_assessment`).

Principios del owner: RESUMEN -> AREAS -> HALLAZGOS -> EVIDENCIA (nunca cientos de hechos crudos primero); este modulo NO relee hechos ni reinterpreta
nada: solo formatea el documento. Los hallazgos repetidos se presentan AGRUPADOS ("ACCESO DIRECTO A TABLA FISICA — 31 ocurrencias"); el JSON
conserva cada ocurrencia. Cobertura, confianza y contexto requerido se muestran como tres medidas separadas. Nunca se escribe "sin problema" cuando la
ausencia no esta establecida. Salida determinista (sin marcas de tiempo; esas viven en el manifiesto de corrida).

IDIOMA: los IDENTIFICADORES tecnicos (ids de senal, reason_code, enums, nombres de artefactos SAP, campos, conexiones) se muestran tal cual; toda la
PRESENTACION es espanol y sale del mapeo centralizado de la seccion "presentacion" (estados, resultados de reglas, dimensiones, areas, niveles).
"""
from __future__ import annotations

from typing import Any, Callable

MAX_OCCURRENCES_SHOWN = 8

# ═══════════════════════════ presentacion (mapeo centralizado ES) ═══════════════════════════
# Estado de EJECUCION del assessment (documento): separado de la evaluabilidad de la evidencia (estados por area).
_EXECUTION_TEXT = {"ASSESSED": "Ejecutado", "NOT_APPLICABLE": "No aplica", "INDETERMINATE": "Indeterminado"}
_STATE_TEXT = {"ASSESSED": "Evaluado", "PARTIALLY_ASSESSED": "Parcialmente evaluado", "NOT_ASSESSED": "No evaluado", "NOT_APPLICABLE": "No aplica",
               "INDETERMINATE": "Indeterminado"}
_STATE_PLURAL_TEXT = {"ASSESSED": "evaluados", "PARTIALLY_ASSESSED": "parcialmente evaluados", "NOT_ASSESSED": "no evaluados", "NOT_APPLICABLE": "no aplicables",
                      "INDETERMINATE": "indeterminados"}
_UNIT_TEXT = {"closure": "cierre semántico completo"}          # unidad de evaluacion que no es un artefacto
_RULE_RESULT_TEXT = {
    "PATTERN_FOUND": "patrón detectado",
    "ASSESSED_ABSENCE": "ausencia evaluada (patrón no detectado)",
    "ABSENCE_NOT_ESTABLISHED": "NO ESTABLECIDO — la regla no pudo evaluarse por completo; no puede afirmarse la ausencia",
    "NOT_APPLICABLE": "no aplica",
}
_AREA_TITLE = {
    "ANALYTIC_MODEL": "Modelo analítico", "PROPAGATION": "Propagación", "HARMONIZATION": "Armonización", "INGESTION": "Ingesta",
    "NAMING": "Convención de nombres", "SOURCE_EXTRACTION": "Fuente / Extracción",
}
_ARCH_AREAS = ("ANALYTIC_MODEL", "PROPAGATION", "HARMONIZATION", "INGESTION")
_MACRO_TEXT = {"ARCHITECTURE_MODELING": "Arquitectura / Modelado", "NAMING": "Convención de nombres", "SOURCE_EXTRACTION": "Fuente / Extracción"}
_MACRO_STATE_TEXT = {"HAS_ASSESSED_EVIDENCE": "con evidencia evaluada",
                     "COMPLETELY_NOT_ASSESSED": "SIN EVIDENCIA EVALUADA — ninguna evidencia requerida pudo evaluarse", "NOT_APPLICABLE": "no aplica"}
_LEVEL_TEXT = {"HIGH": "ALTA", "MEDIUM": "MEDIA", "LOW": "BAJA"}
_KIND_TEXT = {"OBSERVATION": "observación", "REVIEW": "caso a revisar", "DESCRIPTIVE": "descriptivo"}
_FILTER_TEXT = {"PRESENT": "presente", "ABSENT": "ausente", "NOT_ASSESSED": "no evaluado"}
_ROW_KEY_TEXT = {"share": "proporción", "view_type": "tipo", "view_type_basis": "origen del tipo", "membership_basis": "criterio de vista", "applicable": "aplican", "compliant": "cumplen", "non_compliant": "no cumplen", "observed": "observadas", "unavailable": "no disponibles"}
_DIVERGENCE_TEXT = {
    "NAME_LAYER_ABOVE_BOUNDARY_POSITION": "la capa del nombre está por encima de su posición de frontera",
    "NAME_LAYER_INGESTION_BUT_NOT_A_BOUNDARY": "el nombre indica capa 1 pero el artefacto tiene proveedores",
    "NAME_LAYER_DIFFERS_FROM_DECLARED_ROLE_LAYER": "la capa del nombre difiere de la capa del rol declarado",
}
_TIER_TEXT = {"CO_DECLARED_IN_ARTIFACT": "declarados juntos en un artefacto", "EXPOSED_BY_TERMINAL_AM": "expuestos por el modelo analítico terminal"}


def _pct(v: float | None) -> str:
    return "n/d" if v is None else f"{v * 100:.1f}%"


def _cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _code(v: Any) -> str:
    return f"`{v}`"


def _names(items: list[str], limit: int = 6) -> str:
    if len(items) <= limit:
        return ", ".join(_code(x) for x in items)
    return ", ".join(_code(x) for x in items[:limit]) + f", … (+{len(items) - limit})"


def _units(items: list[str], limit: int = 8) -> str:
    """Unidades afectadas: artefactos como identificadores; las unidades que no son artefactos (p. ej. el cierre) con su nombre en espanol."""
    shown = [_UNIT_TEXT[x] if x in _UNIT_TEXT else _code(x) for x in items[:limit]]
    return ", ".join(shown) + (f", … (+{len(items) - limit})" if len(items) > limit else "")


def _involves(n_occurrences: int, n_artifacts: int) -> str:
    art = f"{n_artifacts} artefacto{'s' if n_artifacts != 1 else ''} distinto{'s' if n_artifacts != 1 else ''}"
    return f"(la ocurrencia involucra {art})" if n_occurrences == 1 else f"(las {n_occurrences} ocurrencias involucran {art})"


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _occ(n: int) -> str:
    return _plural(n, "ocurrencia")


def _yn(v: Any) -> str:
    return "sí" if v is True else "no" if v is False else "desconocido"


# ── una linea por ocurrencia, SOLO desde los valores publicos del documento ──
def _fmt_direct_raw(o: dict) -> str:
    v = o["values"]
    return (f"{_code(o['affected_artifacts'][0])} ← tabla remota {_code(v.get('remote_entity'))} (conexión {_code(v.get('remote_connection'))}); "
            f"alcanza el modelo analítico por linaje estricto: {_yn(v.get('reaches_terminal_by_strict_lineage'))}; "
            f"filtro de origen: {_FILTER_TEXT.get(v.get('source_filter'), v.get('source_filter'))}; identidad: {v.get('identity_provenance')}; "
            f"distancia al modelo analítico: {v['lineage_distance'] if v.get('lineage_distance') is not None else 'no definida'}; "
            f"artefactos dependientes aguas abajo: {v.get('transitive_lineage_dependents') if v.get('transitive_lineage_dependents') is not None else 'desconocido'}")


def _fmt_filter(o: dict) -> str:
    return f"{_code(o['affected_artifacts'][0])}: filtro `{o['values'].get('filter_expression_json')}`"


def _fmt_pass_through(o: dict) -> str:
    v = o["values"]
    return f"{_code(v.get('edge'))} — {_plural(v.get('field_count') or 0, 'campo')}; patrón declarado del consumidor: {v.get('consumer_declared_pattern')}"


def _fmt_rederivation(o: dict) -> str:
    v = o["values"]
    return f"{_code(v.get('provider'))} → {_code(v.get('consumer'))}: {_names(v.get('fields') or [], 5)} (distancia de linaje {v.get('lineage_distance')})"


def _fmt_role_layer(o: dict) -> str:
    v = o["values"]
    return f"{_code(v.get('artifact'))} — el nombre codifica la capa {v.get('layer_encoded_in_name')}; " + ", ".join(
        _DIVERGENCE_TEXT.get(k, str(k).lower().replace("_", " ")) for k in v.get("divergence_kinds") or [])


def _fmt_duplicate(o: dict) -> str:
    v = o["values"]
    return f"remota {_code(v.get('remote_entity'))} importada como {_names(v.get('local_artifacts') or [])}"


def _fmt_layer_boundary(o: dict) -> str:
    v = o["values"]
    cons = ", ".join(f"{_code(c['consumer'])} (capa {c['inferred_layer']})" for c in (v.get("lineage_consumers") or [])[:4])
    return f"{_code(v.get('source'))} consumida directamente por {cons}"


def _fmt_flat_central(o: dict) -> str:
    v = o["values"]
    return (f"{_code(v.get('artifact'))} — {_plural(v.get('fan_in') or 0, 'proveedor')} ({v.get('boundary_providers')} de frontera), "
            f"patrón declarado {v.get('declared_pattern')}")


def _fmt_vocab_mix(o: dict) -> str:
    v = o["values"]
    forms = ", ".join(str(x) for x in v.get("distinct_forms") or [])
    return (f"etiqueta “{v.get('label')}” → {_names(v.get('technical_names') or [], 5)}; formas de nombre: {forms}; "
            f"evidencia: {', '.join(_TIER_TEXT.get(t, str(t).lower().replace('_', ' ')) for t in v.get('evidence_tiers') or [])}")


def _fmt_opaque(o: dict) -> str:
    v = o["values"]
    return f"{_code(v.get('field'))} (etiqueta “{v.get('label')}”) expuesto en {_plural(len(v.get('exposed_in') or []), 'artefacto')}"


def _fmt_label(o: dict) -> str:
    v = o["values"]
    return f"{_code(v.get('artifact'))} — {_plural(v.get('field_count') or 0, 'campo')}, todas las etiquetas iguales a sus nombres técnicos"


def _fmt_underuse(o: dict) -> str:
    v = o["values"]
    return (f"{_code(o['affected_artifacts'][0])} — {_plural(v.get('association_count') or 0, 'asociación', 'asociaciones')}, "
            f"{_plural(v.get('measure_count') or 0, 'medida')}, {_plural(v.get('declared_dimension_count') or 0, 'dimensión declarada', 'dimensiones declaradas')}")


_FMT: dict[str, Callable[[dict], str]] = {
    "DIRECT_RAW_TABLE_ACCESS": _fmt_direct_raw, "SOURCE_FILTER_PRESENT": _fmt_filter, "PASS_THROUGH_REVIEW": _fmt_pass_through,
    "CROSS_LAYER_REDERIVATION": _fmt_rederivation, "ROLE_LAYER_DIVERGENCE": _fmt_role_layer, "DUPLICATED_REMOTE_IMPORT": _fmt_duplicate,
    "LAYER_BOUNDARY_REVIEW": _fmt_layer_boundary, "FLAT_CENTRAL_ASSEMBLY_REVIEW": _fmt_flat_central, "FIELD_VOCABULARY_MIX": _fmt_vocab_mix,
    "OPAQUE_FIELD_NAME": _fmt_opaque, "LABEL_NOT_ENRICHING": _fmt_label, "AM_SEMANTIC_UNDERUSE_REVIEW": _fmt_underuse,
}


def _occurrence_line(group_id: str, o: dict) -> str:
    fmt = _FMT.get(group_id)
    if fmt is not None:
        return fmt(o)
    return ", ".join(f"{k}={o['values'][k]}" for k in sorted(o["values"]))


def _row_text(r: dict) -> str:
    rest = [(k, v) for k, v in r.items() if k != "name" and v is not None]
    if len(rest) == 1 and rest[0][0] == "count":
        return f"{r['name']} ({rest[0][1]})"
    return f"{r['name']}: " + ", ".join(f"{_ROW_KEY_TEXT.get(k, k.replace('_', '-'))} {_pct(v) if k == 'share' else v}" for k, v in rest)   # `share` = proporción 0–1


# Perfiles persistidos ANTES de un ajuste de redacción conservan el texto viejo de la razón: se muestra con la redacción vigente (solo copy).
_REASON_COMPAT = {"Se requiere una respuesta humana, de negocio o de arquitectura.": "Se requiere una respuesta del equipo del proyecto (negocio o arquitectura)."}


def reason_text(reason: dict) -> str:
    """Texto visible de una razón pública (``reason_summary``) con la redacción vigente, aun si el perfil persistido trae la anterior."""
    return _REASON_COMPAT.get(reason["reason_summary"], reason["reason_summary"])


SUMMARY_RELATION_TEXT = ("Los casos a revisar son hallazgos concretos que requieren la validación del equipo consultor; las preguntas abiertas representan "
                         "información pendiente del proyecto y pueden ayudar a resolver uno o varios hallazgos.")


# Etiquetas visibles de los indicadores de Propagación / Armonización, por id (una sola fuente para Markdown y HTML). Los perfiles persistidos antes del
# ajuste conservan el texto viejo en su JSON: la lectura usa SIEMPRE esta redacción. No cambia ningún cálculo ni id.
_HIGHLIGHT_LABELS = {
    "lineage_steps_evaluated": "Pasos de linaje evaluados",                 # cuenta aristas proveedor→consumidor evaluadas (también las de contrato idéntico)
    "artifacts_with_derived_fields": "Artefactos con derivaciones",
    "rederived_field_occurrences": "Derivaciones repetidas",                # ocurrencias (campo, consumidor, ancestro), no campos únicos
    "declared_dimensions": "Dimensiones declaradas",
    "composition_artifacts": "Artefactos de composición",
    "cross_model_reuse_artifacts": "Artefactos con reuso",                  # solo entre las dimensiones y destinos de asociación evaluados
}


def highlight_label(h: dict) -> str:
    """Etiqueta visible de un hecho descriptivo (la vigente aun si el perfil persistido trae la anterior)."""
    return _HIGHLIGHT_LABELS.get(h["id"], h["label"])


def view_scope_text(total: int, breakdown: list[dict]) -> str:
    """"Vistas consideradas: 16 · 13 gráficas · 3 SQL[ · 1 sin clasificar]": vistas ÚNICAS que participan en la evidencia evaluada en la sección. Es contexto
    factual (no pertenencia exclusiva a una capa ni juicio): una misma vista puede participar en Armonización y en Propagación."""
    if not total:
        return "Vistas consideradas: 0"
    n = {b["name"]: b["count"] for b in breakdown}
    parts = [str(total), _plural(n.get("gráficas", 0), "gráfica"), f"{n.get('SQL', 0)} SQL"]
    if n.get("sin clasificar", 0):
        parts.append(f"{n['sin clasificar']} sin clasificar")
    return "Vistas consideradas: " + " · ".join(parts)


def review_required_text(n_occurrences: int, n_groups: int) -> tuple[str, str]:
    """(titular, lectura) de "Casos a revisar": OCURRENCIAS de los grupos de revisión (no grupos ni preguntas). La comparten Markdown y HTML.
    Solo vocabulario visible: el contrato interno sigue siendo `review_required` / `occurrence_count`."""
    head = _plural(n_occurrences, "caso")
    if not n_occurrences:
        return head, "El análisis no identificó casos concretos que requieran criterio técnico del consultor."
    detail = (f"El análisis identificó {_plural(n_occurrences, 'caso concreto', 'casos concretos')} que {'requiere' if n_occurrences == 1 else 'requieren'} "
              f"el criterio técnico del consultor, agrupados en {_plural(n_groups, 'tipo de revisión', 'tipos de revisión')}. "
              f"{'No es un defecto confirmado y no mide' if n_occurrences == 1 else 'No son defectos confirmados y no miden'} la calidad del modelo (detalle en *Casos a revisar*).")
    return head, detail


def context_open_text(ctx: dict) -> tuple[str, str]:
    """(titular, lectura) de "Preguntas abiertas": TIPOS de pregunta distintos que requieren información del equipo del proyecto (no ocurrencias).
    Solo vocabulario visible: el contrato interno sigue siendo `open_questions`."""
    n, k, w = ctx["open_questions"], ctx["questions_backed_by_findings"], ctx["questions_without_findings"]
    head = _plural(n, "pregunta")
    if not n:
        return head, "No quedan preguntas abiertas para el equipo del proyecto."
    detail = (f"{'Queda' if n == 1 else 'Quedan'} {_plural(n, 'pregunta distinta', 'preguntas distintas')} que {'requiere' if n == 1 else 'requieren'} información del "
              f"equipo del proyecto. {k} {'está relacionada' if k == 1 else 'están relacionadas'} con hallazgos detectados y {w} "
              f"{'corresponde' if w == 1 else 'corresponden'} a contexto del proyecto sin hallazgo asociado. Una pregunta puede estar vinculada a varias ocurrencias.")
    return head, detail


def view_composition_text(total: int, breakdown: list[dict], *, structural: bool = False) -> str:
    """Lectura objetiva de la composición del cierre por tipo de vista, SIN juicio. La comparten el Markdown y la UI estructurada.
    ``structural`` = al menos una vista se reconoció solo por su marcador estructural (pools planos, sin carpeta-tipo): ahí una vista sin
    marcador no es detectable, así que NO se afirma "0 sin clasificación" como si UNKNOWN fuera exhaustivamente observable (el hecho no cambia)."""
    if not total:
        return "El cierre semántico no contiene vistas de Datasphere."
    n = {b["name"]: b["count"] for b in breakdown}
    g, s, u = n.get("gráficas", 0), n.get("SQL", 0), n.get("sin clasificar", 0)
    if not structural:                                       # pertenencia autoritativa (carpeta `views`): UNKNOWN es observable
        return f"El cierre semántico contiene {_plural(total, 'vista')}: {_plural(g, 'gráfica')}, {s} SQL y {u} sin clasificación."
    items = [_plural(g, "gráfica"), f"{s} SQL"]
    if u:                                                    # mixto: UNKNOWN solo puede provenir de vistas identificadas por carpeta
        items.append(f"{u} sin clasificación (entre las identificadas por carpeta)")
    listed = ", ".join(items[:-1]) + " y " + items[-1]
    return (f"Se identificaron {_plural(total, 'vista')} en el cierre semántico: {listed}. "
            "El pool no informa carpetas por tipo en todos los casos: las vistas sin carpeta solo se reconocen por su marcador gráfico o SQL.")


def has_structural_membership(highlights: list[dict]) -> bool:
    """True si alguna vista del cierre se reconoció solo por marcador estructural (``membership_basis == STRUCTURAL_MARKER``)."""
    arts = next((h for h in highlights if h.get("id") == "view_artifacts"), None)
    return bool(arts) and any(r.get("membership_basis") == "STRUCTURAL_MARKER" for r in arts["value"])


def _value_text(v: Any) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{_ROW_KEY_TEXT.get(k, k.replace('_', ' '))} {x}" for k, x in v.items())
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return "; ".join(_row_text(r) for r in v)
        return ", ".join(str(x) for x in v)
    return str(v)


def _group_heading(g: dict, level: str) -> list[str]:
    return [f"{level} {g['title'].upper() if g['kind'] == 'OBSERVATION' else g['title']} — {_occ(g['occurrence_count'])}", ""]


def _group_block(g: dict, level: str) -> list[str]:
    out = _group_heading(g, level)
    out.append(f"{g['explanation']} (`{g['id']}`, área: {_AREA_TITLE.get(g['area'], g['area'])})")
    out.append("")
    out.append(f"- **Por qué se muestra:** {g['why_shown']}")
    if g["kind"] != "DESCRIPTIVE":
        out.append("- **Estado:** abierto — requiere contexto (no se emite juicio)")
        out.append(f"- **Pregunta para el proyecto:** {g['context_required']['question']}")
        if g.get("legitimate_reasons"):
            out.append("- **Razones legítimas que lo explicarían:** " + "; ".join(g["legitimate_reasons"]))
    n_art = len(g["affected_artifacts"])
    out.append(f"- **Ocurrencias:** {g['occurrence_count']} (unidad de cada ocurrencia: {g['occurrence_unit']})")
    rel = "" if n_art == g["occurrence_count"] else f" {_involves(g['occurrence_count'], n_art)}"
    out.append(f"- **Artefactos involucrados ({n_art}):** {_names(g['affected_artifacts'], 8)}{rel}")
    if g.get("interpretation_limits"):
        out.append("- **Cómo NO leerlo:** " + "; ".join(g["interpretation_limits"]))
    state = g.get("evidence_state")
    if state and state["assessability"] != "ASSESSED":
        out.append(f"- **Límite de evidencia:** los insumos de la regla están {_STATE_PLURAL_TEXT[state['assessability']]} "
                   f"({reason_text(state['reason'])}); pueden existir más ocurrencias fuera de la evidencia que pudo evaluarse.")
    out.append("")
    shown = g["occurrences"][:MAX_OCCURRENCES_SHOWN]
    for o in shown:
        out.append(f"  - {_occurrence_line(g['id'], o)}")
    rest = len(g["occurrences"]) - len(shown)
    if rest > 0:
        out.append(f"  - … y {_plural(rest, 'ocurrencia')} más (todas, con sus referencias de evidencia, están en el JSON)")
    out.append("")
    return out


def _area_block(area: dict, groups_by_id: dict[str, dict], rule_by_area: dict[str, list[dict]], level: str) -> list[str]:
    title = _AREA_TITLE[area["id"]]
    out = [f"{level} {title}", ""]
    st = area["assessability"]
    line = f"**Estado de la evaluación:** {_STATE_TEXT[st]}"
    if area["reason"]:
        line += f" — {reason_text(area['reason'])} (`{area['reason']['reason_code']}`)"
        if st != "NOT_APPLICABLE":
            line += "; ver *No evaluado / Limitaciones de evidencia*"
    out.append(line)
    rs = area["required_surfaces"]
    out.append(f"**Superficies de evidencia requeridas:** {_plural(rs['assessed'], 'evaluada')}, {_plural(rs['partially_assessed'], 'parcialmente evaluada')}, "
               f"{_plural(rs['not_assessed'], 'no evaluada')}, {rs['not_applicable']} no aplican")
    scope = next((h for h in area["highlights"] if h["id"] == "views_considered"), None)
    if scope is not None:
        out.append(view_scope_text(scope["value"], scope.get("breakdown") or []))
    out.append("")
    if [h for h in area["highlights"] if h["id"] != "views_considered"]:
        out.append("Qué se observó (hechos descriptivos, sin juicio):")
        out.append("")
        for h in area["highlights"]:
            if h["id"] == "views_considered":
                continue
            out.append(f"- {highlight_label(h)}: {_value_text(h['value'])}")
            for b in h.get("breakdown") or []:
                out.append(f"  - {b['name']}: {b['count']}")
            if h["id"] == "views_in_closure":
                out.append(f"  - {view_composition_text(h['value'], h.get('breakdown') or [], structural=has_structural_membership(area['highlights']))}")
        out.append("")
    gids = area["finding_groups"]
    if gids:
        out.append("Hallazgos en esta área (detalle en *Observaciones* y *Casos a revisar*):")
        out.append("")
        for gid in gids:
            g = groups_by_id[gid]
            out.append(f"- {g['title']} — {_occ(g['occurrence_count'])} ({_KIND_TEXT[g['kind']]})")
        out.append("")
    rules = rule_by_area.get(area["id"], [])
    if rules:
        out.append("Evaluación de reglas en esta área:")
        out.append("")
        for r in rules:
            suffix = f" ({reason_text(r['reason'])})" if r["reason"] else ""
            out.append(f"- {r['title']}: {_RULE_RESULT_TEXT[r['result']]}{suffix}")
        out.append("")
    if area["limitations_not_observable"]:
        out.append("Limitación: parte de la evidencia de esta área no es observable en la exportación estándar de Modelado; no reduce la cobertura, "
                   "pero limita lo que puede concluirse.")
        out.append("")
    return out


_FAMILY_TEXT = {"analytic-models": "Modelos Analíticos", "views": "Vistas", "tables": "Tablas", "replication-flows": "Replication Flows",
                "data-flows": "Data Flows", "transformation-flows": "Transformation Flows", "task-chains": "Task Chains"}


_ROOT_TYPE_TEXT = {"view": "Vista", "local-table": "Tabla local", "remote-table": "Tabla remota"}


def _is_am_root(scope: dict) -> bool:
    return scope.get("root_type") in (None, "analytic-model")          # perfiles anteriores al campo: siempre AM


def _root_identity_lines(scope: dict) -> list[str]:
    """Identidad de la raíz evaluada. AM: líneas de siempre. Vista/tabla: objeto raíz + tipo (nunca se rotula "Modelo analítico terminal")."""
    if _is_am_root(scope):
        label = scope.get("root_label") or scope.get("terminal_analytic_model_label")
        return [f"- **Modelo analítico terminal:** {scope['terminal_analytic_model'] or '—'}", *([f"- **Descripción:** {label}"] if label else [])]
    return [f"- **Objeto raíz:** {scope.get('root_object') or '—'}", f"- **Tipo de objeto:** {_ROOT_TYPE_TEXT.get(scope['root_type'], scope['root_type'])}",
            *([f"- **Descripción:** {scope['root_label']}"] if scope.get("root_label") else [])]


def evidence_not_acquired(scope: dict | None) -> list[str]:
    """Etiquetas humanas de las familias de evidencia NO adquiridas por la extracción. Vacío si el alcance se desconoce (Catálogo, perfiles
    anteriores al campo) o si fue completo: solo un alcance CONOCIDO y limitado produce una lista."""
    if not scope:
        return []
    effective = set(scope.get("effective_features") or ())
    return [t for k, t in _FAMILY_TEXT.items() if k not in effective]


def _evidence_scope_lines(scope: dict | None) -> list[str]:
    """Solo cuando se conoce el alcance de la extraccion y quedaron familias SIN adquirir: lo no adquirido NO es ausencia evaluada."""
    missing = evidence_not_acquired(scope)
    if not missing:
        return []
    return [f"- **Evidencia no adquirida:** {', '.join(missing)} (no se extrajo; su ausencia en el assessment no implica que no existan)"]


def render_markdown(doc: dict) -> str:
    """Markdown determinista (espanol) a partir del documento publico."""
    scope, summary = doc["scope"], doc["summary"]
    L: list[str] = ["# Autoevaluación de Modelado", ""]
    L += [f"- **Cliente:** {scope['client']}",
          *_root_identity_lines(scope),
          *_evidence_scope_lines(scope.get("evidence_scope")),
          "- **Alcance de la evaluación:** " + (f"{_plural(scope['closure_size'], 'artefacto')} en el cierre semántico del "
                                                + ("modelo analítico terminal" if _is_am_root(scope) else "objeto raíz")
                                                if scope["applicability"] == "APPLICABLE" else "no aplica"),
          f"- **Estado del assessment:** {_EXECUTION_TEXT[doc['assessment_state']]}",
          f"- **Esquema:** `{doc['schema_version']}` · señales `{doc['versions']['signals']}` · evaluabilidad `{doc['versions']['assessability']}` · "
          f"confianza `{doc['versions']['confidence']}`", ""]
    L += ["> Perfil de evidencia, cobertura y confianza — Fase 1 / validación en paralelo. Describe QUÉ se observó, QUÉ requiere revisión y QUÉ no pudo "
          "evaluarse; los hallazgos son observaciones para revisión técnica de nuestros consultores, no conclusiones sobre la calidad del modelo. Cobertura, confianza y "
          "contexto requerido son tres medidas separadas.", ""]

    if doc["assessment_state"] != "ASSESSED":
        r = scope["reason"]
        L += ["## Resumen", ""]
        L.append(f"**Este cliente no tiene evaluación** — {reason_text(r)} (`{r['reason_code']}`)." if r else "**No se produjo una evaluación.**")
        if summary.get("error"):
            L += ["", f"Detalle: {summary['error']}"]
        L += ["", "No se informan cobertura, confianza ni hallazgos porque no hay nada evaluado que calificar.", ""]
        return "\n".join(L).rstrip() + "\n"

    # ── Resumen ──
    cov, conf, ctx = summary["coverage"], summary["confidence"], summary["context_required"]
    rs = cov["required_surfaces"]
    obs_n = sum(g["occurrence_count"] for g in doc["observations"])
    rev_n = sum(g["occurrence_count"] for g in doc["review_required"])
    L += ["## Resumen", "", f"_{SUMMARY_RELATION_TEXT}_", ""]
    L += [f"- **Cobertura:** estricta {_pct(cov['strict'])} · inclusiva {_pct(cov['inclusive'])}",
          f"  - {cov['meaning']}",
          f"  - Superficies de evidencia requeridas: {rs['applicable']} aplicables — {_plural(rs['assessed'], 'evaluada')}, "
          f"{_plural(rs['partially_assessed'], 'parcialmente evaluada')}, {_plural(rs['not_assessed'], 'no evaluada')} "
          f"({rs['not_applicable']} que no aplican quedan excluidas)."]
    lim = conf["main_limitation"]
    L += [f"- **Confianza:** {_LEVEL_TEXT[conf['level']]}"]
    if lim:
        L.append(f"  - Limitación principal: {lim['summary']}")
    L.append(f"  - {conf['meaning']}")
    rev_head, rev_detail = review_required_text(rev_n, len(doc["review_required"]))
    ctx_head, ctx_detail = context_open_text(ctx)
    L += [f"- **Casos a revisar:** {rev_head}", f"  - {rev_detail}",
          f"- **Preguntas abiertas:** {ctx_head}", f"  - {ctx_detail}",
          f"- **Observaciones:** {_plural(len(doc['observations']), 'grupo de observación', 'grupos de observación')} ({_occ(obs_n)})", ""]
    areas = {a["id"]: a for a in doc["areas"]}
    L += ["| Dimensión | Área | Estado | Superficies requeridas (evaluadas / parciales / no evaluadas) |", "|---|---|---|---|"]
    for a in doc["areas"]:
        c = a["required_surfaces"]
        L.append(f"| {_MACRO_TEXT[a['macro_dimension']]} | {_AREA_TITLE[a['id']]} | {_STATE_TEXT[a['assessability']]} | "
                 f"{c['assessed']} / {c['partially_assessed']} / {c['not_assessed']} |")
    L.append("")
    sin_evidencia = [m for m in summary["macro_dimensions"] if m["state"] == "COMPLETELY_NOT_ASSESSED"]
    for m in sin_evidencia:
        L.append(f"**Dimensión {_MACRO_TEXT[m['id']]}:** {_MACRO_STATE_TEXT[m['state']]}.")
    if sin_evidencia:
        L.append("")
    if ctx["questions"]:
        L += ["Preguntas que solo el proyecto puede responder:", ""]
        for q in ctx["questions"]:
            suffix = f" ({_occ(q['occurrence_count'])})" if q["occurrence_count"] else ""
            L.append(f"- {q['question']}{suffix}")
        L.append("")

    groups_by_id = {g["id"]: g for g in (*doc["observations"], *doc["review_required"], *doc["descriptive_signals"])}
    rule_by_area: dict[str, list[dict]] = {}
    for r in doc["rule_results"]:
        rule_by_area.setdefault(r["area"], []).append(r)

    L += ["## Arquitectura / Modelado", ""]
    for aid in _ARCH_AREAS:
        L += _area_block(areas[aid], groups_by_id, rule_by_area, "###")
    L += ["## Convención de nombres", ""] + _area_block(areas["NAMING"], groups_by_id, rule_by_area, "###")[2:]
    L += ["## Fuente / Extracción", ""] + _area_block(areas["SOURCE_EXTRACTION"], groups_by_id, rule_by_area, "###")[2:]

    L += ["## Observaciones", ""]
    if doc["observations"]:
        for g in doc["observations"]:
            L += _group_block(g, "###")
    else:
        L += ["No se detectaron patrones de nivel observación en la evidencia que pudo evaluarse (ver *No evaluado / Limitaciones de evidencia* para lo que no se evaluó).", ""]
    if doc["descriptive_signals"]:
        L += ["### Señales descriptivas (no son casos a revisar)", ""]
        for g in doc["descriptive_signals"]:
            L += _group_block(g, "####")

    L += ["## Casos a revisar", ""]
    if doc["review_required"]:
        for g in doc["review_required"]:
            L += _group_block(g, "###")
    else:
        L += ["Las reglas que pudieron evaluarse no levantaron casos a revisar (ver *No evaluado / Limitaciones de evidencia*).", ""]

    L += ["## No evaluado / Limitaciones de evidencia", "",
          "Las evidencias que cuentan para la cobertura se marcan *cuenta*; las demás se muestran por transparencia y no modifican la cobertura.", ""]
    lims = doc["evidence_limitations"]
    if lims:
        L += ["| Evidencia | Área | Estado | Motivo | ¿Cuenta para la cobertura? |", "|---|---|---|---|---|"]
        for s in lims:
            L.append(f"| {_cell(s['title'])} (`{s['surface_id']}`) | {_AREA_TITLE.get(s['area'], s['area'])} | {_STATE_TEXT[s['assessability']]} | "
                     f"{_cell(reason_text(s['reason']))} (`{s['reason']['reason_code']}`) | {'cuenta' if s['counts_toward_coverage'] else 'no cuenta'} |")
        L.append("")
        with_units = [s for s in lims if s["affected_units"]]
        if with_units:
            L += ["Evidencia no disponible o dependiente de contexto (unidades afectadas):", ""]
            for s in with_units:
                L.append(f"- {s['title']}: {_units(s['affected_units'], 8)}")
            L.append("")
    else:
        L += ["Toda la evidencia aplicable en alcance fue evaluada.", ""]
    nae = [r for r in doc["rule_results"] if r["result"] == "ABSENCE_NOT_ESTABLISHED"]
    if nae:
        L += ["Para estas reglas la ausencia de hallazgos NO está establecida, porque sus insumos estaban incompletos:", ""]
        for r in nae:
            L.append(f"- {r['title']}")
        L.append("")
    if conf["components"]:
        L += ["### Componentes de confianza", "", "| Componente | Valor | Nivel |", "|---|---|---|"]
        for c in conf["components"]:
            L.append(f"| {c['label']} | {c['value']} | {_LEVEL_TEXT[c['level']]} |")
        L += ["", "Los componentes se combinan tomando el nivel más bajo. Los puntos de corte son provisionales y están pendientes de validación. "
                  "La cobertura, las preguntas de contexto abiertas y la evidencia del plano de Procesamiento NO son insumos de la confianza.", ""]

    L += ["## Evidencia / Detalle técnico", ""]
    L += [f"- Firma del conjunto de artefactos: `{scope['pool_signature']}`" if scope.get("pool_signature") else "- Firma del conjunto de artefactos: no registrada",
          f"- Familias de hechos emitidas: {len({f['fact'].split('@')[0] for f in doc['fact_index']})} · instancias de hechos: {len(doc['fact_index'])}",
          "- Cada ocurrencia anterior lleva en el documento JSON sus ids de hechos de evidencia y sus valores normalizados (`observations`, `review_required`, `fact_index`).", ""]
    if doc["not_applicable"]:
        L += ["### Evidencia que no aplica (trazabilidad; no son limitaciones de evidencia)", "", "| Evidencia | Área | Motivo |", "|---|---|---|"]
        for s in doc["not_applicable"]:
            L.append(f"| {_cell(s['title'])} (`{s['surface_id']}`) | {_AREA_TITLE.get(s['area'], s['area'])} | "
                     f"{_cell(reason_text(s['reason']))} (`{s['reason']['reason_code']}`) |")
        L.append("")
    if ctx["deduplicated_questions"]:
        L += ["### Preguntas de contexto no repetidas (ya formuladas por un hallazgo)", ""]
        for q in ctx["deduplicated_questions"]:
            L.append(f"- `{q['id']}` → ya la formula el grupo `{q['covered_by']}`")
        L.append("")
    L += ["### Resultados por regla", "", "| Regla | Resultado | Hallazgos |", "|---|---|---|"]
    for r in doc["rule_results"]:
        L.append(f"| {r['title']} (`{r['rule']}`) | {_RULE_RESULT_TEXT[r['result']]} | {r['finding_count']} |")
    L.append("")
    L += ["### Cobertura por área (superficies requeridas)", "", "| Área | Evaluadas | Parciales | No evaluadas | No aplican |", "|---|---|---|---|---|"]
    for a in doc["areas"]:
        c = a["required_surfaces"]
        L.append(f"| {_AREA_TITLE[a['id']]} | {c['assessed']} | {c['partially_assessed']} | {c['not_assessed']} | {c['not_applicable']} |")
    L.append("")
    return "\n".join(L).rstrip() + "\n"
