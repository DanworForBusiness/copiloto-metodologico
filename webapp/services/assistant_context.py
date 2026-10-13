"""Contexto conversacional ESTRUCTURADO del asistente (paso 3): qué consulta estructurada se hizo por última vez en una conversación, para resolver seguimientos («¿y los de Finanzas?», «los siguientes»,
«abrí el segundo») sin convertir el historial de texto en fuente de verdad.

Qué guarda: la consulta (`QuerySpec`: capacidad, cliente, línea de negocio efectiva, indicador, orden, tamaño de página y desplazamiento), las IDENTIDADES `(kind, ref)` de lo efectivamente mostrado y en
su orden, la identidad del usuario, el instante y una HUELLA del resultado completo. Qué NO guarda: URLs, valores de indicadores, títulos ni copias del Catálogo; todo eso se vuelve a calcular desde las
fuentes gobernadas vigentes cada vez. La huella (`fingerprint`) no es una autoridad sino un VALIDADOR: se compara contra el resultado recalculado y, si difiere, las referencias posicionales se invalidan.

Módulo puro (sin dependencias del resto de la webapp): vive en memoria dentro del `AssistantConversationStore`, con el mismo alcance y ciclo de vida que la conversación (sin persistencia nueva).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

TTL_SECONDS = 15 * 60                      # el contexto vence a los 15 minutos desde su última actualización
LIVE, MISSING, EXPIRED = "live", "missing", "expired"


@dataclass(frozen=True)
class QuerySpec:
    """La consulta estructurada ejecutada (nada de valores ni resultados)."""
    capability: str                 # "summary" | "list" | "rank" | "pending" (aclaración de métrica pendiente)
    client: str | None = None       # cliente del Catálogo (nombre canónico)
    lob: str | None = None          # id de línea de negocio EFECTIVA o "UNCLASSIFIED"
    indicator: str | None = None    # id del registro de indicadores
    order: str = "best_first"       # "best_first" | "worst_first"
    limit: int = 10                 # tamaño de página (≤ 10)
    offset: int = 0                 # desplazamiento de la página mostrada


@dataclass(frozen=True)
class QueryDraft:
    """Lo que una consulta produce para el contexto, antes de asociarlo a un usuario y a un instante (eso lo hace el borde HTTP)."""
    spec: QuerySpec
    refs: tuple[tuple[str, str], ...] = ()      # identidades `(kind, ref)` de las filas mostradas, en orden
    fingerprint: str = ""                       # huella del conjunto ORDENADO completo que originó la lista (todas las páginas)


@dataclass(frozen=True)
class QueryState:
    spec: QuerySpec
    refs: tuple[tuple[str, str], ...]
    fingerprint: str
    user_sub: str
    updated_at: float


@dataclass(frozen=True)
class QueryContext:
    """Lo que el router necesita para resolver un seguimiento: el estado de ESTA conversación, el usuario actual y el instante."""
    state: QueryState | None
    user_sub: str
    now: float

    def status(self) -> str:
        """LIVE si hay estado de ESTE usuario dentro del TTL; EXPIRED si es de este usuario pero venció; MISSING en cualquier otro caso (sin estado o de otro usuario)."""
        s = self.state
        if s is None or not self.user_sub or s.user_sub != self.user_sub:
            return MISSING
        return LIVE if 0 <= self.now - s.updated_at <= TTL_SECONDS else EXPIRED

    def live_state(self) -> QueryState | None:
        return self.state if self.status() == LIVE else None


def stamp(draft: QueryDraft, user_sub: str, now: float) -> QueryState:
    return QueryState(draft.spec, draft.refs, draft.fingerprint, user_sub, now)


def touch(state: QueryState, now: float) -> QueryState:
    return replace(state, updated_at=now)


def fingerprint(spec_key: tuple, rows: list[tuple]) -> str:
    """Huella del conjunto ORDENADO que originó una lista: la consulta (filtros, indicador, orden) más, por cada entrada y en orden, su identidad y los atributos que determinan su pertenencia y su
    posición (valor del indicador, clasificación efectiva, cliente…). Cambia si cambia el universo, el orden, un filtro o una métrica; no basta con un hash de los `entry_id`."""
    h = hashlib.sha256(repr(spec_key).encode("utf-8"))
    for row in rows:
        h.update(b"\x1f" + repr(row).encode("utf-8"))
    return h.hexdigest()[:20]
