"""portability_bdc.py — Evaluador de Portabilidad SAP BDC / cross-runtime.

Dimensión de evaluación SEPARADA de la calidad de extracción (evaluate_compliance).
Versión del scoring: `portability-scoring/v2` — mide la CONSISTENCIA ESTRUCTURAL de los tipos del dominio, independiente del destino; lo que depende del mecanismo/destino
vive en `compat_bdc.py` (`compatibilidad_bdc/v1`). Las heurísticas por nombre no penalizan sin evidencia estructural.
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
import unicodedata
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
    "version_scoring": "portability-scoring/v2",
    # Semántica en TEXTO: solo palabras de magnitud con precisión previa >= 0.4 en la muestra etiquetada; en texto nunca se infiere «monetario» por nombre.
    "semantica_texto": {"porcentaje_ratio": ["percent", "porcentaje", "cobertura", "tasa"], "cantidad": ["cantidad"]},
    # Tokens que describen una unidad/código/fecha/indicador/entidad (no una magnitud): en texto descartan la semántica.
    "no_magnitud": ["unit", "uom", "einheit", "eh", "unidad", "unidades", "currency", "moneda", "waers", "meins", "mon", "code", "codigo", "indicator", "indicador",
                    "flag", "status", "estado", "type", "tipo", "id", "key", "clave", "group", "grupo", "category", "categoria", "date", "fecha", "datum", "dat",
                    "day", "days", "dia", "dias", "year", "ano", "anos", "month", "mes", "time", "hora", "validity", "validez", "vigencia", "vigente", "periodo",
                    "center", "centro", "object", "objeto", "profile", "perfil", "strategy", "estrategia", "name", "nombre", "description", "descripcion", "user",
                    "usuario", "person", "persona", "template", "resource", "recurso", "material", "reason", "motivo", "rule", "regla", "method", "metodo", "step",
                    "paso", "counter", "contador", "area", "function", "funcion", "jurisdiction", "country", "pais", "segment", "segmento", "color", "size",
                    "caracteristica", "determinante", "is", "has", "estimate", "partner", "origin", "controlling", "printer", "hierarchy", "jerarquia", "block",
                    "bloqueo", "variant", "variante", "email", "mail", "conversion", "quotation"],
    # En tipos NUMÉRICOS solo estos códigos blanquean la semántica (un importe «en moneda X» ES un importe).
    "no_magnitud_numerico": ["unit", "uom", "einheit", "eh", "unidad", "unidades", "code", "codigo", "indicator", "indicador", "flag", "status", "estado", "id", "key"],
    # Escalas altas que son legítimas por naturaleza (coordenadas, tipos de cambio): no generan indicio de escala elevada.
    "escala_legitima": ["longitude", "latitude", "latitud", "exchange", "cambio", "fx", "coordinate", "coordenada"],
}

_SEED_REGLAS: list[dict[str, Any]] = [
    {"id": "PB-001", "nombre": "DecimalFloat en campo monetario", "habilitada": True, "depende_de_nombre": True,
     "cuando": {"semantica": "monetario",
                "tipo_o_native_in": ["DecimalFloat", "DECFLOAT", "DECIMAL_FLOAT", "DECFLOAT16", "DECFLOAT34"]},
     # SIN `dominio`: la compatibilidad de DecimalFloat depende del mecanismo/destino (compatibilidad_bdc/v1). Solo safety check en data product (destino conocido).
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 14, "control": "safety_check"},
     "recomendacion": "Normalizar a DECIMAL(38,6) en la capa contractual, antes de la publicación.",
     "capa_correccion": "Capa contractual (antes del artefacto físico destinado a HDLF).",
     "confianza": "alta", "estado_validacion": "validado", "incidentes": ["caso-1-decimalfloat"]},
    {"id": "PB-002", "nombre": "Escala/precisión inferida elevada en derivado (ratio/porcentaje)",
     "habilitada": True,
     "cuando": {"tipo_base": "decimal", "escala_min": 10, "derivado": True, "sin_cast": True},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 20},
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 12,
                      "control": "safety_check"},
     "recomendacion": "Tipificar el campo con CAST en la misma expresión y capa donde nace (p. ej. DECIMAL(15,6)).",
     "capa_correccion": "En la expresión que origina el campo (flujo Datasphere previo a la publicación).",
     "confianza": "alta", "estado_validacion": "validado", "incidentes": ["caso-2-decimal-37-19"]},
    {"id": "PB-002H", "nombre": "Escala/precisión elevada declarada (informativo)", "habilitada": True, "depende_de_nombre": True,
     "cuando": {"tipo_base": "decimal", "escala_min": 10, "derivado": False, "semantica_en": ["monetario", "porcentaje_ratio", "cantidad"], "sin_tokens_escala_legitima": True},
     "dominio": {"tipo_hallazgo": "hint", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Escala elevada declarada en un importe, cantidad o ratio: confirmar que el tipo es intencional antes de publicarlo. No es una incompatibilidad confirmada.",
     "capa_correccion": "Capa donde se declara el tipo.",
     "confianza": "media", "estado_validacion": "recomendado"},
    {"id": "PB-003", "nombre": "Posible magnitud numérica representada como String", "habilitada": True, "depende_de_nombre": True,
     "cuando": {"tipo_base": "string", "semantica_en": ["porcentaje_ratio", "cantidad"], "longitud_min": 2},
     "dominio": {"tipo_hallazgo": "hint", "severidad": "nota", "peso": 0, "control": "workaround"},
     "data_product": {"tipo_hallazgo": "hint", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Indicio por nombre: verificar si el String representa una magnitud numérica (porcentaje o cantidad) y, si es así, preferir el tipo numérico contractual.",
     "capa_correccion": "Capa contractual o expresión de origen.",
     "confianza": "media", "estado_validacion": "recomendado"},
    {"id": "PB-004", "nombre": "Campo derivado sin cast contractual explícito", "habilitada": True, "depende_de_nombre": True,
     "cuando": {"derivado": True, "sin_cast": True, "tipo_base": "decimal",
                "o": [{"semantica_en": ["monetario", "porcentaje_ratio"]}, {"escala_min": 10}]},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 8},
     "data_product": {"tipo_hallazgo": "hint", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Fijar el tipo con CAST explícito en la misma capa/expresión donde nace el campo.",
     "capa_correccion": "Expresión de origen del campo derivado.",
     "confianza": "media", "estado_validacion": "recomendado"},
    {"id": "PB-005", "nombre": "Tipo base distinto entre foreign key y su primary key relacionada",
     "habilitada": True,
     "cuando": {"pk_fk_base_distinta": True},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 18, "bloqueante": True},
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "alto", "peso": 18,
                      "control": "safety_check", "bloqueante": True},
     "recomendacion": "Alinear el tipo de la FK con el de su PK en la capa contractual.",
     "capa_correccion": "Capa contractual (definición de la relación).",
     "confianza": "alta", "estado_validacion": "recomendado"},
    {"id": "PB-005B", "nombre": "FK más ancha que su PK (alineación potencialmente destructiva)", "habilitada": True,
     "cuando": {"pk_fk_reduccion": True},
     "dominio": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 8},
     "data_product": {"tipo_hallazgo": "riesgo", "severidad": "medio", "peso": 8, "control": "safety_check"},
     "recomendacion": "Si se alinea la FK al largo/precisión de la PK, validar que ningún valor existente la supere. Con solo metadata no se puede afirmar truncamiento ni pérdida.",
     "capa_correccion": "Capa contractual (definición de la relación).",
     "confianza": "media", "estado_validacion": "recomendado"},
    {"id": "PB-010", "nombre": "Monetario normalizado a DECIMAL(38,6)", "habilitada": True, "depende_de_nombre": True,
     "cuando": {"semantica": "monetario", "tipo_base": "decimal", "precision_eq": 38, "escala_eq": 6},
     "dominio": {"tipo_hallazgo": "buena_practica", "severidad": "nota", "peso": 0},
     "data_product": {"tipo_hallazgo": "adaptacion", "severidad": "nota", "peso": 0, "control": "workaround"},
     "recomendacion": "Conservar el tipo y documentar la decisión antes de reutilizar el contrato.",
     "capa_correccion": "Ya normalizado en la capa contractual.",
     "confianza": "media", "estado_validacion": "validado", "incidentes": ["caso-1-decimalfloat"]},
    {"id": "PB-011", "nombre": "Ratio/porcentaje normalizado a DECIMAL(15,6)", "habilitada": True, "depende_de_nombre": True,
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
    """Tokeniza un nombre (camelCase + snake/no-alfanum) en palabras minúsculas, SIN acentos y con plural plegado (`dias`→`dia`). Evita el falso positivo por
    SUBSTRING (p. ej. 'Country'→'count', 'Corporate'→'rate')."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    toks = {t.lower() for t in s.split() if t}
    return toks | {t[:-1] for t in toks if len(t) > 3 and t.endswith("s")}


# Compatibilidad v1 (solo referencia histórica; el comportamiento vigente lee `config.no_magnitud*`).
_CODE_TOKENS = {"unit", "uom", "einheit", "eh", "currency", "waers", "meins", "code",
                "indicator", "flag", "status", "type", "id", "key", "group", "category"}


def _semantics(col: dict, config: dict) -> str:
    """Semántica funcional inferida por nombre/label, TIPO-CONSCIENTE (v2).
    · Tipos numéricos: un importe «en moneda X» ES un importe; solo unidades/códigos/indicadores blanquean la semántica.
    · Texto: se descarta si algún token describe una unidad/código/fecha/indicador/entidad (`no_magnitud`) y solo se reconocen las palabras de magnitud
      de `semantica_texto` (nunca «monetario» por nombre: una cantidad de dinero guardada como texto requiere evidencia estructural, no un nombre)."""
    t = _parse_type(col)
    toks = _tokens(col.get("campo") or "") | _tokens(col.get("label") or "")
    if t["base"] in ("decimal", "integer"):
        if toks & set(config.get("no_magnitud_numerico") or []):
            return ""
        for sem, kws in (config.get("semantica") or {}).items():
            if any(kw in toks for kw in (kws or [])):
                return sem
        return ""
    if toks & set(config.get("no_magnitud") or []):
        return ""
    for sem, kws in (config.get("semantica_texto") or {}).items():
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
        elif key == "pk_fk_base_distinta":                                # v2: la FK y su PK difieren en la BASE del tipo (p. ej. Integer64 vs String)
            if bool(val) != (_pk_fk_class(ctx["col"], ctx["pk_index"]) == "base_distinta"):
                return False
        elif key == "pk_fk_reduccion":                                     # v2: FK más ancha que su PK (alinearla a la PK sería potencialmente destructivo)
            if bool(val) != (_pk_fk_class(ctx["col"], ctx["pk_index"]) == "reduccion"):
                return False
        elif key == "sin_tokens_escala_legitima":                          # v2: coordenadas/tipos de cambio tienen escalas altas legítimas
            toks = _tokens(ctx["col"].get("campo") or "") | _tokens(ctx["col"].get("label") or "")
            if bool(val) and toks & set((ctx.get("config") or {}).get("escala_legitima") or []):
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


def _pk_fk_class(col: dict, pk_index: dict) -> str | None:
    """Relación de formas entre una FK y su PK (misma base): 'igual' | 'ampliacion' (FK cabe en la PK) | 'reduccion' (FK más ancha: alinearla sería potencialmente
    destructivo) | 'base_distinta'. None si no es FK resoluble o falta metadata. Solo metadata: nunca afirma truncamiento real."""
    target_entity = str(col.get("assoc") or "").strip()
    target_col = str(col.get("assoc_col") or "").strip()
    if not target_entity or not target_col:
        return None
    pk = pk_index.get((target_entity.lower(), target_col.lower()))
    if not pk:
        return None
    a, b = _parse_type(col), _parse_type(pk)
    if not (a["evaluable"] and b["evaluable"]):
        return None
    if a["base"] != b["base"]:
        return "base_distinta"
    if a["base"] == "string" and a["length"] is not None and b["length"] is not None:
        return "igual" if a["length"] == b["length"] else ("reduccion" if a["length"] > b["length"] else "ampliacion")
    if a["base"] == "decimal" and None not in (a["precision"], a["scale"], b["precision"], b["scale"]):
        if (a["precision"], a["scale"]) == (b["precision"], b["scale"]):
            return "igual"
        return "reduccion" if (a["scale"] > b["scale"] or a["precision"] - a["scale"] > b["precision"] - b["scale"]) else "ampliacion"
    return None


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
        ctx = {"t": t, "sem": sem, "col": col, "pk_index": pk_index, "config": config}
        for regla in reglas:
            beh = regla.get(ctx_key)
            if not beh:   # regla no aplica a este contexto
                continue
            if not _match_leaf(regla.get("cuando") or {}, ctx):
                continue
            findings.append(_finding(regla, beh, col, t, sem))

    score, clasif, semaforo = _score(findings, revisados, no_evaluables, config)
    evidencia = _evidence(columns, pk_index)
    confianza, conf_detalle = _confidence(findings, revisados, no_evaluables, score, reglas, config, evidencia)
    # Sin un solo campo evaluable → NO se asume compatibilidad: estado no_evaluable.
    if revisados == 0:
        clasif, semaforo, confianza = "no_evaluable", "amber", "baja"
    return {
        "version": config.get("version_scoring", "portability-scoring/v2"),
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
        # KPI por CAMPOS DISTINTOS del alcance evaluado (las ocurrencias por artefacto siguen en `findings` y en los `n_*` de arriba).
        "campos_distintos": _distinct_fields(findings),
        "evidencia": evidencia,
        "confianza_detalle": conf_detalle,
        "clase_evidencia": _evidence_class(findings, evidencia),
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


def _distinct_fields(findings: list[dict]) -> dict:
    """Campos DISTINTOS (por nombre de campo) por familia de hallazgo; el detalle por artefacto/ocurrencia se conserva en `findings`."""
    fam = {"riesgos": {"riesgo"}, "indicios": {"hint"}, "normalizaciones": {"adaptacion", "buena_practica"}}
    return {k: len({f["campo"] for f in findings if f["tipo_hallazgo"] in tipos}) for k, tipos in fam.items()}


def _evidence(columns: list[dict], pk_index: dict) -> dict:
    """Evidencia estructural que las reglas pudieron EJERCER: derivadas numéricas (PB-002/004) y relaciones FK resueltas (PB-005/005B).
    No es cobertura de metadata ni compatibilidad con un destino: mide si las reglas estructurales tuvieron algo que evaluar."""
    derivadas = sum(1 for c in columns if c.get("derived") and _parse_type(c)["base"] in ("decimal", "integer"))
    fk = sum(1 for c in columns if str(c.get("assoc") or "").strip() and str(c.get("assoc_col") or "").strip()
             and (str(c["assoc"]).lower(), str(c["assoc_col"]).lower()) in pk_index)
    return {"derivadas_numericas": derivadas, "relaciones_fk_resueltas": fk, "ejercida": bool(derivadas or fk)}


def _evidence_class(findings: list[dict], evidencia: dict) -> str:
    """Clasificación INTERNA (no visible) para interpretar la suficiencia de evidencia."""
    if any(f["afecta_scoring"] for f in findings):
        return "con_penalizacion"
    if not evidencia["ejercida"]:
        return "no_ejercida"
    return "ejercida_con_indicios" if findings else "ejercida_sin_hallazgos"


def _confidence(findings: list[dict], revisados: int, no_evaluables: int, score: int, reglas: list[dict], config: dict, evidencia: dict) -> tuple[str, dict]:
    """Confianza de la EVALUACIÓN (no de la compatibilidad con un destino). Criterio calibrado:
      · baja  — cobertura de metadata < 0.7;
      · media — cobertura < 0.9, o la banda del score cambia al retirar los hallazgos que dependen de nombres (la evaluación es sensible a heurísticas),
                o las reglas estructurales no tuvieron evidencia que ejercer (derivadas numéricas / relaciones FK);
      · alta  — el resto: cobertura completa, banda estable sin heurísticas y evidencia estructural ejercida.
    La ausencia de riesgos NO equivale a evidencia suficiente."""
    total = revisados + no_evaluables
    cobertura = (revisados / total) if total else 0.0
    por_nombre = {r.get("id") for r in reglas if r.get("depende_de_nombre")}
    sin_nombre = _score([f for f in findings if f["regla"] not in por_nombre], revisados, no_evaluables, config)[0]
    sensible = _band(score, config)[0] != _band(sin_nombre, config)[0]
    detalle = {"cobertura": round(cobertura, 3), "sensible_a_heuristicas": sensible, "evidencia_ejercida": evidencia["ejercida"],
               "derivadas_numericas": evidencia["derivadas_numericas"], "relaciones_fk_resueltas": evidencia["relaciones_fk_resueltas"]}
    if not total or cobertura < 0.7:
        return "baja", detalle
    if cobertura < 0.9 or sensible or not evidencia["ejercida"]:
        return "media", detalle
    return "alta", detalle
