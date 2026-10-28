"""Registro centralizado de ETIQUETAS para el consultor de los códigos internos del Assessment (solo PRESENTACIÓN).

Qué es y qué no es
------------------
* El motor emite identificadores (``MISSING_LAYER_PREFIX``, ``ANCHORED_AT_LOCAL_BOUNDARY``…) que son parte de los contratos canónicos y de la trazabilidad. Este registro NO los cambia ni los
  reemplaza en el JSON/Markdown: solo permite que la LECTURA principal del informe use el lenguaje del consultor y que el código siga disponible (en el glosario del detalle técnico y como
  ``title`` de la etiqueta).
* Una entrada existe SOLO si su significado está documentado en el propio motor (docstring, comentario de enum o texto de la regla) y esa fuente figura en ``source``. Las etiquetas son
  paráfrasis fieles de esa documentación; no se traduce ningún nombre de variable por heurística y no se agrega ninguna afirmación metodológica que la regla no haga. Un código sin entrada
  se muestra tal cual (``label()`` devuelve ``None``): los casos ambiguos quedan listados en ``docs/ASSESSMENT_ACTION_READING.md`` para decisión metodológica.
* ``tests/webapp/test_assessment_action_reading.py`` verifica que cada código figure realmente en el archivo fuente declarado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from webapp.services import module_reference as MR


@dataclass(frozen=True)
class Entry:
    label: str          # lo que lee el consultor
    meaning: str        # qué significa, tal como lo documenta el motor
    source: str         # archivo del motor donde está definido/documentado (relativo a la raíz del repo)
    generic: bool = False   # palabra genérica (UNKNOWN, SYSTEM…) que otras familias de códigos también usan: solo se etiqueta en SU contexto (``generic_ok=True``), nunca por coincidencia de texto


_NR = "tools/catalog/modeling_assessment/naming_rules.py"
_GC = "tools/catalog/generate_catalog.py"
_ID = "tools/catalog/modeling_assessment/identity.py"
_FN = "tools/catalog/modeling_assessment/facts_naming.py"

REGISTRY: dict[str, Entry] = {
    # ── familias de incumplimiento de la convención de nombres (§28): el código lo define ``naming_rules.ViolationFamily``; el significado sale de los textos de ``generate_catalog.parse_name`` (``_GC``) que esa tabla mapea ──
    "TYPED_ARTIFACT_WITH_LAYER_DIGIT": Entry(
        "Artefacto tipado con número de capa",
        "Un artefacto tipado (AM, DF, RF, FT, IL, TC, EM, DAC) lleva un número de capa; la convención indica que los artefactos tipados se nombran sin número de capa.", _NR),
    "ARTIFACT_LETTER_NOT_HOMOLOGATED": Entry(
        "Letra de artefacto no homologada",
        "En un nombre con la forma <Capa><Artefacto><Uso>_ la letra de artefacto no es T (tabla) ni V (vista); los modelos y flows son «Otros Artefactos».", _NR),
    "USE_LETTER_NOT_HOMOLOGATED": Entry(
        "Letra de uso no homologada",
        "En un nombre con la forma <Capa><Artefacto><Uso>_ la letra de uso semántico no es una de las homologadas: F (Fact), R (Relacional), D (Dimensión), A (Analítica), T (Texto), H (Jerarquía).", _NR),
    "MISSING_LAYER_PREFIX": Entry(
        "Tabla o vista sin prefijo de capa",
        "El nombre empieza con la letra de artefacto T (tabla) o V (vista) pero no lleva el número de capa (1–4) que la convención antepone: <Capa><Artefacto><Uso>_.", _NR),
    "NO_RECOGNIZED_PATTERN": Entry(
        "Nombre sin patrón reconocido",
        "El nombre no sigue <Capa><Artefacto><Uso>_ ni corresponde a un artefacto homologado (DF, RF, FT, AM…).", _NR),
    "UNMAPPED_ISSUE": Entry(
        "Motivo de incumplimiento sin clasificar",
        "Un motivo de incumplimiento que la clasificación por familias del motor no reconoce (el motor lo marca así para detectarlo en pruebas).", _NR),
    # ── forma estructural del nombre del artefacto (``naming_rules.NameShape``) ──
    "TYPED_ARTIFACT": Entry("Artefacto tipado sin número de capa", "Artefacto de los «Otros Artefactos» (AM, DF, RF…) nombrado sin dígito de capa.", _NR),
    "LAYER_ARTIFACT_USE_PREFIX": Entry("Prefijo <Capa><Artefacto><Uso>_", "El nombre tiene la forma <dígito de capa><artefacto><uso>_….", _NR),
    "UNLAYERED_TABLE_OR_VIEW": Entry("Tabla o vista sin número de capa", "Tabla o vista (T/V) sin dígito de capa.", _NR),
    # ── procedencia del nombre (``identity.py``) ──
    "PROJECT_ASSIGNED": Entry("Nombre asignado por el proyecto", "Artefacto autorado en la solución (tiene query propia).", _ID),
    "SOURCE_DERIVED_IMPORTED": Entry(
        "Nombre heredado de la fuente remota",
        "Importación de una entidad remota cuyo nombre coincide con el de la entidad o se deriva de él (en algunos casos por una regla heurística del motor).", _ID),
    "SYSTEM": Entry("Objeto de sistema", "Artefacto de un namespace de sistema.", _ID, generic=True),
    "UNKNOWN": Entry("Procedencia no determinable", "El nombre no puede atribuirse a la fuente ni al proyecto con la metadata disponible; el motor no lo conjetura.", _ID, generic=True),
    # ── ancla del nombre de un campo en el linaje observable (``facts_naming.FieldNameAnchor``) ──
    "IS_REMOTE_ARTIFACT_FIELD": Entry("Campo de un artefacto remoto", "El artefacto tiene entidad remota: es en sí mismo un ancla remota observada.", _FN),
    "IS_LOCAL_BOUNDARY_FIELD": Entry("Campo de una hoja local", "Hoja sin entidad remota (tabla local o estructura): no hay linaje más temprano observable.", _FN),
    "NO_LINEAGE_PROVIDER": Entry("Sin proveedores de linaje", "Solo hay asociaciones: no hay dónde buscar continuidad del nombre.", _FN),
    "ANCHORED_AT_REMOTE_ARTIFACT": Entry("Ancla en un artefacto remoto", "Alguna ancla más temprana del nombre es un artefacto con entidad remota.", _FN),
    "ANCHORED_AT_LOCAL_BOUNDARY": Entry("Ancla en una hoja local", "Las anclas más tempranas son hojas locales sin entidad remota (y ninguna remota).", _FN),
    "FIRST_APPEARS_IN_INTERMEDIATE": Entry("Aparece primero en una capa intermedia", "El nombre aparece por primera vez en una capa intermedia y se arrastra hacia arriba.", _FN),
    "FIRST_APPEARS_AT_THIS_ARTIFACT": Entry("Aparece primero en este artefacto", "El nombre no está en ningún artefacto del linaje observable.", _FN),
    # ── forma de un nombre de campo (``naming_rules.FieldNameForm``): descripción mecánica, no un juicio de calidad ──
    "ALNUM_1_3": Entry("Alfanumérico de 1 a 3 caracteres", "Forma del nombre técnico: alfanumérico sin separador, 1–3 caracteres.", _NR),
    "ALNUM_4_6": Entry("Alfanumérico de 4 a 6 caracteres", "Forma del nombre técnico: alfanumérico sin separador, 4–6 caracteres (forma corta tipo DDIC).", _NR),
    "ALNUM_7_PLUS": Entry("Alfanumérico de 7 o más caracteres", "Forma del nombre técnico: alfanumérico sin separador, 7 o más caracteres (forma larga tipo API CDS).", _NR),
    "UNDERSCORE_SEPARATED": Entry("Con guiones bajos", "Forma del nombre técnico: contiene «_».", _NR),
    "OTHER_CHARACTERS": Entry("Con espacios o símbolos", "Forma del nombre técnico: espacios, símbolos o vacío; fuera de las formas anteriores.", _NR),
}

# Evidencia de una etiqueta compartida (``facts_naming`` / ``render_md._TIER_TEXT``): el texto ya existe en el Markdown del motor.
EVIDENCE_TIER_TEXT = {"CO_DECLARED_IN_ARTIFACT": "declarados juntos en un artefacto", "EXPOSED_BY_TERMINAL_AM": "expuestos por el modelo analítico terminal"}


def _entry(code: Any, generic_ok: bool = False) -> Entry | None:
    e = REGISTRY.get(str(code))
    return None if (e is None or (e.generic and not generic_ok)) else e


def label(code: Any, *, generic_ok: bool = False) -> str | None:
    e = _entry(code, generic_ok)
    return e.label if e else None


def meaning(code: Any) -> str | None:
    e = REGISTRY.get(str(code))
    return e.meaning if e else None


def cell(code: Any, *, generic_ok: bool = False) -> Any:
    """Celda de tabla: si hay etiqueta, ``{"text", "code"}`` (la plantilla muestra el texto y deja el código como ``title``); si no, el valor tal cual. ``generic_ok``: el contexto de la tabla
    es el de la familia del código (p. ej. la procedencia del nombre para ``UNKNOWN``)."""
    lab = label(code, generic_ok=generic_ok)
    return {"text": lab, "code": str(code)} if lab else code


_CODE_RE = None


def relabel(text: str) -> tuple[str, list[str]]:
    """Reemplaza, en un texto ya redactado por el motor, cada código REGISTRADO (palabra completa) por su etiqueta; devuelve (texto, códigos usados). No toca ninguna otra palabra."""
    global _CODE_RE
    import re
    if _CODE_RE is None:
        _CODE_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted((re.escape(c) for c, e in REGISTRY.items() if not e.generic), key=len, reverse=True)) + r")(?![A-Za-z0-9_])")
    used: list[str] = []

    def _sub(m):
        used.append(m.group(1))
        return REGISTRY[m.group(1)].label[:1].lower() + REGISTRY[m.group(1)].label[1:]
    return _CODE_RE.sub(_sub, text), used


def glossary(codes: Iterable[Any]) -> list[dict]:
    """Glosario (código → etiqueta → significado → fuente) de los códigos PRESENTES en un informe, sin repetidos y en orden estable."""
    out, seen = [], set()
    for c in codes:
        c = str(c)
        if c in seen or c not in REGISTRY:
            continue
        seen.add(c)
        e = REGISTRY[c]
        out.append({"code": c, "label": e.label, "meaning": e.meaning, "source": e.source})
    return out


# ───────────────────────── convención de nombres: familias con su universo completo ─────────────────────────
def _hygiene(doc: dict | None) -> dict:
    return ((((doc or {}).get("canonical_evidence") or {}).get("naming_evidence") or {}).get("hygiene") or {}).get("artifact_name_populations") or {}


def naming_view(doc: dict | None) -> dict | None:
    """Familias de incumplimiento de la sección 28 con los artefactos de cada una, SOLO si la evidencia canónica los atribuye de forma demostrable.

    Regla de atribución (verificable, sin inferir): el perfil conserva, por procedencia del nombre (``by_identity``), la lista de artefactos que no cumplen y el histograma de familias de
    ESE grupo. Si el grupo tiene UNA sola familia y su conteo coincide con la cantidad de artefactos del grupo, todos pertenecen a esa familia. Un grupo con varias familias no permite
    repartir sus artefactos: se informan aparte como «sin atribución por familia» (la evidencia solo guarda el agregado). Nunca se reparte por nombre ni por patrón.
    La «regla evaluada» de una familia se muestra solo cuando la MISMA evidencia la demuestra: el grupo atribuido tiene una única familia y un único motivo persistido cuyo conteo coincide con el de
    sus artefactos. No hay clasificador ni reglas del motor en este módulo (la frontera del paquete es del presenter): si la evidencia no empareja motivo y familia, no se muestra motivo.
    Devuelve ``None`` si el perfil no trae la sección 28 (perfiles anteriores): la lectura vigente queda como estaba."""
    pop = _hygiene(doc)
    s28 = pop.get("section_28") or {}
    if not s28 or not s28.get("applicable"):
        return None
    non = [str(a) for a in s28.get("non_compliant_artifacts") or []]
    comp = [str(a) for a in s28.get("compliant_artifacts") or []]
    reason_by_family: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    attributed: set[str] = set()
    for grp in pop.get("by_identity") or []:
        arts = [str(a) for a in grp.get("non_compliant_artifacts") or []]
        fams = grp.get("violation_family_histogram") or []
        if arts and len(fams) == 1 and int(fams[0].get("count") or 0) == len(arts) and int(grp.get("distinct_violation_families") or 0) == 1:
            members.setdefault(str(fams[0]["key"]), []).extend(arts)
            attributed.update(arts)
            reasons = grp.get("violation_reason_histogram") or []
            if len(reasons) == 1 and int(reasons[0].get("count") or 0) == len(arts) and reasons[0].get("key"):
                reason_by_family.setdefault(str(fams[0]["key"]), MR.strip_ordinals(str(reasons[0]["key"])))      # sin el ordinal histórico de la sección
    families = []
    for f in s28.get("violation_family_histogram") or []:
        code = str(f["key"])
        arts = sorted(members.get(code, []))
        complete = len(arts) == int(f.get("count") or 0)
        families.append({"code": code, "label": label(code) or code, "meaning": meaning(code), "reason": reason_by_family.get(code), "count": int(f.get("count") or 0),
                         "artifacts": arts if complete else None, "dom_id": f"asr-nf-{code}"})
    rest = sorted(set(non) - attributed)
    return {"applicable": int(s28.get("applicable") or 0), "compliant": len(comp), "non_compliant": len(non), "families": families,
            "compliant_artifacts": sorted(comp), "unattributed": rest if rest and any(f["artifacts"] is None for f in families) else [],
            "all_attributed": all(f["artifacts"] is not None for f in families),
            "basis": "La evidencia del Assessment conserva la procedencia del nombre de cada artefacto y las familias de incumplimiento de cada grupo."}
