"""Reglas PURAS y versionadas de Naming (M4) — descripcion mecanica de nombres, NO calidad. Sin imports legacy.

Disciplina de Fase 1: nombre -> HECHO NORMALIZADO. Nada de este modulo juzga si un nombre es bueno:

  * la FORMA de un nombre de campo (`field_name_form`) es un patron descriptivo y heuristico (clase B). Que un
    nombre "parezca DDIC", "parezca de negocio", tenga etiqueta o empiece con Z NO es evidencia de calidad ni de
    procedencia: la procedencia de un nombre de campo se deriva de la CONTINUIDAD por linaje (facts_naming), nunca de
    su forma;
  * las FAMILIAS de incumplimiento de §28 (`violation_family`) son una clave estable derivada del texto de los
    `issues` que devuelve `parse_name()` (que se reutiliza TAL CUAL por el bridge): permiten un histograma sin
    depender del texto libre ni de la letra concreta del incumplimiento. Los motivos EXACTOS se conservan aparte;
  * el vocabulario semantico ("de negocio" vs "tecnico") NO se asigna: no hay diccionario ni regla probada.

`FIELD_NAME_RULESET` versiona las reglas de forma; cambiar un umbral o una clase exige subir la version.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable

FIELD_NAME_RULESET = "field-name-forms/v1"


# ── forma de un nombre de campo (B, heuristica descriptiva) ───────────────────
class FieldNameForm(str, Enum):
    ALNUM_1_3 = "ALNUM_1_3"                        # alfanumerico sin separador, 1-3 caracteres
    ALNUM_4_6 = "ALNUM_4_6"                        # alfanumerico sin separador, 4-6 caracteres (forma corta tipo DDIC)
    ALNUM_7_PLUS = "ALNUM_7_PLUS"                  # alfanumerico sin separador, 7+ caracteres (forma larga tipo API CDS)
    UNDERSCORE_SEPARATED = "UNDERSCORE_SEPARATED"  # contiene `_`
    OTHER_CHARACTERS = "OTHER_CHARACTERS"          # espacios/simbolos/vacio: fuera de las formas anteriores


class CaseStyle(str, Enum):
    UPPER = "UPPER"
    LOWER = "LOWER"
    MIXED = "MIXED"
    NO_LETTERS = "NO_LETTERS"


_SIMPLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_CUSTOM_PREFIX_RE = re.compile(r"^[Zz][A-Za-z0-9]*_")


def field_name_form(name: str) -> dict:
    """Forma mecanica de UN nombre de campo. `custom_namespace_prefix` = prefijo `Z...` seguido de `_` (la FORMA del
    espacio de nombres propio de SAP): NO prueba que sea vocabulario gobernado ni de calidad."""
    s = str(name or "")
    if not s or not _SIMPLE_NAME_RE.match(s):
        form = FieldNameForm.OTHER_CHARACTERS
    elif "_" in s:
        form = FieldNameForm.UNDERSCORE_SEPARATED
    elif len(s) <= 3:
        form = FieldNameForm.ALNUM_1_3
    elif len(s) <= 6:
        form = FieldNameForm.ALNUM_4_6
    else:
        form = FieldNameForm.ALNUM_7_PLUS
    letters = [c for c in s if c.isalpha()]
    if not letters:
        case = CaseStyle.NO_LETTERS
    elif all(c.isupper() for c in letters):
        case = CaseStyle.UPPER
    elif all(c.islower() for c in letters):
        case = CaseStyle.LOWER
    else:
        case = CaseStyle.MIXED
    return {"form": form.value, "case_style": case.value,
            "custom_namespace_prefix": bool(_CUSTOM_PREFIX_RE.match(s)),
            "token_count": len([t for t in s.split("_") if t])}


# ── etiquetas ─────────────────────────────────────────────────────────────────
def normalize_label(label: Any) -> str:
    """Etiqueta comparable: sin espacios sobrantes y sin distincion de mayusculas. NO pliega acentos ni sinonimos."""
    return " ".join(str(label or "").split()).casefold()


def _alnum_fold(text: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(text or "").casefold())


def label_relations(field: str, label: Any) -> dict:
    """Relacion objetiva entre el nombre tecnico y su etiqueta. Presencia/ausencia e igualdad son HECHOS: ninguna
    se lee como calidad (una etiqueta igual al campo no es 'mala' por si sola, ni una distinta es 'buena')."""
    text = " ".join(str(label or "").split())
    present = bool(text)
    return {
        "label": text or None,
        "label_present": present,
        "label_equals_field": present and text == str(field),
        "label_matches_field_normalized": present and _alnum_fold(text) == _alnum_fold(field) and bool(_alnum_fold(field)),
    }


# ── nombres de ARTEFACTO: familias de incumplimiento de §28 y patron de prefijo ─
class ViolationFamily(str, Enum):
    TYPED_ARTIFACT_WITH_LAYER_DIGIT = "TYPED_ARTIFACT_WITH_LAYER_DIGIT"
    ARTIFACT_LETTER_NOT_HOMOLOGATED = "ARTIFACT_LETTER_NOT_HOMOLOGATED"
    USE_LETTER_NOT_HOMOLOGATED = "USE_LETTER_NOT_HOMOLOGATED"
    MISSING_LAYER_PREFIX = "MISSING_LAYER_PREFIX"
    NO_RECOGNIZED_PATTERN = "NO_RECOGNIZED_PATTERN"
    UNMAPPED_ISSUE = "UNMAPPED_ISSUE"              # un texto de `parse_name` que esta tabla no conoce (lo detecta un test)


# (fragmento estable del texto de `parse_name`, familia). El orden importa: la primera que aplica gana.
_ISSUE_FRAGMENTS: tuple[tuple[str, ViolationFamily], ...] = (
    ("no debe llevar prefijo de capa", ViolationFamily.TYPED_ARTIFACT_WITH_LAYER_DIGIT),
    ("no homologado (§28: T/V", ViolationFamily.ARTIFACT_LETTER_NOT_HOMOLOGATED),
    ("uso semántico", ViolationFamily.USE_LETTER_NOT_HOMOLOGATED),
    ("sin prefijo de capa", ViolationFamily.MISSING_LAYER_PREFIX),
    ("no sigue §28", ViolationFamily.NO_RECOGNIZED_PATTERN),
)


def violation_family(issue: str) -> ViolationFamily:
    for fragment, family in _ISSUE_FRAGMENTS:
        if fragment in str(issue):
            return family
    return ViolationFamily.UNMAPPED_ISSUE


class NameShape(str, Enum):
    TYPED_ARTIFACT = "TYPED_ARTIFACT"                                  # AM/DF/RF/... sin digito de capa
    TYPED_ARTIFACT_WITH_LAYER_DIGIT = "TYPED_ARTIFACT_WITH_LAYER_DIGIT"
    LAYER_ARTIFACT_USE_PREFIX = "LAYER_ARTIFACT_USE_PREFIX"            # <digito><artefacto><uso>_...
    UNLAYERED_TABLE_OR_VIEW = "UNLAYERED_TABLE_OR_VIEW"                # T/V sin digito de capa
    NO_RECOGNIZED_PATTERN = "NO_RECOGNIZED_PATTERN"


def name_shape(parsed: dict, issues: Iterable[str]) -> str:
    """Forma estructural del nombre segun lo que `parse_name` YA descompuso (no se re-implementa su regex)."""
    if parsed.get("tipo") == "otro":
        return (NameShape.TYPED_ARTIFACT_WITH_LAYER_DIGIT if parsed.get("capa") else NameShape.TYPED_ARTIFACT).value
    if "artefacto" in parsed:
        return NameShape.LAYER_ARTIFACT_USE_PREFIX.value
    families = {violation_family(i) for i in issues}
    if ViolationFamily.MISSING_LAYER_PREFIX in families:
        return NameShape.UNLAYERED_TABLE_OR_VIEW.value
    return NameShape.NO_RECOGNIZED_PATTERN.value


_DIGITS_RE = re.compile(r"\d+")


def naming_family(name: str) -> str | None:
    """Patron de prefijo: primer segmento antes de `_` con las corridas de digitos generalizadas a `#` (`2VR_X` -> `#VR_`,
    `VD_KNA1` -> `VD_`, `ZCDS_DIM_T134T` -> `ZCDS_`). None si el nombre no tiene `_`. Descriptivo: agrupa nombres que
    comparten convencion de prefijo, sin decidir si la convencion es correcta."""
    s = str(name or "")
    if "_" not in s:
        return None
    return _DIGITS_RE.sub("#", s.split("_", 1)[0]) + "_"


_NUMERIC_SUFFIX_RE = re.compile(r"_?\d+$")


def numeric_suffix_stem(name: str) -> str:
    """Nombre sin su sufijo numerico final (`HSL01` -> `HSL`, `DATA_CTRLR10` -> `DATA_CTRLR`). Descriptivo: sirve para
    distinguir una SERIE numerada de columnas (mismo stem) de nombres con stems distintos que comparten etiqueta."""
    return _NUMERIC_SUFFIX_RE.sub("", str(name or "")) or str(name or "")


def token_difference(small: Iterable[str], big: Iterable[str]) -> list[str]:
    """Tokens de `big` que no estan en `small`, en orden y respetando multiplicidad."""
    pool = list(small)
    out = []
    for t in big:
        if t in pool:
            pool.remove(t)
        else:
            out.append(t)
    return out


def histogram(values: Iterable[Any]) -> list[dict]:
    """Histograma como LISTA de registros (nunca dict con claves de dominio), orden estable: cuenta desc, clave asc."""
    counts: dict[str, int] = {}
    for v in values:
        k = "<none>" if v is None else str(v)
        counts[k] = counts.get(k, 0) + 1
    return [{"key": k, "count": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
