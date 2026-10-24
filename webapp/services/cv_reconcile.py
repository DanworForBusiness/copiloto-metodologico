"""cv_reconcile.py — conciliación de IDENTIDAD de un CV extraído contra los perfiles del Banco y contra el resto del lote, SIN fusionar nada.

Distingue dos identidades que no deben confundirse:
  * identidad DOCUMENTAL: «este contenido ya lo vimos» (hash/sha256; se resuelve en `cv_history_pilot`, no acá);
  * identidad de PERSONA: «esto parece la misma persona» (acá): email, celular y nombre.

Las señales solo SUGIEREN. Ninguna coincidencia —ni siquiera un email idéntico— fusiona, vincula ni crea nada: el resultado es una lista de candidatos con la evidencia de cada uno para que una persona decida.
`fold` y la normalización de email son las MISMAS que usa el alta de perfiles; el email exacto sigue la regla vigente de duplicado (`human_capital_service.find_duplicate`); el resto de las señales (celular,
nombre en otro orden, parecido) son un complemento para la revisión y NO cambian cómo detecta duplicados el alta manual.

Puro: sin disco, sin Graph, sin logs de datos personales."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from webapp.services.cv_extract import fold

EMAIL_EXACT, PHONE_MATCH, NAME_EXACT, NAME_SWAPPED, NAME_TOKENS_EQUAL, NAME_SIMILAR, INITIAL_LAST = (
    "EMAIL_EXACT", "PHONE_MATCH", "NAME_EXACT", "NAME_SWAPPED", "NAME_TOKENS_EQUAL", "NAME_SIMILAR", "INITIAL_LAST")
STRONG, PROBABLE, WEAK = "STRONG", "PROBABLE", "WEAK"
SIMILARITY_THRESHOLD = 0.88
MAX_MATCHES = 5
_PHONE_TAIL = 8                                           # dígitos finales que se comparan (tolera prefijos de país/área y el «15»)


def norm_email(value) -> str:
    return " ".join(str(value or "").lower().split())


def phone_tail(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-_PHONE_TAIL:] if len(digits) >= _PHONE_TAIL else ""


def _full(first, last) -> str:
    return fold(f"{first or ''} {last or ''}")


def _tokens(first, last) -> set[str]:
    return {t for t in _full(first, last).split() if len(t) > 1}


def token_key(first, last) -> frozenset:
    """Conjunto de palabras del nombre (sin tildes ni mayúsculas): clave de índice. Toda coincidencia de nombre FUERTE o PROBABLE (`NAME_EXACT`, `NAME_SWAPPED`, `NAME_TOKENS_EQUAL`) implica la misma clave."""
    return frozenset(_tokens(first, last))


def signals_between(a: dict, b: dict) -> list[str]:
    """Señales entre dos personas con `first_name`, `last_name`, `email`, `phone` (cualquiera puede faltar)."""
    out: list[str] = []
    ea, eb = norm_email(a.get("email")), norm_email(b.get("email"))
    if ea and ea == eb:
        out.append(EMAIL_EXACT)
    pa, pb = phone_tail(a.get("phone") or a.get("phone_digits")), phone_tail(b.get("phone") or b.get("phone_digits"))
    if pa and pa == pb:
        out.append(PHONE_MATCH)
    fa, fb = _full(a.get("first_name"), a.get("last_name")), _full(b.get("first_name"), b.get("last_name"))
    if fa.strip() and fb.strip():
        if fa == fb:
            out.append(NAME_EXACT)
        elif _full(a.get("last_name"), a.get("first_name")) == fb:
            out.append(NAME_SWAPPED)
        elif _tokens(a.get("first_name"), a.get("last_name")) == _tokens(b.get("first_name"), b.get("last_name")):
            out.append(NAME_TOKENS_EQUAL)
        else:
            la, lb = fold(a.get("last_name") or ""), fold(b.get("last_name") or "")
            ia, ib = fold(a.get("first_name") or "")[:1], fold(b.get("first_name") or "")[:1]
            if la and la == lb and ia and ia == ib:
                out.append(INITIAL_LAST)
            elif SequenceMatcher(None, fa, fb).ratio() >= SIMILARITY_THRESHOLD:
                out.append(NAME_SIMILAR)
    return out


def strength(signals: list[str]) -> str | None:
    s = set(signals)
    if EMAIL_EXACT in s or (PHONE_MATCH in s and s & {NAME_EXACT, NAME_SWAPPED, NAME_TOKENS_EQUAL, INITIAL_LAST, NAME_SIMILAR}):
        return STRONG
    if s & {NAME_EXACT, NAME_SWAPPED, NAME_TOKENS_EQUAL, PHONE_MATCH}:
        return PROBABLE
    if s:
        return WEAK
    return None


_ORDER = {STRONG: 0, PROBABLE: 1, WEAK: 2}


def reconcile_with_profiles(fields: dict, profiles: list[dict]) -> list[dict]:
    """Candidatos entre los perfiles existentes (incluidos los dados de baja lógica, marcados). Solo sugerencias: `[{profile_id, strength, signals, deleted}]`, de más a menos fuerte, acotado."""
    matches = []
    for profile in profiles:
        signals = signals_between(fields, profile)
        level = strength(signals)
        if level:
            matches.append({"profile_id": profile.get("profile_id"), "strength": level, "signals": signals, "deleted": bool(profile.get("deleted_at"))})
    matches.sort(key=lambda m: (_ORDER[m["strength"]], -len(m["signals"]), str(m["profile_id"])))
    return matches[:MAX_MATCHES]


def cluster_in_run(records: dict[str, dict]) -> dict[str, list[dict]]:
    """Dentro del lote: para cada documento, OTROS documentos que parecen la misma persona (`{key, strength, signals}`). Es una sugerencia de agrupación de versiones documentales; no fusiona nada."""
    keys = sorted(records)
    out: dict[str, list[dict]] = {k: [] for k in keys}
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            signals = signals_between(records[ka], records[kb])
            level = strength(signals)
            if level:
                out[ka].append({"key": kb, "strength": level, "signals": signals})
                out[kb].append({"key": ka, "strength": level, "signals": signals})
    return out
