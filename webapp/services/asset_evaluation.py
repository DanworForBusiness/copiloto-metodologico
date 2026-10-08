"""Evaluación del activo (presentación): Gobierno & estándares + Portabilidad & preparación BDC.

Una sola implementación para el Catálogo (resumen compacto) y el informe de Assessment (sección). Solo PRESENTA datos que el
generador del catálogo ya produjo (`## Hallazgos…` del flujo y el dict `portabilidad_bdc`); no recalcula, no decide niveles
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

PORT_CLASIF_LABEL = {
    "plena": "Plena", "buena": "Buena", "condicionada": "Condicionada",
    "inspiracional": "Inspiracional / Referencia",
    "no_recomendable": "No recomendable para réplica directa",
    "no_evaluable": "No evaluable",
}
PORT_HALLAZGO_LABEL = {
    "riesgo": "Riesgo", "adaptacion": "Adaptación", "buena_practica": "Buena práctica",
    "hint": "Hint", "no_evaluable": "No evaluable",
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


# Marcador de severidad que el GENERADOR del flujo ya emitió (`severidad`: alto / medio / nota) → semáforo. Es un mapa de presentación del
# dato existente: no se infiere nada del texto; un marcador desconocido queda como «Nota».
_MARKER_STATUS = {"🔴": ("red", "Bloqueante"), "🟡": ("amber", "Observar"), "🔵": ("blue", "Nota"),
                  "🟠": ("orange", "Atender"), "🟢": ("green", "Cumple")}


def compliance_status(marker: str) -> tuple[str, str]:
    """Marcador del generador → (semáforo, etiqueta)."""
    return _MARKER_STATUS.get(marker, ("blue", "Nota"))


def tone_of(semaforo: str) -> str:
    return SEMAFORO_TONE.get((semaforo or "").lower(), NEUTRAL)


def status_badge_html(semaforo: str, label: str) -> str:
    return f'<span class="catalog-card__verdict catalog-card__verdict--{tone_of(semaforo)}">{html.escape(label)}</span>'


# ─────────────────────────── filas (datos) ───────────────────────────
_ITEM_RE = re.compile(r"(?ms)^-\s*(?P<marker>\S+)?\s*\*\*(?P<title>.+?)\*\*\s*(?P<body>.*?)(?=\n-\s+\S*\s*\*\*|\Z)")


def compliance_rows(source: str, classify=None) -> list[dict]:
    """Bullets `- <marcador> **Título** _Evidencia:_ …` del generador → filas {semaforo, tone, label, title, evidence}.
    `classify` (opcional): clasificador legacy `texto → (semáforo, etiqueta)` de las páginas existentes del Catálogo; por defecto manda el marcador."""
    rows = []
    for m in _ITEM_RE.finditer((source or "").strip()):
        marker = m.group("marker") or ""
        title = re.sub(r"\s+", " ", m.group("title") or "").strip()
        body = m.group("body") or ""
        ev = re.search(r"(?is)_?Evidencia:_?\s*(.*)$", body)
        evidence = ev.group(1).strip() if ev else body.strip()
        semaforo, label = classify(f"{marker} {title} {evidence}") if classify else compliance_status(marker)
        rows.append({"semaforo": semaforo, "tone": tone_of(semaforo), "label": label,
                     "title": Markup(inline_html(title)), "evidence": Markup(evidence_html(evidence))})
    return rows


def portability_rows(findings: list) -> list[dict]:
    """Hallazgos de portabilidad AGRUPADOS por regla (una fila por regla: evita listar decenas de campos)."""
    if not findings:
        return []
    order = {"red": 0, "orange": 1, "amber": 2, "blue": 3, "green": 4}
    groups: dict[Any, list] = {}
    for f in sorted(findings, key=lambda x: order.get(x.get("semaforo"), 5)):
        groups.setdefault(f.get("regla"), []).append(f)
    rows = []
    for fs in groups.values():
        f0 = fs[0]
        nombres = list(dict.fromkeys(
            (f"{x.get('artefacto')}.{x.get('campo')}" if x.get("artefacto") else str(x.get("campo") or "")) for x in fs))
        campos = ", ".join(nombres)
        if len(campos) > 160:
            campos = campos[:157] + "…"
        ev = html.escape(campos)
        if f0.get("recomendacion"):
            ev += f" — <em>{html.escape(str(f0['recomendacion']))}</em>"
        semaforo = f0.get("semaforo", "amber")
        rows.append({"semaforo": semaforo, "tone": tone_of(semaforo),
                     "label": PORT_HALLAZGO_LABEL.get(f0.get("tipo_hallazgo"), "Nota"),
                     "title": Markup(f"{len(fs)} campo(s) · {html.escape(str(f0.get('titulo') or ''))}"),
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


def compliance_table_html(source: str, classify=None) -> str:
    rows = compliance_rows(source, classify)
    return _table_html(("Estado", "Hallazgo", "Evidencia"), rows) if rows else (source or "").strip()


def portability_table_html(findings: list) -> str:
    rows = portability_rows(findings)
    return _table_html(("Tipo", "Hallazgo", "Campos · Recomendación"), rows) if rows else ""


# ─────────────────────────── vistas (resumen + filas) ───────────────────────────
_SEVERITY = {"red": 0, "orange": 1, "amber": 2, "blue": 3, "green": 4}


def governance_view(hallazgos_md: str | None) -> dict | None:
    """Lectura de Gobierno & estándares sobre los hallazgos de cumplimiento del flujo. SIN nota global: conteos factuales
    (bloqueantes / a atender / observaciones / cumplen) + los hallazgos clave (los de mayor severidad que no cumplen)."""
    rows = compliance_rows(hallazgos_md or "")
    if not rows:
        return None
    n = {k: sum(1 for r in rows if r["semaforo"] == k) for k in _SEVERITY}
    blocking, attend, observe, notes, ok = n["red"], n["orange"], n["amber"], n["blue"], n["green"]
    pending = sorted((r for r in rows if r["semaforo"] != "green"), key=lambda r: _SEVERITY.get(r["semaforo"], 5))
    parts = [_plural(len(rows), "hallazgo"), _plural(blocking, "bloqueante")]
    if attend:
        parts.append(f"{attend} a atender")
    if observe or notes:
        parts.append(_plural(observe + notes, "observación", "observaciones"))
    return {
        "tiles": [{"value": len(rows), "label": "Hallazgos"}, {"value": blocking, "label": "Bloqueantes"},
                  {"value": attend, "label": "A atender"}, {"value": observe + notes, "label": "Observaciones"}],
        "rows": rows, "total": len(rows), "blocking": blocking, "attend": attend, "observe": observe, "notes": notes, "ok": ok,
        "summary": " · ".join(parts),
        "key_findings": [r["title"] for r in pending[:3]] if len(rows) > 3 else [],     # con ≤3 hallazgos la tabla ya los muestra todos (no repetir)
        "lead": ("Los hallazgos de cumplimiento del activo frente al estándar de modelado y fuente. Son lecturas factuales para "
                 "revisión técnica; no se consolidan en una nota global."),
    }


def portability_view(port: dict | None, acks: list | None = None, asset_type: str = "dominio") -> dict | None:
    """Lectura de Portabilidad & preparación BDC: capacidad TÉCNICA (score, confianza, riesgos). Sin badge de grado."""
    if not port or not isinstance(port, dict):
        return None
    clasif = str(port.get("clasificacion") or "no_evaluable")
    score = port.get("score")
    conf = str(port.get("confianza") or "media").title()
    n_risk, n_adapt = port.get("n_riesgos", 0), port.get("n_adaptaciones", 0)
    rev, noeval = port.get("campos_revisados", 0), port.get("campos_no_evaluables", 0)
    scope = ("Análisis retrospectivo del contrato publicado: safety check de incompatibilidades residuales y detección de "
             "adaptaciones/workarounds (los hints no penalizan)."
             if asset_type == "data_product"
             else "Diagnóstico preventivo de portabilidad hacia HDLF / SAP BDC / Databricks.")
    tiles = []
    if isinstance(score, int):
        tiles.append({"value": f"{score}/100", "label": "Score de portabilidad"})
    tiles += [{"value": conf, "label": "Confianza"}, {"value": n_risk, "label": "Riesgos"},
              {"value": n_adapt, "label": "Adaptaciones / hints"}]
    parts = ([f"Score {score}/100"] if isinstance(score, int) else []) + [f"confianza {conf.lower()}", _plural(n_risk, "riesgo")]
    return {
        "rows": portability_rows(port.get("findings") or []),
        "tiles": tiles, "summary": " · ".join(parts),
        "classification": PORT_CLASIF_LABEL.get(clasif, clasif.replace("_", " ").title()),
        "lead": scope,                                   # la clasificación propia (Plena/Buena/…) NO se muestra como grado: se confundiría con el nivel de adopción
        "reviewed": rev, "not_evaluable": noeval, "n_acks": len(acks or []),
        "coverage": (f"{_plural(rev, 'campo revisado', 'campos revisados')}"
                     + (f" · {_plural(noeval, 'no evaluable', 'no evaluables')}" if noeval else "")),
    }


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def hallazgos_block(body: str | None) -> str | None:
    """El bloque `## Hallazgos…` del `flujo_*.md` (lo que el generador emite como cumplimiento)."""
    m = re.search(r"##\s*Hallazgos[^\n]*\n(.*)$", body or "", re.S)
    return m.group(1) if m else None


def asset_evaluation(entry: Any) -> dict:
    """Gobierno + Portabilidad de un flujo del Catálogo (cada lectura puede faltar → None)."""
    return {
        "governance": governance_view(hallazgos_block(getattr(entry, "body", None))),
        "portability": portability_view(getattr(entry, "portabilidad_bdc", None), getattr(entry, "portabilidad_ack", None), "dominio"),
    }
