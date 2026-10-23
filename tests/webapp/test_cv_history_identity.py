"""Incremento 2.1 del piloto de CVs históricos: dictamen del owner por documento, métricas separadas (cobertura / precisión / no determinados), lote DERIVADO inmutable respecto del original y comportamiento de los
dictámenes entre lotes. Graph SIMULADO; documentos de prueba; ningún documento real."""
from __future__ import annotations

import json

import pytest

import config
from core import microsoft365
from tests.webapp.test_cv_history_pilot import BASE, OWNER, PII, SHARE, TOKEN, client_for, home, seed_profiles, sp, start, tree_bytes  # noqa: F401  (home y sp son fixtures)
from webapp.services import cv_history_manifest as mf, cv_history_pilot as pilot

V = pilot


def processed(sp_install, size=7, profiles=True):
    world = sp_install()
    if profiles:
        seed_profiles()
    run = start(size=size)
    pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    return world, run["run_id"]


def key_of(run_id, representative_name):
    return next(g["content_key"] for g in mf.list_groups(run_id) if g["representative"]["name"] == representative_name)


def make_legacy(run_id, key, *, first, last, text_name, emails=(), flags=("NAME_SINGLE_SOURCE",)):
    """Deja un registro como lo habría dejado el extractor v1 (solo para armar el escenario: el lote 'original' del piloto real)."""
    g = mf.get_group(run_id, key)
    g["extraction"] = {"first_name": first, "last_name": last, "email": (emails[0] if len(emails) == 1 else None), "phone": None, "phone_digits": None, "name_corroborated_by_email": False,
                       "evidence": {"name": ["text"]}, "candidates": {"emails": list(emails), "phones": [], "names": {"text": text_name}}, "flags": list(flags), "blocking": list(flags), "advisory": [], "state": "NEEDS_REVIEW"}
    mf.put_group(run_id, key, g)
    return key


def snapshot(run_id):
    base = ("human-capital", "import-history", run_id)
    return {sub: tree_bytes(config.BASE_DIR, *base, sub) for sub in ("staging", "audit")} | {"run": (config.BASE_DIR.joinpath(*base, "run.json")).read_bytes()}


# ═══════════════════ dictamen: estados, validación, almacenamiento aparte ═══════════════════
def test_the_three_verdicts_are_stored_apart_append_only_and_never_rewrite_the_extraction(sp):
    _, rid = processed(sp)
    maria, lucia = key_of(rid, "Maria Fernandez.docx"), key_of(rid, "lucia.pdf")
    before = snapshot(rid)
    doc = pilot.record_verdict(rid, maria, V.V_CORRECT, actor=OWNER)
    assert doc["current"]["status"] == "CORRECT" and doc["current"]["evaluated_proposal"]["first_name"] == "María" and doc["current"]["actor"] == OWNER
    pilot.record_verdict(rid, lucia, V.V_CORRECTED, actor=OWNER, first_name="Lucía", last_name="Gómez Paz")
    doc2 = pilot.record_verdict(rid, lucia, V.V_UNDETERMINED, actor=OWNER)
    assert [h["status"] for h in doc2["history"]] == ["CORRECTED", "UNDETERMINED"] and doc2["current"]["seq"] == 2 and doc2["history"][0]["corrected"] == {"first_name": "Lucía", "last_name": "Gómez Paz"}
    assert snapshot(rid)["staging"] == before["staging"] and snapshot(rid)["run"] == before["run"]                      # el resultado de la extracción y el lote no se tocaron
    assert mf.get_verdict(rid, maria)["current"]["status"] == "CORRECT" and set(mf.list_verdicts(rid)) == {maria, lucia}
    events = [e["event"] for e in mf.get_audit(rid, lucia)["events"]]
    assert events.count("VERDICT") == 2
    audit_blob = json.dumps(mf.get_audit(rid, lucia))
    assert "Gómez Paz" not in audit_blob and "Lucía" not in audit_blob                                                    # la auditoría no lleva la corrección (dato personal)


def test_verdict_validation_rejects_what_cannot_be_evaluated(sp):
    world, rid = processed(sp)
    maria = key_of(rid, "Maria Fernandez.docx")
    scanned = key_of(rid, "escaneado.pdf")
    alias = next(g["content_key"] for g in mf.list_groups(rid) if g["state"] == V.ALIAS)
    for kw in ({"status": "OTRO"}, {"status": V.V_CORRECTED}, {"status": V.V_CORRECTED, "first_name": "Ana", "last_name": ""}, {"status": V.V_CORRECTED, "first_name": "An4", "last_name": "Ruiz"},
               {"status": V.V_CORRECTED, "first_name": "A" * 61, "last_name": "Ruiz"}, {"status": V.V_CORRECTED, "first_name": "<b>x</b>", "last_name": "Ruiz"}):
        with pytest.raises(pilot.PilotInputError):
            pilot.record_verdict(rid, maria, actor=OWNER, **kw)
    for key in (scanned, alias, "0" * 16):
        with pytest.raises(pilot.PilotInputError):
            pilot.record_verdict(rid, key, V.V_UNDETERMINED, actor=OWNER)
    assert mf.list_verdicts(rid) == {}
    ok = pilot.record_verdict(rid, maria, V.V_CORRECTED, actor=OWNER, first_name="  María   José ", last_name="O'Connor-Díaz")
    assert ok["current"]["corrected"] == {"first_name": "María José", "last_name": "O'Connor-Díaz"}


def test_a_document_without_proposal_cannot_be_marked_correct_but_can_be_resolved_manually(sp):
    _, rid = processed(sp)
    key = key_of(rid, "Maria Fernandez.docx")
    make_legacy(rid, key, first=None, last=None, text_name="Experiencia Laboral", flags=("NAME_MISSING",))
    assert pilot._proposal(mf.get_group(rid, key)["extraction"]) is None
    with pytest.raises(pilot.PilotInputError):
        pilot.record_verdict(rid, key, V.V_CORRECT, actor=OWNER)
    doc = pilot.record_verdict(rid, key, V.V_CORRECTED, actor=OWNER, first_name="María", last_name="Fernández")
    assert doc["current"]["evaluated_proposal"] is None and pilot.confirmed_identity(doc) == {"first_name": "María", "last_name": "Fernández"}


# ═══════════════════ métricas: cobertura, precisión y no determinados, por separado ═══════════════════
def test_report_separates_coverage_precision_and_undetermined_cases(sp):
    _, rid = processed(sp)
    keys = {g["representative"]["name"]: g["content_key"] for g in mf.list_groups(rid) if g["state"] == V.EXTRACTED}
    keys["pablo.pdf"] = next(g["content_key"] for g in mf.list_groups(rid) if (g.get("extraction") or {}).get("first_name") == "Pablo")      # su representante es uno de dos alias del mismo contenido
    r0 = pilot.run_report(rid)["identity"]
    assert r0["readable"] == 5 and r0["coverage"]["with_proposal"] == 5 and r0["verdicts"]["pending"] == 5 and r0["proposal_quality"]["precision_pct"] is None
    assert r0["proposal_quality"]["unevaluated"] == 5 and r0["by_state"] == {"CORROBORATED": 5} and r0["extractor_versions"] == {"3": 5}
    pilot.record_verdict(rid, keys["Maria Fernandez.docx"], V.V_CORRECT, actor=OWNER)
    pilot.record_verdict(rid, keys["lucia.pdf"], V.V_CORRECT, actor=OWNER)
    pilot.record_verdict(rid, keys["CV Juan Perez v2.pdf"], V.V_CORRECTED, actor=OWNER, first_name="Juan", last_name="Perez")     # propuesta incorrecta
    pilot.record_verdict(rid, keys["pablo.pdf"], V.V_UNDETERMINED, actor=OWNER)                                               # propuesta que el owner no puede juzgar
    idn = pilot.run_report(rid)["identity"]
    q = idn["proposal_quality"]
    assert (q["evaluated"], q["correct"], q["wrong"], q["precision_pct"]) == (3, 2, 1, 66.7) and q["undetermined"] == 1 and q["unevaluated"] == 1       # el no determinado NO entra en la precisión
    assert idn["verdicts"] == {"pending": 1, "correct": 2, "corrected": 1, "undetermined": 1, "evaluated_documents": 4}
    assert idn["coverage"]["coverage_pct"] == 100.0 and q["by_state"]["CORROBORATED"] == {"correct": 2, "wrong": 1, "precision_pct": 66.7}


def test_absence_of_data_is_not_an_identity_error(sp):
    _, rid = processed(sp)
    a, b, c = key_of(rid, "Maria Fernandez.docx"), key_of(rid, "lucia.pdf"), key_of(rid, "CV Juan Perez v2.pdf")
    for key in (a, b, c):
        make_legacy(rid, key, first=None, last=None, text_name="Experiencia Laboral", flags=("NAME_MISSING",))
    pilot.record_verdict(rid, a, V.V_UNDETERMINED, actor=OWNER)
    pilot.record_verdict(rid, b, V.V_CORRECTED, actor=OWNER, first_name="Lucía", last_name="Gómez")
    idn = pilot.run_report(rid)["identity"]
    assert idn["coverage"]["without_proposal"] == 3 and idn["proposal_quality"]["wrong"] == 0 and idn["proposal_quality"]["evaluated"] == 0         # sin propuesta no hay error de precisión
    assert idn["undetermined"]["without_proposal_undetermined"] == 1 and idn["undetermined"]["without_proposal_resolved_manually"] == 1 and idn["undetermined"]["without_proposal_unevaluated"] == 1


def test_the_report_never_carries_personal_data_even_with_corrections(sp):
    _, rid = processed(sp)
    pilot.record_verdict(rid, key_of(rid, "lucia.pdf"), V.V_CORRECTED, actor=OWNER, first_name="Zulema", last_name="Quintana")
    blob = json.dumps(pilot.run_report(rid), ensure_ascii=False)
    assert "Zulema" not in blob and "Quintana" not in blob and not [p for p in PII if p in blob]


# ═══════════════════ lote derivado ═══════════════════
def legacy_source(sp):
    """Lote original con datos de la versión 1: una propuesta INCORRECTA (frase funcional), una correcta y una de conflicto."""
    world, rid = processed(sp)
    maria = make_legacy(rid, key_of(rid, "Maria Fernandez.docx"), first="Experiencia", last="Laboral", text_name="Experiencia Laboral", emails=["contacto@empresa.com"])
    lucia = make_legacy(rid, key_of(rid, "lucia.pdf"), first="Lucía", last="Gómez", text_name="Lucía Gómez", emails=["lucia.gomez@mail.com"], flags=())
    juan = make_legacy(rid, key_of(rid, "CV Juan Perez v2.pdf"), first="Sofía", last="Martínez", text_name="Sofía Martínez", emails=["sofia@mail.com"], flags=("NAME_CONFLICT", "NAME_SINGLE_SOURCE"))
    pilot.record_verdict(rid, maria, V.V_CORRECTED, actor=OWNER, first_name="María", last_name="Fernández")
    pilot.record_verdict(rid, lucia, V.V_CORRECT, actor=OWNER)
    pilot.record_verdict(rid, juan, V.V_UNDETERMINED, actor=OWNER)
    return world, rid, {"maria": maria, "lucia": lucia, "juan": juan}


def test_the_derived_batch_reevaluates_from_stored_data_without_touching_the_original_or_graph(sp, monkeypatch):
    world, rid, k = legacy_source(sp)
    before, downloads = snapshot(rid), list(world.downloads)
    verdicts_before = {key: json.dumps(v, sort_keys=True) for key, v in mf.list_verdicts(rid).items()}
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("la reevaluación no puede descargar")))
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("la reevaluación no puede llamar a Graph")))
    derived = pilot.start_rearbitration(rid, actor=OWNER)
    did = derived["run_id"]
    assert did != rid and derived["kind"] == "rearbitration" and derived["derived_from"]["run_id"] == rid and derived["derived_from"]["mode"] == "OFFLINE_FROM_STORED_DATA" and world.downloads == downloads
    assert snapshot(rid) == before and {key: json.dumps(v, sort_keys=True) for key, v in mf.list_verdicts(rid).items()} == verdicts_before         # el original es inmutable
    src_keys = {g["content_key"] for g in mf.list_groups(rid) if g["state"] in (V.EXTRACTED, V.ALIAS)}
    groups = {g["content_key"]: g for g in mf.list_groups(did)}
    assert set(groups) == src_keys and all(g["derived_from"] == {"run_id": rid, "content_key": g["content_key"]} for g in groups.values())          # trazable por content_key
    maria = groups[k["maria"]]["extraction"]
    assert maria["extractor_version"] == 3 and maria["first_name"] is None and "NAME_UNCORROBORATED" in maria["blocking"]                       # la frase funcional ya no es una propuesta
    assert maria["name_decision"]["rejected_candidates"][0]["tokens"] == "Experiencia Laboral" and "OFFLINE_REEVALUATION" in maria["name_decision"]["limits"]
    assert (groups[k["lucia"]]["extraction"]["first_name"], groups[k["lucia"]]["extraction"]["name_decision"]["rule"]) == ("Lucía", "R2")
    conflict = groups[k["juan"]]["extraction"]
    assert conflict["first_name"] is None and "NAME_CONFLICT" in conflict["blocking"] and "NAME_SINGLE_SOURCE" not in conflict["flags"] and {a["tokens"] for a in conflict["name_decision"]["alternatives"]} == {"Sofía Martínez", "Juan Perez"}
    audit = mf.get_audit(did, k["maria"])["events"]
    assert audit[-1]["event"] == "REARBITRATED" and audit[-1]["from_run"] == rid
    assert [e["event"] for e in mf.get_audit(rid, k["maria"])["events"]].count("REARBITRATED") == 0


def test_the_derived_batch_never_processes_or_downloads(sp):
    world, rid, _ = legacy_source(sp)
    did = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    downloads = list(world.downloads)
    assert pilot.process_run(did, TOKEN, sleep=lambda s: None) == {"processed": 0, "failed": 0, "remaining": 0, "stopped_reason": "NOT_A_PILOT_RUN"} and world.downloads == downloads
    with pytest.raises(pilot.PilotInputError):
        pilot.start_rearbitration(did, actor=OWNER)                                                                            # no se reevalúa un derivado


def test_comparison_shows_what_happened_to_each_original_proposal_against_the_owner_verdicts(sp):
    _, rid, k = legacy_source(sp)
    did = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    cmp = pilot.run_report(did)["comparison"]
    assert cmp["source_run_id"] == rid
    assert cmp["original_wrong_proposals"] == {"total": 1, "removed": 1, "changed": 0, "still_same": 0}                          # la propuesta incorrecta se retiró
    assert cmp["original_correct_proposals"] == {"total": 1, "kept": 1, "lost": 0, "changed": 0}                                  # la correcta se conservó (sin regresión)
    assert cmp["against_confirmed_identity"] == {"known": 2, "matches": 1, "abstains": 1, "contradicts": 0}
    assert cmp["moves"].get("removed", 0) >= 1 and cmp["moves"].get("unchanged", 0) >= 1
    assert pilot.run_report(rid)["comparison"] is None
    blob = json.dumps(cmp, ensure_ascii=False)
    assert "María" not in blob and "Lucía" not in blob


def test_a_contradicting_derived_proposal_is_reported_as_such(sp):
    _, rid, k = legacy_source(sp)
    pilot.record_verdict(rid, k["lucia"], V.V_CORRECTED, actor=OWNER, first_name="Lucía", last_name="Otra")                       # el owner dice que la identidad real es otra
    did = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    cmp = pilot.run_report(did)["comparison"]
    assert cmp["against_confirmed_identity"]["contradicts"] == 1 and cmp["original_wrong_proposals"]["still_same"] == 1


def test_verdicts_of_each_batch_never_overwrite_each_other_and_the_confirmed_identity_is_a_reference(sp):
    _, rid, k = legacy_source(sp)
    did = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    original_verdict = json.dumps(mf.get_verdict(rid, k["lucia"]), sort_keys=True)
    rows = {r["content_key"]: r for r in pilot.review_view(did)}
    assert rows[k["maria"]]["reference_identity"] == {"first_name": "María", "last_name": "Fernández"} and rows[k["maria"]]["original_proposal"]["first_name"] == "Experiencia"     # referencia reutilizable
    assert rows[k["lucia"]]["reference_identity"] == {"first_name": "Lucía", "last_name": "Gómez"} and rows[k["lucia"]]["verdict"] is None                                     # pendiente en el derivado
    pilot.record_verdict(did, k["lucia"], V.V_CORRECT, actor=OWNER)
    pilot.record_verdict(did, k["maria"], V.V_CORRECTED, actor=OWNER, first_name="María", last_name="Fernández")                  # usa la referencia como corrección en el derivado
    assert json.dumps(mf.get_verdict(rid, k["lucia"]), sort_keys=True) == original_verdict                                        # el dictamen del original no cambió
    assert mf.get_verdict(did, k["lucia"])["run_id"] == did
    assert mf.get_verdict(rid, k["maria"])["current"]["status"] == "CORRECTED" and mf.get_verdict(did, k["maria"])["history"][0]["evaluated_proposal"] is None     # cada lote conserva la propuesta que evaluó
    assert mf.get_verdict(rid, k["maria"])["history"][0]["evaluated_proposal"]["first_name"] == "Experiencia"
    q = pilot.run_report(did)["identity"]["proposal_quality"]
    assert q["correct"] == 1 and q["wrong"] == 0 and pilot.run_report(rid)["identity"]["proposal_quality"]["wrong"] == 1                # métricas independientes por lote


def test_the_derived_batch_requires_available_source_data(sp):
    _, rid, k = legacy_source(sp)
    pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    with pytest.raises(pilot.PilotInputError):
        pilot.start_rearbitration(rid, actor=OWNER)
    with pytest.raises(pilot.PilotInputError):
        pilot.record_verdict(rid, k["lucia"], V.V_UNDETERMINED, actor=OWNER)


def test_discarding_removes_the_verdict_files_with_manual_corrections_and_keeps_counts_and_audit(sp):
    _, rid, k = legacy_source(sp)
    assert mf.staging_files_present(rid) > 7                                                                                      # staging + dictámenes
    pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    assert mf.staging_files_present(rid) == 0 and mf.list_verdicts(rid) == {} and mf.get_audit(rid, k["maria"])["events"][-1]["event"] == "STAGING_DISCARDED"
    final = pilot.run_report(rid)
    assert final["identity"]["verdicts"]["corrected"] == 1 and final["identity"]["proposal_quality"]["wrong"] == 1                 # los conteos se conservan en el reporte final
    assert "María" not in json.dumps(final, ensure_ascii=False)
    with pytest.raises(pilot.PilotInputError):
        pilot.record_verdict(rid, k["maria"], V.V_UNDETERMINED, actor=OWNER)


# ═══════════════════ interfaz (rutas solo-owner) ═══════════════════
def verdict_post(c, rid, key, status, **names):
    return c.post(f"{BASE}/{rid}/veredicto", data={"content_key": key, "status": status, **names})


def test_detail_page_shows_proposal_alternatives_and_verdict_forms_only_while_data_exists(sp, monkeypatch):
    _, rid, k = legacy_source(sp)
    owner = client_for(OWNER, monkeypatch)
    html = owner.get(f"{BASE}/{rid}").text
    assert "data-verdict-box" in html and f'hx-post="{BASE}/{rid}/veredicto"' in html and 'name="status" value="CORRECTED"' in html and 'name="status" value="UNDETERMINED"' in html
    assert "data-alternatives" in html and "data-proposal" in html and "data-pilot-rearbitrate-form" in html and "Crear lote derivado" in html
    assert "data-proposal-precision" in html and "Precisión de propuestas evaluadas" in html and "Cobertura (con propuesta / legibles)" in html
    pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    after = owner.get(f"{BASE}/{rid}").text
    assert "data-verdict-box" not in after and "data-pilot-rearbitrate-form" not in after


def test_verdict_route_saves_returns_the_box_and_refreshes_the_metrics_out_of_band(sp, monkeypatch):
    _, rid = processed(sp)
    owner = client_for(OWNER, monkeypatch)
    key = key_of(rid, "lucia.pdf")
    ok = verdict_post(owner, rid, key, "CORRECT")
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store" and 'data-verdict="CORRECT"' in ok.text and "Propuesta correcta" in ok.text
    assert 'id="cvrun-panel"' in ok.text and 'hx-swap-oob="true"' in ok.text and "data-verdict-correct>1<" in ok.text.replace("\r", "").replace("\n", "")                    # métricas refrescadas en la misma respuesta
    assert mf.get_verdict(rid, key)["current"]["actor"] == OWNER
    manual = verdict_post(owner, rid, key, "CORRECTED", first_name="Lucía", last_name="Gómez Paz")
    assert 'data-verdict="CORRECTED"' in manual.text and "Gómez Paz" in manual.text and "2 dictámenes" in manual.text


def test_verdict_route_errors_are_200_with_a_notice_and_change_nothing(sp, monkeypatch):
    _, rid = processed(sp)
    owner = client_for(OWNER, monkeypatch)
    key = key_of(rid, "lucia.pdf")
    for data in ({"status": "CORRECTED"}, {"status": "CORRECTED", "first_name": "x1", "last_name": "y"}, {"status": "NOPE"}):
        res = verdict_post(owner, rid, key, data.pop("status"), **data)
        assert res.status_code == 200 and 'data-error-kind="verdict_invalid"' in res.text and 'data-verdict="PENDING"' in res.text
    assert mf.list_verdicts(rid) == {}
    assert verdict_post(owner, rid, "no-es-clave", "CORRECT").status_code == 404 and verdict_post(owner, "ch-20200101T000000Z", key, "CORRECT").status_code == 404
    assert verdict_post(owner, rid, "0" * 16, "CORRECT").status_code == 404                                                       # clave válida pero inexistente en el lote


def test_rearbitrate_route_creates_the_derived_batch_and_renders_its_comparison(sp, monkeypatch):
    world, rid, k = legacy_source(sp)
    owner = client_for(OWNER, monkeypatch)
    downloads = list(world.downloads)
    res = owner.post(f"{BASE}/{rid}/reevaluar")
    assert res.status_code == 200 and "data-pilot-derived" in res.text and "data-pilot-comparison" in res.text and world.downloads == downloads and not [p for p in PII if p in res.text]
    did = [r["run_id"] for r in mf.list_runs() if r.get("kind") == "rearbitration"][0]
    assert did in res.text and "no se leyó ningún documento" in res.text
    page = owner.get(f"{BASE}/{did}").text
    assert 'data-reference-identity' in page and "Identidad confirmada en el lote original" in page and "original: Experiencia Laboral" in page
    assert 'data-pilot-rearbitrate-form' not in page                                                                              # un derivado no se reevalúa de nuevo
    listing = owner.get(BASE).text
    assert "derivado de " + rid in listing
    bad = owner.post(f"{BASE}/{did}/reevaluar")
    assert 'data-error-kind="invalid_input"' in bad.text
    assert len([r for r in mf.list_runs() if r.get("kind") == "rearbitration"]) == 1


def test_extraction_version_and_verdict_data_survive_processing_idempotently(sp):
    world, rid = processed(sp)
    key = key_of(rid, "lucia.pdf")
    pilot.record_verdict(rid, key, V.V_CORRECT, actor=OWNER)
    before = json.dumps(mf.get_verdict(rid, key), sort_keys=True)
    assert pilot.process_run(rid, TOKEN, sleep=lambda s: None)["processed"] == 0 and json.dumps(mf.get_verdict(rid, key), sort_keys=True) == before


def test_routes_record_the_authenticated_actor_from_the_session_subject(sp, monkeypatch):
    """El claim de la sesión es `sub` (no `email`): la autorización del lote y la autoría de cada dictamen deben quedar con el owner, no vacías."""
    sp()
    owner = client_for(OWNER, monkeypatch)
    res = owner.post(f"{BASE}/piloto", data={"graph_token": TOKEN, "share_url": SHARE, "sample_size": "10", "authorization": pilot.AUTHORIZATION_PHRASE})
    rid = [r["run_id"] for r in mf.list_runs()][0]
    assert rid in res.text and mf.get_run(rid)["authorization"]["confirmed_by"] == OWNER and mf.get_run(rid)["created_by"] == OWNER


# ═══════════════════ extractor v3: variante normalizada en el lote derivado ═══════════════════
def rc_source(sp):
    """Lote original con el defecto reportado: v1 propuso «Nacional Experimental»; el archivo se llama «RC Eliana Ojeda»; el owner confirmó «Eliana Ojeda»."""
    world, rid = processed(sp)
    key = make_legacy(rid, key_of(rid, "Maria Fernandez.docx"), first="Nacional", last="Experimental", text_name="Nacional Experimental", emails=["eliana.ojeda@gmail.com"])
    group = mf.get_group(rid, key)
    group["representative"] = {**group["representative"], "name": "RC Eliana Ojeda.pdf"}
    mf.put_group(rid, key, group)
    pilot.record_verdict(rid, key, V.V_CORRECTED, actor=OWNER, first_name="Eliana", last_name="Ojeda")
    return world, rid, key


def test_the_derived_batch_offers_the_normalized_variant_from_persisted_data_and_requires_review(sp, monkeypatch):
    world, rid, key = rc_source(sp)
    before, downloads = snapshot(rid), list(world.downloads)
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("la reevaluación no descarga")))
    did = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    ext = mf.get_group(did, key)["extraction"]
    assert (ext["first_name"], ext["last_name"], ext["extractor_version"]) == ("Eliana", "Ojeda", 3) and "NAME_EDGE_TOKEN_UNEXPLAINED" in ext["blocking"]
    assert (ext["name_decision"]["rule"], ext["name_decision"]["state"]) == ("R2", "CORROBORATED")
    edge = next(a for a in ext["name_decision"]["alternatives"] if "edge_token" in a)["edge_token"]
    assert edge["original"] == "Rc Eliana Ojeda" and edge["token"] == "Rc"                                           # se conserva el valor original
    assert mf.get_group(did, key)["review"]["required"] is True and "NAME_EDGE_TOKEN_UNEXPLAINED" in mf.get_group(did, key)["review"]["reasons"]
    assert snapshot(rid) == before and world.downloads == downloads                                                    # el lote original y sus dictámenes no se tocan
    cmp = pilot.run_report(did)["comparison"]
    assert cmp["against_confirmed_identity"] == {"known": 1, "matches": 1, "abstains": 0, "contradicts": 0}            # ya no contradice la identidad confirmada por el owner
    assert cmp["original_wrong_proposals"]["changed"] == 1


def test_earlier_derived_batches_stay_intact_when_a_new_one_is_created(sp):
    _, rid, key = rc_source(sp)
    first = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    group = mf.get_group(first, key)
    group["extraction"] = {**group["extraction"], "extractor_version": 2, "first_name": "Rc Eliana", "last_name": "Ojeda"}               # lote derivado con datos v2 (el defecto reportado)
    mf.put_group(first, key, group)
    pilot.record_verdict(first, key, V.V_CORRECTED, actor=OWNER, first_name="Eliana", last_name="Ojeda")
    frozen = {"first": snapshot(first), "verdicts": json.dumps(mf.get_verdict(first, key), sort_keys=True), "original": snapshot(rid)}
    second = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    assert second != first and mf.get_group(second, key)["extraction"]["first_name"] == "Eliana"
    assert snapshot(first) == frozen["first"] and json.dumps(mf.get_verdict(first, key), sort_keys=True) == frozen["verdicts"] and snapshot(rid) == frozen["original"]
    assert pilot.run_report(first)["identity"]["extractor_versions"] != pilot.run_report(second)["identity"]["extractor_versions"]            # los lotes v2 y v3 se distinguen en el reporte


def test_the_owner_view_shows_the_edge_token_and_the_original_value(sp, monkeypatch):
    _, rid, key = rc_source(sp)
    did = pilot.start_rearbitration(rid, actor=OWNER)["run_id"]
    html = client_for(OWNER, monkeypatch).get(f"{BASE}/{did}").text
    assert "data-edge-token" in html and "token de borde «Rc» (inicio) en «Rc Eliana Ojeda»" in html and "NAME_EDGE_TOKEN_UNEXPLAINED" in html and "Eliana" in html
