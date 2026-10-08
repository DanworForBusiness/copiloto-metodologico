"""Clasificación de dominio v1 — SUGERENCIA determinista y explicable (nunca mueve nada).

HERMANA del Juicio metodológico, jamás insumo suyo: ``classify_domain`` solo mira vocabulario (nombres/etiquetas de artefactos y dominios
del cliente) y NO importa ni lee el Juicio, el perfil del Assessment, la evidencia, la confianza ni el veredicto del catálogo. Cambiar la
sugerencia no puede cambiar ningún resultado metodológico (BUENA / ADECUADA / PARCIAL / NO DETERMINABLE).

Función PURA: sin red, sin LLM, sin embeddings, sin lectura de disco, sin reloj. Misma entrada → misma salida (``input_signature``).

Contrato (``DOMAIN_CLASSIFICATION_VERSION``):
    version, current_domain, current_domain_is_generic,
    classification_status   ALIGNED | SUGGEST_RECLASSIFICATION | UNDETERMINED
    suggested_domain        etiqueta o None
    suggestion_type         EXISTING_DOMAIN | NEW_DOMAIN_CANDIDATE | None
    confidence              HIGH | MEDIUM | LOW        (sin porcentajes)
    evidence[]              {family, term, artifacts[]}
    alternatives[]          otros dominios existentes compatibles (solo informativo)
    reason                  código estable (la UI lo traduce)
    input_signature         sha256 de las entradas (frescura propia de este derivado)

Algoritmo (sin pesos arbitrarios):
  1. Tokens: se parten nombres/etiquetas (guiones bajos, puntos, camelCase), se normalizan (minúsculas, sin tildes, singular simple) y se
     descartan prefijos/sufijos técnicos de capa (``4MA_``, ``V_``, ``_Final``…), palabras vacías y números.
  2. Familias de evidencia independientes:  ROOT = vocabulario del objeto raíz (nombre técnico + etiqueta);
     CLOSURE = vocabulario REPETIDO en ≥2 artefactos del cierre distintos de la raíz.
  3. Un dominio EXISTENTE del cliente queda respaldado por una familia si cubre ≥50 % de sus tokens con el vocabulario de esa familia.
     Confianza: 2 familias → HIGH · 1 familia → MEDIUM · 0 → sin respaldo.
  4. Dominio actual específico y respaldado → ALIGNED. Dominio actual genérico (General/Otros/vacío…) NO es un error por sí solo: solo se
     sugiere cuando hay evidencia coherente de uno más específico (existente > candidato nuevo). Sin evidencia → UNDETERMINED.
  5. Candidato NUEVO (solo si el actual es genérico y no hay dominio existente respaldado): los tokens de la RAÍZ que además se repiten en el
     cierre, en el orden observado en la raíz, con la grafía observada. ≥2 artefactos del cierre → HIGH · 1 → MEDIUM · 0 → UNDETERMINED
     (no se fabrica un candidato con la sola palabra de la raíz).
  6. Conflicto (dos dominios existentes igual de respaldados) → UNDETERMINED con ambos como alternativas.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Iterable

DOMAIN_CLASSIFICATION_VERSION = "domain-classification/v1"

ALIGNED, SUGGEST, UNDETERMINED = "ALIGNED", "SUGGEST_RECLASSIFICATION", "UNDETERMINED"
EXISTING_DOMAIN, NEW_DOMAIN_CANDIDATE = "EXISTING_DOMAIN", "NEW_DOMAIN_CANDIDATE"
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"

# Códigos de motivo estables.
R_ALIGNED = "CURRENT_DOMAIN_SUPPORTED"
R_GENERIC_STRONG = "GENERIC_DOMAIN_STRONG_COHERENT_VOCABULARY"
R_SPECIFIC_UNSUPPORTED_BETTER = "CURRENT_DOMAIN_UNSUPPORTED_EXISTING_DOMAIN_SUPPORTED"
R_NO_EVIDENCE = "NO_COHERENT_EVIDENCE"
R_CURRENT_NO_EVIDENCE = "CURRENT_DOMAIN_NO_EVIDENCE"
R_CONFLICT = "CONFLICTING_CANDIDATES"

_GENERIC_LABELS = {"general", "otros", "otro", "other", "others", "misc", "miscelanea", "miscelaneo", "varios", "sin dominio", "sin clasificar", "n/a", "na", "none", "null"}
_STOPWORDS = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "of", "the", "and", "for", "por", "con", "sin", "para", "al", "un", "una", "a", "o", "or"}
# Vocabulario TÉCNICO de capa/objeto (no de negocio); independiente del cliente.
_TECH = {"am", "em", "vw", "v", "t", "tbl", "view", "vista", "final", "base", "test", "tmp", "temp", "delta", "hist", "copy", "sap", "csv", "dac", "ma"}
_LAYER_PREFIX = re.compile(r"^\d[a-z]{1,3}$")        # 1TR, 2VF, 3VS, 4MA…
_WORD = re.compile(r"^(?=[a-z]*[aeiou])[a-z]{4,20}$")   # candidato "con forma de palabra": ≥4 letras con vocal; rechaza siglas (FNC) y concatenaciones largas
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_MAX_EVIDENCE_ARTIFACTS = 6
_MAX_NEW_TOKENS = 3


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _stem(tok: str) -> str:
    """Singular simple y determinista (pedidos→pedido, entregas→entrega, items→item); no es un stemmer lingüístico."""
    if len(tok) > 4 and tok.endswith("es"):
        return tok[:-2] if tok[-3] not in "aeiou" else tok[:-1]
    if len(tok) > 3 and tok.endswith("s"):
        return tok[:-1]
    return tok


def _pieces(text: str) -> list[str]:
    out = []
    for chunk in re.split(r"[^0-9A-Za-zÀ-ÿ]+", text or ""):
        out.extend(p for p in _CAMEL.split(chunk) if p)
    return out


def _tokens(text: str) -> list[tuple[str, str]]:
    """[(token_normalizado, grafía_observada)] en orden de aparición, sin vacías/técnicas/numéricas."""
    out = []
    for p in _pieces(text):
        low = _fold(p).lower()
        if low in _STOPWORDS or low in _TECH or _LAYER_PREFIX.match(low) or not low.isalpha() or len(low) < 3:
            continue
        out.append((_stem(low), p))
    return out


def is_generic_domain(label: str | None) -> bool:
    text = _fold((label or "").strip()).lower()
    return (not text) or text in _GENERIC_LABELS or not _tokens(label or "")


def _display(spelling: str) -> str:
    return spelling[:1].upper() + spelling[1:].lower()


def _signature(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _support(domain_tokens: set[str], root_tokens: set[str], repeated: set[str]) -> tuple[list[str], list[str]]:
    """Familias que respaldan un dominio: (familias, términos coincidentes). ≥50 % de los tokens del dominio cubiertos por la familia."""
    families, terms = [], []
    for fam, vocab in (("ROOT", root_tokens), ("CLOSURE", repeated)):
        hit = domain_tokens & vocab
        if hit and len(hit) / len(domain_tokens) >= 0.5:
            families.append(fam)
            terms.extend(sorted(hit))
    return families, terms


def classify_domain(*, current_domain: str | None, root: dict, closure: Iterable[dict], client_domains: Iterable[str] = (),
                    client: str = "") -> dict:
    """``root`` = {name, label?}; ``closure`` = [{name, label?}] (incluida o no la raíz); ``client_domains`` = dominios EXISTENTES del cliente."""
    cur = (current_domain or "").strip()
    root_name = str(root.get("name") or "")
    arts = [{"name": str(a.get("name") or ""), "label": str(a.get("label") or "")} for a in closure if a.get("name")]
    others = [a for a in arts if a["name"] != root_name]
    client_tokens = {t for t, _ in _tokens(client)}

    root_tok = [(t, s) for t, s in _tokens(root_name) + _tokens(str(root.get("label") or "")) if t not in client_tokens]
    root_set = {t for t, _ in root_tok}
    per_art: dict[str, set[str]] = {}
    spelling: dict[str, str] = {}
    for a in others:
        toks = {t for t, s in _tokens(a["name"]) + _tokens(a["label"]) if t not in client_tokens}
        per_art[a["name"]] = toks
    for t, s in root_tok:
        spelling.setdefault(t, s)
    for a in others:
        for t, s in _tokens(a["name"]) + _tokens(a["label"]):
            spelling.setdefault(t, s)
    count: dict[str, list[str]] = {}
    for name, toks in per_art.items():
        for t in toks:
            count.setdefault(t, []).append(name)
    repeated = {t for t, names in count.items() if len(names) >= 2}

    domains = []
    for d in dict.fromkeys(str(x).strip() for x in client_domains if str(x).strip()):
        if d == cur or is_generic_domain(d):
            continue
        toks = {t for t, _ in _tokens(d)}
        fams, terms = _support(toks, root_set, repeated)
        domains.append({"domain": d, "families": fams, "terms": terms})

    def _evidence(terms: Iterable[str], fams: Iterable[str]) -> list[dict]:
        ev = []
        for t in terms:
            holders = ([root_name] if t in root_set else []) + count.get(t, [])
            ev.append({"family": "ROOT+CLOSURE" if ("ROOT" in fams and "CLOSURE" in fams) else ("ROOT" if "ROOT" in fams else "CLOSURE"),
                       "term": _display(spelling.get(t, t)), "artifacts": list(dict.fromkeys(holders))[:_MAX_EVIDENCE_ARTIFACTS]})
        return ev

    result = {"version": DOMAIN_CLASSIFICATION_VERSION, "current_domain": cur or None, "current_domain_is_generic": is_generic_domain(cur),
              "classification_status": UNDETERMINED, "suggested_domain": None, "suggestion_type": None, "confidence": LOW,
              "evidence": [], "alternatives": [], "reason": R_NO_EVIDENCE}
    result["input_signature"] = _signature({"v": DOMAIN_CLASSIFICATION_VERSION, "cur": cur, "root": [root_name, str(root.get("label") or "")],
                                            "closure": sorted((a["name"], a["label"]) for a in arts), "domains": sorted({str(x).strip() for x in client_domains if str(x).strip()}),
                                            "client": client})

    def _rank(d):                       # más familias, luego más términos, luego nombre (determinista)
        return (-len(d["families"]), -len(d["terms"]), d["domain"].lower())

    # 1) Dominio actual ESPECÍFICO
    if not result["current_domain_is_generic"]:
        cur_fams, cur_terms = _support({t for t, _ in _tokens(cur)}, root_set, repeated)
        better = sorted((d for d in domains if d["families"]), key=_rank)
        if cur_fams:
            result.update(classification_status=ALIGNED, confidence=HIGH if len(cur_fams) == 2 else MEDIUM, reason=R_ALIGNED,
                          evidence=_evidence(cur_terms, cur_fams), alternatives=[d["domain"] for d in better][:3])
            return result
        if better:
            if len(better) > 1 and _rank(better[0])[:2] == _rank(better[1])[:2]:
                result.update(reason=R_CONFLICT, alternatives=[d["domain"] for d in better][:3])
                return result
            top = better[0]
            result.update(classification_status=SUGGEST, suggested_domain=top["domain"], suggestion_type=EXISTING_DOMAIN,
                          confidence=MEDIUM,                                  # contradice la etiqueta actual → nunca HIGH
                          reason=R_SPECIFIC_UNSUPPORTED_BETTER, evidence=_evidence(top["terms"], top["families"]))
            return result
        result.update(reason=R_CURRENT_NO_EVIDENCE)                           # específico sin respaldo ni alternativa: sin recomendación
        return result

    # 2) Dominio actual GENÉRICO: dominio existente respaldado > candidato nuevo > sin recomendación
    ranked = sorted((d for d in domains if d["families"]), key=_rank)
    if ranked:
        if len(ranked) > 1 and _rank(ranked[0])[:2] == _rank(ranked[1])[:2]:
            result.update(reason=R_CONFLICT, alternatives=[d["domain"] for d in ranked][:3])
            return result
        top = ranked[0]
        result.update(classification_status=SUGGEST, suggested_domain=top["domain"], suggestion_type=EXISTING_DOMAIN,
                      confidence=HIGH if len(top["families"]) == 2 else MEDIUM, reason=R_GENERIC_STRONG,
                      evidence=_evidence(top["terms"], top["families"]), alternatives=[d["domain"] for d in ranked[1:3]])
        return result
    # Candidato NUEVO: tokens de la RAÍZ que se repiten en el cierre. ≥2 artefactos por token → HIGH; 1 → MEDIUM (se prefiere el conjunto repetido).
    seen, hi, lo = set(), [], []
    for t, _s in root_tok:
        if t in seen or t not in count or not _WORD.match(_fold(spelling.get(t, t)).lower()):
            continue
        seen.add(t)
        (hi if len(count[t]) >= 2 else lo).append(t)
    chosen, conf = (hi, HIGH) if hi else (lo, MEDIUM)
    chosen = chosen[:_MAX_NEW_TOKENS]
    if chosen:
        result.update(classification_status=SUGGEST, suggested_domain=" / ".join(_display(spelling[t]) for t in chosen),
                      suggestion_type=NEW_DOMAIN_CANDIDATE, confidence=conf, reason=R_GENERIC_STRONG,
                      evidence=_evidence(chosen, ("ROOT", "CLOSURE")))
    return result
