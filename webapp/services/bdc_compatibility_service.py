"""bdc_compatibility_service.py — Compatibilidad BDC del dominio, en SOLO LECTURA (presentación).

Compone la evidencia que el Catálogo YA tiene (el `tipo`/`longitud` canónico de cada columna, en `vistas/<artefacto>.md`) con el contexto de
publicación que el equipo DECLARA en el flujo (`manual.publicacion_bdc`) y se lo entrega al motor puro `tools/catalog/compat_bdc.py`. No persiste
nada, no toca `portabilidad_bdc` (score histórico), el linaje ni el Juicio; fail-soft: ante cualquier problema devuelve None y el informe se muestra
como siempre. Misma estrategia que la clasificación de dominio: derivar en lectura desde el Catálogo, sin migración.

El resultado se traduce a un diagnóstico DESCRIPTIVO (sin puntaje): escenario evaluado, estado, restricciones relevantes con su evidencia y su
estado de validación (SAP documentado / Validado Danwor / Pendiente de verificación), recomendaciones y enlaces a las fuentes (Support Links) y a la
sección metodológica del Copiloto (referencias SEMÁNTICAS por `key_topics`, vía `module_reference`).
"""
from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from markupsafe import Markup

from tools.catalog import compat_bdc
from webapp.services import content_service, module_reference as MR
from webapp.services.status_tone import ACCEPTABLE, ATTENTION, NEUTRAL, POSITIVE

log = logging.getLogger(__name__)

PACK = "datasphere_core"
_STATE_TONE = {"compatible": POSITIVE, "conversion_documentada": ACCEPTABLE, "condicionada": NEUTRAL,
               "adaptacion_requerida": ATTENTION, "incompatible_explicita": ATTENTION, "no_evaluable": NEUTRAL}
_VALIDATION_TONE = {"sap_documentado": POSITIVE, "danwor_empirico": ACCEPTABLE, "pendiente_verificacion": NEUTRAL}
_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_cache: dict[tuple, tuple[list[dict], Any]] = {}


def _front(path: Path) -> dict:
    m = _FM.match(path.read_text(encoding="utf-8", errors="ignore"))
    return (yaml.load(m.group(1), Loader=_LOADER) or {}) if m else {}


def _signature(entry: Any, names: list[str]) -> tuple:
    base = Path(entry.path)
    vistas = base.parent / "vistas"
    stamps = [base.stat().st_mtime_ns]
    for n in names:
        try:
            stamps.append((vistas / f"{n}.md").stat().st_mtime_ns)
        except OSError:
            stamps.append(0)
    return (str(base), tuple(stamps))


def _evidence(entry: Any) -> tuple[list[dict], Any]:
    """(columnas {artefacto, campo, tipo, longitud}, `manual.publicacion_bdc` crudo) del flujo. Cacheado por mtime de los archivos del Catálogo."""
    names = [str(n) for n in (getattr(entry, "artefactos", None) or [])]
    key = _signature(entry, names)
    if key not in _cache:
        if len(_cache) > 32:
            _cache.clear()
        cols: list[dict] = []
        vistas = Path(entry.path).parent / "vistas"
        for n in names:
            try:
                auto = (_front(vistas / f"{n}.md").get("auto")) or {}
            except OSError:
                continue
            for f in auto.get("campos") or []:
                cols.append({"artefacto": n, "campo": f.get("campo"), "tipo": f.get("tipo"), "longitud": f.get("longitud")})
        manual = (_front(Path(entry.path)).get("manual")) or {}
        _cache[key] = (cols, manual.get("publicacion_bdc"))
    return _cache[key]


def evaluate_entry(entry: Any, pack: str = PACK) -> dict | None:
    """Resultado crudo del motor para un flujo del Catálogo (None sin matriz, sin artefactos o ante error)."""
    try:
        matrix = compat_bdc.load_compatibility(pack)
        if not matrix or entry is None or not getattr(entry, "artefactos", None):
            return None
        columns, raw = _evidence(entry)
        if not columns:
            return None
        declared, invalid = compat_bdc.parse_declared(raw, matrix)
        result = compat_bdc.evaluate_compatibility(columns, declared, matrix, invalid)
        ident: dict[str, set] = {}
        for c in columns:                                    # {tipo: {(artefacto, columna)}}: identidad técnica real; una columna cuenta UNA vez aunque aparezca en varias restricciones/escenarios
            ident.setdefault(str(c.get("tipo") or ""), set()).add((str(c.get("artefacto") or ""), str(c.get("campo") or "")))
        result["identidades"] = ident
        return result
    except Exception:                                   # noqa: BLE001 — el informe nunca se rompe por este eje
        log.warning("compatibilidad BDC: no se pudo evaluar %s", getattr(entry, "entry_id", "?"), exc_info=True)
        return None


def _module_url(module_key: str, slug: str | None, pack: str) -> str:
    if not slug:
        return ""
    try:
        summary = next((m for m in content_service.list_modules(pack) if m.number == str(module_key).zfill(2)), None)
    except Exception:                                   # noqa: BLE001
        summary = None
    return f"/modules/{quote(summary.stem)}?pack={quote(pack)}#{quote(slug)}" if summary else ""


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


_ROW_STATE = {"incompatible_explicita": ("Incompatibilidad", ATTENTION), "adaptacion_requerida": ("Adaptación requerida", ATTENTION),
              "conversion_documentada": ("Conversión documentada", ACCEPTABLE), "condicionada": ("Condicionado por destino", NEUTRAL)}
_SEVERITY = {"incompatible_explicita": 4, "adaptacion_requerida": 3, "condicionada": 2, "conversion_documentada": 1}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def _reference(ref: dict | None, pack: str) -> dict | None:
    """Referencia semántica al contenido (`key_topics` + ancla explícita del H3); nunca por número de sección."""
    if not ref:
        return None
    r = MR.resolve_ref(ref, pack)
    return {"text": r["text"], "url": _module_url(r["module_key"], r["slug"] and (ref.get("ancla") or r["slug"]), pack)}


def _evidence_text(hit: dict) -> str:
    ej = ", ".join(hit["ejemplos"])
    return (f"{_plural(hit['campos'], 'campo distinto', 'campos distintos')} ({_plural(hit['columnas'], 'columna', 'columnas')} en "
            f"{_plural(hit.get('artefactos', 0), 'artefacto', 'artefactos')})" + (f", p. ej. {ej}" if ej else ""))


def _identity_summary(types: list[str], ident: dict | None, hits: list[dict]) -> tuple[str, list[str]]:
    """«N columnas en M artefactos (K campos distintos): Tipo ×n, …» deduplicado por identidad técnica (artefacto, columna) + ejemplos `artefacto.columna`."""
    cols: set = set(); por_tipo: dict[str, int] = {}
    for t in types:
        ids = (ident or {}).get(t)
        if ids is None:                                          # sin identidades (resultado del motor sin servicio): se usa el conteo del hallazgo, sin sumar entre restricciones
            por_tipo[t] = max((h["tipos"].get(t, 0) for h in hits), default=0)
            continue
        cols |= ids; por_tipo[t] = len(ids)
    if cols:
        arts = {a for a, _c in cols}; campos = {c for _a, c in cols}
        head = (f"{_plural(len(cols), 'columna', 'columnas')} en {_plural(len(arts), 'artefacto', 'artefactos')} "
                f"({_plural(len(campos), 'campo distinto', 'campos distintos')})")
        ej = [f"{a}.{c}" if a else c for a, c in sorted(cols)[:3]]
    else:
        total = sum(por_tipo.values()); head = _plural(total, "columna", "columnas"); ej = []
    detail = ", ".join(f"{t} ×{n}" for t, n in sorted(por_tipo.items(), key=lambda kv: (-kv[1], kv[0])))
    return f"{head}: {detail}" + (f", p. ej. {', '.join(ej)}" if ej else ""), ej


def _consequence_bullets(items: list[tuple[str, str, str]], labels: dict) -> str:
    """Síntesis legible por mecanismo/destino. `items` = (escenario, consecuencia, estado de validación). Se conservan las consecuencias materialmente distintas y NO se repiten
    las equivalentes: lo exclusivo de un escenario va en su viñeta; lo idéntico en varios escenarios, en UNA viñeta que los nombra. Cada consecuencia lleva su base (SAP documentado /
    Validado Danwor / Pendiente de verificación): una restricción de un destino concreto nunca se presenta como universal."""
    escs_of: dict[tuple, list[str]] = {}
    order: list[str] = []
    for esc, texto, val in items:
        if esc not in order:
            order.append(esc)
        escs = escs_of.setdefault((texto, val), [])
        if esc not in escs:
            escs.append(esc)
    tag = lambda texto, val: f"{texto} ({labels.get(val, val)})"
    bullets = []
    for esc in order:
        own = [tag(t, v) for (t, v), es in escs_of.items() if es == [esc]]
        if own:
            bullets.append(f"{esc}: {'; '.join(own)}")
    for (t, v), es in escs_of.items():
        if len(es) > 1:
            bullets.append(f"{_join(es)}: {tag(t, v)}")
    return '<ul class="eval-cell-list">' + "".join(f"<li>{html.escape(b)}</li>" for b in bullets) + "</ul>"


def table_rows(result: dict | None, pack: str = PACK) -> list[dict]:
    """Restricciones `compatibilidad_bdc/v1` proyectadas como filas del MISMO patrón de la tabla de portabilidad (`{tone, label, title, evidence, reference_*}`).
    Tres responsabilidades separadas: HALLAZGO (una conclusión por estado), ALCANCE (tipos, campos, columnas y artefactos deduplicados por identidad técnica real) y
    RECOMENDACIÓN (las consecuencias técnicas de cada mecanismo/destino, sin repetir las equivalentes; el texto sale de las restricciones `SAPC-*`). Composición pura sobre el
    resultado ya calculado: no cuenta como riesgo ni toca el score.
    · Destino NO declarado: UNA fila «Condicionado por destino».
    · Destino declarado: una fila por ESTADO (conversión documentada, adaptación requerida, incompatibilidad, condicionado): los estados distintos no se mezclan."""
    if not result:
        return []
    matrix = compat_bdc.load_compatibility(pack) or {}
    synth = {r["id"]: r.get("sintesis", "") for r in matrix.get("restricciones") or []}
    labels = matrix.get("estados_validacion") or {}
    ident = result.get("identidades")
    hits = [(sc, h) for sc in result["escenarios"] for h in sc["resultados"]]
    if not hits:
        return []
    ref_hit = max((h for _sc, h in hits), key=lambda h: (_SEVERITY.get(h["estado"], 0), h["columnas"]))
    ref = _reference(ref_hit["metodologia"], pack)

    def row(label, tone, title, evidence, types):
        return {"semaforo": "", "tone": tone, "label": label, "title": Markup(html.escape(title)), "evidence": Markup(evidence),
                "reference_text": (ref or {}).get("text", ""), "reference_url": (ref or {}).get("url", ""), "origen": "compatibilidad", "tipos": types}

    def text_of(h):
        return synth.get(h["restriccion"], h["nombre"]).replace("{tipos}", _join(list(h["tipos"])))

    def build(grp, closing):
        types = list(dict.fromkeys(t for _sc, h in grp for t in h["tipos"]))
        scope, _ej = _identity_summary(types, ident, [h for _sc, h in grp])
        items = [(sc["nombre"], text_of(h), h["estado_validacion"]) for sc, h in grp]
        head, sep, ejemplos = scope.partition(", p. ej. ")
        scope_html = html.escape(head) + (html.escape(sep) + html.escape(ejemplos).replace(".", ".<wbr>") if sep else "")      # <wbr>: los `artefacto.columna` pueden partirse (sin CSS)
        return types, f"{scope_html}{_consequence_bullets(items, labels)}{html.escape(closing)}"

    if not result["declarado"]:
        types, ev = build(hits, "El resultado depende del mecanismo y del destino de publicación; declarar el destino lo concreta. Detalle y fuentes en el Assessment.")
        return [row("Condicionado por destino", NEUTRAL, "Tipos sujetos a restricciones según el mecanismo y el destino de publicación", ev, types)]
    rows = []
    for estado in sorted({h["estado"] for _sc, h in hits}, key=lambda e: -_SEVERITY.get(e, 0)):
        grp = [(sc, h) for sc, h in hits if h["estado"] == estado]
        label, tone = _ROW_STATE.get(estado, ("Condicionado por destino", NEUTRAL))
        title = {"incompatible_explicita": "Tipos sin soporte en el destino declarado", "adaptacion_requerida": "Tipos que requieren adaptación en el destino declarado",
                 "conversion_documentada": "Tipos que el destino declarado convierte (documentado por SAP)", "condicionada": "Tipos con restricciones por confirmar en el destino declarado"}[estado]
        recs = list(dict.fromkeys(h["recomendacion"] for _sc, h in grp if h["recomendacion"]))
        types, ev = build(grp, "")
        rows.append(row(label, tone, title, ev + (f"<em>{html.escape(recs[0])}</em>" if len(recs) == 1 else ""), types))
    return rows


def build_view(result: dict | None, pack: str = PACK) -> dict | None:
    """Resultado del motor → datos de presentación (texto plano: la plantilla escapa)."""
    if not result:
        return None
    matrix = compat_bdc.load_compatibility(pack) or {}
    states = {k: v["etiqueta"] for k, v in (matrix.get("estados_compatibilidad") or {}).items()}
    validation = matrix.get("estados_validacion") or {}
    fuentes = matrix.get("fuentes") or {}
    mecs, dests = matrix.get("mecanismos") or {}, matrix.get("destinos") or {}
    declared = result["declarado"]
    inv = result["inventario"]

    def src(sid: str) -> dict:
        f = fuentes.get(sid) or {}
        return {"id": sid, "label": f.get("etiqueta", sid), "title": f.get("titulo", sid), "url": f.get("url", "")}

    def reference(ref: dict | None) -> dict | None:
        return _reference(ref, pack)

    scenarios = []
    seen: dict[tuple, str] = {}
    for s in result["escenarios"]:
        rows = []
        for r in s["resultados"]:
            tipos = ", ".join(f"{t} ×{n}" for t, n in r["tipos"].items())
            ev = f"{_plural(r['columnas'], 'columna', 'columnas')} · {_plural(r['campos'], 'campo distinto', 'campos distintos')} · {tipos}"
            rows.append({
                "id": r["restriccion"], "name": r["nombre"], "state": r["estado"], "state_label": states.get(r["estado"], r["estado"]),
                "tone": _STATE_TONE.get(r["estado"], NEUTRAL), "validation": r["estado_validacion"],
                "validation_label": validation.get(r["estado_validacion"], r["estado_validacion"]),
                "validation_tone": _VALIDATION_TONE.get(r["estado_validacion"], NEUTRAL),
                "evidence": ev, "examples": r["ejemplos"], "detail": r["detalle"], "implication": r["implicancia"], "converts_to": r["convierte_a"],
                "recommendation": r["recomendacion"],
                "evidences": [{"label": validation.get(e["estado_validacion"], e["estado_validacion"]),
                               "tone": _VALIDATION_TONE.get(e["estado_validacion"], NEUTRAL), "text": e["texto"]} for e in r["evidencias"]],
                "sources": [src(f) for f in r["fuentes"]], "reference": reference(r["metodologia"]),
                "also": [t["id"] for t in r["tambien"]], "repeat_of": seen.get((r["restriccion"], r["estado"]), "")})
            seen.setdefault((r["restriccion"], r["estado"]), s["nombre"])
        scenarios.append({
            "id": s["id"], "name": s["nombre"], "declared": s["declarado"], "note": s["nota"], "state": s["estado"],
            "state_label": states.get(s["estado"], s["estado"]), "tone": _STATE_TONE.get(s["estado"], NEUTRAL), "rows": rows,
            "conditions": [{"text": c["texto"], "sources": [src(f) for f in c.get("fuentes") or []]} for c in s["condiciones"]]})

    if declared:
        gstate = result["estado_global"]
        state = {"key": gstate, "label": states.get(gstate, gstate), "tone": _STATE_TONE.get(gstate, NEUTRAL)}
        names = " · ".join(s["name"] for s in scenarios)
        context = f"Destino declarado por el equipo: {names}."
    else:
        state = {"key": result["estado_global"], "label": "Compatibilidad condicionada al destino", "tone": NEUTRAL}
        context = ("No hay un destino de publicación declarado: se muestra qué ocurre con los tipos del dominio según cada mecanismo y destino posible. "
                   "Cuando el equipo declare el contexto, el diagnóstico se acota a ese escenario.")
    n_types = len(inv["tipos"])
    recognized = inv["no_reconocidas"] == 0
    inv_summary = (f"{_plural(inv['columnas'], 'columna', 'columnas')} · {_plural(n_types, 'tipo', 'tipos')} · "
                   + ("todos válidos en Datasphere" if recognized else f"{_plural(inv['no_reconocidas'], 'columna', 'columnas')} con tipo no reconocido por la matriz"))
    sources = [src(f) for f in result["fuentes"]]
    raw = " ".join(f'{s["label"]}="{s["url"]}"' for s in sources if s["url"])
    return {
        "state": state, "declared": declared, "context": context, "scenarios": scenarios, "invalid": result["invalidos"],
        "summary": (f"{_plural(result['n_restricciones'], 'restricción relevante', 'restricciones relevantes')} · "
                    + (state["label"].lower() if declared else "destino no declarado")),
        "lead": ("Diagnóstico descriptivo por restricciones, sin puntaje: indica qué cambia al publicar los tipos del dominio en cada mecanismo y destino. "
                 "Un tipo válido en Datasphere no es un error del modelo: puede requerir una decisión de contrato según dónde se publique."),
        "inventory": [{"tipo": t["tipo"], "columnas": t["columnas"], "campos": t["campos"],
                       "valid": "Válido en Datasphere" if t["valido_datasphere"] else "No reconocido por la matriz"} for t in inv["tipos"]],
        "inventory_summary": inv_summary, "questions": result["preguntas"], "sources": sources,
        "support_links": Markup(content_service._support_links_html(raw)) if raw else Markup(""),
        "declaration": (matrix.get("declaracion") or {}),
        "mecanismos": mecs, "destinos": dests,
    }


def view_for_entry(entry: Any, pack: str = PACK) -> dict | None:
    return build_view(evaluate_entry(entry, pack), pack)
