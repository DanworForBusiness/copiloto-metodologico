"""compat_bdc.py — Compatibilidad BDC por restricciones (contrato `compatibilidad_bdc/v1`).

EJE ADITIVO e INDEPENDIENTE del score de portabilidad (`portability_bdc.py`): no emite puntaje, no lee ni modifica
`portabilidad_bdc` y no usa nombres/labels de campos. Describe, por ESCENARIO (mecanismo × destino) y por RESTRICCIÓN, qué
ocurre con cada TIPO de columna (el `tipo`/`longitud` canónico del CSN, tal como ya lo consume el evaluador de portabilidad).

Fuente única de las restricciones: `content_packs/<pack>/portability_bdc.yaml`, sección `compatibilidad`. Este módulo no
tiene seeds de respaldo a propósito: duplicar las restricciones en código crearía dos fuentes de verdad. Sin la sección, el
eje devuelve `None` (el Assessment simplemente no lo muestra).

Estados por campo/escenario (de menos a más severo): compatible · conversion_documentada · condicionada · adaptacion_requerida ·
incompatible_explicita; `no_evaluable` aparte (tipo desconocido o columna sin tipo: nunca se asume compatibilidad). La ausencia
de un tipo en una matriz SAP NO es una prohibición: es `condicionada` + `pendiente_verificacion`.

API:
    load_compatibility(pack) -> dict | None
    parse_declared(raw, matrix) -> (escenarios_declarados, invalidos)
    evaluate_compatibility(columns, declared, matrix) -> dict   (diagnóstico descriptivo, sin score)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_BASE_DIR = Path(__file__).resolve().parents[2]
CONTRACT = "compatibilidad_bdc/v1"
DEFAULT_PACK = "datasphere_core"
UNDECLARED = "condicionada_a_destino"
_EXAMPLES = 3

_cache: dict[tuple[str, int], dict | None] = {}


def load_compatibility(pack: str | None = None) -> dict | None:
    """Sección `compatibilidad` del YAML del pack (cae al pack base si el derivado no la trae). None si falta o es inválida."""
    if yaml is None:
        return None
    for name in dict.fromkeys([pack or DEFAULT_PACK, DEFAULT_PACK]):
        path = _BASE_DIR / "content_packs" / name / "portability_bdc.yaml"
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            continue
        if key not in _cache:
            try:
                data = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("compatibilidad")
            except Exception:  # noqa: BLE001
                data = None
            _cache[key] = data if isinstance(data, dict) and data.get("contrato") == CONTRACT else None
        if _cache[key]:
            return _cache[key]
    return None


def _norm_type(value: Any) -> str:
    low = str(value or "").strip().lower()
    return low[4:] if low.startswith("cds.") else low


def _scenarios(matrix: dict) -> dict[str, dict]:
    return {s["id"]: s for s in matrix.get("escenarios") or []}


def parse_declared(raw: Any, matrix: dict) -> tuple[list[str], list[dict]]:
    """Contexto manual `publicacion_bdc` → ([id de escenario], [declaraciones inválidas con motivo]). Admite un dict o una lista de dicts
    `{mecanismo, destino}`. Un valor desconocido NO se corrige ni se infiere: queda como inválido y el contexto se trata como no declarado."""
    if raw in (None, "", [], {}):
        return [], []
    items = raw if isinstance(raw, list) else [raw]
    by_pair = {(s["mecanismo"], s["destino"]): s["id"] for s in matrix.get("escenarios") or []}
    ok: list[str] = []
    bad: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            bad.append({"valor": str(it)[:80], "motivo": "se esperaba {mecanismo, destino}"})
            continue
        mec, dest = str(it.get("mecanismo") or "").strip(), str(it.get("destino") or "").strip()
        sid = by_pair.get((mec, dest))
        if sid:
            if sid not in ok:
                ok.append(sid)
        elif mec in (matrix.get("mecanismos") or {}) and dest in (matrix.get("destinos") or {}):
            bad.append({"valor": f"{mec} → {dest}", "motivo": "combinación sin escenario definido"})
        else:
            bad.append({"valor": f"{mec or '?'} → {dest or '?'}", "motivo": "mecanismo o destino desconocido"})
    return ok, bad


def _specificity(restriction: dict) -> tuple[int, int]:
    alc = restriction.get("alcance") or {}
    mecs = alc.get("mecanismos") or ["*"]
    return (0 if "*" in mecs else 1, -len(alc.get("destinos") or []))


def _applies(restriction: dict, scenario: dict) -> bool:
    alc = restriction.get("alcance") or {}
    mecs, dests = alc.get("mecanismos") or ["*"], alc.get("destinos") or []
    return ("*" in mecs or scenario["mecanismo"] in mecs) and scenario["destino"] in dests


def _inventory(columns: list[dict], valid: set[str]) -> dict:
    types: dict[str, dict] = {}
    for c in columns:
        t = _norm_type(c.get("tipo"))
        row = types.setdefault(t, {"tipo": str(c.get("tipo") or "").strip(), "columnas": 0, "_campos": set(), "_arts": set()})
        row["columnas"] += 1
        row["_campos"].add(str(c.get("campo") or ""))
        row["_arts"].add(str(c.get("artefacto") or ""))
    inv = []
    for t, row in sorted(types.items(), key=lambda kv: (-kv[1]["columnas"], kv[0])):
        inv.append({"tipo": row["tipo"] or "—", "columnas": row["columnas"], "campos": len(row["_campos"]),
                    "artefactos": len(row["_arts"]), "valido_datasphere": bool(t) and t in valid})
    return {"tipos": inv, "columnas": len(columns), "no_reconocidas": sum(r["columnas"] for r in inv if not r["valido_datasphere"])}


def evaluate_compatibility(columns: list[dict], declared: list[str] | None, matrix: dict, invalid: list[dict] | None = None) -> dict:
    """Diagnóstico descriptivo de compatibilidad. ``declared`` = ids de escenario declarados explícitamente (vacío = destino no declarado:
    se evalúan TODOS los escenarios como hipótesis y el estado global queda condicionado al destino). Nunca devuelve un puntaje."""
    scen = _scenarios(matrix)
    order = {k: v["orden"] for k, v in (matrix.get("estados_compatibilidad") or {}).items()}
    valid = {_norm_type(t) for t in matrix.get("tipos_datasphere") or []}
    declared = [d for d in (declared or []) if d in scen]
    evaluate_ids = declared or list(scen)
    restrictions = [r for r in matrix.get("restricciones") or [] if r.get("habilitada", True)]

    out_scen = []
    for sid in evaluate_ids:
        sc = scen[sid]
        applicable = [r for r in restrictions if _applies(r, sc)]
        groups: dict[str, dict] = {}                                   # restricción ganadora → columnas
        by_type: dict[str, list[dict]] = {}
        for c in columns:
            t = _norm_type(c.get("tipo"))
            if not t or t not in valid:
                continue
            hits = [r for r in applicable if t in {_norm_type(x) for x in r.get("tipos") or []}]
            if not hits:
                continue
            hits.sort(key=lambda r: (_specificity(r), order.get(r["resultado"], 0)), reverse=True)
            win = hits[0]
            g = groups.setdefault(win["id"], {"restriccion": win, "columnas": 0, "_campos": {}, "_tipos": {}, "tambien": {}})
            g["columnas"] += 1
            g["_campos"].setdefault(str(c.get("campo") or ""), str(c.get("artefacto") or ""))
            g["_tipos"][str(c.get("tipo"))] = g["_tipos"].get(str(c.get("tipo")), 0) + 1
            for other in hits[1:]:
                g["tambien"][other["id"]] = other["nombre"]
        resultados = []
        for g in groups.values():
            r = g["restriccion"]
            campos = list(g["_campos"])
            resultados.append({
                "restriccion": r["id"], "nombre": r["nombre"], "estado": r["resultado"],
                "estado_validacion": r["estado_validacion"], "tipos": g["_tipos"], "columnas": g["columnas"], "campos": len(campos),
                "ejemplos": campos[:_EXAMPLES],
                "convierte_a": r.get("convierte_a", ""), "detalle": r.get("detalle", ""), "implicancia": r.get("implicancia", ""),
                "recomendacion": r.get("recomendacion", ""), "pregunta": r.get("pregunta", ""),
                "fuentes": list(r.get("fuentes") or []), "evidencias": list(r.get("evidencias") or []),
                "metodologia": r.get("metodologia"), "tambien": [{"id": k, "nombre": v} for k, v in g["tambien"].items()]})
        resultados.sort(key=lambda x: (-order.get(x["estado"], 0), x["restriccion"]))
        worst = max((order.get(x["estado"], 0) for x in resultados), default=0)
        estado = next((k for k, v in order.items() if v == worst and k != "no_evaluable"), "compatible")
        out_scen.append({"id": sid, "nombre": sc["nombre"], "mecanismo": sc["mecanismo"], "destino": sc["destino"], "declarado": sid in declared,
                         "nota": sc.get("nota", ""), "condiciones": list(sc.get("condiciones") or []), "estado": estado, "resultados": resultados})

    inv = _inventory(columns, valid)
    if declared:
        worst_state = max((s["estado"] for s in out_scen), key=lambda e: order.get(e, 0))
        estado_global = worst_state
    else:
        estado_global = UNDECLARED
    questions = [{"id": q["id"], "texto": q["texto"]} for q in matrix.get("preguntas") or []] if not declared else []
    seen = {q["texto"] for q in questions}
    open_states = {"condicionada", "adaptacion_requerida", "incompatible_explicita"}
    for s in out_scen:
        for r in s["resultados"]:
            if r["pregunta"] and r["pregunta"] not in seen and (not declared or r["estado"] in open_states):
                seen.add(r["pregunta"])
                questions.append({"id": r["restriccion"], "texto": r["pregunta"]})
    sources = []
    for s in out_scen:
        for r in s["resultados"]:
            for f in r["fuentes"]:
                if f not in sources:
                    sources.append(f)
        for cnd in s["condiciones"]:
            for f in cnd.get("fuentes") or []:
                if f not in sources:
                    sources.append(f)
    return {
        "contrato": CONTRACT, "declarado": bool(declared), "invalidos": list(invalid or []),
        "estado_global": estado_global, "inventario": inv, "escenarios": out_scen, "preguntas": questions, "fuentes": sources,
        "n_restricciones": sum(len(s["resultados"]) for s in out_scen),
    }
