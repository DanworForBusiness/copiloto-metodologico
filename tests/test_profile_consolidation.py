"""«Consolidar perfiles» (Capital Humano): dos perfiles del Banco de CVs que son la MISMA persona pasan a ser UNA identidad activa, con todos sus documentos y sin tocar los archivos.

Datos 100 % SINTÉTICOS bajo ``tmp_path`` (``config.BASE_DIR``): ningún perfil real, ningún SharePoint, ningún Spaces. Los repositorios, el servicio y la auditoría son los REALES.

Contrato del owner que fijan estas pruebas: permiso APP_OWNER (router) · bloqueo estricto ante relaciones del secundario · los archivos de SharePoint no se mueven · una consolidación parcial NUNCA
aparece como exitosa ni deja a los dos perfiles operables · la operación es recuperable (reanudar / revertir) y deja auditoría durable · idempotente · sin fusiones automáticas.
"""
from __future__ import annotations

import json
import threading

import pytest

import config
from core import remote_store
from webapp.services import (
    busqueda_repository, entrevista_repository, human_capital_service, onboarding_repository, profile_consolidation_service as pcs,
    profile_document_repository as docs, profile_repository as profiles,
)

P_A, P_B, P_C = "11111111-aaaa-4aaa-8aaa-111111111111", "22222222-bbbb-4bbb-8bbb-222222222222", "33333333-cccc-4ccc-8ccc-333333333333"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")


def mk_profile(pid, **over):
    base = {"first_name": "Julia", "last_name": "Sintetica", "email": "julia@example.test", "phone": "", "linkedin_url": "", "location": "CABA", "profile_skills": "SAP BTP",
            "seniority": "Senior", "source": "LinkedIn", "tags": [], "notes": "", "potential_rating": None, "potential_mark": None}
    profile, _ = profiles.create_with_explicit_id(pid, {**base, **over})
    return profile


def mk_doc(pid, n, **over):
    document = {"profile_id": pid, "provider": "sharepoint", "site_id": "site-1", "drive_id": "drive-1", "item_id": f"item-{pid[:4]}-{n}", "web_url": f"https://sp.example.test/{pid[:4]}/{n}.pdf",
                "filename": f"cv-{n}.pdf", "content_type": "application/pdf", "size_bytes": 1000 + n, "uploaded_at": f"2026-01-0{n}T10:00:00+00:00", "uploaded_by": "u@example.test",
                "folder_item_id": f"folder-{pid[:4]}", "import_origin": {"run": "hist-1", "key": f"k{n}"}, **over}
    created, _ = docs.create_with_explicit_id(f"doc-{pid[:4]}-{n}", document)
    return created


def two_profiles(**dup_over):
    mk_profile(P_A)
    mk_profile(P_B, **dup_over)
    mk_doc(P_A, 1)
    mk_doc(P_B, 2)


def consolidate(primary=P_A, duplicate=P_B, *, resolutions=None, actor="owner@example.test", confirmed=True, digest=None):
    plan = pcs.build_plan(primary, duplicate)
    return pcs.execute(primary, duplicate, resolutions=resolutions or {}, plan_digest=plan.digest if digest is None else digest, actor=actor, confirmed=confirmed)


def active_ids():
    return sorted(p["profile_id"] for p in human_capital_service.list_profiles())


def doc_ids(pid):
    return sorted(d["document_id"] for d in docs.list_for_profile(pid))


# ═══════════════ el caso base: dos perfiles, un CV cada uno ═══════════════
def test_two_profiles_with_one_cv_each_become_one_active_identity_with_both_cvs():
    two_profiles()
    before_files = sorted(p.name for p in (config.BASE_DIR / "human-capital" / "profiles").iterdir())
    out = consolidate()
    assert out.status == pcs.COMPLETED, out
    assert active_ids() == [P_A]                                                                    # una única identidad activa…
    assert doc_ids(P_A) == ["doc-1111-1", "doc-2222-2"] and doc_ids(P_B) == []                      # …con los dos CVs en la ficha del principal
    assert sorted(p.name for p in (config.BASE_DIR / "human-capital" / "profiles").iterdir()) == before_files        # NO se creó ningún perfil nuevo
    primary, duplicate = profiles.get(P_A), profiles.get(P_B)
    assert primary["profile_id"] == P_A and primary["consolidation"]["state"] == pcs.COMPLETED and primary["consolidated_from"][0]["profile_id"] == P_B
    assert duplicate["deleted_at"] and duplicate["consolidated_into"] == P_A and duplicate["profile_id"] == P_B     # baja LÓGICA: el registro sigue ahí
    assert human_capital_service.get_profile(P_A) is not None


def test_two_profiles_with_several_cvs_each_keep_every_document():
    mk_profile(P_A)
    mk_profile(P_B)
    for n in (1, 2, 3):
        mk_doc(P_A, n)
    for n in (4, 5):
        mk_doc(P_B, n)
    assert consolidate().status == pcs.COMPLETED
    assert doc_ids(P_A) == ["doc-1111-1", "doc-1111-2", "doc-1111-3", "doc-2222-4", "doc-2222-5"] and doc_ids(P_B) == []


def test_documents_keep_every_sharepoint_identifier_and_only_gain_the_provenance_mark():
    two_profiles()
    originals = {d["document_id"]: d for d in docs.list_for_profile(P_B)}
    assert consolidate().status == pcs.COMPLETED
    for document in docs.list_for_profile(P_A):
        if document["document_id"] not in originals:
            continue
        before = originals[document["document_id"]]
        assert {k: v for k, v in document.items() if k not in ("profile_id", "consolidation")} == {k: v for k, v in before.items() if k != "profile_id"}      # NADA más cambió
        assert document["profile_id"] == P_A and document["consolidation"]["from_profile_id"] == P_B
        for sharepoint_key in ("site_id", "drive_id", "item_id", "web_url", "folder_item_id", "import_origin"):
            assert document[sharepoint_key] == before[sharepoint_key]                                  # los identificadores Graph y los enlaces originales


# ═══════════════ conflictos entre atributos ═══════════════
def test_conflicting_attributes_require_an_explicit_choice_and_the_choice_is_applied():
    two_profiles(email="otra@example.test", location="Rosario", seniority="Semi Senior", notes="nota del duplicado")
    plan = pcs.build_plan(P_A, P_B)
    assert {c["field"] for c in plan.conflicts} >= {"email", "location", "seniority"}
    assert [f["field"] for f in plan.fills] == ["notes"]                                           # el principal estaba vacío: el duplicado aporta el dato
    out = consolidate()                                                                             # sin resolver
    assert out.status == pcs.INVALID and out.code == "UNRESOLVED_CONFLICT" and profiles.get(P_B).get("deleted_at") is None
    choice = {c["field"]: (P_B if c["field"] == "location" else P_A) for c in plan.conflicts}
    assert consolidate(resolutions=choice).status == pcs.COMPLETED
    merged = profiles.get(P_A)
    assert merged["location"] == "Rosario" and merged["seniority"] == "Senior" and merged["notes"] == "nota del duplicado"        # elección explícita + relleno


def test_resolutions_for_non_conflicting_fields_or_for_unknown_profiles_are_rejected():
    two_profiles(location="Rosario")
    plan = pcs.build_plan(P_A, P_B)
    base = {c["field"]: P_A for c in plan.conflicts}
    assert consolidate(resolutions={**base, "first_name": P_A}).code == "UNRESOLVED_CONFLICT"
    assert consolidate(resolutions={**base, "location": P_C}).code == "UNRESOLVED_CONFLICT"


def test_similar_names_alone_never_consolidate_anything():
    two_profiles()
    assert human_capital_service.find_duplicate(profiles.get(P_B), [profiles.get(P_A)]) is not None       # el sistema SUGIERE el duplicado…
    assert profiles.get(P_B).get("deleted_at") is None and not pcs.list_operations()               # …pero no fusiona nada por sí solo
    assert consolidate(confirmed=False).code == "CONFIRMATION_REQUIRED" and profiles.get(P_B).get("deleted_at") is None


# ═══════════════ referencias externas y dependencias no migrables ═══════════════
def _busqueda_with(pid):
    b = busqueda_repository.create({"nombre_puesto": "Analista", "fecha_apertura": "2026-01-01", "area_departamento": "Consultoria"}, actor="x")
    busqueda_repository.add_candidate(b["busqueda_id"], pid, actor="x")
    return b["busqueda_id"]


def test_a_duplicate_with_an_application_is_blocked_strictly_and_nothing_is_written():
    two_profiles()
    _busqueda_with(P_B)
    plan = pcs.build_plan(P_A, P_B)
    assert plan.status == pcs.BLOCKED and plan.blockers[0]["code"] == "DUPLICATE_HAS_RELATIONS" and plan.relations[P_B].counts()["busquedas"] == 1
    out = consolidate()
    assert out.status == pcs.BLOCKED and out.code == "DUPLICATE_HAS_RELATIONS"
    assert profiles.get(P_B).get("consolidation") is None and doc_ids(P_B) == ["doc-2222-2"] and not pcs.list_operations() and not pcs.audit_root().exists()


def test_a_duplicate_with_interviews_or_onboarding_is_blocked():
    two_profiles()
    entrevista_repository.create({}, profile_id=P_B, busqueda_id=None, application_id=None)
    assert pcs.build_plan(P_A, P_B).relations[P_B].counts()["entrevistas"] == 1 and pcs.build_plan(P_A, P_B).status == pcs.BLOCKED
    two = P_C
    mk_profile(two)
    onboarding_repository.create_if_absent(application_id="app-1", profile_id=two, busqueda_id="b-1")
    plan = pcs.build_plan(P_A, two)
    assert plan.status == pcs.BLOCKED and plan.relations[two].counts()["onboardings"] == 1


def test_relations_of_the_primary_do_not_block_and_stay_untouched():
    two_profiles()
    busqueda_id = _busqueda_with(P_A)
    assert consolidate().status == pcs.COMPLETED
    assert [c["profile_id"] for c in busqueda_repository.get(busqueda_id)["candidatos"]] == [P_A]


def test_relations_that_cannot_be_verified_block_instead_of_assuming_none(monkeypatch):
    two_profiles()
    monkeypatch.setattr(busqueda_repository, "list", lambda: (_ for _ in ()).throw(busqueda_repository.BusquedaStorageError("boom")))
    plan = pcs.build_plan(P_A, P_B)
    assert plan.status == pcs.BLOCKED and plan.blockers[0]["code"] == "RELATIONS_UNVERIFIABLE"
    assert consolidate().status == pcs.BLOCKED and profiles.get(P_B).get("deleted_at") is None


# ═══════════════ perfil eliminado, previamente consolidado, reintento ═══════════════
def test_a_deleted_or_missing_profile_cannot_be_consolidated():
    two_profiles()
    profiles.soft_delete(P_B, "x")
    assert pcs.build_plan(P_A, P_B).blockers[0]["code"] == "PROFILE_DELETED" and consolidate().status == pcs.INVALID
    assert pcs.build_plan(P_A, "no-existe").blockers[0]["code"] == "PROFILE_NOT_FOUND"
    assert pcs.build_plan(P_A, P_A).blockers[0]["code"] == "NEED_TWO_PROFILES"


def test_retrying_a_completed_consolidation_is_a_no_op():
    two_profiles()
    first = consolidate()
    revisions = (profiles.get(P_A)["revision"], profiles.get(P_B)["revision"])
    again = pcs.execute(P_A, P_B, resolutions={}, plan_digest="viejo", actor="owner@example.test", confirmed=True)
    assert again.status == pcs.ALREADY_CONSOLIDATED and again.operation_id == first.operation_id
    assert (profiles.get(P_A)["revision"], profiles.get(P_B)["revision"]) == revisions and len(pcs.list_operations()) == 1


def test_a_profile_consolidated_into_another_cannot_be_reused():
    two_profiles()
    mk_profile(P_C)
    assert consolidate().status == pcs.COMPLETED
    assert pcs.build_plan(P_C, P_B).blockers[0]["code"] == "ALREADY_CONSOLIDATED_ELSEWHERE"
    assert pcs.build_plan(P_B, P_C).status == pcs.INVALID


def test_a_stale_plan_is_rejected_when_something_changed_after_the_summary():
    two_profiles()
    plan = pcs.build_plan(P_A, P_B)
    mk_doc(P_B, 3)                                                                                   # alguien subió otro CV al duplicado mientras se revisaba
    out = pcs.execute(P_A, P_B, resolutions={}, plan_digest=plan.digest, actor="o@example.test", confirmed=True)
    assert out.status == pcs.STALE_PLAN and profiles.get(P_B).get("deleted_at") is None
    assert consolidate().status == pcs.COMPLETED and len(doc_ids(P_A)) == 3


# ═══════════════ fallo de persistencia: nunca éxito parcial, visibilidad controlada, recuperable ═══════════════
def _fail_second_document(monkeypatch):
    real, calls = docs.reassign_for_consolidation, {"n": 0}

    def flaky(document_id, **kw):
        if kw.get("restore") is None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise docs.ProfileDocumentStorageError("disco lleno (inyectado)")
        return real(document_id, **kw)
    monkeypatch.setattr(docs, "reassign_for_consolidation", flaky)
    return real


def _three_docs_world():
    mk_profile(P_A)
    mk_profile(P_B)
    mk_doc(P_A, 1)
    mk_doc(P_B, 2)
    mk_doc(P_B, 3)


def test_a_failure_midway_is_pending_never_success_and_both_profiles_stay_hidden_and_locked(monkeypatch):
    _three_docs_world()
    mk_profile(P_C)
    real = _fail_second_document(monkeypatch)
    out = consolidate()
    assert out.status == pcs.PENDING and out.code == "ProfileDocumentStorageError" and out.operation_id
    # visibilidad controlada: NINGUNO de los dos perfiles aparece ni se abre ni se edita
    assert active_ids() == [P_C] and human_capital_service.get_profile(P_A) is None and human_capital_service.get_profile(P_B) is None
    with pytest.raises(profiles.ProfileLockedError):
        profiles.update(P_A, profiles.get(P_A)["revision"], {"location": "otro"})
    with pytest.raises(profiles.ProfileLockedError):
        profiles.soft_delete(P_B, "x")
    with pytest.raises(docs.ProfileDocumentLockedError):
        mk_doc(P_A, 9)
    with pytest.raises(docs.ProfileDocumentLockedError):
        docs.delete("doc-1111-1")
    with pytest.raises(ValueError):
        human_capital_service.add_candidate_to_busqueda("b", P_A)
    assert pcs.build_plan(P_A, P_C).blockers[0]["code"] == "PROFILE_LOCKED"                          # ni otra consolidación puede tocarlos
    assert [o["status"] for o in pcs.list_operations()] == [pcs.PENDING] and pcs.pending_count() == 1
    audit = pcs.read_audit(out.operation_id)
    assert audit["intent"] and audit["result"] is None                                               # la auditoría durable no declara resultado
    monkeypatch.setattr(docs, "reassign_for_consolidation", real)
    resumed = pcs.resume(out.operation_id, actor="owner@example.test")                               # reanudar = completar lo que falta, idempotente
    assert resumed.status == pcs.COMPLETED
    assert active_ids() == sorted([P_A, P_C]) and doc_ids(P_A) == ["doc-1111-1", "doc-2222-2", "doc-2222-3"] and pcs.read_audit(out.operation_id)["result"]["outcome"] == pcs.COMPLETED
    assert pcs.resume(out.operation_id, actor="x").code == "ALREADY_CLOSED"


def test_a_pending_consolidation_can_be_reverted_to_the_exact_previous_state(monkeypatch):
    _three_docs_world()
    pre_profiles = {pid: profiles.get(pid) for pid in (P_A, P_B)}
    pre_docs = {d["document_id"]: d for pid in (P_A, P_B) for d in docs.list_for_profile(pid)}
    _fail_second_document(monkeypatch)
    out = consolidate()
    assert out.status == pcs.PENDING
    reverted = pcs.abort(out.operation_id, actor="owner@example.test")
    assert reverted.status == pcs.ABORTED
    assert active_ids() == sorted([P_A, P_B])                                                        # ambos vuelven a ser operables
    for pid, before in pre_profiles.items():
        after = profiles.get(pid)
        ignore = ("revision", "updated_at", "consolidation")
        assert {k: v for k, v in after.items() if k not in ignore and v is not None} == {k: v for k, v in before.items() if k not in ignore and v is not None}      # (clave ausente == None)
        assert not profiles.is_locked(after) and after.get("deleted_at") is None
    assert {d["document_id"]: d for pid in (P_A, P_B) for d in docs.list_for_profile(pid)} == pre_docs      # documentos idénticos a su imagen previa (byte a byte)
    assert pcs.read_audit(out.operation_id)["result"]["outcome"] == pcs.ABORTED and pcs.pending_count() == 0
    assert pcs.abort(out.operation_id, actor="x").code == "ALREADY_CLOSED"
    assert consolidate().status == pcs.COMPLETED                                                     # y se puede volver a intentar


def test_a_completed_consolidation_cannot_be_reverted_from_here():
    two_profiles()
    out = consolidate()
    assert pcs.abort(out.operation_id, actor="x").code == "ALREADY_COMPLETED" and active_ids() == [P_A]


@pytest.mark.parametrize("step", ["finalize_duplicate", "finalize_primary", "result"])
def test_every_late_step_failure_stays_pending_until_resumed(monkeypatch, step):
    two_profiles()
    name = "_write_audit" if step == "result" else "_update"
    real = getattr(pcs, name)
    if step == "result":
        monkeypatch.setattr(pcs, name, lambda path, payload, **kw: (_ for _ in ()).throw(pcs.ConsolidationAuditUnavailable("x")) if path.name == "result.json" else real(path, payload, **kw))
    else:
        monkeypatch.setattr(pcs, name, lambda pid, fields, op: (_ for _ in ()).throw(RuntimeError("inyectado")) if (fields.get("deleted_at") if step == "finalize_duplicate" else fields.get("consolidated_from")) else real(pid, fields, op))
    out = consolidate()
    assert out.status == pcs.PENDING
    if step != "result":
        assert active_ids() == []                                                                    # aunque los documentos ya se movieron, ninguno de los dos es operable
    monkeypatch.setattr(pcs, name, real)
    assert pcs.resume(out.operation_id, actor="o").status == pcs.COMPLETED and active_ids() == [P_A]


def test_the_audit_must_be_durable_before_anything_changes(monkeypatch):
    two_profiles()
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(remote_store, "write_through", lambda path, data, **kw: remote_store.DURABLE_LOCAL)         # Spaces no confirma
    out = consolidate()
    assert out.status == pcs.AUDIT_UNAVAILABLE
    assert profiles.get(P_B).get("consolidation") is None and doc_ids(P_B) == ["doc-2222-2"] and active_ids() == sorted([P_A, P_B])


def test_a_remote_failure_while_moving_a_document_restores_it_exactly(monkeypatch):
    two_profiles()
    before = docs.get("doc-2222-2")
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(remote_store, "push_path", lambda path: False)
    with pytest.raises(docs.ProfileDocumentRemoteOutcomeUnknownError):
        docs.reassign_for_consolidation("doc-2222-2", operation_id="o" * 32, authorized={"doc-2222-2": {"from": P_B, "to": P_A}})
    assert docs.get("doc-2222-2") == before                                                          # el local quedó como estaba: sin mover a medias


# ═══════════════ la reasignación de documentos es excepcional y acotada ═══════════════
def test_reassignment_is_limited_to_what_the_consolidation_authorized():
    two_profiles()
    op = "a" * 32
    grant = {"doc-2222-2": {"from": P_B, "to": P_A}}
    with pytest.raises(docs.ProfileDocumentReassignError):
        docs.reassign_for_consolidation("doc-1111-1", operation_id=op, authorized=grant)             # no autorizado
    with pytest.raises(docs.ProfileDocumentReassignError):
        docs.reassign_for_consolidation("doc-2222-2", operation_id=op, authorized={"doc-2222-2": {"from": P_C, "to": P_A}})      # no pertenece al origen esperado
    with pytest.raises(docs.ProfileDocumentReassignError):
        docs.reassign_for_consolidation("doc-2222-2", operation_id="", authorized=grant)
    assert docs.get("doc-2222-2")["profile_id"] == P_B
    moved, changed = docs.reassign_for_consolidation("doc-2222-2", operation_id=op, authorized=grant)
    assert changed and moved["profile_id"] == P_A
    _, again = docs.reassign_for_consolidation("doc-2222-2", operation_id=op, authorized=grant)
    assert again is False                                                                            # idempotente


def test_the_document_repository_exposes_no_generic_metadata_update():
    public = {n for n, v in vars(docs).items() if callable(v) and not n.startswith("_") and getattr(v, "__module__", "") == docs.__name__}
    assert not [n for n in public if n.startswith(("update", "patch", "set_", "replace", "rename"))]
    assert "reassign_for_consolidation" in public


# ═══════════════ concurrencia ═══════════════
def test_two_simultaneous_confirmations_of_the_same_pair_produce_one_consolidation():
    two_profiles()
    plan = pcs.build_plan(P_A, P_B)
    results: list = []
    gate = threading.Barrier(2)

    def go():
        gate.wait()
        results.append(pcs.execute(P_A, P_B, resolutions={}, plan_digest=plan.digest, actor="o@example.test", confirmed=True).status)
    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(results) == sorted([pcs.COMPLETED, pcs.ALREADY_CONSOLIDATED]) and len(pcs.list_operations()) == 1
    assert doc_ids(P_A) == ["doc-1111-1", "doc-2222-2"]


def test_two_overlapping_consolidations_never_interleave():
    mk_profile(P_A)
    mk_profile(P_B)
    mk_profile(P_C)
    for pid, n in ((P_A, 1), (P_B, 2), (P_C, 3)):
        mk_doc(pid, n)
    plan_ab, plan_bc = pcs.build_plan(P_A, P_B), pcs.build_plan(P_B, P_C)
    results: dict = {}
    gate = threading.Barrier(2)

    def go(key, primary, duplicate, plan):
        gate.wait()
        results[key] = pcs.execute(primary, duplicate, resolutions={}, plan_digest=plan.digest, actor="o", confirmed=True).status
    threads = [threading.Thread(target=go, args=("ab", P_A, P_B, plan_ab)), threading.Thread(target=go, args=("bc", P_B, P_C, plan_bc))]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(results.values()).count(pcs.COMPLETED) == 1                                        # exactamente una gana; la otra se rechaza (perfil ya consolidado / cambió)
    assert len(active_ids()) == 2 and sum(1 for pid in (P_A, P_B, P_C) if profiles.get(pid).get("consolidated_into")) == 1
    assert not pcs.pending_count() and sorted(d for pid in active_ids() for d in doc_ids(pid)) == ["doc-1111-1", "doc-2222-2", "doc-3333-3"]      # ningún documento se perdió ni se duplicó


# ═══════════════ auditoría durable ═══════════════
def test_the_audit_keeps_the_intent_with_pre_images_and_the_result_and_survives_a_revert(monkeypatch):
    two_profiles()
    out = consolidate(actor="owner@example.test")
    audit = pcs.read_audit(out.operation_id)
    intent, result = audit["intent"], audit["result"]
    assert intent["actor"] == "owner@example.test" and intent["primary_id"] == P_A and intent["duplicate_id"] == P_B
    assert intent["pre"]["primary"]["profile_id"] == P_A and intent["pre"]["duplicate"]["profile_id"] == P_B and intent["pre"]["duplicate"].get("deleted_at") is None
    assert intent["documents"]["doc-2222-2"]["pre_image"]["profile_id"] == P_B and intent["documents"]["doc-2222-2"]["from"] == P_B
    assert result["outcome"] == pcs.COMPLETED and result["documents_moved"] == ["doc-2222-2"]
    listed = pcs.list_operations()[0]
    assert listed["status"] == pcs.COMPLETED and listed["primary_name"] == "Julia Sintetica"


def test_audit_records_are_write_once():
    two_profiles()
    out = consolidate()
    path = pcs.audit_root() / out.operation_id / "intent.json"
    with pytest.raises(pcs.ConsolidationAuditUnavailable):
        pcs._write_audit(path, {"distinto": True})
    assert json.loads(path.read_text(encoding="utf-8"))["operation_id"] == out.operation_id


# ═══════════════ el resto del Hub respeta la visibilidad ═══════════════
def test_other_flows_cannot_use_a_locked_profile(monkeypatch):
    _three_docs_world()
    _fail_second_document(monkeypatch)
    consolidate()
    with pytest.raises(ValueError):
        human_capital_service.create_entrevista_manual(P_A, {})
    from webapp.services import cv_history_import_exec
    assert cv_history_import_exec._revalidate_link({"profile_id": P_A, "source": "manual"}, {}) == "LINK_TARGET_MISSING"
