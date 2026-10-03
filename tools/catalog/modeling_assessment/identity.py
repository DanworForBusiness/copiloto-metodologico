"""Procedencia de implementacion e identidad de artefactos (M1) — CLASIFICACION, no calidad.

Responde "QUE TIPO DE IDENTIDAD TIENE?". No juzga si un nombre es bueno, ni cumplimiento §28, ni
madurez: solo clasifica con reglas EXPLICITAS, DETERMINISTAS y con PROCEDENCIA (cada resultado trae
la regla aplicada, su certeza y las referencias de evidencia usadas). Puro (sin imports legacy): lee
atributos de la entidad por duck typing (`name`, `remote_entity`, `repository_type`, `has_query`, `file`).

EJE A — ImplementationProvenance (de donde sale el artefacto, por atributos):
    SYSTEM_PROVIDED  nombre en el namespace reservado de plataforma (`SAP` / `SAP.*`)
    REMOTE_IMPORTED  tiene `remote_entity`
    LOCAL_TABLE      `repository_type == DWC_LOCAL_TABLE` (aunque traiga `query`: es la proyeccion de
                     activacion de una tabla persistida, no una vista autorada)
    QUERY_DEFINED    tiene `query` (vista/modelo autorado en la solucion)
    STRUCTURE_ONLY   sin remote, sin query y sin tipo de repositorio declarado

EJE B — IdentityProvenance (quien asigno el nombre):
    SYSTEM, SOURCE_DERIVED_IMPORTED, PROJECT_ASSIGNED, UNKNOWN

Reglas (ordenadas, la primera que aplica gana; `IDENTITY_RULESET` versiona el conjunto):
    ID_SYSTEM_NAMESPACE              SYSTEM                  PROVEN
    ID_SOURCE_EXACT                  SOURCE_DERIVED_IMPORTED PROVEN     nombre == stem de la fuente remota
    ID_SOURCE_NAME_EXTENDS_STEM      SOURCE_DERIVED_IMPORTED HEURISTIC  el stem es subsecuencia ordenada de los tokens del nombre
                                                                        (el proyecto agrego prefijo/sufijo/tokens)
    ID_SOURCE_NAME_ABBREVIATES_STEM  SOURCE_DERIVED_IMPORTED HEURISTIC  los tokens del nombre son subsecuencia ordenada del stem
                                                                        (el proyecto quito tokens)
    ID_PROJECT_AUTHORED_QUERY        PROJECT_ASSIGNED        PROVEN     artefacto autorado en la solucion (query)
    ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE UNKNOWN             -          remoto cuyo nombre NO se deriva del stem por ninguna de las
                                                                        relaciones anteriores. NO se conjetura PROJECT_ASSIGNED: la
                                                                        semejanza de nombres no demuestra quien asigno la identidad
    ID_UNKNOWN_LOCAL_TABLE           UNKNOWN                 -          tabla local sin remoto ni query de autoria
    ID_UNKNOWN_STRUCTURE_ONLY        UNKNOWN                 -          estructura sin remoto ni query
    ID_UNKNOWN_EMPTY_REMOTE_STEM     UNKNOWN                 -          `remote_entity` sin stem utilizable

Decision del owner (revision de M1): el motor v2 es MAS CONSERVADOR que el dossier humano historico. La
clasificacion humana de CASE 4 (30/16/5/3) conto `ZCDS_COSP_BAK` como PROJECT_ASSIGNED reconociendo que era
una decision heuristica; el motor v2 no retiene esa conjetura para reproducir el conteo historico: sin
metadata que PRUEBE la identidad local (en el modelo de entidad actual no existe ninguna), la identidad es UNKNOWN
(CASE 4 v2 = 29/16/5/4). No es una calibracion fallida: es mayor disciplina de procedencia.

Las relaciones de tokens EXTENDS/ABBREVIATES son reglas deterministas de derivacion (subsecuencia estricta de
tokens) con certeza HEURISTIC declarada; no se presentan como prueba.

"PROVEN" = mecanicamente demostrable desde atributos; "HEURISTIC" = inferencia de nombres: se
registra explicitamente, con su evidencia, y NO se promueve a certeza. Donde la identidad no puede
probarse mecanicamente se prefiere UNKNOWN antes que una conjetura sin soporte.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .graph import modeling_population
from .model import EvidenceRef, Plane

IDENTITY_RULESET = "identity-rules/v1"

LOCAL_TABLE_REPOSITORY_TYPE = "DWC_LOCAL_TABLE"
SYSTEM_NAMESPACE = "SAP"


class ImplementationProvenance(str, Enum):
    SYSTEM_PROVIDED = "SYSTEM_PROVIDED"
    REMOTE_IMPORTED = "REMOTE_IMPORTED"
    LOCAL_TABLE = "LOCAL_TABLE"
    QUERY_DEFINED = "QUERY_DEFINED"
    STRUCTURE_ONLY = "STRUCTURE_ONLY"


class IdentityProvenance(str, Enum):
    PROJECT_ASSIGNED = "PROJECT_ASSIGNED"
    SOURCE_DERIVED_IMPORTED = "SOURCE_DERIVED_IMPORTED"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class Certainty(str, Enum):
    PROVEN = "PROVEN"
    HEURISTIC = "HEURISTIC"


class IdentityRule(str, Enum):
    ID_SYSTEM_NAMESPACE = "ID_SYSTEM_NAMESPACE"
    ID_SOURCE_EXACT = "ID_SOURCE_EXACT"
    ID_SOURCE_NAME_EXTENDS_STEM = "ID_SOURCE_NAME_EXTENDS_STEM"
    ID_SOURCE_NAME_ABBREVIATES_STEM = "ID_SOURCE_NAME_ABBREVIATES_STEM"
    ID_PROJECT_AUTHORED_QUERY = "ID_PROJECT_AUTHORED_QUERY"
    ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE = "ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE"
    ID_UNKNOWN_LOCAL_TABLE = "ID_UNKNOWN_LOCAL_TABLE"
    ID_UNKNOWN_STRUCTURE_ONLY = "ID_UNKNOWN_STRUCTURE_ONLY"
    ID_UNKNOWN_EMPTY_REMOTE_STEM = "ID_UNKNOWN_EMPTY_REMOTE_STEM"


@dataclass(frozen=True)
class IdentityClassification:
    artifact: str
    implementation: ImplementationProvenance
    identity: IdentityProvenance
    rule: IdentityRule
    certainty: Certainty | None            # None cuando la identidad es UNKNOWN
    remote_stem: str | None
    evidence: tuple[EvidenceRef, ...]
    ruleset_version: str = IDENTITY_RULESET


# ── stems y tokens ──────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[0-9A-Za-z]+")


def remote_stem(remote_entity: str) -> str:
    """Identificador de objeto de un `remote_entity` ("FAMILIA.objeto$sufijo").

    `ABAP_CDS.IFICOSTCENTER$P` -> `IFICOSTCENTER`; `SAPI.0COORDER_ATTR` -> `0COORDER_ATTR`;
    `ABAPTABLES./BIC/AZA_X` -> `AZA_X`; `"dbo"."GP_Clientes"` -> `GP_Clientes`.
    """
    s = str(remote_entity or "").strip()
    if "." in s:
        s = s.split(".", 1)[1]
    s = s.split("$", 1)[0]
    s = s.rsplit("/", 1)[-1]
    return s.strip().strip("\"'").strip()


def name_tokens(text: str) -> tuple[str, ...]:
    """Tokens alfanumericos en mayuscula (el separador no importa: `_`, `-`, espacio...)."""
    return tuple(t.upper() for t in _TOKEN_RE.findall(str(text or "")))


def _is_ordered_subsequence(small: tuple[str, ...], big: tuple[str, ...]) -> bool:
    it = iter(big)
    return all(tok in it for tok in small)


# ── clasificacion ──────────────────────────────────────────────────────────
def _is_system_name(name: str) -> bool:
    return name == SYSTEM_NAMESPACE or name.startswith(SYSTEM_NAMESPACE + ".")


def implementation_of(entity: Any) -> ImplementationProvenance:
    name = str(getattr(entity, "name", "") or "")
    if _is_system_name(name):
        return ImplementationProvenance.SYSTEM_PROVIDED
    if getattr(entity, "remote_entity", ""):
        return ImplementationProvenance.REMOTE_IMPORTED
    if getattr(entity, "repository_type", "") == LOCAL_TABLE_REPOSITORY_TYPE:
        return ImplementationProvenance.LOCAL_TABLE
    if getattr(entity, "has_query", False):
        return ImplementationProvenance.QUERY_DEFINED
    return ImplementationProvenance.STRUCTURE_ONLY


def _ref(entity: Any, attr: str) -> EvidenceRef:
    return EvidenceRef(str(getattr(entity, "name", "")), attr, getattr(entity, "file", None) or None, Plane.MODELING)


def classify(entity: Any) -> IdentityClassification:
    name = str(getattr(entity, "name", "") or "")
    impl = implementation_of(entity)
    P, S, U, SYS = (IdentityProvenance.PROJECT_ASSIGNED, IdentityProvenance.SOURCE_DERIVED_IMPORTED,
                    IdentityProvenance.UNKNOWN, IdentityProvenance.SYSTEM)

    def done(identity, rule, certainty, stem, *attrs):
        return IdentityClassification(name, impl, identity, rule, certainty, stem,
                                      tuple(_ref(entity, a) for a in attrs))

    if impl is ImplementationProvenance.SYSTEM_PROVIDED:
        return done(SYS, IdentityRule.ID_SYSTEM_NAMESPACE, Certainty.PROVEN, None, "name")

    if impl is ImplementationProvenance.REMOTE_IMPORTED:
        stem = remote_stem(getattr(entity, "remote_entity", ""))
        s_tokens, n_tokens = name_tokens(stem), name_tokens(name)
        if not s_tokens:
            return done(U, IdentityRule.ID_UNKNOWN_EMPTY_REMOTE_STEM, None, stem or None, "remote_entity", "name")
        if n_tokens == s_tokens:
            return done(S, IdentityRule.ID_SOURCE_EXACT, Certainty.PROVEN, stem, "name", "remote_entity")
        if len(n_tokens) > len(s_tokens) and _is_ordered_subsequence(s_tokens, n_tokens):
            return done(S, IdentityRule.ID_SOURCE_NAME_EXTENDS_STEM, Certainty.HEURISTIC, stem, "name", "remote_entity")
        if len(n_tokens) < len(s_tokens) and _is_ordered_subsequence(n_tokens, s_tokens):
            return done(S, IdentityRule.ID_SOURCE_NAME_ABBREVIATES_STEM, Certainty.HEURISTIC, stem,
                        "name", "remote_entity")
        return done(U, IdentityRule.ID_UNKNOWN_REMOTE_NAME_NOT_DERIVABLE, None, stem, "name", "remote_entity")

    if impl is ImplementationProvenance.QUERY_DEFINED:
        return done(P, IdentityRule.ID_PROJECT_AUTHORED_QUERY, Certainty.PROVEN, None, "has_query", "repository_type")

    if impl is ImplementationProvenance.LOCAL_TABLE:
        return done(U, IdentityRule.ID_UNKNOWN_LOCAL_TABLE, None, None, "repository_type", "remote_entity")

    return done(U, IdentityRule.ID_UNKNOWN_STRUCTURE_ONLY, None, None, "remote_entity", "has_query", "repository_type")


def classify_universe(entities: Mapping[str, Any]) -> dict[str, IdentityClassification]:
    """Clasifica la POBLACION DE MODELING (orden determinista por nombre). Los targets de Data Flow son
    contexto de Processing y quedan fuera de la poblacion de identidad."""
    return {n: classify(e) for n, e in sorted(modeling_population(entities).items())}


def identity_distribution(classifications: Iterable[IdentityClassification]) -> dict[str, int]:
    """Conteo por IdentityProvenance (todas las claves presentes, con 0 si no hay)."""
    c = Counter(x.identity for x in classifications)
    return {i.value: c.get(i, 0) for i in IdentityProvenance}


def heuristic_distribution(classifications: Iterable[IdentityClassification]) -> dict[str, int]:
    """Cuantas clasificaciones de cada identidad descansan en una regla HEURISTICA (no probada)."""
    c = Counter(x.identity for x in classifications if x.certainty is Certainty.HEURISTIC)
    return {i.value: c.get(i, 0) for i in IdentityProvenance}


def implementation_distribution(classifications: Iterable[IdentityClassification]) -> dict[str, int]:
    c = Counter(x.implementation for x in classifications)
    return {i.value: c.get(i, 0) for i in ImplementationProvenance}
