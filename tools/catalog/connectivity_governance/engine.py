"""Motor PURO de evaluación de conectividad (`connectivity-rules/v1`, PoC F1).

Sin I/O, sin red, sin persistencia, sin UI. Demuestra las 5 capacidades pedidas por el owner:
  1. Comparar configuraciones observadas.                      -> compare_configurations()
  2. Reconocer equivalencia funcional con asignación lógica.    -> evaluate(): logical_system_id + CONFORME
  3. Detectar divergencias sin calificarlas de incumplimiento.  -> evaluate(): Conformance.DIVERGENTE (nunca NO_CONFORME sin contrato)
  4. Evaluar conformidad sólo frente a homologación explícita.  -> evaluate(): NO_CONFORME sólo con perfil esperado ACTIVE
  5. Representar por separado disponibilidad operativa y         -> evaluate(): ejes Operational y Coverage independientes
     suficiencia de evidencia.

Regla dura (sección 3.1): sin contrato, el techo de conformidad es DIVERGENTE; JAMÁS NO_CONFORME.
Los tres ejes se concluyen por separado; no se promedian ni se funden en un score.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from .contract import HomologationContract
from .model import (
    ConnectivityVerdict,
    Conformance,
    Coverage,
    Divergence,
    ObservedConnection,
    Operational,
)


# ── Eje 3.2: estado operativo ────────────────────────────────────────────────
def _operational(conn: ObservedConnection) -> Operational:
    status = (conn.agent.status or "").upper()
    if status == "CONNECTED":
        return Operational.CONECTADO
    if status == "DISCONNECTED":
        return Operational.DESCONECTADO
    return Operational.DESCONOCIDO


# ── Capacidad 1: comparar configuraciones observadas ─────────────────────────
def compare_configurations(connections: Iterable[ObservedConnection]) -> list[Divergence]:
    """Agrupa por identidad de sistema FÍSICO observada (no por nombre) y reporta divergencias de perfil
    entre entornos. Es SEÑAL observada, no un juicio de conformidad (capacidad 1 + base de la 3)."""
    groups: dict[tuple, list[ObservedConnection]] = defaultdict(list)
    for c in connections:
        if c.physical.observable:
            groups[c.physical.key].append(c)

    divergences: list[Divergence] = []
    for key, members in groups.items():
        profiles = {m.agent.profile for m in members}
        envs = {m.identity.environment for m in members}
        # Divergencia = mismo sistema físico, más de un perfil de conectividad observado.
        if len(members) > 1 and len(profiles) > 1:
            divergences.append(Divergence(
                physical_key=key,
                environments=tuple(sorted(envs)),
                connection_names=tuple(sorted({m.identity.connection_name for m in members})),
                kind="agent",
                detail="mismo sistema físico con perfil de conectividad (agente) distinto entre entornos",
            ))
    return divergences


def _divergence_for(conn: ObservedConnection, all_conns: list[ObservedConnection]) -> Optional[Divergence]:
    """La divergencia (si existe) en la que participa ESTA conexión."""
    if not conn.physical.observable:
        return None
    for d in compare_configurations(all_conns):
        if d.physical_key == conn.physical.key and conn.identity.connection_name in d.connection_names:
            return d
    return None


def _shares_physical_across_env(conn: ObservedConnection, all_conns: list[ObservedConnection]) -> bool:
    """¿Otra conexión de OTRO entorno apunta al mismo sistema físico observado?"""
    if not conn.physical.observable:
        return False
    for c in all_conns:
        if (c is not conn and c.physical.observable and c.physical.key == conn.physical.key
                and c.identity.environment != conn.identity.environment):
            return True
    return False


def _profile_matches(conn: ObservedConnection, expected) -> bool:
    if expected.expected_agent is not None and (conn.agent.agent_name or "") != expected.expected_agent:
        return False
    if expected.expected_agent_group is not None and (conn.agent.agent_group or "") != expected.expected_agent_group:
        return False
    if expected.expected_physical is not None and conn.physical.key[:2] != tuple(expected.expected_physical)[:2]:
        # compara (host, sysid) si el contrato declara un físico esperado
        return False
    return True


# ── Eje 3.1: conformidad arquitectónica ──────────────────────────────────────
def _conformance(conn, all_conns, contract, divergence):
    expected = contract.expected_for(conn) if contract else None
    if expected is not None:                      # contrato ACTIVE con perfil esperado homologado
        if _profile_matches(conn, expected):
            return Conformance.CONFORME, None, "lo observado coincide con el perfil homologado"
        exc = contract.active_exception_for(conn)
        if exc:
            # Contradice el perfil, pero hay excepción gobernada vigente → se presenta como excepción.
            return Conformance.NO_CONFORME, f"{exc.rationale} (aprob. {exc.approver}, revisar antes de {exc.review_by})", \
                "contradice el perfil homologado pero tiene excepción gobernada vigente"
        return Conformance.NO_CONFORME, None, "lo observado contradice el perfil homologado (sin excepción vigente)"
    # Sin perfil esperado (sin contrato, o el contrato no fija perfil para este miembro):
    if divergence is not None:
        return Conformance.DIVERGENTE, None, "divergencia observada sin perfil homologado: pendiente de revisión"
    return Conformance.NO_EVALUABLE, None, "sin contrato ni divergencia observable: sin base para juzgar conformidad"


# ── Eje 3.3: cobertura y confianza de evidencia ──────────────────────────────
def _evidence(conn, all_conns, logical, operational) -> Coverage:
    # Si la conclusión relevante es la caída operativa, está plenamente observada.
    if operational == Operational.DESCONECTADO:
        return Coverage.COMPLETA
    # Conectado/desconocido pero sin destino observable → no se puede concluir destino/equivalencia.
    if not conn.physical.observable:
        return Coverage.INSUFICIENTE
    # Destino observable: la equivalencia se puede fundamentar si hay asignación lógica gobernada
    # o si se observa el MISMO sistema físico en otro entorno.
    groundable = (logical is not None) or _shares_physical_across_env(conn, all_conns)
    return Coverage.COMPLETA if groundable else Coverage.PARCIAL


# ── Etiquetas de presentación (derivadas de los tres ejes — sección 3.4) ─────
def _labels(conformance, operational, evidence, governed_exception) -> tuple:
    labels: list[str] = []
    if conformance == Conformance.CONFORME:
        labels.append("Homologada / equivalente")
    elif conformance == Conformance.DIVERGENTE:
        labels.append("Diferente — requiere revisión")
    elif conformance == Conformance.NO_CONFORME:
        labels.append("Excepción gobernada" if governed_exception else "No homologada")
    else:
        labels.append("No evaluable")
    if operational == Operational.DESCONECTADO:
        labels.append("Agente desconectado")
    if evidence == Coverage.INSUFICIENTE:
        labels.append("Evidencia insuficiente")
    return tuple(labels)


def evaluate(conn: ObservedConnection,
             all_connections: Iterable[ObservedConnection],
             contract: Optional[HomologationContract] = None) -> ConnectivityVerdict:
    """Evalúa UNA conexión en los tres ejes ortogonales, con o sin contrato de homologación."""
    all_conns = list(all_connections)
    operational = _operational(conn)
    logical = contract.logical_of(conn) if contract else None
    divergence = _divergence_for(conn, all_conns)
    conformance, governed_exc, conf_rat = _conformance(conn, all_conns, contract, divergence)
    evidence = _evidence(conn, all_conns, logical, operational)
    labels = _labels(conformance, operational, evidence, governed_exc)
    return ConnectivityVerdict(
        connection_key=conn.identity.key,
        conformance=conformance,
        operational=operational,
        evidence=evidence,
        logical_system_id=logical,
        divergence=divergence,
        governed_exception=governed_exc,
        rationale={
            "conformance": conf_rat,
            "operational": f"agentStatus={conn.agent.status!r}",
            "evidence": f"destino_observable={conn.physical.observable}, logico={logical!r}",
        },
        labels=labels,
    )


def evaluate_all(connections: Iterable[ObservedConnection],
                 contract: Optional[HomologationContract] = None) -> list[ConnectivityVerdict]:
    conns = list(connections)
    return [evaluate(c, conns, contract) for c in conns]
