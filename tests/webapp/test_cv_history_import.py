"""Importación histórica de CVs — incremento I3.1: análisis completo, decisión de importación (plan), manifiesto y reporte. Sin escrituras en el Banco ni en SharePoint. Todo con datos sintéticos; Graph simulado."""
from __future__ import annotations

import json
import random
import time

import pytest

import config
from core import microsoft365
from tests.webapp.cv_docs import make_pdf
from tests.webapp.test_cv_history_inventory import http_error
from tests.webapp.test_cv_history_pilot import BASE, HC_USER, OWNER, PHRASE, PII, SHARE, TOKEN, client_for, home, seed_profiles, sp, standard_world, tree_bytes  # noqa: F401  (home y sp son fixtures)
from webapp.services import cv_extract, cv_history_import as imp, cv_history_manifest as mf, cv_history_pilot as pilot, cv_reconcile, profile_document_repository, profile_document_service, profile_repository


# ═══════════════════ decisión pura: tabla de decisión ═══════════════════
def extraction(lines, filename="documento.pdf"):
    return cv_extract.extract_fields("\n".join(lines), filename)


def group(key, lines, filename="documento.pdf", specialties=("Datasphere",), **over):
    e = extraction(lines, filename)
    return {"content_key": key, "state": pilot.EXTRACTED, "extraction": e, "specialties": list(specialties), "members": [], "representative": {"name": filename}, **over}


def profile(pid, first, last, email=None, **extra):
    return {"profile_id": pid, "first_name": first, "last_name": last, "email": email, **extra}


def plan_of(groups, profiles=()):
    return imp.build_plan(groups, list(profiles))


def act(plan, key):
    return plan["decisions"][key]


ELIANA = ["Eliana Ojeda", "11 5555 1111", "eliana.ojeda@gmail.com"]


def test_a_corroborated_identity_with_one_non_generic_email_creates_a_profile():
    p = plan_of([group("a" * 16, ELIANA, "CV Eliana Ojeda.pdf")])
    d = act(p, "a" * 16)
    assert d["action"] == imp.CREATE and d["cluster"] == "c0001" and d["warnings"] == [] and p["clusters"]["c0001"]["leader"] == "a" * 16
    assert p["summary"]["profiles_to_create"] == 1 and p["summary"]["documents_to_upload"] == 1


def test_documents_sharing_the_email_are_grouped_into_one_profile_and_the_leader_is_the_strongest_evidence():
    weaker = group("1" * 16, ["Experiencia laboral", "11 5555 1111", "eliana.ojeda@gmail.com"], "Eliana Ojeda.pdf")                  # solo el archivo: R2 (orden incierto)
    stronger = group("2" * 16, ELIANA, "CV Eliana Ojeda.pdf", specialties=("IA", "Databricks"))                                      # texto y archivo coinciden: R1
    no_name = group("3" * 16, ["Experiencia laboral", "eliana.ojeda@gmail.com"], "documento.pdf")
    p = plan_of([weaker, stronger, no_name])
    assert p["clusters"] == {"c0001": {"leader": "2" * 16, "members": ["2" * 16, "1" * 16, "3" * 16], "warnings": []}}
    assert [act(p, k)["action"] for k in ("2" * 16, "1" * 16, "3" * 16)] == [imp.CREATE, imp.ATTACH, imp.ATTACH] and p["summary"]["profiles_to_create"] == 1


@pytest.mark.parametrize("email, expected", [("info@empresa.com", imp.GENERIC_EMAIL), ("ventas.perez@empresa.com", imp.GENERIC_EMAIL), ("rrhh@empresa.com", imp.GENERIC_EMAIL)])
def test_generic_or_role_mailboxes_never_create_link_or_group(email, expected):
    g1, g2 = group("a" * 16, ["Carlos Diaz", "11 5555 1111", email]), group("b" * 16, ["Marta Lopez", "11 5555 2222", email])
    p = plan_of([g1, g2], [profile("p1", "Carlos", "Diaz", email)])                                             # ni siquiera con un perfil existente que tenga ese email
    assert [act(p, k)["action"] for k in ("a" * 16, "b" * 16)] == [imp.PENDING, imp.PENDING] and act(p, "a" * 16)["reasons"][0] == expected and p["clusters"] == {}


def test_documents_without_a_usable_email_are_pending_with_the_reason():
    none = group("a" * 16, ["Eliana Ojeda", "11 5555 1111"], "CV Eliana Ojeda.pdf")
    several = group("b" * 16, ["Pablo Ruiz", "pablo.ruiz@a.com y pruiz@b.com"], "pablo.pdf")
    p = plan_of([none, several])
    assert act(p, "a" * 16)["action"] == imp.PENDING and act(p, "a" * 16)["reasons"][0] == imp.NO_EMAIL
    assert act(p, "b" * 16)["reasons"][0] == imp.MULTIPLE_EMAILS and p["summary"]["profiles_to_create"] == 0


def test_contradicting_identities_for_the_same_email_are_not_grouped_nor_created():
    email = "anaruizpablogomez@gmail.com"
    a = group("a" * 16, ["Ana Ruiz", "11 5555 1111", email], "CV Ana Ruiz.pdf")
    b = group("b" * 16, ["Pablo Gomez", "11 5555 2222", email], "CV Pablo Gomez.pdf")
    assert cv_extract.email_strong(["Ana", "Ruiz"], [email]) and cv_extract.email_strong(["Pablo", "Gomez"], [email])                  # ambas "corroboradas": justo el caso engañoso
    p = plan_of([a, b])
    assert [act(p, k) for k in ("a" * 16, "b" * 16)] == [{"action": imp.PENDING, "reasons": [imp.EMAIL_CONFLICT]}] * 2 and p["clusters"] == {}


@pytest.mark.parametrize("lines, filename, reason", [
    (["Experiencia Laboral", "11 5555 1111", "x.y@gmail.com"], "documento.pdf", imp.NO_RELIABLE_NAME),
    (["Nombre: Laura Sosa", "11 5555 1111", "lau@gmail.com"], "documento.pdf", imp.NAME_DECLARED_ONLY),
    (["Sofia Martinez", "11 5555 1111", "sofia@gmail.com"], "CV Roberto Diaz.pdf", imp.NAME_CONFLICT),
    (["Diego Torres", "11 5555 1111", "dpersonal@gmail.com"], "documento.pdf", imp.NAME_UNCORROBORATED),
    (["Nacional Experimental", "11 5555 1111", "eliana.ojeda@gmail.com"], "RC Eliana Ojeda.pdf", imp.NAME_EDGE_TOKEN),
])
def test_names_without_sufficient_corroboration_stay_pending_without_inventing_one(lines, filename, reason):
    key = "a" * 16
    p = plan_of([group(key, lines, filename)])
    assert act(p, key)["action"] == imp.PENDING and act(p, key)["reasons"] == [reason] and p["clusters"] == {}


def test_the_rc_case_is_never_created_with_the_ambiguous_token_nor_silently_normalized():
    key = "a" * 16
    g = group(key, ["Nacional Experimental", "11 5555 1111", "eliana.ojeda@gmail.com"], "RC Eliana Ojeda.pdf")
    assert g["extraction"]["first_name"] == "Eliana" and cv_extract.NAME_EDGE_TOKEN_UNEXPLAINED in g["extraction"]["blocking"]        # el extractor ofrece la variante, pero exige revisión
    assert act(plan_of([g]), key)["action"] == imp.PENDING


def test_name_order_and_split_uncertainty_do_not_block_but_are_recorded_as_warnings():
    key = "a" * 16
    p = plan_of([group(key, ["Experiencia laboral", "11 5555 1111", "juan.carlos.perez@gmail.com"], "Juan Carlos Perez.pdf")])
    d = act(p, key)
    assert d["action"] == imp.CREATE and set(d["warnings"]) == {cv_extract.NAME_ORDER_UNCERTAIN, cv_extract.NAME_SPLIT_AMBIGUOUS}
    assert p["summary"]["created_with_warnings"] == 1 and p["summary"]["warnings"][cv_extract.NAME_ORDER_UNCERTAIN] == 1 and p["clusters"]["c0001"]["warnings"] == d["warnings"]


def test_an_exact_email_match_with_a_consistent_existing_profile_links_without_changing_it():
    key = "a" * 16
    existing = profile("p-1", "Eliana", "Ojeda", "Eliana.Ojeda@GMAIL.com")                                          # el email se compara normalizado
    snapshot = json.dumps(existing, sort_keys=True)
    p = plan_of([group(key, ELIANA, "CV Eliana Ojeda.pdf")], [existing])
    assert act(p, key) == {"action": imp.LINK, "reasons": [], "link_profile_id": "p-1"} and p["clusters"] == {} and p["summary"]["existing_profiles_to_link"] == 1
    assert json.dumps(existing, sort_keys=True) == snapshot


def test_link_without_a_reliable_name_needs_the_email_to_corroborate_the_existing_profile_name():
    nameless = ["Experiencia laboral", "11 5555 1111", "eliana.ojeda@gmail.com"]
    ok = plan_of([group("a" * 16, nameless)], [profile("p-1", "Eliana", "Ojeda", "eliana.ojeda@gmail.com")])
    assert act(ok, "a" * 16)["action"] == imp.LINK                                                                   # el email coincide con el nombre del perfil: sostiene la identidad
    bad = plan_of([group("a" * 16, ["Experiencia laboral", "11 5555 1111", "mpersonal@gmail.com"])], [profile("p-1", "Marta", "Lopez", "mpersonal@gmail.com")])
    assert act(bad, "a" * 16) == {"action": imp.PENDING, "reasons": [imp.EMAIL_MATCH_UNCORROBORATED], "candidates": ["p-1"]}


def test_an_exact_email_match_with_a_contradicting_name_is_pending():
    p = plan_of([group("a" * 16, ELIANA, "CV Eliana Ojeda.pdf")], [profile("p-1", "Marta", "Lopez", "eliana.ojeda@gmail.com")])
    assert act(p, "a" * 16) == {"action": imp.PENDING, "reasons": [imp.EXISTING_NAME_CONFLICT], "candidates": ["p-1"]}


def test_deleted_or_ambiguous_existing_profiles_are_never_linked_nor_duplicated():
    doc = group("a" * 16, ELIANA, "CV Eliana Ojeda.pdf")
    deleted = plan_of([doc], [profile("p-1", "Eliana", "Ojeda", "eliana.ojeda@gmail.com", deleted_at="2026-01-01T00:00:00Z")])
    assert act(deleted, "a" * 16)["reasons"] == [imp.EXISTING_DELETED] and deleted["clusters"] == {}                 # no se resucita ni se duplica
    two = plan_of([doc], [profile("p-1", "Eliana", "Ojeda", "eliana.ojeda@gmail.com"), profile("p-2", "Eliana", "Ojeda", "eliana.ojeda@gmail.com")])
    assert act(two, "a" * 16) == {"action": imp.PENDING, "reasons": [imp.MULTIPLE_EXISTING_MATCH], "candidates": ["p-1", "p-2"]}
    live_and_deleted = plan_of([doc], [profile("p-1", "Eliana", "Ojeda", "eliana.ojeda@gmail.com", deleted_at="x"), profile("p-2", "Eliana", "Ojeda", "eliana.ojeda@gmail.com")])
    assert act(live_and_deleted, "a" * 16)["action"] == imp.LINK and act(live_and_deleted, "a" * 16)["link_profile_id"] == "p-2"


def test_a_name_match_without_an_email_match_is_a_possible_duplicate_not_an_automatic_creation_or_link():
    key = "a" * 16
    p = plan_of([group(key, ELIANA, "CV Eliana Ojeda.pdf")], [profile("p-old", "Eliana", "Ojeda", "otra.cuenta@old.com")])
    assert act(p, key) == {"action": imp.PENDING, "reasons": [imp.POSSIBLE_DUPLICATE], "candidates": ["p-old"]} and p["clusters"] == {}
    unrelated = plan_of([group(key, ELIANA, "CV Eliana Ojeda.pdf")], [profile("p-x", "Zoe", "Zeta", "zoe@mail.com")])
    assert act(unrelated, key)["action"] == imp.CREATE


def test_a_non_leader_with_a_clearly_different_name_is_not_attached():
    email = "juan.perez.gomez@gmail.com"
    leader = group("1" * 16, ["Juan Perez Gomez", "11 5555 1111", email], "CV Juan Perez Gomez.pdf")
    other = group("2" * 16, ["Marta Lopez", "11 5555 2222", email], "CV Marta Lopez.pdf")                          # su nombre NO está corroborado por ese email
    p = plan_of([leader, other])
    assert act(p, "1" * 16)["action"] == imp.CREATE and act(p, "2" * 16) == {"action": imp.PENDING, "reasons": [imp.EMAIL_NAME_MISMATCH]}


def test_aliases_failures_and_unreadable_documents():
    alias = {"content_key": "b" * 16, "state": pilot.ALIAS, "alias_of": "a" * 16, "specialties": ["IA"], "extraction": None}
    failed = {"content_key": "c" * 16, "state": pilot.FAILED, "failure": "DOWNLOAD_FORBIDDEN", "specialties": ["IA"], "extraction": None}
    unreadable = {"content_key": "d" * 16, "state": pilot.EXTRACTED, "specialties": ["IA"], "extraction": cv_extract.extract("escaneado.pdf", b"%PDF-1.4\n" + b"x" * 40)}
    pending = {"content_key": "e" * 16, "state": pilot.SELECTED, "specialties": ["IA"], "extraction": None}
    p = plan_of([group("a" * 16, ELIANA, "CV Eliana Ojeda.pdf"), alias, failed, unreadable, pending])
    assert act(p, "b" * 16) == {"action": imp.DUPLICATE, "reasons": [], "alias_of": "a" * 16}
    assert act(p, "c" * 16) == {"action": imp.ERROR, "reasons": ["DOWNLOAD_FORBIDDEN"]} and act(p, "e" * 16) == {"action": imp.ERROR, "reasons": [imp.NOT_ANALYZED]}
    assert act(p, "d" * 16)["action"] == imp.PENDING and act(p, "d" * 16)["reasons"][0] == imp.READ_FAILURE
    s = p["summary"]
    assert s["actions"] == {"CREATE": 1, "ATTACH": 0, "LINK": 0, "PENDING": 1, "DUPLICATE": 1, "ERROR": 2} and s["pending_by_reason"] == {"DOWNLOAD_FORBIDDEN": 1, imp.NOT_ANALYZED: 1, imp.READ_FAILURE: 1}


def test_the_plan_is_deterministic_and_independent_of_the_input_order():
    rnd = random.Random(7)
    groups = [group(f"{i:016x}", [f"{f} {l}", "11 5555 1111", f"{f.lower()}.{l.lower()}@gmail.com"], f"CV {f} {l}.pdf", specialties=(rnd.choice(["IA", "Datasphere", "Databricks"]),))
              for i, (f, l) in enumerate([("Ana", "Ruiz"), ("Ana", "Ruiz"), ("Luis", "Paz"), ("Eva", "Sol"), ("Pedro", "Mora")] * 3)]
    profiles = [profile("p1", "Luis", "Paz", "luis.paz@gmail.com"), profile("p2", "Zoe", "Zeta", "zoe@mail.com")]
    baseline = plan_of(groups, profiles)
    for seed in range(5):
        shuffled = groups[:]
        random.Random(seed).shuffle(shuffled)
        assert json.dumps(plan_of(shuffled, profiles), sort_keys=True) == json.dumps(baseline, sort_keys=True)
    assert baseline["summary"]["profiles_to_create"] == 3 and baseline["summary"]["existing_profiles_to_link"] == 1


def test_every_creation_respects_the_banco_contract_and_the_plan_carries_no_personal_data():
    gs = [group(f"{i:016x}", ["Eliana Ojeda", "11 5555 1111", "eliana.ojeda@gmail.com"], "CV Eliana Ojeda.pdf") for i in range(3)] + [group("f" * 16, ["Experiencia laboral", "x.y@gmail.com"])]
    p = plan_of(gs)
    for cid, cluster in p["clusters"].items():
        e = next(g for g in gs if g["content_key"] == cluster["leader"])["extraction"]
        assert (e["first_name"] or "").strip() and (e["last_name"] or "").strip()                                   # nombre y apellido siempre presentes
    blob = json.dumps(p, ensure_ascii=False)
    assert "Eliana" not in blob and "Ojeda" not in blob and "@" not in blob and "gmail" not in blob


def test_plan_scales_to_the_real_volume():
    rnd = random.Random(11)
    firsts = [f"Nombre{chr(97 + i)}{chr(97 + j)}" for i in range(20) for j in range(2)]
    lasts = [f"Apellido{chr(97 + i)}{chr(97 + j)}" for i in range(20) for j in range(2)]
    pairs = [(rnd.choice(firsts), rnd.choice(lasts)) for _ in range(800)]
    gs = [group(f"{i:016x}", [f"{f} {l}", "11 5555 1111", f"{f.lower()}.{l.lower()}@gmail.com"], f"CV {f} {l}.pdf") for i, (f, l) in enumerate(pairs)]
    profiles = [profile(f"p{i}", rnd.choice(firsts), rnd.choice(lasts), f"otro{i}@mail.com") for i in range(600)]
    started = time.monotonic()
    p = plan_of(gs, profiles)
    assert time.monotonic() - started < 5 and sum(p["summary"]["actions"].values()) == 800


# ═══════════════════ análisis de punta a punta (Graph simulado) ═══════════════════
@pytest.fixture()
def no_writes(monkeypatch):
    """I3.1 NO escribe en el Banco ni en SharePoint: tras `arm()`, cualquier intento falla el test (se arma DESPUÉS de sembrar el Banco de prueba)."""
    def boom(*a, **k):
        raise AssertionError("I3.1 no puede escribir en el Banco ni en SharePoint")

    def arm():
        for target, names in WRITERS:
            for name in names:
                monkeypatch.setattr(target, name, boom)
    return arm


WRITERS = ((profile_repository, ("create", "create_with_explicit_id", "update", "soft_delete")), (profile_document_repository, ("create_with_explicit_id", "delete")),
                          (profile_document_service, ("upload_document", "retry_upload_document", "retry_commit_document", "delete_document")),
                          (microsoft365, ("graph_post_json", "graph_put_bytes", "graph_delete")))


def start(**kw):
    return imp.start_import(TOKEN, SHARE, actor=OWNER, authorization=PHRASE, sleep=lambda s: None, **kw)


def analyze_all(run_id, batch=None, world=None):
    out = []
    for _ in range(40):
        r = imp.analyze(run_id, TOKEN, max_documents=batch, sleep=lambda s: None)
        out.append(r)
        if not r["remaining"] or r["stopped_reason"] not in (None, "BATCH_LIMIT", "TIME_BUDGET"):
            break
    return out


def banco_bytes():
    return tree_bytes(config.BASE_DIR, "human-capital", "profiles") if (config.BASE_DIR / "human-capital" / "profiles").is_dir() else {}


def test_start_import_indexes_every_content_without_downloading_or_writing(sp, no_writes):
    world = sp()
    seed_profiles()
    no_writes()
    before = banco_bytes()
    run = start()
    assert run["kind"] == "import" and run["state"] == "CREATED" and run["counters"]["selected"] == 7 and world.downloads == []
    assert len(mf.get_index(run["run_id"])["groups"]) == 7 and mf.list_groups(run["run_id"]) == []                         # los registros por contenido se materializan al analizar
    assert run["authorization"]["confirmed_by"] == OWNER and run["staging"]["state"] == "PRESENT" and banco_bytes() == before


def test_start_import_requires_the_authorization_and_a_complete_inventory(sp):
    sp()
    for bad in ("", "si", PHRASE.lower()):
        with pytest.raises(pilot.PilotInputError):
            imp.start_import(TOKEN, SHARE, actor=OWNER, authorization=bad, sleep=lambda s: None)
    assert mf.list_runs() == []
    sp(errors={("F-IA", 0): [http_error(403)]})
    with pytest.raises(pilot.PilotInputError, match="incompleto"):
        start()
    assert mf.list_runs() == []                                                                                          # un origen a medio recorrer no genera ningún lote


def test_analysis_runs_in_resumable_batches_downloads_each_content_once_and_never_redownloads(sp, no_writes):
    world = sp()
    seed_profiles()
    no_writes()
    rid = start()["run_id"]
    first = imp.analyze(rid, TOKEN, max_documents=3, sleep=lambda s: None)
    assert first == {"processed": 3, "failed": 0, "remaining": 4, "stopped_reason": "BATCH_LIMIT"} and mf.get_run(rid)["state"] == "PROCESSING"
    rest = analyze_all(rid, batch=3)
    assert rest[-1]["remaining"] == 0 and rest[-1]["stopped_reason"] is None and mf.get_run(rid)["state"] == "DONE"
    assert len(world.downloads) == 7 and len(set(world.downloads)) == 7                                                  # un contenido, una descarga (el alias se detecta tras descargarlo)
    downloads = list(world.downloads)
    again = imp.analyze(rid, TOKEN, sleep=lambda s: None)
    assert again == {"processed": 0, "failed": 0, "remaining": 0, "stopped_reason": None} and world.downloads == downloads
    states = {g["state"] for g in mf.list_groups(rid)}
    assert states == {pilot.EXTRACTED, pilot.ALIAS} and mf.get_run(rid)["counters"] == {"selected": 7, "extracted": 6, "failed": 0, "alias": 1}


def test_batches_configurable_and_bounded_and_a_resumed_analysis_equals_a_single_pass(sp):
    sp()
    seed_profiles()
    one = start()["run_id"]
    imp.analyze(one, TOKEN, sleep=lambda s: None)
    sp()
    other = start()["run_id"]
    analyze_all(other, batch=2)
    normal = lambda rid: {g["representative"]["name"]: (g["state"], (g.get("extraction") or {}).get("first_name"), (g.get("extraction") or {}).get("email")) for g in mf.list_groups(rid)}      # noqa: E731
    assert normal(one) == normal(other)
    assert imp.analyze(other, TOKEN, max_documents=10 ** 6, sleep=lambda s: None)["processed"] == 0
    assert imp.BATCH_MAX_DOCUMENTS == 200


def test_an_expired_token_stops_the_analysis_without_marking_failures_and_it_resumes(sp):
    world = sp()
    rid = start()["run_id"]
    world.dl_errors["d2"] = [http_error(401)]
    world.dl_errors["i1"] = [http_error(401)]
    first = imp.analyze(rid, TOKEN, sleep=lambda s: None)
    assert first["stopped_reason"] == "TOKEN_EXPIRED" and first["failed"] == 0 and first["remaining"] > 0
    assert not [g for g in mf.list_groups(rid) if g["state"] == pilot.FAILED]
    for _ in range(5):                                                                                                    # cada corte consume un 401; se reanuda con un token nuevo hasta terminar
        last = imp.analyze(rid, TOKEN, sleep=lambda s: None)
        if not last["remaining"]:
            break
    assert last["remaining"] == 0 and last["stopped_reason"] is None and mf.get_run(rid)["counters"]["failed"] == 0


def test_the_time_budget_stops_cleanly(sp):
    sp()
    rid = start()["run_id"]
    assert imp.analyze(rid, TOKEN, time_budget=0, sleep=lambda s: None) == {"processed": 0, "failed": 0, "remaining": 7, "stopped_reason": "TIME_BUDGET"}


def test_the_plan_needs_the_complete_analysis_and_is_built_from_the_banco_without_writing(sp, no_writes):
    sp()
    seed_profiles()
    no_writes()
    rid = start()["run_id"]
    with pytest.raises(pilot.PilotInputError, match="Faltan 7"):
        imp.generate_plan(rid, actor=OWNER)
    imp.analyze(rid, TOKEN, max_documents=2, sleep=lambda s: None)
    with pytest.raises(pilot.PilotInputError, match="Faltan 5"):
        imp.generate_plan(rid, actor=OWNER)
    assert mf.get_plan(rid) is None
    analyze_all(rid)
    before = banco_bytes()
    doc = imp.generate_plan(rid, actor=OWNER)
    s = doc["summary"]
    assert doc["revision"] == 1 and doc["generated_by"] == OWNER and s["contents"] == 7 and set(doc["decisions"]) == {g["content_key"] for g in mf.list_groups(rid)} | {g["content_key"] for g in pilot._import_groups(rid).values()}
    assert s["actions"] == {"CREATE": 2, "ATTACH": 1, "LINK": 1, "PENDING": 2, "DUPLICATE": 1, "ERROR": 0} and s["profiles_to_create"] == 2 and s["documents_to_upload"] == 4 and s["existing_profiles_to_link"] == 1
    assert s["pending_by_reason"] == {imp.MULTIPLE_EMAILS: 1, imp.READ_FAILURE: 1}
    by_key = {g["representative"]["name"]: doc["decisions"][g["content_key"]] for g in mf.list_groups(rid)}
    assert by_key["lucia.pdf"]["action"] == imp.LINK and by_key["Maria Fernandez.docx"]["action"] == imp.CREATE and by_key["escaneado.pdf"]["reasons"][0] == imp.READ_FAILURE
    assert mf.get_run(rid)["plan"]["summary"] == s and banco_bytes() == before                                           # el Banco no cambió
    assert imp.generate_plan(rid, actor=OWNER)["revision"] == 2                                                          # regenerar es idempotente: mismo plan, nueva revisión
    assert mf.get_plan(rid)["decisions"] == doc["decisions"]


def test_the_plan_reflects_the_current_banco_when_regenerated(sp):
    sp()
    rid = start()["run_id"]
    analyze_all(rid)
    assert imp.generate_plan(rid, actor=OWNER)["summary"]["actions"]["LINK"] == 0
    seed_profiles()                                                                                                       # el Banco cambia entre el análisis y el plan
    assert imp.generate_plan(rid, actor=OWNER)["summary"]["actions"]["LINK"] == 1


def test_plan_file_and_report_carry_no_personal_data_and_survive_the_discard(sp):
    sp()
    seed_profiles()
    rid = start()["run_id"]
    analyze_all(rid)
    imp.generate_plan(rid, actor=OWNER)
    plan_text = (config.BASE_DIR / "human-capital" / "import-history" / rid / "import-plan.json").read_text(encoding="utf-8")
    assert not [p for p in PII if p in plan_text] and "@" not in plan_text.replace(OWNER, "")
    assert not [p for p in PII if p in json.dumps(pilot.run_report(rid), ensure_ascii=False)]
    index = (config.BASE_DIR / "human-capital" / "import-history" / rid / "import-index.json")
    assert index.is_file() and "copia.pdf" in index.read_text(encoding="utf-8")                                          # el índice sí lleva nombres de archivo: es área protegida
    assert mf.staging_files_present(rid) > 7
    removed = pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    assert mf.staging_files_present(rid) == 0 and not index.exists() and mf.get_index(rid) is None and removed["state"] == "DISCARDED"
    assert mf.get_plan(rid)["summary"]["contents"] == 7 and (config.BASE_DIR / "human-capital" / "import-history" / rid / "import-plan.json").is_file()   # el plan (sin PII) sobrevive
    final = pilot.run_report(rid)
    assert final["plan"]["summary"]["actions"]["CREATE"] == 2 and final["staging"]["state"] == "DISCARDED"
    with pytest.raises(pilot.PilotInputError):
        imp.generate_plan(rid, actor=OWNER)


def test_only_import_runs_use_the_import_services(sp):
    sp()
    pilot_run = pilot.start_run(TOKEN, SHARE, sample_size=7, authorization=PHRASE, actor=OWNER, min_sample=1, sleep=lambda s: None)
    for call in (lambda: imp.analyze(pilot_run["run_id"], TOKEN), lambda: imp.generate_plan(pilot_run["run_id"], actor=OWNER)):
        with pytest.raises(pilot.PilotInputError):
            call()


# ═══════════════════ interfaz (rutas solo-owner) ═══════════════════
def post_import(c, **over):
    return c.post(f"{BASE}/importacion", data={"graph_token": TOKEN, "share_url": SHARE, "authorization": PHRASE, **over})


def rid_from(html: str) -> str:
    import re
    return re.search(r"(ch-\d{8}T\d{6}Z-[a-z0-9]{4})", html).group(1)


def test_import_routes_are_owner_only_and_validate_input(sp, monkeypatch):
    sp()
    from fastapi.testclient import TestClient
    from webapp.main import app
    rid = mf.new_run_id()
    mf.create_run(rid, {"state": "CREATED", "kind": "import"})
    for method, url, kw in (("post", f"{BASE}/importacion", {"data": {"graph_token": TOKEN, "share_url": SHARE, "authorization": PHRASE}}), ("post", f"{BASE}/{rid}/plan", {})):
        assert getattr(TestClient(app, follow_redirects=False), method)(url, **kw).status_code in (302, 303, 307)
        assert getattr(client_for(HC_USER, monkeypatch), method)(url, **kw).status_code == 403
    assert len(mf.list_runs()) == 1
    owner = client_for(OWNER, monkeypatch)
    assert 'data-error-kind="missing_token"' in post_import(owner, graph_token="").text
    assert 'data-error-kind="invalid_input"' in post_import(owner, authorization="").text and "confirmación explícita" in post_import(owner, authorization="").text
    assert 'data-error-kind="invalid_input"' in post_import(owner, share_url="http://x").text
    assert len(mf.list_runs()) == 1


def test_import_flow_through_the_routes_analyze_in_batches_then_plan(sp, monkeypatch, no_writes):
    world = sp()
    seed_profiles()
    no_writes()
    owner = client_for(OWNER, monkeypatch)
    page = owner.get(BASE).text
    assert 'id="dw-cvimport-form"' in page and f'hx-post="{BASE}/importacion"' in page and "data-dw-graph-token" in page and "sessionStorage.getItem" not in page
    started = post_import(owner)
    rid = rid_from(started.text)
    assert "7 contenidos únicos" in started.text and "data-pilot-analyze" in started.text and "data-pilot-created" in started.text and world.downloads == [] and TOKEN not in started.text
    assert "Crear lote derivado" not in started.text and "data-pilot-plan" not in started.text                             # todavía no hay análisis: sin botón de plan
    step = owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN, "batch": "3"})
    assert "Procesados 3 · con error 0 · pendientes 4" in step.text and "data-pilot-autocontinue" in step.text and 'hx-trigger="load delay:700ms, submit"' in step.text and len(world.downloads) == 3
    while "data-pilot-autocontinue" in step.text:                                                                          # el navegador encadena las tandas con el disparador `load`
        step = owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN, "batch": "3"})
    assert 'data-run-state="DONE"' in step.text and "data-pilot-plan" in step.text and "Generar plan" in step.text and "data-pilot-analyze" not in step.text and len(world.downloads) == 7
    planned = owner.post(f"{BASE}/{rid}/plan")
    assert "data-pilot-planned" in planned.text and "data-plan-actions" in planned.text and "data-plan-reasons" in planned.text and "data-plan-specialties" in planned.text and "Regenerar plan" in planned.text
    assert 'data-plan-create>2<' in planned.text.replace("\r", "").replace("\n", "") and "todavía no se creó ni se vinculó" in planned.text and not [p for p in PII if p in planned.text]
    assert owner.post(f"{BASE}/ch-20200101T000000Z/plan").status_code == 404 and owner.post(f"{BASE}/no-es-un-lote/plan").status_code == 404


def test_the_plan_button_explains_when_the_analysis_is_incomplete_and_a_stop_does_not_autocontinue(sp, monkeypatch):
    world = sp()
    owner = client_for(OWNER, monkeypatch)
    rid = rid_from(post_import(owner).text)
    world.dl_errors["d2"] = [http_error(401)]
    world.dl_errors["i1"] = [http_error(401)]
    stopped = owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN, "batch": "50"})
    assert "TOKEN_EXPIRED" in stopped.text and "data-pilot-autocontinue" not in stopped.text                                # un corte no se reintenta solo
    refused = owner.post(f"{BASE}/{rid}/plan")
    assert 'data-error-kind="invalid_input"' in refused.text and "Faltan" in refused.text and mf.get_plan(rid) is None


def test_the_owner_detail_shows_the_decision_per_content_and_no_verdict_forms(sp, monkeypatch):
    sp()
    seed_profiles()
    owner = client_for(OWNER, monkeypatch)
    rid = rid_from(post_import(owner).text)
    owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN, "batch": "50"})
    owner.post(f"{BASE}/{rid}/plan")
    html = owner.get(f"{BASE}/{rid}").text
    assert "Decisión del plan" in html and html.count("data-plan-col") >= 6 and "perfil nuevo c0001" in html and "MULTIPLE_EMAILS" in html and "READ_FAILURE" in html
    assert "data-verdict-box" not in html and "Dictamen del owner" not in html and 'href="/cchh/candidatos/' in html
    assert "importación" in owner.get(BASE).text


def test_an_anchored_name_that_contradicts_an_existing_profile_blocks_the_link_even_without_a_corroborated_proposal():
    shared = "contabilidad.marta@gmail.com"
    doc = group("a" * 16, ["Marta Lopez", "11 5555 1111", shared], "CV Marta Lopez.pdf")                          # texto y archivo coinciden en «Marta Lopez» (sin corroborar por email)
    assert doc["extraction"]["first_name"] is None
    p = plan_of([doc], [profile("p-1", "Carlos", "Diaz", shared)])
    assert act(p, "a" * 16)["action"] == imp.PENDING                                                                # (contabilidad = buzón de rol: ni se evalúa)
    other = "marta.l@gmail.com"
    d2 = group("b" * 16, ["Marta Lopez", "11 5555 1111", other], "CV Marta Lopez.pdf")
    assert act(plan_of([d2], [profile("p-1", "Carlos", "Diaz", other)]), "b" * 16) == {"action": imp.PENDING, "reasons": [imp.EXISTING_NAME_CONFLICT], "candidates": ["p-1"]}
    assert act(plan_of([d2], [profile("p-1", "Marta", "Lopez", other)]), "b" * 16)["action"] == imp.LINK


def test_the_duplicate_prefilter_is_equivalent_to_comparing_against_every_profile():
    key = "a" * 16
    doc = group(key, ELIANA, "CV Eliana Ojeda.pdf")
    unrelated = [profile(f"u{i}", f"Nombre{i}", f"Apellido{i}", f"u{i}@mail.com") for i in range(30)]
    swapped = plan_of([doc], unrelated + [profile("p-sw", "Ojeda", "Eliana", "otra@old.com")])                           # nombre en otro orden
    assert act(swapped, key) == {"action": imp.PENDING, "reasons": [imp.POSSIBLE_DUPLICATE], "candidates": ["p-sw"]}
    by_phone = plan_of([doc], unrelated + [profile("p-ph", "Eliana", "Ojedo", "otra@old.com", phone="+54 9 11 5555-1111")])  # celular igual + nombre parecido: STRONG por celular
    assert act(by_phone, key) == {"action": imp.PENDING, "reasons": [imp.POSSIBLE_DUPLICATE], "candidates": ["p-ph"]}
    only_similar = plan_of([doc], unrelated + [profile("p-sim", "Eliana", "Ojedo", "otra@old.com")])                       # solo parecido: señal débil, no bloquea el alta
    assert act(only_similar, key)["action"] == imp.CREATE
    every = unrelated + [profile("p-sw", "Ojeda", "Eliana", "otra@old.com")]
    person = {"first_name": "Eliana", "last_name": "Ojeda", "email": "eliana.ojeda@gmail.com", "phone": None}
    brute = {m["profile_id"] for m in cv_reconcile.reconcile_with_profiles(person, every) if m["strength"] in (cv_reconcile.STRONG, cv_reconcile.PROBABLE)}          # contra TODOS los perfiles
    assert brute == {"p-sw"} == set(act(swapped, key)["candidates"])
