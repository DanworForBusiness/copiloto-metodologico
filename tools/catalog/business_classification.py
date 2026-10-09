"""Clasificación de negocio v2 — LÍNEA DE NEGOCIO + DOMINIO desde el linaje canónico (determinista, explicable, con abstención).

EXTIENDE ``domain_classification`` (v1: misma tokenización, el mismo motor de dominio) y sigue siendo HERMANA del Juicio metodológico: no importa ni lee el
Juicio, el perfil del Assessment, la evidencia, la confianza ni los hallazgos. Función PURA: sin red, sin LLM, sin embeddings, sin disco, sin reloj.

Dos preguntas jerárquicas:   Línea de negocio (taxonomía GOBERNADA y finita, IDs estables)  →  Dominio (inferido; el motor v1).

Taxonomía (``LINES_OF_BUSINESS``): CINCO líneas de negocio, orientadas al negocio y NO al vocabulario SAP (IDs estables, en inglés neutro; etiquetas en español):
FINANCE · SALES · SUPPLY_CHAIN · MANUFACTURING · HUMAN_RESOURCES. Los prefijos de nombre de la convención del módulo 04 (``content_packs/<pack>/trees/04_convencion_de_nombres.yaml
› lob_prefixes``) son solo EVIDENCIA hacia una línea —FNC→FINANCE, SND→SALES, IVT/PUR→SUPPLY_CHAIN (inventarios / compras), MNF→MANUFACTURING, RRHH→HUMAN_RESOURCES—, no definen la
taxonomía. «Datos maestros» (MD) NO es una línea de negocio (es una característica transversal de los datos): no genera señal en v2. El clasificador NUNCA inventa una línea
desde nombres de modelos; se extiende agregando entradas (IDs históricos intactos). El dominio (nivel 2) sigue siendo el motor v1, determinista y explicable.

Familias de evidencia independientes (cada una vota por UNA línea de negocio o se abstiene):
  ROOT     el objeto raíz: código LoB en su nombre técnico (señal fuerte, gobernada), vocabulario funcional de su nombre/etiqueta, objetos SAP en el nombre.
  LINEAGE  el cierre canónico: código LoB / vocabulario funcional / objetos SAP repetidos en ≥2 artefactos distintos de la raíz y (≥2 señales distintas o un código LoB gobernado); no cuentan los
           artefactos descriptivos (dimensiones, textos, jerarquías, datos maestros) ni los objetos de artefactos con origen remoto (los cuenta SOURCE).
  SOURCE   identidades de las hojas fuente (entidades remotas): ≥2 objetos SAP distintos de la misma línea de negocio.
  (El dominio actual del cliente es solo un PRIOR contextual: se informa su alineación, jamás fuerza ni bloquea el resultado. Los campos NO se usan en v2.)

Reglas (sin pesos arbitrarios):
  · Dentro de una familia gana la línea con MAYOR masa estricta; un empate la hace ABSTENERSE (no se desempata).
  · HIGH   ≥2 familias coinciden en la misma línea Y hay ≥2 señales distintas o un código LoB gobernado (una sola palabra ruidosa nunca alcanza), y ninguna otra línea tiene el mismo respaldo.
  · MEDIUM un candidato útil con una sola familia (o una sola señal), sin conflicto.
  · LOW / UNDETERMINED  evidencia ausente, genérica o en conflicto (dos líneas con el mismo respaldo → CROSS_LINE_CONFLICT; jamás se resuelve al azar).
Estado: CLASSIFIED (HIGH, automática) · SUGGESTED (MEDIUM, solo sugerencia) · UNDETERMINED. La asignación MANUAL y la agrupación EFECTIVA se resuelven aparte
(``effective_classification``): manual > HIGH automática > sin clasificar; una MEDIUM nunca mueve un activo.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from tools.catalog import domain_classification as dc

BUSINESS_CLASSIFICATION_VERSION = "business-classification/v2"
TAXONOMY_VERSION = "lob-taxonomy/v1"
LEXICON_VERSION = "lob-lexicon/v1"

CLASSIFIED, SUGGESTED, UNDETERMINED = "CLASSIFIED", "SUGGESTED", "UNDETERMINED"
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
ROOT, LINEAGE, SOURCE = "ROOT", "LINEAGE", "SOURCE"

# Códigos de motivo estables.
R_ROOT_AND_LINEAGE = "ROOT_AND_LINEAGE_AGREE"
R_SOURCE_AND_LINEAGE = "SOURCE_AND_LINEAGE_AGREE"
R_ROOT_AND_SOURCE = "ROOT_AND_SOURCE_AGREE"
R_SINGLE_FAMILY = "SINGLE_FAMILY_EVIDENCE"
R_SINGLE_SIGNAL = "SINGLE_SIGNAL_ONLY"
R_CONFLICT = "CROSS_LINE_CONFLICT"
R_INSUFFICIENT = "INSUFFICIENT_BUSINESS_EVIDENCE"

# Taxonomía GOBERNADA v1 (IDs estables, neutros; `codes` = prefijos de nombre que son EVIDENCIA hacia la línea, no su definición). Orden estable.
LINES_OF_BUSINESS: tuple[dict, ...] = (
    {"id": "FINANCE", "label": "Finanzas", "codes": ("FNC",)},
    {"id": "SALES", "label": "Ventas", "codes": ("SND",)},
    {"id": "SUPPLY_CHAIN", "label": "Cadena de suministro", "codes": ("IVT", "PUR")},
    {"id": "MANUFACTURING", "label": "Manufactura", "codes": ("MNF",)},
    {"id": "HUMAN_RESOURCES", "label": "Recursos Humanos", "codes": ("RRHH",)},
)
LOB_BY_ID = {x["id"]: x for x in LINES_OF_BUSINESS}
LOB_BY_CODE = {c: x["id"] for x in LINES_OF_BUSINESS for c in x["codes"]}

# Léxico funcional (palabras de negocio, ES/EN). Se normaliza con la MISMA tokenización de v1. Solo términos con significado de negocio inequívoco.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "SALES": ("pedido", "entrega", "venta", "factura", "facturacion", "remito", "despacho", "cotizacion", "devolucion", "descuento", "precio",
              "sales", "delivery", "billing", "invoice", "sellout", "sell", "price"),
    "FINANCE": ("ledger", "journal", "accounting", "contable", "contabilidad", "asiento", "gasto", "expense", "banco", "bank", "tesoreria", "cobranza",
                "payment", "pago", "cobro", "impuesto", "tax", "presupuesto", "budget", "finanzas", "finance"),
    "SUPPLY_CHAIN": ("stock", "inventario", "inventory", "almacen", "warehouse", "lote", "batch",                       # inventarios
                     "compra", "purchase", "procurement", "proveedor", "supplier", "requisicion"),                        # compras
    "MANUFACTURING": ("produccion", "manufactura", "manufacturing", "production", "fabricacion", "bom"),
    "HUMAN_RESOURCES": ("empleado", "employee", "nomina", "payroll", "rrhh", "workforce", "headcount", "vacante"),
}
# Objetos SAP semánticos conocidos (tablas / entidades) → línea de negocio. Identidad de OBJETO, no módulo técnico: solo objetos cuyo significado de negocio es inequívoco.
# (Los objetos puramente maestros / de estructura organizativa —KNA1, MARA, LFA1, T001, T001L, T001W…— no votan: «datos maestros» no es una línea de negocio.)
_OBJECTS: dict[str, tuple[str, ...]] = {
    "SALES": ("VBAK", "VBAP", "VBEP", "VBFA", "VBKD", "VBPA", "VBRK", "VBRP", "LIKP", "LIPS", "TVAK", "TVAP", "TVKO", "VBUK", "VBUP", "KONV", "KONP"),
    "FINANCE": ("BKPF", "BSEG", "BSIK", "BSAK", "BSID", "BSAD", "ACDOCA", "ACDOCT", "FAGLFLEXA", "SKA1", "SKAT", "SKB1", "CSKS", "CSKT", "COEP", "COSP", "COSS", "ANLA", "ANLC"),
    "SUPPLY_CHAIN": ("MARD", "MCHB", "MCH1", "MSEG", "MKPF", "MBEW", "MARC", "MSKA", "MKOL", "MLGT", "LAGP", "LQUA",       # inventarios
                     "EKKO", "EKPO", "EKET", "EKBE", "EBAN", "EINA", "EINE", "EORD"),                                         # compras
    "MANUFACTURING": ("AFKO", "AFPO", "AUFK", "RESB", "PLPO", "MAPL", "CRHD", "STKO", "STPO"),
    "HUMAN_RESOURCES": ("PA0001", "PA0002", "PA0008", "PA0105", "T500P", "HRP1000"),
}
_KEYWORD_LOB = {dc.normalize_word(w): lob for lob, ws in _KEYWORDS.items() for w in ws}
_OBJECT_LOB = {o: lob for lob, os_ in _OBJECTS.items() for o in os_}
_RAW_SPLIT = re.compile(r"[^0-9A-Za-z]+")
# Artefactos DESCRIPTIVOS / maestros: por el uso semántico de la convención de nombres (D = Dimensión, T = Texto, H = Jerarquía; ``2VD_``, ``2VT_``, ``3TH_``) o por marcadores
# de dimensión / dato maestro / atributos en su nombre o etiqueta. Describen entidades (clientes, materiales, zonas), no la transacción: su vocabulario NO vota en el linaje.
_USE_LETTER_DESCRIPTIVE = re.compile(r"^[1-4][TV][DTH](?:_|$)")
_DESCRIPTIVE_MARKERS = {dc.normalize_word(w) for w in ("dim", "dimension", "maestro", "master", "attr", "hier", "jerarquia", "hierarchy", "texto", "text")}
_MAX_EVIDENCE = 8
_MAX_ARTIFACTS = 6


def lob_label(lob_id: str | None) -> str | None:
    return LOB_BY_ID[lob_id]["label"] if lob_id in LOB_BY_ID else None


def _signals(text: str) -> dict[str, set[str]]:
    """Señales de línea de negocio de UN texto (nombre técnico / etiqueta / entidad): {lob: {clave}} con claves `code:X` · `kw:x` · `obj:X`."""
    out: dict[str, set[str]] = {}
    for tok in _RAW_SPLIT.split(text or ""):
        up = tok.upper()
        if up in LOB_BY_CODE:
            out.setdefault(LOB_BY_CODE[up], set()).add(f"code:{up}")
        if up in _OBJECT_LOB:
            out.setdefault(_OBJECT_LOB[up], set()).add(f"obj:{up}")
    for stem, _spelling in dc.tokenize(text or ""):
        lob = _KEYWORD_LOB.get(stem)
        if lob:
            out.setdefault(lob, set()).add(f"kw:{stem}")
    return out


def _merge(into: dict[str, set[str]], other: dict[str, set[str]]) -> None:
    for lob, keys in other.items():
        into.setdefault(lob, set()).update(keys)


def _vote(mass: dict[str, int]) -> tuple[str | None, list[str]]:
    """(línea ganadora de la familia, líneas empatadas). Empate → la familia se abstiene (no se desempata) y se informan las empatadas."""
    pool = {k: v for k, v in mass.items() if v > 0}
    if not pool:
        return None, []
    top = max(pool.values())
    winners = sorted(k for k, v in pool.items() if v == top)
    return (winners[0], []) if len(winners) == 1 else (None, winners)


def _is_descriptive(name: str, label: str) -> bool:
    if _USE_LETTER_DESCRIPTIVE.match(name or ""):
        return True
    toks = {t for t, _s in dc.tokenize(f"{name} {label}")}
    return bool(toks & _DESCRIPTIVE_MARKERS)


def _signature(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def classify_business(*, current_domain: str | None, root: dict, closure: Iterable[dict], client_domains: Iterable[str] = (), client: str = "") -> dict:
    """``root`` = {name, label?}; ``closure`` = [{name, label?, source?}] (``source`` = entidad remota de origen si la hay); ver el docstring del módulo."""
    root_name = str(root.get("name") or "")
    arts = [{"name": str(a.get("name") or ""), "label": str(a.get("label") or ""), "source": str(a.get("source") or "")} for a in closure if a.get("name")]
    others = [a for a in arts if a["name"] != root_name]
    domain_result = dc.classify_domain(current_domain=current_domain, root=root, closure=arts, client_domains=client_domains, client=client)

    # ── ROOT
    root_sig: dict[str, set[str]] = {}
    _merge(root_sig, _signals(root_name))
    _merge(root_sig, _signals(str(root.get("label") or "")))
    root_mass = {lob: 10 * sum(1 for k in keys if k.startswith("code:")) + len(keys) for lob, keys in root_sig.items()}

    # ── LINEAGE: artefactos del cierre (≠ raíz) con señal de cada línea
    holders: dict[str, dict[str, set[str]]] = {}                      # lob → {clave → {artefactos}}
    per_lob_art: dict[str, set[str]] = {}
    for a in others:
        sig: dict[str, set[str]] = {}
        _merge(sig, _signals(a["name"]))
        _merge(sig, _signals(a["label"]))
        if _is_descriptive(a["name"], a["label"]):                     # dimensiones / textos / jerarquías / maestros: no describen la transacción
            continue
        if a["source"]:                                                # el objeto SAP de un artefacto con origen remoto ya lo cuenta la familia SOURCE: no se duplica como linaje
            sig = {lob: {k for k in keys if not k.startswith("obj:")} for lob, keys in sig.items()}
        for lob, keys in sig.items():
            if not keys:
                continue
            per_lob_art.setdefault(lob, set()).add(a["name"])
            for k in keys:
                holders.setdefault(lob, {}).setdefault(k, set()).add(a["name"])
    # soporte de linaje: ≥2 artefactos Y ≥2 señales distintas (una palabra repetida en artefactos auxiliares no es corroboración)
    lineage_mass = {lob: len(names) for lob, names in per_lob_art.items()
                    if len(names) >= 2 and (len(holders.get(lob, {})) >= 2 or any(k.startswith("code:") for k in holders.get(lob, {})))}

    # ── SOURCE: objetos SAP distintos por línea en las entidades remotas
    src_objs: dict[str, dict[str, set[str]]] = {}                     # lob → {objeto → {artefactos}}
    for a in others + [x for x in arts if x["name"] == root_name]:
        if not a["source"]:
            continue
        for lob, keys in _signals(a["source"]).items():
            for k in keys:
                if k.startswith("obj:"):
                    src_objs.setdefault(lob, {}).setdefault(k, set()).add(a["name"])
    source_mass = {lob: len(objs) for lob, objs in src_objs.items() if len(objs) >= 2}

    votes: dict[str, set[str]] = {}
    abstained: list[str] = []
    tied_lobs: set[str] = set()
    for family, mass in ((ROOT, root_mass), (LINEAGE, lineage_mass), (SOURCE, source_mass)):
        winner, tie = _vote(mass)
        if winner:
            votes.setdefault(winner, set()).add(family)
        elif tie:
            abstained.append(family)
            tied_lobs.update(tie)

    def _keys(lob: str) -> set[str]:
        ks = set(root_sig.get(lob, ())) if ROOT in votes.get(lob, ()) else set()
        if LINEAGE in votes.get(lob, ()):
            ks |= set(holders.get(lob, {}))
        if SOURCE in votes.get(lob, ()):
            ks |= set(src_objs.get(lob, {}))
        return ks

    result = {"contract_version": BUSINESS_CLASSIFICATION_VERSION, "taxonomy_version": TAXONOMY_VERSION, "lexicon_version": LEXICON_VERSION,
              "line_of_business": None, "domain": None, "confidence": LOW, "status": UNDETERMINED, "reason_code": R_INSUFFICIENT,
              "evidence": [], "alignment": None, "competing": [],
              "domain_classification": {k: domain_result[k] for k in ("version", "classification_status", "current_domain", "suggested_domain", "suggestion_type", "confidence")}}
    result["input_signature"] = _signature({"v": BUSINESS_CLASSIFICATION_VERSION, "tax": TAXONOMY_VERSION, "lex": LEXICON_VERSION, "dom": domain_result["input_signature"],
                                            "src": sorted((a["name"], a["source"]) for a in arts if a["source"])})

    # dominio (nivel 2): el motor v1 (actual si está respaldado, sugerido si hay evidencia coherente)
    if domain_result["classification_status"] == dc.ALIGNED and domain_result["current_domain"] and not _technical_label(domain_result["current_domain"]):
        result["domain"] = {"id": _slug(domain_result["current_domain"]), "label": domain_result["current_domain"], "origin": "CURRENT"}
    elif domain_result["classification_status"] == dc.SUGGEST and domain_result["suggested_domain"]:
        result["domain"] = {"id": _slug(domain_result["suggested_domain"]), "label": domain_result["suggested_domain"], "origin": "SUGGESTED"}

    if not votes:
        result["reason_code"] = R_CONFLICT if abstained else R_INSUFFICIENT
        result["competing"] = [{"id": l, "label": lob_label(l)} for l in sorted(tied_lobs)]
        return result
    ranked = sorted(votes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    top_lob, top_fams = ranked[0]
    tied = [lob for lob, fams in ranked if len(fams) == len(top_fams)]
    if len(tied) > 1:                                                  # dos líneas con el mismo respaldo: jamás se desempata
        result.update(reason_code=R_CONFLICT, competing=[{"id": l, "label": lob_label(l)} for l in tied])
        return result
    result["competing"] = [{"id": l, "label": lob_label(l)} for l, _ in ranked[1:]]
    keys = _keys(top_lob)
    result["line_of_business"] = {"id": top_lob, "label": lob_label(top_lob)}
    families = sorted(top_fams, key=(ROOT, LINEAGE, SOURCE).index)
    strong = any(k.startswith("code:") for k in keys)                   # un código LoB gobernado corroborado por ≥2 familias no es una palabra ruidosa
    if len(families) >= 2 and (len(keys) >= 2 or strong):               # ≥2 familias coincidentes Y (≥2 señales distintas o un código gobernado): una sola palabra ruidosa no alcanza
        fam = set(families)
        reason = R_ROOT_AND_LINEAGE if {ROOT, LINEAGE} <= fam else (R_SOURCE_AND_LINEAGE if {SOURCE, LINEAGE} <= fam else R_ROOT_AND_SOURCE)
        result.update(confidence=HIGH, status=CLASSIFIED, reason_code=reason)
    else:                                                              # un candidato útil sin corroboración independiente suficiente: solo sugerencia
        result.update(confidence=MEDIUM, status=SUGGESTED, reason_code=R_SINGLE_FAMILY if len(families) == 1 else R_SINGLE_SIGNAL)
    result["evidence"] = _evidence(top_lob, families, root_sig, root_name, holders, src_objs)

    cur = (current_domain or "").strip()                               # prior contextual: alineación, nunca decisión
    if cur and not dc.is_generic_domain(cur):
        prior = {}
        _merge(prior, _signals(cur))
        prior_lob, _t = _vote({l: len(k) for l, k in prior.items()})
        result["alignment"] = ("AGREES" if prior_lob == top_lob else "DISAGREES") if prior_lob else None
    return result


def _technical_label(label: str) -> bool:
    """¿La etiqueta es un identificador técnico (``AM_Lista_Precios``, ``4AM_X``) y no un dominio de negocio? Evita micro-dominios que solo repiten el nombre del modelo."""
    t = label.strip()
    return bool(re.fullmatch(r"[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+", t)) or bool(re.fullmatch(r"\d?[A-Z]{2,3}_\S+", t))


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", dc._fold(label).lower()).strip("-")


def _evidence(lob: str, families: list[str], root_sig, root_name, holders, src_objs) -> list[dict]:
    """Las razones más fuertes de la línea ganadora, por familia (códigos gobernados primero, luego objetos y vocabulario). Sin puntajes crudos."""
    def _label(key: str) -> str:
        kind, val = key.split(":", 1)
        return {"code": f"Código de línea de negocio {val}", "obj": f"Objeto SAP {val}", "kw": f"Vocabulario funcional «{val}»"}[kind]

    order = {"code": 0, "obj": 1, "kw": 2}
    out: list[dict] = []
    if ROOT in families:
        for k in sorted(root_sig.get(lob, ()), key=lambda k: (order[k.split(":")[0]], k)):
            out.append({"family": ROOT, "signal": _label(k), "artifacts": [root_name]})
    if LINEAGE in families:
        for k, names in sorted(holders.get(lob, {}).items(), key=lambda kv: (order[kv[0].split(":")[0]], -len(kv[1]), kv[0])):
            if len(names) >= 1:
                out.append({"family": LINEAGE, "signal": _label(k), "artifacts": sorted(names)[:_MAX_ARTIFACTS]})
    if SOURCE in families:
        for k, names in sorted(src_objs.get(lob, {}).items()):
            out.append({"family": SOURCE, "signal": _label(k), "artifacts": sorted(names)[:_MAX_ARTIFACTS]})
    return out[:_MAX_EVIDENCE]


# ── agrupación EFECTIVA del Catálogo (separada de lo observado/sugerido) ───────────────────────────────────────────────────────────────────
def effective_classification(business: dict | None, manual_lob: str | None = None, manual_domain: str | None = None) -> dict:
    """Precedencia inicial: asignación MANUAL explícita > clasificación automática HIGH > SIN CLASIFICAR. Una MEDIUM queda como sugerencia y NO mueve el activo.
    Con una asignación manual se conserva y se informa si la automática coincide (ALIGNED) o discrepa (DISAGREES)."""
    auto = ((business or {}).get("line_of_business") or {}).get("id") if (business or {}).get("status") in (CLASSIFIED, SUGGESTED) else None
    auto_high = auto if (business or {}).get("status") == CLASSIFIED else None
    if manual_lob in LOB_BY_ID:
        align = None if not auto else ("ALIGNED" if auto == manual_lob else "DISAGREES")
        return {"line_of_business": manual_lob, "label": lob_label(manual_lob), "source": "MANUAL", "automatic_alignment": align,
                "domain": manual_domain or ((business or {}).get("domain") or {}).get("label")}
    if auto_high:
        return {"line_of_business": auto_high, "label": lob_label(auto_high), "source": "AUTOMATIC", "automatic_alignment": None,
                "domain": ((business or {}).get("domain") or {}).get("label")}
    return {"line_of_business": None, "label": None, "source": "UNCLASSIFIED", "automatic_alignment": None,
            "suggestion": {"id": auto, "label": lob_label(auto)} if auto else None, "domain": manual_domain}
