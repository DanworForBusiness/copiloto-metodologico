"""PoC F1 del motor de gobierno de conectividad (`connectivity-rules/v1`).

Contrato: docs/CONNECTIVITY_GOVERNANCE_F0.md (`connectivity-governance/v1`, aprobado 2026-10-30).
PoC PURA sobre snapshots existentes (fixtures SINTÉTICAS derivadas de patrones observados (no copias literales de infraestructura de ningún cliente)): sin OAuth,
sin red, sin persistencia, sin UI.

Demuestra las 5 capacidades pedidas por el owner:
  1. Comparar configuraciones observadas.
  2. Reconocer equivalencia funcional cuando existe una asignación lógica gobernada.
  3. Detectar divergencias SIN calificarlas automáticamente como incumplimientos.
  4. Evaluar conformidad SÓLO frente a una homologación explícita.
  5. Representar por separado disponibilidad operativa y suficiencia de evidencia.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.catalog.connectivity_governance import (
    Conformance,
    Coverage,
    ExpectedMember,
    GovernedException,
    HomologationContract,
    LogicalSystem,
    Operational,
    compare_configurations,
    evaluate,
    evaluate_all,
    from_snapshot,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "connectivity_governance"

# `environment` es un ATRIBUTO GOBERNADO: lo provee este mapa (el registro), NO se deriva del nombre del
# espacio. DESARROLLO→DEV, CALIDAD→QAS, en el mismo tenant Datasphere (sintético).
GOVERNED_SPACE_MAP = {
    "DESARROLLO": ("tenant-sample", "DEV"),
    "CALIDAD": ("tenant-sample", "QAS"),
}


def _load(space: str, name: str):
    doc = json.loads((FIXTURES / f"{space}__{name}.json").read_text(encoding="utf-8"))
    tenant, env = GOVERNED_SPACE_MAP[space]
    return from_snapshot(doc, tenant=tenant, environment=env)


@pytest.fixture(scope="module")
def connections():
    return [
        _load("DESARROLLO", "BW_DS"), _load("CALIDAD", "BW_DS"),
        _load("DESARROLLO", "S4ABAP"), _load("CALIDAD", "S4ABAP"),
        _load("DESARROLLO", "S4PREM"), _load("CALIDAD", "S4PREM"),
        _load("CALIDAD", "PostgreSQL"),
        _load("CALIDAD", "SQL_SERVER_2019_onpremise"),
    ]


def _by(verdicts):
    """Index: (environment, connection_name) -> verdict."""
    return {(v.connection_key[1], v.connection_key[3]): v for v in verdicts}


# ── Capacidad 1: comparar configuraciones observadas ─────────────────────────
def test_cap1_compare_observed_configurations(connections):
    divergences = compare_configurations(connections)
    # BW_DS apunta al MISMO sistema físico en DEV y QA pero con agente distinto → una divergencia.
    assert len(divergences) == 1
    d = divergences[0]
    assert d.connection_names == ("BW_DS",)
    assert set(d.environments) == {"DEV", "QAS"}
    assert d.kind == "agent"
    # La divergencia se agrupa por sistema FÍSICO observado (host/sysid/...), no por nombre.
    assert d.physical_key[0] == "bw-host.invalid" and d.physical_key[1] == "BWX"


# ── Capacidad 3: detectar divergencias SIN llamarlas incumplimiento (sin contrato) ──
def test_cap3_divergence_is_never_noncompliance_without_contract(connections):
    verdicts = _by(evaluate_all(connections, contract=None))
    # BW_DS: divergencia observada → DIVERGENTE (pendiente), JAMÁS NO_CONFORME.
    for env in ("DEV", "QAS"):
        v = verdicts[(env, "BW_DS")]
        assert v.conformance == Conformance.DIVERGENTE
        assert "Diferente — requiere revisión" in v.labels
    # Regla dura: SIN contrato, ninguna conexión puede quedar NO_CONFORME.
    assert all(v.conformance != Conformance.NO_CONFORME for v in verdicts.values())


def test_cap3_no_logical_assignment_is_not_evaluable(connections):
    verdicts = _by(evaluate_all(connections, contract=None))
    # S4ABAP/S4PREM apuntan a sistemas físicos DISTINTOS entre DEV y QA: sin asignación lógica no se afirma
    # que sean la misma familia → conformidad NO_EVALUABLE (no se inventa equivalencia por nombre).
    for name in ("S4ABAP", "S4PREM"):
        for env in ("DEV", "QAS"):
            v = verdicts[(env, name)]
            assert v.conformance == Conformance.NO_EVALUABLE
            assert v.logical_system_id is None


# ── Capacidad 5: operativo y evidencia, ejes independientes ──────────────────
def test_cap5_operational_and_evidence_are_independent_axes(connections):
    verdicts = _by(evaluate_all(connections, contract=None))
    # PostgreSQL (QA): DESCONECTADO (operativo) — independiente de su conformidad (NO_EVALUABLE).
    pg = verdicts[("QAS", "PostgreSQL")]
    assert pg.operational == Operational.DESCONECTADO
    assert pg.conformance == Conformance.NO_EVALUABLE
    assert pg.evidence == Coverage.COMPLETA            # la caída está plenamente observada
    assert "Agente desconectado" in pg.labels
    # SQL_SERVER (QA): CONECTADO pero sin destino observable → EVIDENCIA INSUFICIENTE.
    sql = verdicts[("QAS", "SQL_SERVER_2019_onpremise")]
    assert sql.operational == Operational.CONECTADO
    assert sql.evidence == Coverage.INSUFICIENTE
    assert "Evidencia insuficiente" in sql.labels
    # BW_DS: destino observable + mismo físico en otro entorno → evidencia COMPLETA.
    assert verdicts[("DEV", "BW_DS")].evidence == Coverage.COMPLETA
    # S4ABAP sin contrato: destino observable pero sin equivalencia fundamentable → PARCIAL.
    assert verdicts[("DEV", "S4ABAP")].evidence == Coverage.PARCIAL


# ── Capacidades 2 y 4: equivalencia y conformidad SÓLO con homologación explícita ──
def _contract(as_of: str = "2026-10-30") -> HomologationContract:
    return HomologationContract(
        client="ClienteDemo",
        owner="responsable-gobierno@clientedemo",
        approved_at="2026-10-30",
        as_of=as_of,
        systems=(
            LogicalSystem(
                logical_system_id="S4_FAMILIA",
                description="S/4HANA (familia DEV/QAS, sintético)",
                members=(
                    ExpectedMember(environment="DEV", related_connections=("S4ABAP", "S4PREM"), expected_agent="AGENT_SHARED"),
                    ExpectedMember(environment="QAS", related_connections=("S4ABAP", "S4PREM"), expected_agent="AGENT_SHARED"),
                ),
            ),
            LogicalSystem(
                logical_system_id="BW_FAMILIA",
                description="BW/4 (sintético)",
                members=(
                    ExpectedMember(environment="DEV", related_connections=("BW_DS",), expected_agent="AGENT_SHARED"),
                    ExpectedMember(environment="QAS", related_connections=("BW_DS",), expected_agent="AGENT_SHARED"),
                ),
                # El agente AGENT_LOCAL de BW_DS en DEV está autorizado por excepción vigente.
                exceptions=(
                    GovernedException(environment="DEV", connection_name="BW_DS",
                                      rationale="agente AGENT_LOCAL heredado, migración planificada",
                                      approver="responsable-gobierno@clientedemo", review_by="2027-06-30"),
                ),
            ),
        ),
    )


def test_cap2_functional_equivalence_only_with_governed_logical_assignment(connections):
    verdicts = _by(evaluate_all(connections, contract=_contract()))
    # Con la asignación lógica gobernada, S4ABAP/S4PREM DEV y QA se reconocen como la MISMA familia
    # (equivalencia funcional) y, al coincidir con el perfil homologado, quedan CONFORME.
    for name in ("S4ABAP", "S4PREM"):
        for env in ("DEV", "QAS"):
            v = verdicts[(env, name)]
            assert v.logical_system_id == "S4_FAMILIA"
            assert v.conformance == Conformance.CONFORME
            assert v.evidence == Coverage.COMPLETA        # la asignación lógica fundamenta la equivalencia


def test_cap4_conformance_only_against_explicit_homologation(connections):
    verdicts = _by(evaluate_all(connections, contract=_contract()))
    # BW_DS QA: coincide con el perfil homologado (AGENT_SHARED) → CONFORME.
    assert verdicts[("QAS", "BW_DS")].conformance == Conformance.CONFORME
    # BW_DS DEV: contradice el perfil (AGENT_LOCAL != AGENT_SHARED) → NO_CONFORME, PERO con excepción gobernada
    # vigente → se presenta como «Excepción gobernada», no como «No homologada».
    dev = verdicts[("DEV", "BW_DS")]
    assert dev.conformance == Conformance.NO_CONFORME
    assert dev.governed_exception is not None
    # La excepción se anota PERO no oculta la no conformidad (invariante 2).
    assert "No homologada" in dev.labels
    assert "Excepción gobernada vigente" in dev.labels


def test_cap4_expired_exception_no_longer_shields(connections):
    # Misma contradicción, pero evaluada DESPUÉS de la caducidad de la excepción → deja de proteger.
    verdicts = _by(evaluate_all(connections, contract=_contract(as_of="2027-07-01")))
    dev = verdicts[("DEV", "BW_DS")]
    assert dev.conformance == Conformance.NO_CONFORME
    assert dev.governed_exception is None
    assert "No homologada" in dev.labels


# ── Invariante 1: equivalencia lógica != conformidad ─────────────────────────
def test_inv1_logical_equivalence_alone_is_not_conformance(connections):
    """Asignar S4ABAP a una familia (equivalencia) SIN perfil homologado explícito NO debe dar CONFORME."""
    contract = HomologationContract(client="ClienteDemo", as_of="2026-10-30", systems=(
        LogicalSystem("S4_FAMILIA", "S/4HANA", (
            ExpectedMember(environment="DEV", related_connections=("S4ABAP",)),   # SIN expected_agent → sólo familia
            ExpectedMember(environment="QAS", related_connections=("S4ABAP",)),
        )),
    ))
    verdicts = _by(evaluate_all(connections, contract=contract))
    for env in ("DEV", "QAS"):
        v = verdicts[(env, "S4ABAP")]
        assert v.logical_system_id == "S4_FAMILIA"        # la equivalencia SÍ se reconoce
        assert v.conformance != Conformance.CONFORME         # pero NO alcanza para conformidad
        assert v.conformance == Conformance.NO_EVALUABLE     # sin perfil explícito: no evaluable
        assert "Homologada / equivalente" not in v.labels


def test_inv1_expired_homologation_profile_is_not_conformance(connections):
    """Un perfil homologado VENCIDO (review_by < as_of) deja de ser aplicable: no produce CONFORME aunque coincida."""
    contract = HomologationContract(client="ClienteDemo", as_of="2026-10-30", systems=(
        LogicalSystem("S4_FAMILIA", "S/4HANA", (
            ExpectedMember(environment="DEV", related_connections=("S4ABAP",), expected_agent="AGENT_SHARED"),
            ExpectedMember(environment="QAS", related_connections=("S4ABAP",), expected_agent="AGENT_SHARED"),
        ), review_by="2025-01-01"),   # homologación vencida
    ))
    v = _by(evaluate_all(connections, contract=contract))[("DEV", "S4ABAP")]
    assert v.logical_system_id == "S4_FAMILIA"
    assert v.conformance != Conformance.CONFORME             # el perfil observado coincide, pero la homologación venció
    assert v.conformance == Conformance.NO_EVALUABLE


# ── Invariante 2: excepción != conformidad (vigente / vencida / ausente / no aplicable) ──
def _bw_contract(*, with_exception: bool, review_by: str = "2027-06-30",
                 exc_env: str = "DEV", exc_conn: str = "BW_DS", as_of: str = "2026-10-30"):
    exceptions = ()
    if with_exception:
        exceptions = (GovernedException(environment=exc_env, connection_name=exc_conn,
                                        rationale="desviación autorizada", approver="gob@bio", review_by=review_by),)
    return HomologationContract(client="ClienteDemo", as_of=as_of, systems=(
        LogicalSystem("BW_FAMILIA", "BW/4", (
            ExpectedMember(environment="DEV", related_connections=("BW_DS",), expected_agent="AGENT_SHARED"),
            ExpectedMember(environment="QAS", related_connections=("BW_DS",), expected_agent="AGENT_SHARED"),
        ), exceptions=exceptions),
    ))


@pytest.mark.parametrize("label,contract,expect_exc", [
    ("vigente",     _bw_contract(with_exception=True, review_by="2027-06-30"), True),
    ("vencida",     _bw_contract(with_exception=True, review_by="2025-01-01"), False),
    ("ausente",     _bw_contract(with_exception=False), False),
    ("no_aplicable", _bw_contract(with_exception=True, exc_env="QAS"), False),   # excepción para otro entorno
])
def test_inv2_exception_never_changes_technical_verdict(connections, label, contract, expect_exc):
    """BW_DS DEV (AGENT_LOCAL) contradice el perfil homologado (AGENT_SHARED): el veredicto TÉCNICO es NO_CONFORME
    en los cuatro estados de excepción. La excepción vigente sólo se ANOTA; nunca vuelve CONFORME."""
    dev = _by(evaluate_all(connections, contract=contract))[("DEV", "BW_DS")]
    assert dev.conformance == Conformance.NO_CONFORME            # invariante 2: nunca cambia por la excepción
    assert "No homologada" in dev.labels
    assert (dev.governed_exception is not None) == expect_exc
    if expect_exc:
        assert "Excepción gobernada vigente" in dev.labels
    else:
        assert "Excepción gobernada vigente" not in dev.labels


def test_identity_environment_is_governed_not_derived(connections):
    # `environment` viene del mapa gobernado, no del archivo: el snapshot sólo trae `space_name`.
    bw_dev = next(c for c in connections if c.identity.connection_name == "BW_DS" and c.identity.space == "DESARROLLO")
    assert bw_dev.identity.environment == "DEV"          # gobernado (DESARROLLO→DEV)
    assert bw_dev.identity.tenant == "tenant-sample" # identidad técnica estable
