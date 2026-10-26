"""Entrega C — ACEPTACIÓN CONTROLADA de borradores de «Históricos sin validación»: revisión de solo lectura (`preflight`), confirmación (`confirm`), exclusión común con la importación y los ganchos aditivos de `import_exec`. Todo sintético: SharePoint,
Graph y Spaces simulados con el mismo mundo de las pruebas de la ejecución (con inyección de fallos). Nada contra DigitalOcean ni SharePoint reales."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

import config
from tests.webapp.test_cv_history_import_exec import OWNER, TOKEN, banco_profiles, begin, by_name, create_in_banco, env, ex, mf, planned_run, planning, profile_document_repository, profile_repository, run_all, seeded_snapshot  # noqa: F401
from tests.webapp.test_cv_history_pilot import home, sp  # noqa: F401  (fixtures que usa `env`)
from webapp.services import cv_history_acceptance as acc, cv_history_drafts as drafts, human_capital_service

OTHER = "otra-persona@danwor.com"
ALL_ACKS = (acc.ACK_IRREVERSIBLE, acc.ACK_NO_EMAIL, acc.ACK_MATCHES, acc.ACK_DUPLICATE, acc.ACK_WEAK_LINK)
PABLO = {"first_name": "Pablo", "last_name": "Ruiz", "email": "pablo.ruiz@a.com", "phone": "", "notes": "observación interna que NO va al perfil"}


def digest() -> dict:
    root = Path(config.BASE_DIR)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def ready(env):
    """Importación ya ejecutada: quedan por resolver la excepción de Pablo (varios emails) y la del PDF escaneado."""
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    pablo = next(k for k, d in mf.get_plan(rid)["decisions"].items() if d["reasons"][:1] == [planning.MULTIPLE_EMAILS])
    return store, rid, pablo


def draft(rid, key, **over):
    current = mf.get_draft(rid, key)
    return drafts.save(rid, key, {**PABLO, **over}, actor=OWNER, expected_revision=(current or {}).get("revision", 0))["doc"]


def go(rid, key, pre, *, op="create", acks=None, target=None, store_token=TOKEN, **over):
    """Confirma lo que mostró la revisión `pre` (con todas las confirmaciones que ella exige, salvo que se indique otra cosa)."""
    kw = {"op": op, "fingerprint": pre["fingerprint"], "draft_revision": pre["draft"]["revision"], "acks": list(pre["create"]["acks"] if op == "create" else pre["link"]["acks"]) if acks is None else acks}
    if op == "link":
        match = next(m for m in pre["matches"] if m["profile_id"] == target)
        kw.update(target_profile_id=target, target_revision=match["revision"])
        if not match["relevant"] and acks is None:
            kw["acks"] = kw["acks"] + [acc.ACK_WEAK_LINK]
    kw.update(over)
    return acc.confirm(rid, key, store_token, actor=OWNER, **kw)


# ═══════════════════ C1 · revisión de solo lectura ═══════════════════
def test_preflight_without_a_saved_draft_blocks_and_writes_nothing(env):
    store, rid, pablo = ready(env)
    before = digest()
    pre = acc.preflight(rid, pablo)
    assert pre["fingerprint"] is None and pre["draft"] is None and any("Guardá un borrador" in b for b in pre["blockers"]) and digest() == before
    with pytest.raises(mf.ManifestNotFoundError):
        acc.preflight("ch-20200101T000000Z-aaaa", pablo)


def test_preflight_shows_what_would_happen_is_read_only_and_loading_a_draft_never_accepts(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    before, profiles, puts = digest(), seeded_snapshot(), list(store.puts)
    pre = acc.preflight(rid, pablo)
    assert not pre["blockers"] and pre["draft"]["revision"] == 1 and pre["create"]["allowed"] and pre["create"]["fields"] == {"first_name": "Pablo", "last_name": "Ruiz", "email": "pablo.ruiz@a.com", "phone": None}
    assert pre["create"]["duplicate"] is None and pre["relevant_total"] == 1 and pre["matches"][0]["name"] == "Pablo Ruiz" and pre["matches"][0]["strength"] == "PROBABLE"                      # el Pablo del Banco: coincidencia probable por nombre
    assert pre["create"]["acks"] == [acc.ACK_IRREVERSIBLE, acc.ACK_MATCHES] and pre["link"]["acks"] == [acc.ACK_IRREVERSIBLE] and len(pre["fingerprint"]) == 64
    assert digest() == before and seeded_snapshot() == profiles and store.puts == puts and mf.get_ledger(rid, pablo) is None                                                  # revisar no escribe ni acepta nada
    assert acc.preflight(rid, pablo)["fingerprint"] == pre["fingerprint"]                                                                                                      # determinista


def test_find_duplicate_is_kept_as_is_and_complemented_with_the_reconcile_matches(env):
    store, rid, pablo = ready(env)
    twin = create_in_banco("Pablo", "Ruiz", "pablo.ruiz@a.com")
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    dup = human_capital_service.find_duplicate(ex._manual_profile_fields(mf.get_group(rid, pablo), "Pablo", "Ruiz", "pablo.ruiz@a.com", ""), profile_repository.list())
    assert pre["create"]["duplicate"]["profile_id"] == dup["profile_id"] == twin["profile_id"] and pre["create"]["acks"] == [acc.ACK_IRREVERSIBLE, acc.ACK_MATCHES, acc.ACK_DUPLICATE]
    strong = next(m for m in pre["matches"] if m["profile_id"] == twin["profile_id"])
    assert strong["strength"] == "STRONG" and "EMAIL_EXACT" in strong["signals"] and {m["name"] for m in pre["matches"]} == {"Pablo Ruiz"} and pre["relevant_total"] == 2


def test_the_universe_of_matches_is_not_capped_and_the_fingerprint_covers_all_of_it(env):
    store, rid, pablo = ready(env)
    for n in range(7):
        create_in_banco("Pablo", "Ruiz", f"homonimo{n}@x.com")                                                                                                                  # 8 homónimos con el del Banco: más que el tope de 5
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    assert pre["relevant_total"] == 8 and len([m for m in pre["matches"] if m["relevant"]]) == 8                                                                                # se muestran todas las relevantes, no cinco
    capped = len(__import__("webapp.services.cv_reconcile", fromlist=["x"]).reconcile_with_profiles({"first_name": "Pablo", "last_name": "Ruiz", "email": "pablo.ruiz@a.com"}, profile_repository.list()))
    assert capped == 5                                                                                                                                                          # la función existente sí recorta
    hidden = sorted(profile_repository.list(), key=lambda p: p["profile_id"])[-1]
    fp = pre["fingerprint"]
    create_in_banco("Pablo", "Ruiz", "noveno@x.com")                                                                                                                            # aparece un candidato relevante nuevo
    assert acc.preflight(rid, pablo)["fingerprint"] != fp
    base = acc.preflight(rid, pablo)["fingerprint"]
    victim = next(m for m in acc.preflight(rid, pablo)["matches"][-1:])                                                                                                          # cambia (revisión) una coincidencia que ni siquiera estaba entre las cinco primeras
    profile_repository.update(victim["profile_id"], victim["revision"], {**{k: v for k, v in profile_repository.get(victim["profile_id"]).items() if k in ("first_name", "last_name", "email", "phone", "linkedin_url", "location", "profile_skills", "seniority", "source", "tags", "potential_rating", "potential_mark")}, "notes": "editado"})
    assert acc.preflight(rid, pablo)["fingerprint"] != base
    assert hidden


def test_the_fingerprint_changes_with_the_draft_the_ledger_and_deletions_but_not_with_unrelated_profiles(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    base = acc.preflight(rid, pablo)
    create_in_banco("Nadie", "Relacionado", "nadie@x.com")
    assert acc.preflight(rid, pablo)["fingerprint"] == base["fingerprint"]                                                                                                      # un perfil sin relación no invalida
    draft(rid, pablo, notes="otra observación")
    assert acc.preflight(rid, pablo)["fingerprint"] != base["fingerprint"]                                                                                                      # otra revisión del borrador
    now = acc.preflight(rid, pablo)
    target = next(m for m in now["matches"] if m["relevant"])
    profile_repository.soft_delete(target["profile_id"])
    after = acc.preflight(rid, pablo)
    assert after["fingerprint"] != now["fingerprint"] and next(m for m in after["matches"] if m["profile_id"] == target["profile_id"])["deleted"] and not next(m for m in after["matches"] if m["profile_id"] == target["profile_id"])["selectable"]


def test_alternative_emails_of_the_document_and_phone_only_matches_are_reported_honestly(env):
    store, rid, pablo = ready(env)
    other_email = create_in_banco("Quien", "Sea", "pruiz@b.com")                                                                                                                # el OTRO email que trae el documento
    shared_phone = create_in_banco("Otra", "Gente", None, phone="+54 9 11 5555-0101")
    draft(rid, pablo, email="pablo.ruiz@a.com", phone="011 5555 0101")
    pre = acc.preflight(rid, pablo)
    alt = next(m for m in pre["matches"] if m["profile_id"] == other_email["profile_id"])
    phone = next(m for m in pre["matches"] if m["profile_id"] == shared_phone["profile_id"])
    assert alt["strength"] == "STRONG" and alt["signals"] == ["EMAIL_EXACT"] and phone["phone_only"] is True and phone["signals"] == ["PHONE_MATCH"]                                  # celular solo: se rotula, no es identidad


def test_an_incomplete_draft_cannot_be_created_and_a_resolved_content_cannot_be_reviewed(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo, first_name="", last_name="")
    pre = acc.preflight(rid, pablo)
    assert pre["create"]["allowed"] is False and "nombre y apellido" in " ".join(pre["create"]["blockers"]).lower()
    assert acc.confirm(rid, pablo, TOKEN, actor=OWNER, op="create", fingerprint=pre["fingerprint"], draft_revision=1, acks=list(ALL_ACKS))["ok"] is False and mf.get_ledger(rid, pablo) is None
    scanned = by_name(rid)["escaneado.pdf"]
    ex.dismiss(rid, scanned, actor=OWNER, reason="NOT_A_CV")
    assert any("resuelto" in b for b in acc.preflight(rid, scanned)["blockers"])


# ═══════════════════ C2 · ganchos aditivos de import_exec ═══════════════════
def test_without_the_new_arguments_the_existing_manual_actions_behave_as_before_and_leave_no_acceptance(env):
    store, rid, pablo = ready(env)
    done = ex.complete_identity(rid, pablo, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz", email="pablo.ruiz@a.com", duplicate_confirmed=True)
    assert done["ok"] and done["outcome"] == ex.DONE and "accepted" not in mf.get_ledger(rid, pablo) and "accepted_draft_revision" not in profile_repository.get(done["profile_id"])["import_origin"]
    assert ex.exception_row(rid, pablo)["ledger"]["accepted"] is None


def test_the_acceptance_is_recorded_in_the_ledger_the_profile_origin_and_the_audit_without_personal_data(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    res = go(rid, pablo, pre)
    assert res["ok"] and res["accepted"] and res["outcome"] == ex.DONE and not res.get("idempotent")
    led = mf.get_ledger(rid, pablo)
    record = led["accepted"]
    assert led["state"] == ex.DONE and led["action"] == ex.MANUAL_CREATE and (record["by"], record["op"], record["profile_id"], record["draft_revision"]) == (OWNER, "create", res["profile_id"], 1)
    assert record["confirmed_sha256"] == acc.confirmed_hash("create", res["profile_id"], {"first_name": "Pablo", "last_name": "Ruiz", "email": "pablo.ruiz@a.com", "phone": None}) and record["fingerprint"] == pre["fingerprint"]
    assert set(record["acks"]) == {acc.ACK_IRREVERSIBLE, acc.ACK_MATCHES} and record["duplicate_confirmed"] is False and record["target_revision"] is None
    profile = profile_repository.get(res["profile_id"])
    assert profile["import_origin"]["accepted_draft_revision"] == 1 and profile["import_origin"]["confirmed_sha256"] == record["confirmed_sha256"] and profile["import_origin"]["completed_by"] == OWNER
    assert profile["tags"] == "Histórico SharePoint" and profile.get("notes") is None and "observación interna" not in json.dumps(profile, ensure_ascii=False)                                  # D5: las observaciones NO se copian
    events = [e for e in mf.get_audit(rid, pablo)["events"] if e["event"] == "IMPORT_DONE"]
    assert events and events[-1]["draft_revision"] == 1
    raw = json.dumps([led, mf.get_audit(rid, pablo)], ensure_ascii=False)
    assert not any(secret in raw for secret in ("Pablo", "Ruiz", "pablo.ruiz@a.com", "observación"))                                                                           # el libro y la auditoría no llevan datos personales
    assert profile_document_repository.get(led["document_id"])["profile_id"] == res["profile_id"] and ex.exception_row(rid, pablo)["status"] == "resolved"


def test_link_requires_the_confirmed_revision_of_the_target_and_does_not_touch_the_profile(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    target = next(m for m in pre["matches"] if m["relevant"])
    snapshot = seeded_snapshot()
    profile = profile_repository.get(target["profile_id"])
    profile_repository.update(target["profile_id"], target["revision"], {k: profile.get(k) for k in ("first_name", "last_name", "email", "phone", "linkedin_url", "location", "profile_skills", "seniority", "source", "tags", "notes", "potential_rating", "potential_mark")} | {"notes": "cambió"})
    edited = seeded_snapshot()
    stale = ex.link_existing(rid, pablo, TOKEN, actor=OWNER, profile_id=target["profile_id"], expected_target_revision=target["revision"])
    assert stale["ok"] is False and stale["stale"] is True and seeded_snapshot() == edited and mf.get_ledger(rid, pablo) is None                                                       # cambió: nada se escribe
    fresh = profile_repository.get(target["profile_id"])
    ok = ex.link_existing(rid, pablo, TOKEN, actor=OWNER, profile_id=target["profile_id"], expected_target_revision=fresh["revision"], accepted={"op": "link", "profile_id": target["profile_id"], "confirmed_sha256": "h", "draft_revision": 1})
    assert ok["ok"] and ok["outcome"] == ex.DONE and seeded_snapshot() == edited and snapshot != edited and mf.get_ledger(rid, pablo)["accepted"]["profile_id"] == target["profile_id"]      # vincular no modifica ningún campo
    assert [d["filename"] for d in profile_document_repository.list_for_profile(target["profile_id"])]


def test_old_manual_actions_cannot_overwrite_a_pending_acceptance_and_dismiss_is_refused(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    store.deny_put = True
    first = go(rid, pablo, pre)
    assert first["ok"] and first["outcome"].startswith("STOP:") and mf.get_ledger(rid, pablo)["accepted"] and mf.get_ledger(rid, pablo)["state"] != ex.DONE                             # aceptación a medias
    snapshot = json.dumps(mf.get_ledger(rid, pablo), sort_keys=True)
    old = ex.complete_identity(rid, pablo, TOKEN, actor=OWNER, first_name="Otro", last_name="Nombre", duplicate_confirmed=True)
    assert old["ok"] is False and "pendiente de completarse" in old["error"]
    zoe = next(p for p in profile_repository.list() if p["first_name"] == "Zoe")
    assert ex.link_existing(rid, pablo, TOKEN, actor=OWNER, profile_id=zoe["profile_id"])["ok"] is False
    assert ex.dismiss(rid, pablo, actor=OWNER, reason="OTHER")["ok"] is False and json.dumps(mf.get_ledger(rid, pablo), sort_keys=True) == snapshot


# ═══════════════════ C3 · confirmación ═══════════════════
def test_confirm_creates_the_profile_uploads_the_cv_resolves_the_exception_and_freezes_the_draft(env):
    store, rid, pablo = ready(env)
    saved = draft(rid, pablo)
    before_profiles = len(profile_repository.list())
    res = go(rid, pablo, acc.preflight(rid, pablo))
    assert res["ok"] and res["outcome"] == ex.DONE and len(profile_repository.list()) == before_profiles + 1
    assert ex.exceptions_overview(rid)["by_reason"].get(planning.MULTIPLE_EMAILS) is None and ex.exceptions_overview(rid, status="resolved")["total"] == 1
    assert mf.get_draft(rid, pablo) == saved                                                                                                                                  # el borrador no se toca: queda conservado
    with pytest.raises(drafts.DraftError, match="ya no es una excepción|ya fue aceptado"):
        drafts.save(rid, pablo, {**PABLO, "notes": "cambio posterior"}, actor=OWNER, expected_revision=1)
    assert drafts.accepted_drafts(rid) == [{"content_key": pablo, "op": "create", "profile_id": res["profile_id"], "accepted_by": OWNER, "accepted_at": mf.get_ledger(rid, pablo)["accepted"]["at"], "accepted_revision": 1,
                                            "draft_revision": 1, "unchanged_since_acceptance": True}]
    assert ex.active_drafts(rid) == 0 and ex.discard_blockers(rid) == {"remaining": 0, "exceptions_open": 1}                                                                    # resuelto: ya no bloquea el descarte por borradores (queda el PDF escaneado)


def test_confirm_refuses_a_stale_fingerprint_without_writing_anything(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    create_in_banco("Pablo", "Ruiz", "pablo.ruiz@a.com")                                                                                                                        # aparece un duplicado fuerte entre la revisión y la confirmación
    before, profiles, puts = digest(), seeded_snapshot(), list(store.puts)
    res = go(rid, pablo, pre)
    assert res["ok"] is False and res["stale"] is True and res["preflight"]["create"]["duplicate"] and digest() == before and store.puts == puts
    assert mf.get_ledger(rid, pablo) is None and seeded_snapshot() == profiles                                                  # el Banco quedó exactamente como estaba antes del intento
    fresh = acc.preflight(rid, pablo)
    assert go(rid, pablo, fresh, acks=[acc.ACK_IRREVERSIBLE, acc.ACK_MATCHES])["missing_acks"] == [acc.ACK_DUPLICATE]                                                           # con lo nuevo, faltan las confirmaciones nuevas
    assert go(rid, pablo, fresh)["ok"]


def test_confirm_requires_every_acknowledgement_the_review_demanded(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo, email="")
    pre = acc.preflight(rid, pablo)
    assert pre["create"]["acks"] == [acc.ACK_IRREVERSIBLE, acc.ACK_NO_EMAIL, acc.ACK_MATCHES, acc.ACK_DUPLICATE]                  # sin email el aviso de duplicado es por nombre (la regla de siempre) y ya existe un «Pablo Ruiz»
    before = digest()
    for given in ([], [acc.ACK_IRREVERSIBLE], [acc.ACK_IRREVERSIBLE, acc.ACK_NO_EMAIL], [acc.ACK_NO_EMAIL, acc.ACK_MATCHES, acc.ACK_DUPLICATE], [acc.ACK_IRREVERSIBLE, acc.ACK_NO_EMAIL, acc.ACK_MATCHES]):
        res = go(rid, pablo, pre, acks=given)
        assert res["ok"] is False and res["missing_acks"] == [a for a in pre["create"]["acks"] if a not in given] and res["error"].startswith("Faltan confirmaciones")
    assert digest() == before
    assert go(rid, pablo, pre)["ok"] and profile_repository.get(mf.get_ledger(rid, pablo)["profile_id"])["email"] is None                                                       # D2: se crea sin email, con advertencia y confirmación


def test_idempotent_success_only_for_the_same_operation_target_and_data_hash(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    first = go(rid, pablo, pre)
    puts, profiles, ledger = list(store.puts), seeded_snapshot(), json.dumps(mf.get_ledger(rid, pablo), sort_keys=True)
    again = go(rid, pablo, pre)                                                                                                                                                 # doble clic / reintento con la MISMA decisión
    assert again["ok"] and again["idempotent"] is True and again["profile_id"] == first["profile_id"] and store.puts == puts and seeded_snapshot() == profiles
    assert json.dumps(mf.get_ledger(rid, pablo), sort_keys=True) == ledger and len([p for p in profile_repository.list() if p["first_name"] == "Pablo" and p.get("import_origin")]) == 1
    zoe = next(p for p in profile_repository.list() if p["first_name"] == "Zoe")
    other_op = acc.confirm(rid, pablo, TOKEN, actor=OWNER, op="link", target_profile_id=zoe["profile_id"], target_revision=zoe["revision"], fingerprint="x", draft_revision=1, acks=list(ALL_ACKS))
    assert other_op["ok"] is False and other_op["conflict"]["reason"] == "ALREADY_ACCEPTED_DIFFERENTLY" and other_op["conflict"]["op"] == "create" and not other_op.get("idempotent")      # otra operación: conflicto, nunca éxito
    assert store.puts == puts and mf.get_ledger(rid, pablo)["accepted"]["op"] == "create"


def test_a_different_data_hash_after_the_acceptance_is_a_conflict_not_an_apparent_success(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    store.deny_put = True
    assert go(rid, pablo, pre)["outcome"].startswith("STOP:")                                                                                                                  # perfil creado, subida pendiente
    profile_id = mf.get_ledger(rid, pablo)["profile_id"]
    with pytest.raises(drafts.DraftError, match="ya fue aceptado"):
        drafts.save(rid, pablo, {**PABLO, "last_name": "Ruiz Diaz"}, actor=OWNER, expected_revision=1)                                                                          # el borrador queda inmutable al aceptarse
    forced = dict(mf.get_draft(rid, pablo), revision=2, fields={**PABLO, "last_name": "Ruiz Diaz"})
    mf.update_draft(rid, pablo, lambda previous: forced)                                                                                                                         # (alguien lo reescribe por debajo de la API)
    store.deny_put = False
    res = acc.confirm(rid, pablo, TOKEN, actor=OWNER, op="create", fingerprint="x", draft_revision=2, acks=list(ALL_ACKS))
    assert res["ok"] is False and res["conflict"]["reason"] == "ALREADY_ACCEPTED_DIFFERENTLY" and profile_repository.get(profile_id)["last_name"] == "Ruiz" and mf.get_ledger(rid, pablo)["state"] != ex.DONE


def test_partial_graph_failure_keeps_the_acceptance_and_the_same_decision_resumes_without_duplicates(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    store.deny_put = True
    first = go(rid, pablo, pre)
    led = mf.get_ledger(rid, pablo)
    assert first["ok"] and first["outcome"] == "STOP:PERMISSION_DENIED" and led["accepted"]["by"] == OWNER and led["profile_created"] and ex.exception_row(rid, pablo)["status"] == "in_progress"
    created = [p for p in profile_repository.list() if (p.get("import_origin") or {}).get("content_key") == pablo]
    assert len(created) == 1
    store.deny_put = False
    stale_pre = go(rid, pablo, pre)                                                                                                                                             # la huella vieja ya no coincide (el Banco incluye lo creado), pero es la MISMA decisión: se reanuda
    assert stale_pre["ok"] and stale_pre["outcome"] == ex.DONE and not stale_pre.get("idempotent")
    done = mf.get_ledger(rid, pablo)
    assert done["state"] == ex.DONE and done["accepted"] == led["accepted"] and len([p for p in profile_repository.list() if (p.get("import_origin") or {}).get("content_key") == pablo]) == 1       # quién y cuándo aceptó, intactos
    assert len(profile_document_repository.list_for_profile(created[0]["profile_id"])) == 1


def test_a_lost_upload_ack_resumes_through_the_remote_reconciliation(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    filename = mf.get_group(rid, pablo)["representative"]["name"]
    store.lost_ack = {filename}                                                                                                  # el archivo SE GUARDA pero el cliente recibe un error de red
    first = go(rid, pablo, pre)
    assert first["ok"] and first["outcome"] != ex.DONE and len([f for f in store.real_files().values() if f["name"].endswith(filename)]) == 1
    store.lost_ack = set()
    res = go(rid, pablo, pre)                                                                                                    # misma decisión: se concilia contra el estado REAL de SharePoint
    assert res["ok"] and res["outcome"] == ex.DONE and mf.get_ledger(rid, pablo)["state"] == ex.DONE
    assert len(profile_document_repository.list_for_profile(mf.get_ledger(rid, pablo)["profile_id"])) == 1 and len([f for f in store.real_files().values() if f["name"].endswith(filename)]) == 1       # un solo archivo remoto


def test_ledger_or_spaces_failure_after_the_profile_exists_never_duplicates_it_on_retry(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    real = mf.put_ledger
    state = {"fail": True}

    def flaky(run_id, key, entry):
        if state["fail"] and key == pablo:
            raise mf.ManifestDurabilityError("escritura no confirmada en Spaces")
        return real(run_id, key, entry)
    monkeypatch.setattr(mf, "put_ledger", flaky)
    with pytest.raises(mf.ManifestError):
        go(rid, pablo, pre)                                                                                                                                                    # el perfil se creó, el libro no se confirmó
    assert len([p for p in profile_repository.list() if (p.get("import_origin") or {}).get("content_key") == pablo]) == 1 and mf.get_ledger(rid, pablo) is None
    state["fail"] = False
    fresh = acc.preflight(rid, pablo)                                                                                                                                          # el Banco ya incluye lo creado: la revisión lo ve (y no lo toma por duplicado de sí mismo)
    assert fresh["create"]["allowed"] and fresh["create"]["duplicate"] is None and fresh["fingerprint"] != pre["fingerprint"]
    assert go(rid, pablo, fresh)["ok"]
    assert mf.get_ledger(rid, pablo)["state"] == ex.DONE and len([p for p in profile_repository.list() if (p.get("import_origin") or {}).get("content_key") == pablo]) == 1       # reanuda sin duplicar el perfil


def test_link_to_a_candidate_with_all_the_checks_and_without_any_data_applied_to_the_profile(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo, first_name="Pablito", email="otro@x.com", notes="no se aplica")
    pre = acc.preflight(rid, pablo)
    pablo_profile = next(p for p in profile_repository.list() if p["first_name"] == "Pablo" and not p.get("import_origin"))
    candidate = next(m for m in pre["matches"] if m["profile_id"] == pablo_profile["profile_id"])
    snapshot = seeded_snapshot()
    res = go(rid, pablo, pre, op="link", target=pablo_profile["profile_id"])
    assert res["ok"] and res["op"] == "link" and res["outcome"] == ex.DONE and res["profile_id"] == pablo_profile["profile_id"] and seeded_snapshot() == snapshot                                # D1: ningún dato del borrador va al perfil
    record = mf.get_ledger(rid, pablo)["accepted"]
    assert (record["op"], record["profile_id"], record["target_revision"], record["draft_revision"]) == ("link", pablo_profile["profile_id"], candidate["revision"], 1) and record["confirmed_sha256"] == acc.confirmed_hash("link", pablo_profile["profile_id"], None)
    assert [d["filename"] for d in profile_document_repository.list_for_profile(pablo_profile["profile_id"])]


def test_link_is_refused_when_the_target_changed_was_deleted_or_was_not_offered(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    target = next(m for m in pre["matches"] if m["relevant"])
    snapshot, puts = digest(), list(store.puts)
    wrong_rev = acc.confirm(rid, pablo, TOKEN, actor=OWNER, op="link", target_profile_id=target["profile_id"], target_revision=target["revision"] + 5, fingerprint=pre["fingerprint"], draft_revision=1, acks=list(ALL_ACKS))
    not_offered = acc.confirm(rid, pablo, TOKEN, actor=OWNER, op="link", target_profile_id="no-ofrecido", target_revision=1, fingerprint=pre["fingerprint"], draft_revision=1, acks=list(ALL_ACKS))
    assert wrong_rev["stale"] and not_offered["stale"] and digest() == snapshot and store.puts == puts
    profile_repository.soft_delete(target["profile_id"])
    gone = acc.confirm(rid, pablo, TOKEN, actor=OWNER, op="link", target_profile_id=target["profile_id"], target_revision=target["revision"], fingerprint=pre["fingerprint"], draft_revision=1, acks=list(ALL_ACKS))
    assert gone["ok"] is False and gone["stale"] and mf.get_ledger(rid, pablo) is None


def test_linking_to_a_weak_match_demands_its_own_acknowledgement(env):
    store, rid, pablo = ready(env)
    weak = create_in_banco("Pablo", "Ruis", None)                                                                                                                                # parecido (typo), no coincidencia fuerte ni probable
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    match = next(m for m in pre["matches"] if m["profile_id"] == weak["profile_id"])
    assert match["strength"] == "WEAK" and not match["relevant"]
    res = go(rid, pablo, pre, op="link", target=weak["profile_id"], acks=[acc.ACK_IRREVERSIBLE])
    assert res["ok"] is False and res["missing_acks"] == [acc.ACK_WEAK_LINK] and mf.get_ledger(rid, pablo) is None
    assert go(rid, pablo, pre, op="link", target=weak["profile_id"])["ok"]


# ═══════════════════ concurrencia: aceptación manual, importación y doble clic ═══════════════════
def test_two_simultaneous_confirmations_have_exactly_one_effect(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    barrier, results = threading.Barrier(2), []

    def worker():
        barrier.wait()
        results.append(go(rid, pablo, pre))
    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r["ok"] for r in results) and sorted(bool(r.get("idempotent")) for r in results) == [False, True]                                                                  # uno hace; el otro ve lo hecho
    assert len([p for p in profile_repository.list() if (p.get("import_origin") or {}).get("content_key") == pablo]) == 1
    assert len(profile_document_repository.list_for_profile(mf.get_ledger(rid, pablo)["profile_id"])) == 1


def test_the_import_batch_defers_a_unit_that_an_acceptance_is_resolving_and_nothing_runs_twice(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    entered, release = threading.Event(), threading.Event()
    real_upload = ex._upload_document

    def slow_upload(run, key, entry, group, token, actor, sleep):
        if key == pablo:
            entered.set()
            assert release.wait(30)
        return real_upload(run, key, entry, group, token, actor, sleep)
    monkeypatch.setattr(ex, "_upload_document", slow_upload)
    result = {}
    thread = threading.Thread(target=lambda: result.update(go(rid, pablo, pre)))
    thread.start()
    assert entered.wait(30)                                                                                                                                                     # la aceptación está a mitad de camino y tiene el candado del contenido
    batch = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert batch["deferred"] == 1 and batch["processed"] == 0                                                                                                                   # la tanda NO corre esa unidad (y no se queda esperando)
    release.set()
    thread.join(30)
    assert result["ok"] and result["outcome"] == ex.DONE
    again = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert again["deferred"] == 0 and mf.get_ledger(rid, pablo)["state"] == ex.DONE and len(profile_document_repository.list_for_profile(mf.get_ledger(rid, pablo)["profile_id"])) == 1


def test_a_unit_resolved_by_hand_after_the_batch_listed_it_is_skipped_not_run_again(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    go(rid, pablo, acc.preflight(rid, pablo))
    puts = list(store.puts)
    batch = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert batch["processed"] == 0 and store.puts == puts and mf.get_ledger(rid, pablo)["accepted"]["by"] == OWNER


def test_acceptance_waits_for_a_running_batch_unit_and_then_sees_the_final_ledger(env, monkeypatch):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    held, release = threading.Event(), threading.Event()

    def holder():
        with mf.ledger_lock(rid, pablo):                                                                                                                                       # lo que toma la tanda sobre ese contenido
            held.set()
            release.wait(30)
    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(30)
    monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 0.3)
    with pytest.raises(mf.ManifestBusyError):
        go(rid, pablo, pre)                                                                                                                                                    # espera y, si no se libera, no escribe nada
    assert mf.get_ledger(rid, pablo) is None
    release.set()
    thread.join(30)
    monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 45.0)
    assert go(rid, pablo, pre)["ok"]


def test_the_manual_actions_and_the_acceptance_share_the_same_exclusion(env, monkeypatch):
    store, rid, pablo = ready(env)
    held, release = threading.Event(), threading.Event()

    def holder():
        with mf.ledger_lock(rid, pablo):
            held.set()
            release.wait(30)
    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(30)
    monkeypatch.setattr(mf, "LEDGER_LOCK_TIMEOUT_S", 0.3)
    for call in (lambda: ex.complete_identity(rid, pablo, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz", duplicate_confirmed=True), lambda: ex.dismiss(rid, pablo, actor=OWNER, reason="OTHER"),
                 lambda: ex.link_existing(rid, pablo, TOKEN, actor=OWNER, profile_id=next(p["profile_id"] for p in profile_repository.list()))):
        with pytest.raises(mf.ManifestBusyError):
            call()
    release.set()
    thread.join(30)
    assert mf.get_ledger(rid, pablo) is None


def test_the_ledger_lock_is_reentrant_for_the_same_thread_and_exclusive_across_threads(env):
    store, rid, pablo = ready(env)
    with mf.ledger_lock(rid, pablo):
        with mf.ledger_lock(rid, pablo, timeout=0):
            assert True
        other = {}

        def attempt():
            try:
                with mf.ledger_lock(rid, pablo, timeout=0):
                    other["got"] = True
            except mf.ManifestBusyError:
                other["got"] = False
        t = threading.Thread(target=attempt)
        t.start()
        t.join(10)
        assert other["got"] is False                                                                                             # otro hilo no entra mientras este lo tiene
    t = threading.Thread(target=attempt)
    t.start()
    t.join(10)
    assert other["got"] is True                                                                                                  # y al soltarlo, sí


def test_a_retry_of_the_same_decision_keeps_who_and_when_it_was_first_accepted(env):
    store, rid, pablo = ready(env)
    draft(rid, pablo)
    pre = acc.preflight(rid, pablo)
    pid = pre["create"]["profile_id"]
    base = {"op": "create", "profile_id": pid, "confirmed_sha256": pre["create"]["confirmed_sha256"], "draft_revision": 1}
    first = {**base, "by": OWNER, "at": "2026-10-26T10:00:00Z"}
    retry = {**base, "by": OTHER, "at": "2026-10-26T11:00:00Z"}
    store.deny_put = True
    assert ex.complete_identity(rid, pablo, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz", email="pablo.ruiz@a.com", duplicate_confirmed=True, accepted=first)["outcome"].startswith("STOP:")
    store.deny_put = False
    done = ex.complete_identity(rid, pablo, TOKEN, actor=OTHER, first_name="Pablo", last_name="Ruiz", email="pablo.ruiz@a.com", duplicate_confirmed=True, accepted=retry)
    assert done["ok"] and done["outcome"] == ex.DONE and mf.get_ledger(rid, pablo)["accepted"] == first                          # el reintento no reescribe la autoría de la primera aceptación
