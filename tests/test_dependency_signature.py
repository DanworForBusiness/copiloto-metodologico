"""P1 · granularidad de la vigencia: la firma de DEPENDENCIAS de un AM cubre solo lo que su assessment puede leer.

Se prueba contra el MOTOR REAL (sin dobles): para cada cambio en el pool, un AM cuya firma de dependencias NO cambió tiene un perfil idéntico (salvo las anclas a la firma del pool), y un AM
cuya firma cambió es exactamente el que debe regenerarse. La firma es una cota superior conservadora: puede marcar de más, nunca de menos.
"""
from __future__ import annotations

import json

import pytest

from tests.publication_fakes import CLIENT, SPACE, analytic_model, local_table, replication_flow, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog.modeling_assessment import dependency_signature as dsig
from tools.catalog.modeling_assessment import shadow as ma_shadow

AM_A, AM_B = "AM_Lista_Precios", "AM_Otro"


def seed_third(t):
    """Tercera cadena SIN nada en común con las otras dos."""
    t.put(SPACE, "local-tables", "RAW_TER", local_table("RAW_TER"))
    t.put(SPACE, "views", "3VF_TER", view("3VF_TER", ["RAW_TER"]))
    t.put(SPACE, "analytic-models", "AM_Tercero", analytic_model("AM_Tercero", "3VF_TER", [], label="Modelo Analítico - Tercero"))


def signatures(ctx):
    key, group = ma_shadow.select_pool(ctx.pool, CLIENT)
    entities, universe, _ = ma_shadow.semantic_universe(ma_shadow.load_pool(group), group)
    return dsig.dependency_signatures(ctx.pool, group, entities, universe)


def normalized_profile(ctx, am):
    doc, _meta = ma_shadow.assess(ctx.pool, CLIENT, am, "General")

    def scrub(node):
        if isinstance(node, dict):
            return {k: (None if k == "pool_signature" else scrub(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return node
    return json.dumps(scrub(doc), sort_keys=True, ensure_ascii=False)


@pytest.fixture()
def two(ctx):                                                                                      # noqa: F811
    assert ctx.publish(AM_A)[0].ok and ctx.publish(AM_B)[0].ok
    return ctx


def test_every_terminal_am_gets_a_signature_and_it_is_deterministic(two):
    first, again = signatures(two), signatures(two)
    assert sorted(first) == [AM_A, AM_B] and all(isinstance(v, str) and len(v) == 64 for v in first.values())
    assert first == again and first[AM_A] != first[AM_B]


def test_an_unrelated_chain_changes_nobody_elses_signature_nor_profile(two):
    before = signatures(two)
    profiles = {am: normalized_profile(two, am) for am in (AM_A, AM_B)}
    seed_third(two.tenant)
    assert two.publish("AM_Tercero")[0].ok
    after = signatures(two)
    assert after[AM_A] == before[AM_A] and after[AM_B] == before[AM_B] and "AM_Tercero" in after
    for am in (AM_A, AM_B):                                                                       # independencia DEMOSTRADA con el motor real: mismo perfil
        assert normalized_profile(two, am) == profiles[am], am


def test_changing_an_object_only_one_am_depends_on_changes_only_that_am(two):
    before = signatures(two)
    profile_a = normalized_profile(two, AM_A)
    two.tenant.put(SPACE, "views", "3VF_OT", view("3VF_OT", ["RAW_OT"], cols=("A", "B", "C_NUEVA")))
    assert two.publish(AM_B)[0].ok
    after = signatures(two)
    assert after[AM_B] != before[AM_B] and after[AM_A] == before[AM_A]
    assert normalized_profile(two, AM_A) == profile_a


def test_changing_a_shared_dimension_affects_every_am_that_contains_it(two):
    before = signatures(two)
    two.tenant.put(SPACE, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"], cols=("A", "B", "D_NUEVA")))
    assert two.publish(AM_A)[0].ok
    after = signatures(two)
    assert after[AM_A] != before[AM_A] and after[AM_B] != before[AM_B]


def test_a_new_flow_that_mentions_a_closure_object_affects_that_am_even_without_touching_its_files(two):
    before = signatures(two)
    # el flujo no es una raíz publicable: se coloca directo en el pool (lo que dejaría una extracción que lo alcance)
    flow = two.pool / CLIENT / "replication-flows" / "RF_OT.json"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text(json.dumps(replication_flow("RF_OT", "RAW_OT")), encoding="utf-8")
    after = signatures(two)
    assert after[AM_B] != before[AM_B] and after[AM_A] == before[AM_A]


def test_removing_an_unrelated_object_changes_nothing(two):
    seed_third(two.tenant)
    assert two.publish("AM_Tercero")[0].ok
    before = signatures(two)
    for rel in ("analytic-models/AM_Tercero", "views/3VF_TER", "local-tables/RAW_TER"):
        (two.pool / CLIENT / f"{rel}.json").unlink()
    after = signatures(two)
    assert "AM_Tercero" not in after and after[AM_A] == before[AM_A] and after[AM_B] == before[AM_B]


def test_a_missing_closure_object_is_part_of_the_signature_so_its_appearance_is_detected(two):
    target = two.pool / CLIENT / "local-tables" / "RAW_OT.json"
    saved = target.read_bytes()
    target.unlink()
    without = signatures(two)
    target.write_bytes(saved)
    assert signatures(two)[AM_B] != without.get(AM_B)


def test_an_unreadable_file_makes_every_signature_uncertain(two, monkeypatch):
    real = dsig.bridge.file_sha256
    monkeypatch.setattr(dsig.bridge, "file_sha256", lambda p: (_ for _ in ()).throw(OSError("denegado")) if p.name == "RAW_OT.json" else real(p))
    assert signatures(two) == {AM_A: None, AM_B: None}                                            # incierto => el llamador cae a la firma del pool (STALE explícito)


def test_an_entity_file_that_is_not_unique_is_uncertain_for_the_ams_that_contain_it(two):
    dup = two.pool / CLIENT / "views" / "RAW_OT.json"                                              # mismo nombre de archivo en otra carpeta del pool
    dup.write_text(json.dumps(local_table("RAW_OT")), encoding="utf-8")
    sigs = signatures(two)
    assert sigs[AM_B] is None and isinstance(sigs[AM_A], str)
