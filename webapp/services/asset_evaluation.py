"""Evaluación del activo (presentación): Gobierno & estándares + Portabilidad & preparación BDC.

Una sola implementación para el Catálogo (resumen compacto) y el informe de Assessment (sección). Solo PRESENTA datos ya producidos: los hallazgos
del Assessment gobernado persistido y el dict `portabilidad_bdc`, y SOLO si existe un Assessment; no recalcula, no decide niveles
y NO emite un puntaje/nota global de cumplimiento. Las tres lecturas del activo son independientes y no se consumen entre sí:

    · Gobierno & estándares ........ hallazgos de cumplimiento (notas / bloqueantes) — sin nota propia
    · Portabilidad & preparación BDC .. capacidad técnica (score/confianza/riesgos) — sin badge de grado
    · Adopción metodológica ........ ÚNICA lectura con grado (BUENA/ADECUADA/PARCIAL/No determinable; `status_tone`)

COMPOSICIÓN SOLA: lee lo que ya produjeron los motores (marcadores/severidad del generador del catálogo, dict `portabilidad_bdc` ya calculado,
Juicio persistido que consume el presentador) y solo agrega/da formato (conteos, texto, tonos). No importa ningún motor ni recalcula score,
severidad, nivel ni cobertura.

El color de cada fila sale del mapeo canónico del Hub (`status_tone`): aquí el semáforo del generador se traduce a los
modificadores del badge `.catalog-card__verdict--*`. Las filas se devuelven como DATOS (texto escapado → `Markup`), no como
HTML a medida, para que cada plantilla las renderice con la tabla nativa del Hub.
"""
from __future__ import annotations

import html
import re
from typing import Any

from markupsafe import Markup

from webapp.services.status_tone import ATTENTION, ACCEPTABLE, NEUTRAL, POSITIVE

# Semáforo legacy del generador → tono canónico del Hub. No hay tono "peligro": rojo/crítico cae en `parcial`
# (la ETIQUETA conserva el significado: "Crítico").
SEMAFORO_TONE = {"green": POSITIVE, "amber": ACCEPTABLE, "orange": ATTENTION, "red": ATTENTION, "blue": NEUTRAL}

# Etiquetas VISIBLES de la banda del score (los ids persistidos `plena/buena/…` no cambian): el score mide la CONSISTENCIA ESTRUCTURAL de los tipos del dominio,
# no la compatibilidad con un destino; por eso no usa «Plena/Buena/Condicionada» (colisionan con la adopción metodológica y con «Compatibilidad condicionada»).
PORT_CLASIF_LABEL = {
    "plena": "Sin hallazgos estructurales", "buena": "Observaciones menores", "condicionada": "Observaciones relevantes",
    "inspiracional": "Inconsistencias estructurales",
    "no_recomendable": "Inconsistencias críticas",
    "no_evaluable": "No evaluable",
}
PORT_CLASIF_NOT_EXERCISED = "Sin elementos estructurales que evaluar"
PORT_HALLAZGO_LABEL = {
    "riesgo": "Riesgo", "adaptacion": "Normalización detectada", "buena_practica": "Buena práctica",
    "hint": "Indicio", "no_evaluable": "No evaluable",
}


# ─────────────────────────── helpers de texto (escapan; devuelven Markup) ───────────────────────────
def inline_html(text: str) -> str:
    safe = html.escape((text or "").strip())
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<em>\1</em>", safe)
    return safe


def evidence_html(text: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return "Sin evidencia detallada."
    parts = [part.strip() for part in clean.split(" | ") if part.strip()]
    if len(parts) >= 2:
        return "<ul>" + "".join(f"<li>{inline_html(part)}</li>" for part in parts) + "</ul>"
    comma_parts = [part.strip() for part in clean.split(",") if part.strip()]
    if len(comma_parts) >= 3:
        artifact_like = sum(
            1 for part in comma_parts
            if "->" in part or "→" in part or "ABAP_CDS." in part.upper() or re.search(r"\bZ[A-Z0-9_*$]+\b", part.upper())
        )
        if artifact_like >= max(2, len(comma_parts) // 2):
            return "<ul>" + "".join(f"<li>{inline_html(part)}</li>" for part in comma_parts) + "</ul>"
    return inline_html(clean)


def tone_of(semaforo: str) -> str:
    return SEMAFORO_TONE.get((semaforo or "").lower(), NEUTRAL)


def status_badge_html(semaforo: str, label: str) -> str:
    return f'<span class="catalog-card__verdict catalog-card__verdict--{tone_of(semaforo)}">{html.escape(label)}</span>'


# ─────────────────────────── filas (datos) ───────────────────────────
_ITEM_RE = re.compile(r"(?ms)^-\s*(?P<marker>\S+)?\s*\*\*(?P<title>.+?)\*\*\s*(?P<body>.*?)(?=\n-\s+\S*\s*\*\*|\Z)")


def compliance_rows(source: str, classify) -> list[dict]:
    """Bullets `- <marcador> **Título** _Evidencia:_ …` del generador → filas {semaforo, tone, label, title, evidence}.
    `classify`: clasificador legacy `texto → (semáforo, etiqueta)` de las demás páginas del Catálogo (conexiones, seguridad, data products)."""
    rows = []
    for m in _ITEM_RE.finditer((source or "").strip()):
        marker = m.group("marker") or ""
        title = re.sub(r"\s+", " ", m.group("title") or "").strip()
        body = m.group("body") or ""
        ev = re.search(r"(?is)_?Evidencia:_?\s*(.*)$", body)
        evidence = ev.group(1).strip() if ev else body.strip()
        semaforo, label = classify(f"{marker} {title} {evidence}")
        rows.append({"semaforo": semaforo, "tone": tone_of(semaforo), "label": label,
                     "title": Markup(inline_html(title)), "evidence": Markup(evidence_html(evidence))})
    return rows


def portability_rows(findings: list) -> list[dict]:
    """Hallazgos de portabilidad CONSOLIDADOS por regla (una fila por regla), con campos DISTINTOS; las ocurrencias por artefacto se informan en la evidencia."""
    if not findings:
        return []
    order = {"red": 0, "orange": 1, "amber": 2, "blue": 3, "green": 4}
    groups: dict[Any, list] = {}
    for f in sorted(findings, key=lambda x: order.get(x.get("semaforo"), 5)):
        groups.setdefault(f.get("regla"), []).append(f)
    rows = []
    for fs in groups.values():
        f0 = fs[0]
        nombres = list(dict.fromkeys(str(x.get("campo") or "") for x in fs))
        arts = {str(x.get("artefacto") or "") for x in fs}
        campos = ", ".join(nombres)
        if len(campos) > 160:
            campos = campos[:157] + "…"
        ev = html.escape(campos)
        if len(fs) > len(nombres):
            ev += html.escape(f" ({len(fs)} ocurrencias en {_plural(len(arts), 'artefacto')})")
        if f0.get("recomendacion"):
            ev += f" — <em>{html.escape(str(f0['recomendacion']))}</em>"
        semaforo = f0.get("semaforo", "amber")
        rows.append({"semaforo": semaforo, "tone": tone_of(semaforo),
                     "label": PORT_HALLAZGO_LABEL.get(f0.get("tipo_hallazgo"), "Nota"),
                     "title": Markup(f"{_plural(len(nombres), 'campo', 'campos')} · {html.escape(str(f0.get('titulo') or ''))}"),
                     "evidence": Markup(ev)})
    return rows


# ─────────────────────────── tablas HTML (compat. con las demás páginas del Catálogo) ───────────────────────────
def _table_html(headers: tuple[str, str, str], rows: list[dict]) -> str:
    body = "".join(
        f'<tr><td><span class="catalog-card__verdict catalog-card__verdict--{r["tone"]}">{html.escape(r["label"])}</span></td>'
        f'<td>{r["title"]}</td><td>{r["evidence"]}</td></tr>' for r in rows)
    head = "".join(f"<th>{h}</th>" for h in headers)
    # Tabla nativa del Hub (hereda `.methodology table`); el wrapper solo da aire + scroll en mobile.
    return f'<div class="catalog-compliance"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def compliance_table_html(source: str, classify) -> str:
    rows = compliance_rows(source, classify)
    return _table_html(("Estado", "Hallazgo", "Evidencia"), rows) if rows else (source or "").strip()


def portability_table_html(findings: list) -> str:
    rows = portability_rows(findings)
    return _table_html(("Tipo", "Hallazgo", "Campos · Recomendación"), rows) if rows else ""


# ─────────────────────────── vistas (resumen + filas) ───────────────────────────
_KIND_LABEL = {"OBSERVATION": "Observación", "REVIEW_REQUIRED": "Caso a revisar", "DESCRIPTIVE": "Señal descriptiva"}


NAMING_CONVENTION_TYPE = "NAMING_CONVENTION"


def governed_findings(groups: list[dict], section_28: dict | None, reference: dict | None = None, naming: dict | None = None) -> list[dict]:
    """Hallazgos de GOBIERNO del Assessment gobernado PERSISTIDO (única fuente; el evaluador legacy del Catálogo no interviene).
    ``groups``: grupos de hallazgo del área NAMING ({id, title, explanation, kind_text}); ``section_28``: evidencia canónica de higiene de nombres
    ({applicable, non_compliant, non_compliant_artifacts, violation_reason_histogram}) con los motivos YA sin ordinales; ``reference``: referencia
    SEMÁNTICA resuelta por el presentador ({module_key, section_key, text}). Solo COMPONE: no detecta ni recalcula nada. La identidad del hallazgo
    es ``finding_type`` + ``reference`` (módulo/sección por clave), NUNCA un número de sección: el número (``reference_text``) se resuelve al presentar.
    ``naming`` (opcional): familias de incumplimiento con SUS artefactos (``assessment_labels.naming_view``, derivadas de la evidencia canónica); con él la evidencia se redacta por familia y el
    universo completo queda disponible bajo demanda (``detail``) en lugar de un recorte «…(+N)». Sin él se conserva la redacción anterior."""
    rows = []
    s28 = section_28 or {}
    n_bad = int(s28.get("non_compliant") or 0)
    if n_bad > 0:
        arts = [str(a) for a in s28.get("non_compliant_artifacts") or []]
        reasons = [str(r.get("key")) for r in s28.get("violation_reason_histogram") or [] if r.get("key")]
        shown = ", ".join(arts[:6]) + (f" (+{len(arts) - 6})" if len(arts) > 6 else "")
        lead = f"{n_bad} de {int(s28.get('applicable') or 0)} artefactos: " if n_bad > 1 else ""
        evidence = lead + shown + ((": " if shown else "") + " | ".join(reasons) if reasons else "")
        detail = None
        if naming and naming.get("families") and naming.get("all_attributed"):       # universo completo atribuido por familia: se redacta por familia y se listan TODOS bajo demanda
            fams = " · ".join(f"{f['label']} ({f['count']})" for f in naming["families"])
            more = f" y {len(arts) - 6} más" if len(arts) > 6 else ""
            evidence = f"{n_bad} de {naming['applicable']} artefactos: {', '.join(arts[:6])}{more} — {fams}."
            detail = {"kind": "naming", "view": naming}
        elif naming and naming.get("families"):                                      # atribución parcial: redacción compacta de siempre + el detalle de lo que la evidencia sí identifica
            detail = {"kind": "naming", "view": naming}
        ref = reference or {}
        rows.append({"id": NAMING_CONVENTION_TYPE, "finding_type": NAMING_CONVENTION_TYPE, "dom_id": "asr-a-NAMING", "tone": NEUTRAL, "label": "Nota",
                     "title": "Oportunidades en la convención de nombres", "evidence": evidence or "Sin detalle de artefactos.", "detail": detail,
                     "link": ({"href": "#asr-a-NAMING", "text": f"Ver los {n_bad} artefactos por familia" if naming.get("all_attributed") else f"Ver el detalle de los {n_bad} artefactos"} if detail else None),
                     "reference": {k: ref[k] for k in ("module_key", "section_key") if k in ref} or None, "reference_text": ref.get("text")})
    for g in groups or []:
        rows.append({"id": g["id"], "finding_type": g["id"], "dom_id": f"asr-g-{g['id']}", "tone": NEUTRAL, "label": g.get("kind_text") or _KIND_LABEL.get(g.get("kind"), "Observación"),
                     "title": g["title"], "evidence": g.get("explanation") or "", "detail": g.get("detail"), "reference": None, "reference_text": None,
                     "link": ({"href": f"#asr-g-{g['id']}", "text": (f"Ver las {len(g['detail']['items'])} etiquetas con sus nombres técnicos" if (g.get("detail") or {}).get("kind") == "label_groups"
                                                                         else "Ver la evidencia del hallazgo")} if g.get("detail") or g.get("id") else None)})
    return rows


def governance_summary(n: int) -> str:
    return _plural(n, "observación de gobierno", "observaciones de gobierno") if n else "Sin observaciones de gobierno"


def governance_view(findings: list[dict] | None) -> dict | None:
    """Lectura de Gobierno & estándares sobre los hallazgos GOBERNADOS (sin nota global). ``None`` si no hay Assessment (``findings is None``)."""
    if findings is None:
        return None
    rows = [{**f, "title": Markup(html.escape(f["title"])), "evidence": Markup(html.escape(f["evidence"]))} for f in findings]   # (evidencia: la referencia va aparte, en `reference_text`)
    return {
        "rows": rows, "total": len(rows), "summary": governance_summary(len(rows)),
        "tiles": [{"value": len(rows), "label": "Observaciones de gobierno"}],
        "key_findings": [],
        "lead": ("Observaciones de gobierno y estándares del Assessment vigente (convención de nombres y señales asociadas). Son lecturas factuales "
                 "para revisión técnica; no se consolidan en una nota global."),
    }


def _confidence_help(port: dict) -> list[str]:
    """Qué evidencia respalda la confianza (no confundir cobertura de metadata ni compatibilidad con un destino)."""
    det = port.get("confianza_detalle")
    if not isinstance(det, dict):
        return ["Evaluado con reglas anteriores (v1): la confianza solo reflejaba la cobertura de metadata."]
    lines = ["Confianza de la evaluación estructural; no mide la compatibilidad con un destino.",
             f"Cobertura de metadata: {round(float(det.get('cobertura') or 0) * 100)} %."]
    if det.get("evidencia_ejercida"):
        lines.append(f"Evidencia estructural ejercida: {_plural(det.get('derivadas_numericas', 0), 'campo numérico derivado', 'campos numéricos derivados')} y "
                     f"{_plural(det.get('relaciones_fk_resueltas', 0), 'relación FK resuelta', 'relaciones FK resueltas')}.")
    else:
        lines.append("Sin campos numéricos derivados ni relaciones FK que evaluar: las reglas estructurales no tuvieron entrada.")
    if det.get("sensible_a_heuristicas"):
        lines.append("La banda cambia al retirar los indicios que dependen de nombres.")
    return lines


def portability_view(port: dict | None, acks: list | None = None, asset_type: str = "dominio", compat_rows: list | None = None) -> dict | None:
    """Lectura de Portabilidad & preparación BDC (portability-scoring/v2): CONSISTENCIA estructural de los tipos (score, confianza, riesgos) + restricciones por destino
    (`compat_rows`, proyección de `compatibilidad_bdc/v1` en la MISMA tabla; no suman al score ni a los riesgos). Sin badge de grado."""
    if not port or not isinstance(port, dict):
        return None
    clasif = str(port.get("clasificacion") or "no_evaluable")
    score = port.get("score")
    conf = str(port.get("confianza") or "media").title()
    dist = port.get("campos_distintos") if isinstance(port.get("campos_distintos"), dict) else None
    n_risk = dist["riesgos"] if dist else port.get("n_riesgos", 0)                      # KPI por campos distintos; el detalle conserva las ocurrencias
    n_ind = (dist["indicios"] + dist["normalizaciones"]) if dist else port.get("n_adaptaciones", 0)
    rev, noeval = port.get("campos_revisados", 0), port.get("campos_no_evaluables", 0)
    compat_rows = list(compat_rows or [])
    legacy = not port.get("version")
    scope = ("Análisis retrospectivo del contrato publicado: safety check de incompatibilidades residuales y detección de "
             "adaptaciones/workarounds (los indicios no penalizan)."
             if asset_type == "data_product"
             else "Consistencia estructural de los tipos del dominio, independiente del destino de publicación. Lo que depende del mecanismo o del destino se muestra en la "
                  "misma tabla como «condicionado por destino».")
    tiles = []
    if isinstance(score, int):
        tiles.append({"value": f"{score}/100", "label": "Consistencia de tipos",
                      "help": ["Consistencia estructural de los tipos del dominio, sin considerar el destino de publicación."]})
    tiles += [{"value": conf, "label": "Confianza", "help": _confidence_help(port)}, {"value": n_risk, "label": "Riesgos"},
              {"value": n_ind, "label": "Normalizaciones / indicios"}]
    parts = ([f"Consistencia {score}/100"] if isinstance(score, int) else []) + [f"confianza {conf.lower()}", _plural(n_risk, "riesgo")]
    n_tipos = len({t for r in compat_rows for t in r.get("tipos", [])})                         # tipos DISTINTOS sujetos a restricciones por destino (no filas ni escenarios)
    if compat_rows:
        parts.append(_plural(n_tipos, "tipo sujeto a restricciones por destino", "tipos sujetos a restricciones por destino"))
    label = (PORT_CLASIF_NOT_EXERCISED if (clasif == "plena" and port.get("clase_evidencia") == "no_ejercida")
             else PORT_CLASIF_LABEL.get(clasif, clasif.replace("_", " ").title()))
    return {
        "rows": portability_rows(port.get("findings") or []) + compat_rows,
        "tiles": tiles, "summary": " · ".join(parts),
        "classification": label,
        "lead": scope,                                   # la clasificación propia NO se muestra como grado: se confundiría con el nivel de adopción
        "reviewed": rev, "not_evaluable": noeval, "n_acks": len(acks or []), "legacy": legacy, "n_compat": n_tipos,
        "coverage": (f"{_plural(rev, 'campo revisado', 'campos revisados')}"
                     + (f" · {_plural(noeval, 'no evaluable', 'no evaluables')}" if noeval else "")
                     + (" · Evaluado con reglas anteriores (v1): regenerá el Catálogo para actualizarlo." if legacy else "")),
    }


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def asset_evaluation(entry: Any, findings: list[dict] | None = None, assessed: bool = False) -> dict:
    """Gobierno + Portabilidad de un flujo SOLO cuando existe un Assessment gobernado (``assessed``): sin Assessment no se expone ninguna conclusión
    del evaluador legacy del Catálogo. ``findings`` = hallazgos de gobierno del Assessment persistido (``governed_findings``)."""
    if not assessed:
        return {"governance": None, "portability": None}
    from webapp.services import bdc_compatibility_service       # eje ADITIVO e independiente del score (derivado en solo lectura; fail-soft): UN resultado alimenta tabla y detalle
    compat = bdc_compatibility_service.evaluate_entry(entry)
    return {
        "governance": governance_view(findings if findings is not None else []),
        "portability": portability_view(getattr(entry, "portabilidad_bdc", None), getattr(entry, "portabilidad_ack", None), "dominio",
                                        compat_rows=bdc_compatibility_service.table_rows(compat)),
        "compatibility": bdc_compatibility_service.build_view(compat),
    }
