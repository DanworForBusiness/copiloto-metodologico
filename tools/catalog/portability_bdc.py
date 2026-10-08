"""portability_bdc.py — Evaluador de Portabilidad SAP BDC / cross-runtime.

Dimensión de evaluación SEPARADA de la calidad de extracción (evaluate_compliance).
Estima el riesgo de portar un dominio/data product hacia HDLF/Object Store/Databricks,
a partir de la metadata de tipos que ya capturamos (tipo, precisión/escala, native
dataType, derivado/cast, PK/FK) y una MATRIZ configurable (content_packs/<pack>/
portability_bdc.yaml, con seeds de respaldo acá).

Dos contextos, NO se mezclan:
  • "dominio"      → diagnóstico PREVENTIVO.
  • "data_product" → retrospectivo: safety_check (residuales, penalizan) +
                     detección de adaptaciones/workarounds (hint/adaptación, no penaliza).

5 tipos de hallazgo: riesgo | adaptacion | buena_practica | hint | no_evaluable.
La falta de metadata produce "no_evaluable" (nunca un positivo asumido).

API principal:
    load_matrix(pack) -> dict
    evaluate_portability(columns, asset_type, matrix=None) -> dict   (resultado explicable)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

_BASE_DIR = Path(__file__).resolve().parents[2]

# Confianza textual → factor multiplicativo del scoring.
_CONF_FACTOR = {"alta": 1.0, "media": 0.7, "baja": 0.4}

# Semáforo por tipo de hallazgo (la UI lo mapea al badge canónico del Hub `.catalog-card__verdict--*` vía `_catalog_status_badge`).
SEMAFORO_HALLAZGO = {
    "riesgo": "red",
    "adaptacion": "blue",
    "buena_practica": "green",
    "hint": "blue",
    "no_evaluable": "amber",
}


# ── Seeds de respaldo (si falta el YAML del pack) ──────────────────────────────────
# Espejo mínimo de content_packs/<pack>/portability_bdc.yaml para que el evaluador
# funcione aún sin el archivo (mismo criterio defensivo que compliance_criteria).
_SEED_CONFIG: dict[str, Any] = {
    "bandas": [
        {"desde": 90, "clasificacion": "plena", "semaforo": "green"},
        {"desde": 75, "clasificacion": "buena", "semaforo": "green"},
        {"desde": 50, "clasificacion": "condicionada", "semaforo": "amber"},
        {"desde": 25, "clasificacion": "inspiracional", "semaforo": "orange"},
        {"desde": 0, "clasificacion": "no_recomendable", "semaforo": "red"},
    ],
    "peso_global": 0,
    "score_base": 100,
    "penalizacion_metadata_max": 12,
    "factor_severidad": {"alto": 1.0, "medio": 0.5, "nota": 0.15},
    "bonus_normalizacion": 4,
    "bonus_max": 12,
    # Cap de penalización por regla: muchos hallazgos menores de la MISMA regla no hunden
    # el score de forma desproporcionada (requisito de diseño).
    "penalizacion_max_por_regla": 20,
    "semantica": {
        "monetario": ["amount", "importe", "monto", "price", "precio", "cost", "costo",
                      "valor", "betrag", "wrbtr", "dmbtr"],
        "porcentaje_ratio": ["percent", "porcentaje", "ratio", "coverage", "cobertura",
                             "rate", "tasa", "proportion"],
        "cantidad": ["quantity", "cantidad", "qty", "menge", "weight", "peso",
                     "volume", "volumen"],
    },
    "escala_alta": 10,
    "precision_alta": 30,
}

_SEED_REGLAS: list[dict[str, Any]] = [
    {"id": "PB-001", "nombre": "DecimalFloat en campo monetario", "habilitada": True,
     "cuando": {"semantica": "monetario",
                "tipo_o_native_in": ["DecimalFloat", "DECFLOAT", "DECIMAL_FLOAT",
                                     "DECFLOAT16", "DECFLOAT34"]},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 22},
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 14,
                      "control": "safety_check"},
     "recomendacion": "Normalizar a DECIMAL(38,6) en la capa contractual, antes de la publicación.",
     "capa_correccion": "Capa contractual (antes del artefacto físico destinado a HDLF).",
     "confianza": "alta", "estado_validacion": "validado", "incidentes": ["caso-1-decimalfloat"]},
    {"id": "PB-002", "nombre": "Escala/precisión inferida elevada en derivado (ratio/porcentaje)",
     "habilitada": True,
     "cuando": {"tipo_base": "decimal", "escala_min": 10},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 20},
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 12,
                      "control": "safety_check"},
     "recomendacion": "Tipificar el campo con CAST en la misma expresión y capa donde nace (p. ej. DECIMAL(15,6)).",
     "capa_correccion": "En la expresión que origina el campo (flujo Datasphere previo a la publicación).",
     "confianza": "alta", "estado_validacion": "validado", "incidentes": ["caso-2-decimal-37-19"]},
    {"id": "PB-003", "nombre": "Campo con semántica numérica representado como String",
     "habilitada": True,
     "cuando": {"tipo_base": "string", "semantica_en": ["monetario", "porcentaje_ratio", "cantidad"]},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "nota", "peso": 6},
     "data_product": {"tipo_hallazgo": "hint", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Revisar si el String responde a una limitación técnica; preferir el tipo numérico contractual.",
     "capa_correccion": "Capa contractual o expresión de origen.",
     "confianza": "media", "estado_validacion": "recomendado"},
    {"id": "PB-004", "nombre": "Campo derivado sin cast contractual explícito", "habilitada": True,
     "cuando": {"derivado": True, "sin_cast": True,
                "o": [{"semantica_en": ["monetario", "porcentaje_ratio"]}, {"escala_min": 10}]},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 8},
     "data_product": {"tipo_hallazgo": "hint", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Fijar el tipo con CAST explícito en la misma capa/expresión donde nace el campo.",
     "capa_correccion": "Expresión de origen del campo derivado.",
     "confianza": "media", "estado_validacion": "recomendado"},
    {"id": "PB-005", "nombre": "Tipo distinto entre foreign key y su primary key relacionada",
     "habilitada": True,
     "cuando": {"pk_fk_tipo_distinto": True},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 18, "bloqueante": True},
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 18,
                      "control": "safety_check", "bloqueante": True},
     "recomendacion": "Alinear el tipo/longitud de la FK con su PK en la capa contractual.",
     "capa_correccion": "Capa contractual (definición de la relación).",
     "confianza": "alta", "estado_validacion": "recomendado"},
    {"id": "PB-010", "nombre": "Monetario normalizado a DECIMAL(38,6)", "habilitada": True,
     "cuando": {"semantica": "monetario", "tipo_base": "decimal", "precision_eq": 38, "escala_eq": 6},
     "dominio": {"tipo_hallazgo": "buena_practica", "severidad": "nota", "peso": 0},
     "data_product": {"tipo_hallazgo": "adaptacion", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Conservar el tipo y documentar la decisión antes de reutilizar el contrato.",
     "capa_correccion": "Ya normalizado en la capa contractual.",
     "confianza": "media", "estado_validacion": "validado", "incidentes": ["caso-1-decimalfloat"]},
    {"id": "PB-011", "nombre": "Ratio/porcentaje normalizado a DECIMAL(15,6)", "habilitada": True,
     "cuando": {"semantica": "porcentaje_ratio", "tipo_base": "decimal", "precision_eq": 15, "escala_eq": 6},
     "dominio": {"tipo_hallazgo": "buena_practica", "severidad": "nota", "peso": 0},
     "data_product": {"tipo_hallazgo": "adaptacion", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Conservar el cast en la capa donde nace el indicador y documentar la decisión.",
     "capa_correccion": "Ya normalizado en la expresión de origen.",
     "confianza": "media", "estado_validacion": "validado", "incidentes": ["caso-2-decimal-37-19"]},
]


def load_matrix(pack: str | None = None) -> dict:
    """Carga la matriz del pack (portability_bdc.yaml). Si falta o no parsea, cae a los
    seeds. Devuelve {'config': {...}, 'reglas': [...]}, con config completado por defaults."""
    reglas, config = None, None
    if pack and yaml is not None:
        path = _BASE_DIR / "content_packs" / pack / "portability_bdc.yaml"
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                reglas = data.get("reglas")
                config = data.get("config")
            except Exception:
                reglas, config = None, None
    merged = dict(_SEED_CONFIG)
    if isinstance(config, dict):
        merged.update({k: v for k, v in config.items() if v is not None})
    return {"config": merged, "reglas": reglas if isinstance(reglas, list) else _SEED_REGLAS}


# ── Parsing de tipos ───────────────────────────────────────────────────────────────
_DECIMAL_TYPES = {"decimal", "decimalfloat", "decfloat", "double", "float", "number", "numeric"}
_STRING_TYPES = {"string", "char", "nchar", "varchar", "nvarchar", "text", "largestring"}
_INT_TYPES = {"integer", "int", "int32", "int64", "integer64", "smallint", "tinyint", "bigint"}


def _parse_type(col: dict) -> dict:
    """Normaliza el tipo de una columna a {base, precision, scale, length, evaluable}.
    `base` ∈ {'decimal','string','integer','otro'}; precisión/escala salen de `longitud`
    ('p,s'); para String, `length` es la longitud de caracteres."""
    tipo = str(col.get("tipo") or "").strip()
    native = str(col.get("native") or "").strip()
    low = tipo.lower()
    base = "otro"
    if low in _DECIMAL_TYPES or "decimal" in low or "decfloat" in native.lower():
        base = "decimal"
    elif low in _STRING_TYPES:
        base = "string"
    elif low in _INT_TYPES:
        base = "integer"
    precision = scale = length = None
    raw = str(col.get("longitud") or "").strip()
    if raw:
        if "," in raw:
            a, b = raw.split(",", 1)
            precision = _to_int(a)
            scale = _to_int(b)
        elif base == "string":
            length = _to_int(raw)
        else:
            precision = _to_int(raw)
    evaluable = bool(tipo) and tipo != "—"
    return {"base": base, "precision": precision, "scale": scale, "length": length,
            "evaluable": evaluable, "tipo": tipo, "native": native}


def _to_int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _tokens(name: str) -> set[str]:
    """Tokeniza un nombre (camelCase + snake/no-alfanum) en palabras minúsculas. Evita
    el falso positivo por SUBSTRING (p. ej. 'Country'→'count', 'Corporate'→'rate')."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name or ""))
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    return {t.lower() for t in s.split() if t}


# Tokens que denotan un CÓDIGO/unidad (no una cantidad numérica): un "Weight unit" o un
# "Currency" es texto legítimo, no un numérico-como-String. Evita falsos positivos PB-003.
_CODE_TOKENS = {"unit", "uom", "einheit", "eh", "currency", "waers", "meins", "code",
                "indicator", "flag", "status", "type", "id", "key", "group", "category"}


def _semantics(col: dict, config: dict) -> str:
    """Semántica funcional inferida por nombre/label, matcheando keywords contra
    TOKENS/palabras completas (no substring), para no confundir 'account'≠'count'.
    Descarta códigos/unidades (no son cantidades numéricas)."""
    toks = _tokens(col.get("campo") or "") | _tokens(col.get("label") or "")
    if toks & _CODE_TOKENS:
        return ""
    for sem, kws in (config.get("semantica") or {}).items():
        if any(kw in toks for kw in (kws or [])):
            return sem
    return ""


# ── Matcher de condiciones ─────────────────────────────────────────────────────────
def _match_leaf(cond: dict, ctx: dict) -> bool:
    """Evalúa un dict de condiciones (AND de sus claves; `o` = OR de sub-condiciones)."""
    t, sem = ctx["t"], ctx["sem"]
    for key, val in cond.items():
        if key == "o":
            if not any(_match_leaf(sub, ctx) for sub in (val or [])):
                return False
        elif key == "semantica":
            if sem != val:
                return False
        elif key == "semantica_en":
            if sem not in (val or []):
                return False
        elif key == "tipo_base":
            if t["base"] != val:
                return False
        elif key == "tipo_o_native_in":
            opts = {str(x).lower() for x in (val or [])}
            if t["tipo"].lower() not in opts and t["native"].lower() not in opts:
                return False
        elif key == "precision_min":
            if t["precision"] is None or t["precision"] < val:
                return False
        elif key == "precision_max":
            if t["precision"] is None or t["precision"] > val:
                return False
        elif key == "precision_eq":
            if t["precision"] != val:
                return False
        elif key == "escala_min":
            if t["scale"] is None or t["scale"] < val:
                return False
        elif key == "escala_max":
            if t["scale"] is None or t["scale"] > val:
                return False
        elif key == "escala_eq":
            if t["scale"] != val:
                return False
        elif key == "longitud_min":
            if t["length"] is None or t["length"] < val:
                return False
        elif key == "longitud_max":
            if t["length"] is None or t["length"] > val:
                return False
        elif key == "derivado":
            if bool(ctx["col"].get("derived")) != bool(val):
                return False
        elif key == "sin_cast":
            derived = bool(ctx["col"].get("derived"))
            has_cast = bool(str(ctx["col"].get("cast") or "").strip())
            if val and not (derived and not has_cast):
                return False
        elif key == "pk_fk_tipo_distinto":
            if bool(val) != _pk_fk_mismatch(ctx["col"], ctx["pk_index"]):
                return False
        else:
            return False   # condición desconocida → no matchea (defensivo)
    return True


def _pk_fk_mismatch(col: dict, pk_index: dict) -> bool:
    """True si la columna es FK (assoc + assoc_col) y su tipo difiere del de la PK
    referenciada. Requiere la extensión que captura la columna target del `on`."""
    target_entity = str(col.get("assoc") or "").strip()
    target_col = str(col.get("assoc_col") or "").strip()
    if not target_entity or not target_col:
        return False
    pk = pk_index.get((target_entity.lower(), target_col.lower()))
    if not pk:
        return False
    a, b = _parse_type(col), _parse_type(pk)
    if not (a["evaluable"] and b["evaluable"]):
        return False
    return (a["base"], a["precision"], a["scale"], a["length"]) != \
           (b["base"], b["precision"], b["scale"], b["length"])


# ── Evaluación ─────────────────────────────────────────────────────────────────────
def _type_str(t: dict) -> str:
    if t["base"] == "string" and t["length"] is not None:
        return f"String({t['length']})"
    if t["precision"] is not None and t["scale"] is not None:
        return f"{t['tipo']}({t['precision']},{t['scale']})"
    if t["precision"] is not None:
        return f"{t['tipo']}({t['precision']})"
    return t["tipo"] or "—"


def evaluate_portability(columns: list[dict], asset_type: str, matrix: dict | None = None) -> dict:
    """Evalúa la portabilidad BDC de un conjunto de columnas (todas las de un dominio o
    data product). `asset_type` ∈ {'dominio','data_product'}. Cada `col` es un field-dict
    extendido: {artefacto, campo, label, tipo, longitud, native, key, medida, assoc,
    assoc_col, derived, cast}. Devuelve un resultado explicable (score, clasificación,
    semáforo, confianza, conteos, findings por campo)."""
    matrix = matrix or load_matrix(None)
    config = matrix["config"]
    reglas = [r for r in matrix["reglas"] if r.get("habilitada", True)]
    ctx_key = "data_product" if asset_type == "data_product" else "dominio"

    pk_index = {(str(c.get("artefacto") or "").lower(), str(c.get("campo") or "").lower()): c
                for c in columns if c.get("key")}

    findings: list[dict] = []
    revisados = no_evaluables = 0
    for col in columns:
        if col.get("medida") is None and not col.get("campo"):
            continue
        t = _parse_type(col)
        if not t["evaluable"]:
            no_evaluables += 1
            continue
        revisados += 1
        sem = _semantics(col, config)
        ctx = {"t": t, "sem": sem, "col": col, "pk_index": pk_index}
        for regla in reglas:
            beh = regla.get(ctx_key)
            if not beh:   # regla no aplica a este contexto
                continue
            if not _match_leaf(regla.get("cuando") or {}, ctx):
                continue
            findings.append(_finding(regla, beh, col, t, sem))

    score, clasif, semaforo = _score(findings, revisados, no_evaluables, config)
    confianza = _global_confidence(columns, revisados, no_evaluables)
    # Sin un solo campo evaluable → NO se asume compatibilidad: estado no_evaluable.
    if revisados == 0:
        clasif, semaforo, confianza = "no_evaluable", "amber", "baja"
    return {
        "asset_type": ctx_key,
        "score": score,
        "clasificacion": clasif,
        "semaforo": semaforo,
        "confianza": confianza,
        "campos_revisados": revisados,
        "campos_no_evaluables": no_evaluables,
        "n_riesgos": sum(1 for f in findings if f["tipo_hallazgo"] == "riesgo"),
        "n_adaptaciones": sum(1 for f in findings if f["tipo_hallazgo"] in ("adaptacion", "hint")),
        "n_buenas_practicas": sum(1 for f in findings if f["tipo_hallazgo"] == "buena_practica"),
        "findings": findings,
        "peso_global": config.get("peso_global", 0),
    }


def _finding_semaforo(tipo_h: str, severidad: str) -> str:
    """Semáforo del hallazgo. Un riesgo alto es rojo; medio naranja; nota ámbar (revisión).
    Adaptación/hint = azul (informativo, NO se ve como error); buena práctica = verde."""
    if tipo_h == "riesgo":
        return {"alto": "red", "medio": "orange", "nota": "amber"}.get(severidad, "orange")
    return SEMAFORO_HALLAZGO.get(tipo_h, "amber")


def _finding(regla: dict, beh: dict, col: dict, t: dict, sem: str) -> dict:
    tipo_h = beh.get("tipo_hallazgo", "riesgo")
    afecta = bool(beh.get("peso", 0)) and tipo_h == "riesgo"
    return {
        "regla": regla.get("id"),
        "titulo": regla.get("nombre") or regla.get("id"),
        "artefacto": col.get("artefacto") or "",
        "campo": col.get("campo") or "",
        "tipo_actual": _type_str(t),
        "semantica": sem,
        "tipo_hallazgo": tipo_h,
        "severidad": beh.get("severidad", "nota"),
        "confianza": regla.get("confianza", "media"),
        "control": beh.get("control", ""),
        "bloqueante": bool(beh.get("bloqueante")),
        "afecta_scoring": afecta,
        "peso": beh.get("peso", 0),
        "recomendacion": regla.get("recomendacion", ""),
        "capa_correccion": regla.get("capa_correccion", ""),
        "estado_validacion": regla.get("estado_validacion", ""),
        "semaforo": _finding_semaforo(tipo_h, beh.get("severidad", "nota")),
    }


def _score(findings: list[dict], revisados: int, no_evaluables: int, config: dict) -> tuple[int, str, str]:
    from collections import defaultdict
    base = config.get("score_base", 100)
    fsev = config.get("factor_severidad", {})
    cap_regla = config.get("penalizacion_max_por_regla", 20)
    por_regla: dict[str, float] = defaultdict(float)
    bonus = 0.0
    bloqueado = False
    n_bonus = 0
    for f in findings:
        if f.get("bloqueante") and f["tipo_hallazgo"] == "riesgo":
            bloqueado = True
        if f["afecta_scoring"]:
            conf = _CONF_FACTOR.get(f["confianza"], 0.7)
            por_regla[f["regla"]] += f["peso"] * fsev.get(f["severidad"], 0.5) * conf
        elif f["tipo_hallazgo"] in ("buena_practica", "adaptacion") and n_bonus < 6:
            bonus += config.get("bonus_normalizacion", 4)
            n_bonus += 1
    # Cap por regla → muchos hallazgos menores de la misma regla no hunden el score.
    penal = sum(min(p, cap_regla) for p in por_regla.values())
    bonus = min(bonus, config.get("bonus_max", 12))
    # Penalización ACOTADA por metadata faltante (nunca hunde el score sola).
    total = revisados + no_evaluables
    if total:
        gap = (no_evaluables / total) * config.get("penalizacion_metadata_max", 12)
    else:
        gap = config.get("penalizacion_metadata_max", 12)
    score = max(0, min(100, round(base - penal + bonus - gap)))
    if bloqueado:
        score = min(score, 49)   # una regla bloqueante impide "buena"/"plena"
    clasif, semaforo = _band(score, config)
    return score, clasif, semaforo


def _band(score: int, config: dict) -> tuple[str, str]:
    for banda in sorted(config.get("bandas", []), key=lambda b: -b.get("desde", 0)):
        if score >= banda.get("desde", 0):
            return banda.get("clasificacion", "condicionada"), banda.get("semaforo", "amber")
    return "no_recomendable", "red"


def _global_confidence(columns: list[dict], revisados: int, no_evaluables: int) -> str:
    total = revisados + no_evaluables
    if not total:
        return "baja"
    cobertura = revisados / total
    # La confianza también depende de si tenemos señales de derivación (lineage/expresión).
    tiene_derivacion = any("derived" in c for c in columns)
    if cobertura >= 0.9 and tiene_derivacion:
        return "alta"
    if cobertura >= 0.7:
        return "media"
    return "baja"
