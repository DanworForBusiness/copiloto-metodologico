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
        return compat_bdc.evaluate_compatibility(columns, declared, matrix, invalid)
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


_PROSE_ORDER = {"incompatible_explicita": 0, "adaptacion_requerida": 1, "conversion_documentada": 2, "condicionada": 3}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def catalog_signal(result: dict, matrix: dict) -> str | None:
    """Conclusión editorial breve para el Catálogo (Mirada Danwor), derivada del MISMO resultado del motor: una o dos frases en lenguaje natural, sin etiquetas
    de estado ni ids del motor. Solo existe cuando hay restricciones relevantes para el escenario; sin nada que advertir devuelve None (sin ruido)."""
    synth = {r["id"]: r.get("sintesis", "") for r in matrix.get("restricciones") or []}
    hits = [(s, r) for s in result["escenarios"] for r in s["resultados"]]
    if not hits:
        return None
    if not result["declarado"]:
        explicit = any(r["estado"] == "incompatible_explicita" for _s, r in hits)
        return ("La compatibilidad de publicación depende del destino elegido. "
                + ("El modelo contiene tipos de datos que algunos destinos no admiten y que habría que adaptar para BDC." if explicit
                   else "El modelo contiene tipos de datos que pueden requerir adaptación para BDC."))
    sentences = []
    for s in result["escenarios"]:
        ordered = sorted(s["resultados"], key=lambda r: _PROSE_ORDER.get(r["estado"], 9))             # primero lo que cambia, al final lo que solo hay que confirmar
        parts = [synth.get(r["restriccion"], "").replace("{tipos}", _join(list(r["tipos"]))) for r in ordered]
        parts = [p for p in parts if p]
        if parts:
            sentences.append(f"Para {s['nombre']}, {_join(parts)}.")
    return " ".join(sentences) or None


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
        if not ref:
            return None
        r = MR.resolve_ref(ref, pack)
        return {"text": r["text"], "url": _module_url(r["module_key"], r["slug"] and (ref.get("ancla") or r["slug"]), pack)}

    scenarios = []
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
                "also": [t["id"] for t in r["tambien"]]})
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
    signal = catalog_signal(result, matrix)
    n_types = len(inv["tipos"])
    recognized = inv["no_reconocidas"] == 0
    inv_summary = (f"{_plural(inv['columnas'], 'columna', 'columnas')} · {_plural(n_types, 'tipo', 'tipos')} · "
                   + ("todos válidos en Datasphere" if recognized else f"{_plural(inv['no_reconocidas'], 'columna', 'columnas')} con tipo no reconocido por la matriz"))
    sources = [src(f) for f in result["fuentes"]]
    raw = " ".join(f'{s["label"]}="{s["url"]}"' for s in sources if s["url"])
    return {
        "state": state, "declared": declared, "context": context, "signal": signal, "scenarios": scenarios, "invalid": result["invalidos"],
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
