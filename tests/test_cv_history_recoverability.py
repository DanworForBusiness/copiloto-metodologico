"""Diagnóstico de recuperabilidad de excepciones de la importación histórica (`cv_history_recoverability`). Lotes SINTÉTICOS construidos con el motor real (`planning.build_plan`) sobre un disco temporal: nada contra DigitalOcean ni datos reales.
El diagnóstico es de solo lectura y no es un segundo motor de identidad: cada caso verifica que la categoría sale de la evidencia ya existente (extracción, plan, `cv_reconcile`)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

import config
from webapp.services import cv_history_import as planning, cv_history_manifest as mf, cv_history_pilot as pilot, cv_history_recoverability as rec, profile_repository

RUN = "ch-20261024T014615Z-6827"
PII = ("Valeria", "Ibarra", "ibarra.v@sintetico.test", "5491155550001")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)


# ═══════════════════ constructores sintéticos ═══════════════════
def _ext(first=None, last=None, emails=(), phone=None, rule="R5", state="UNCORROBORATED", alts=(), flags=(), failure=None, by_email=False):
    emails = list(emails)
    blocking = sorted(set(flags) & {"NAME_CONFLICT", "NAME_UNCORROBORATED", "NAME_MISSING", "NAME_EDGE_TOKEN_UNEXPLAINED", "EMAIL_MISSING", "EMAIL_MULTIPLE", "NAME_ORDER_UNCERTAIN", "NAME_PARTIAL_MATCH"}) if not failure else [failure]
    return {"extractor_version": 3, "failure": failure, "first_name": first, "last_name": last, "email": emails[0] if len(emails) == 1 else None, "phone": phone, "phone_digits": re.sub(r"\D", "", phone or "") or None,
            "name_corroborated_by_email": by_email, "name_state": state,
            "name_decision": {"rule": rule, "state": state, "proposed": 0 if first else None, "alternatives": [{"tokens": t, "sources": ["text"], "evidence": [], "state": s} for t, s in alts]},
            "candidates": {"emails": emails[:3], "phones": [], "names": {}}, "flags": sorted(flags), "blocking": blocking, "advisory": [], "state": "NEEDS_REVIEW" if blocking else "COMPLETE"}


class World:
    """Un lote de importación sintético: contenidos, perfiles del Banco, plan real y libro de ejecución."""

    def __init__(self):
        self.docs: list[tuple[str, dict]] = []
        self.profiles: list[dict] = []
        self.n = 0

    def doc(self, extraction: dict, specialties=("Contador",)) -> str:
        self.n += 1
        key = f"{self.n:016x}"
        self.docs.append((key, {"content_key": key, "state": pilot.EXTRACTED, "representative": {"name": f"cv{self.n}.pdf"}, "specialties": list(specialties), "sha256": hashlib.sha256(key.encode()).hexdigest(), "size": 1, "extraction": extraction}))
        return key

    def profile(self, first, last, email=None, phone=None, deleted=False, from_run=False) -> str:
        data = {"first_name": first, "last_name": last, "email": email, "phone": phone}
        if from_run:
            data["import_origin"] = {"kind": "sharepoint_historical", "run_id": RUN}
        p = profile_repository.create(data)
        if deleted:
            profile_repository.soft_delete(p["profile_id"])
        return p["profile_id"]

    def build(self, ledgers: dict | None = None) -> str:
        mf.create_run(RUN, {"kind": pilot.KIND_IMPORT, "state": "CREATED", "staging": {"state": "PRESENT"}, "source": {}, "counters": {}})
        for key, group in self.docs:
            mf.put_group(RUN, key, group)
        plan = planning.build_plan([g for _k, g in self.docs], profile_repository.list())
        mf.put_plan(RUN, {"generated_at": mf.now_iso(), "generated_by": "t", "summary": plan["summary"], "decisions": plan["decisions"], "clusters": plan["clusters"]})
        for key, entry in (ledgers or {}).items():
            mf.put_ledger(RUN, key, {"source": "manual", "profile_id": None, "document_id": None, "attempts": 0, "history": [], **entry})
        return RUN


def _report(world: World, ledgers=None) -> dict:
    world.build(ledgers)
    return rec.build_report(rec.load_inputs(RUN))


def _by_key(report: dict) -> dict:
    return {d["content_key"]: d for d in report["detail"]}


# ═══════════════════ clasificación ═══════════════════
def test_every_open_exception_gets_exactly_one_category_and_reconciles_with_the_manifest():
    w = World()
    w.doc(_ext(failure="NO_TEXT", rule="R6", state="MISSING"))
    w.doc(_ext(rule="R6", state="MISSING", flags=["NAME_MISSING"]))
    w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    r = _report(w)
    agg = r["aggregate"]
    assert agg["excepciones_analizadas"] == 3 and agg["conciliacion"]["abiertas_segun_manifiesto"] == 3 and agg["conciliacion"]["diferencia"] == 0
    assert sum(agg["categorias"].values()) == 3 and all(len([d for d in r["detail"] if d["content_key"] == k]) == 1 for k, _g in w.docs)
    assert agg["categorias"] == {rec.LINK: 0, rec.ASSISTED: 1, rec.CONFLICT: 0, rec.INSUFFICIENT: 1, rec.EXTRACTION: 1}


def test_read_failures_are_extraction_problems_with_their_code_and_never_identity_candidates():
    w = World()
    for code in ("NO_TEXT", "PDF_ENCRYPTED", "EXTRACTION_ERROR", "FORMAT_UNSUPPORTED"):
        w.doc(_ext(failure=code, rule="R6", state="MISSING"))
    r = _report(w)
    assert r["aggregate"]["problema_de_extraccion"] == {"documentos": 4, "por_codigo": {"EXTRACTION_ERROR": 1, "FORMAT_UNSUPPORTED": 1, "NO_TEXT": 1, "PDF_ENCRYPTED": 1}}
    assert all(d["categoria"] == rec.EXTRACTION and d["perfiles"] == [] for d in r["detail"])


def test_a_missing_staging_record_is_reported_not_silently_dropped():
    w = World()
    key = w.doc(_ext(emails=["a.b@sintetico.test"], alts=[("Ana Benitez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.build()
    (Path(config.BASE_DIR) / "human-capital" / "import-history" / RUN / "staging" / f"{key}.json").unlink()
    r = rec.build_report(rec.load_inputs(RUN))
    assert r["aggregate"]["excepciones_analizadas"] == 1 and _by_key(r)[key]["subcategoria"] == "SIN_REGISTRO_DE_STAGING" and _by_key(r)[key]["categoria"] == rec.EXTRACTION


def test_an_alternative_email_matching_an_existing_profile_is_a_strong_link_with_name_support():
    w = World()
    pid = w.profile("Ana", "Lopez", email="ana.alt@sintetico.test")
    key = w.doc(_ext(emails=["ana.personal@sintetico.test", "ana.alt@sintetico.test"], alts=[("Ana Lopez", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    r = _report(w)
    d = _by_key(r)[key]
    assert "MULTIPLE_EMAILS" in d["razones_plan"] and d["categoria"] == rec.LINK and d["fuerza"] == "STRONG" and d["respaldo_nombre"] is True
    assert d["perfiles"][0]["profile_id"] == pid and "EMAIL_EXACT" in d["evidencia"] and d["subcategoria"] == "STRONG_CON_RESPALDO_DE_NOMBRE"
    assert r["aggregate"]["posible_vinculacion"]["por_fuerza"] == {"STRONG": 1, "PROBABLE": 0}


def test_a_shared_phone_with_the_same_name_is_a_strong_link_and_a_phone_alone_is_probable_with_a_phone_only_risk():
    w = World()
    pid = w.profile("Carlos", "Diaz", phone="+54 9 11 5555-0101")
    strong = w.doc(_ext(phone="+54 9 11 5555-0101", alts=[("Carlos Diaz", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    only_phone = w.doc(_ext(phone="+54 9 11 5555-0101", rule="R6", state="MISSING", flags=["EMAIL_MISSING", "NAME_MISSING"]))
    r = _report(w)
    assert _by_key(r)[strong]["categoria"] == rec.LINK and _by_key(r)[strong]["fuerza"] == "STRONG" and "PHONE_MATCH" in _by_key(r)[strong]["evidencia"]
    d = _by_key(r)[only_phone]
    assert d["categoria"] == rec.LINK and d["fuerza"] == "PROBABLE" and d["respaldo_nombre"] is False and "SOLO_CELULAR" in d["riesgos"] and d["perfiles"][0]["profile_id"] == pid
    assert "el nombre (el documento no aporta evidencia propia)" in d["confirmar"]                       # sin respaldo propio: se pide confirmar el nombre


def test_homonyms_are_never_resolved_to_one_person():
    w = World()
    w.profile("Juan", "Perez", email="juan.perez1@sintetico.test")
    w.profile("Juan", "Perez", email="juan.perez2@sintetico.test")
    key = w.doc(_ext(alts=[("Juan Perez", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    d = _by_key(_report(w))[key]
    assert d["categoria"] == rec.CONFLICT and "VARIOS_PERFILES_CANDIDATOS" in d["evidencia"] and d["fuerza"] == "PROBABLE"


def test_a_single_homonym_is_only_a_probable_proposal_not_a_verified_identity():
    w2 = World()
    pid = w2.profile("Marina", "Soto", email="marina@sintetico.test")
    key2 = w2.doc(_ext(alts=[("Marina Soto", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    d2 = _by_key(_report(w2))[key2]
    assert d2["categoria"] == rec.LINK and d2["fuerza"] == "PROBABLE" and d2["perfiles"][0]["profile_id"] == pid and "NAME_EXACT" in d2["evidencia"]      # un solo homónimo: propuesta PROBABLE, no verificada


def test_a_similar_name_is_only_weak_and_never_a_link():
    w = World()
    w.profile("Marta", "Gonzalez", email="marta.g@sintetico.test")
    key = w.doc(_ext(emails=["marta.gonzales@sintetico.test"], alts=[("Marta Gonzales", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    r = _report(w)
    d = _by_key(r)[key]
    assert d["fuerza"] == "WEAK" and d["categoria"] == rec.ASSISTED and "COINCIDENCIA_WEAK_EXISTE" in d["riesgos"]
    agg = r["aggregate"]
    assert agg["mejor_coincidencia_con_el_banco_por_documento"] == {"STRONG": 0, "PROBABLE": 0, "WEAK": 1, "NINGUNA": 0} and agg["posible_vinculacion"]["documentos"] == 0


def test_contradictory_evidence_is_a_conflict_even_when_the_bank_supports_one_alternative():
    w = World()
    w.profile("Pablo", "Rios", phone="+54 9 11 5555-0401")
    key = w.doc(_ext(phone="+54 9 11 5555-0401", rule="R4", state="CONFLICT", alts=[("Pablo Rios", "DECLARED"), ("Mario Vega", "DECLARED")], flags=["NAME_CONFLICT", "EMAIL_MISSING"]))
    d = _by_key(_report(w))[key]
    assert d["categoria"] == rec.CONFLICT and "NAME_CONFLICT" in d["evidencia"] and "IDENTIDADES_CONTRADICTORIAS" in d["evidencia"] and "BANCO_RESPALDA_UNA_ALTERNATIVA" in d["riesgos"]


def test_a_deleted_profile_match_is_a_conflict_to_decide_not_a_link():
    w = World()
    w.profile("Sofia", "Luna", email="sofia.luna@sintetico.test", deleted=True)
    key = w.doc(_ext("Sofia", "Luna", emails=["sofia.luna@sintetico.test"], rule="R2", state="CORROBORATED", alts=[("Sofia Luna", "CORROBORATED")], by_email=True))
    r = _report(w)
    d = _by_key(r)[key]
    assert "EXISTING_DELETED" in d["razones_plan"] and d["categoria"] == rec.CONFLICT and d["perfiles"] == []         # el perfil dado de baja nunca se sugiere


def test_assisted_creation_needs_a_single_anchored_identity_and_reports_what_to_confirm():
    w = World()
    a = w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    b = w.doc(_ext(rule="R3", state="DECLARED", first="Diego", last="Paz", alts=[("Diego Paz", "DECLARED")], flags=["NAME_DECLARED", "EMAIL_MISSING"]))
    c = w.doc(_ext(emails=["x@sintetico.test", "y@sintetico.test"], alts=[("Elena Rojas", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    r = _report(w)
    da, db, dc = (_by_key(r)[k] for k in (a, b, c))
    assert (da["categoria"], da["subcategoria"]) == (rec.ASSISTED, "TEXTO_Y_ARCHIVO_COINCIDEN") and da["confirmar"] == ["nombre y apellido", "email del documento"]
    assert (db["categoria"], db["subcategoria"]) == (rec.ASSISTED, "DECLARADA") and "sin email: solo celular o nada" in db["confirmar"]
    assert dc["categoria"] == rec.ASSISTED and "elegir el email entre 2" in dc["confirmar"]
    assert r["aggregate"]["alta_asistida"]["por_ancla"] == {"DECLARADA": 1, "TEXTO_Y_ARCHIVO_COINCIDEN": 2}


def test_missing_or_structural_only_evidence_is_insufficient_and_a_generic_mailbox_is_not_identity():
    w = World()
    none = w.doc(_ext(rule="R6", state="MISSING", flags=["NAME_MISSING"]))
    structural = w.doc(_ext(alts=[("Nombre Posible", "STRUCTURAL")], emails=["ventas@sintetico.test"], flags=["NAME_UNCORROBORATED"]))
    r = _report(w)
    assert (_by_key(r)[none]["categoria"], _by_key(r)[none]["subcategoria"]) == (rec.INSUFFICIENT, "SIN_IDENTIDAD")
    assert (_by_key(r)[structural]["categoria"], _by_key(r)[structural]["subcategoria"]) == (rec.INSUFFICIENT, "SOLO_ESTRUCTURAL") and _by_key(r)[structural]["perfiles"] == []
    assert r["aggregate"]["informacion_insuficiente"]["por_subtipo"] == {"SIN_IDENTIDAD": 1, "SOLO_ESTRUCTURAL": 1}


def test_uncorroborated_names_are_not_promoted_to_safe_creation():
    w = World()
    key = w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    d = _by_key(_report(w))[key]
    assert "NAME_UNCORROBORATED" in d["razones_plan"] and d["categoria"] == rec.ASSISTED and d["fuerza"] is None            # propuesta a confirmar por una persona, nunca un alta segura


def test_the_blocked_exception_is_reconciled_apart_from_the_original_pending_ones():
    w = World()
    pending = w.doc(_ext(emails=["a.pend@sintetico.test"], alts=[("Ana Pendiente", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    strong = w.doc(_ext("Tomas", "Ruiz", emails=["tomas.ruiz@sintetico.test"], rule="R2", state="CORROBORATED", alts=[("Tomas Ruiz", "CORROBORATED")], by_email=True))
    w.build()
    assert mf.get_plan(RUN)["decisions"][strong]["action"] == planning.CREATE
    twin = w.profile("Tomas", "Ruiz", email="tomas.ruiz@sintetico.test")                                                   # el Banco cambió: ahora existe -> la ejecución lo bloqueó
    mf.put_ledger(RUN, strong, {"source": "plan", "action": planning.CREATE, "profile_id": None, "document_id": None, "state": "BLOCKED", "reason": "POSSIBLE_DUPLICATE", "attempts": 0, "history": []})
    r = rec.build_report(rec.load_inputs(RUN))
    c = r["aggregate"]["conciliacion"]
    assert c["abiertas_segun_manifiesto"] == 2 == c["clasificadas"] and c["pendientes_originales"] == 1 and c["adicionales_bloqueadas_o_con_error"] == 1 and c["adicionales_por_motivo"] == {"POSSIBLE_DUPLICATE": 1}
    assert c["por_estado"] == {"blocked": 1, "open": 1} and c["estado_ledger_de_las_abiertas"] == {"BLOCKED": 1, "SIN_LEDGER": 1}
    d = _by_key(r)[strong]
    assert d["categoria"] == rec.LINK and d["fuerza"] == "STRONG" and d["perfiles"][0]["profile_id"] == twin and d["estado"] == "blocked"
    assert _by_key(r)[pending]["categoria"] == rec.ASSISTED


def test_resolved_and_dismissed_exceptions_are_not_part_of_the_open_set_and_the_plan_difference_is_explicit():
    w = World()
    keep = w.doc(_ext(emails=["k@sintetico.test"], alts=[("Karen Mora", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    gone = w.doc(_ext(emails=["g@sintetico.test"], alts=[("Gabriel Mora", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    r = _report(w, ledgers={gone: {"action": "DISMISS", "state": "DISMISSED", "reason": "NOT_A_CV"}})
    c = r["aggregate"]["conciliacion"]
    assert [d["content_key"] for d in r["detail"]] == [keep] and c["plan"]["pendientes_en_el_plan"] == 2 and c["plan"]["resueltas_o_descartadas_desde_el_plan"] == 1 and c["diferencia"] == 0


# ═══════════════════ agrupaciones tentativas ═══════════════════
def test_documents_of_the_same_person_form_tentative_groups_and_estimated_decisions_drop():
    w = World()
    rosa = [w.doc(_ext(phone="+54 9 11 5555-0201", alts=[("Rosa Vega", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]), specialties=(s,)) for s in ("Contador", "Auditor", "Impuestos")]
    shared = [w.doc(_ext(emails=["dos.mails@sintetico.test", f"otro{i}@sintetico.test"], alts=[("Nora Paz", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"])) for i in range(2)]
    homonym = [w.doc(_ext(alts=[("Hugo Bravo", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"])) for _ in range(2)]      # solo el nombre coincide: PROBABLE
    alone = w.doc(_ext(rule="R6", state="MISSING", flags=["NAME_MISSING"]))
    r = _report(w)
    g = r["aggregate"]["agrupaciones_tentativas"]
    assert g["documentos_analizados"] == 8 and g["grupos_fuertes"] == 2 and g["documentos_en_grupos_fuertes"] == 5          # celular+nombre y email compartido (EMAIL_EXACT) = STRONG; solo nombre = PROBABLE
    assert g["grupos_extendidos"] == 3 and g["documentos_en_grupos_extendidos"] == 7 and g["tamano_de_grupos_extendidos"] == {2: 2, 3: 1}
    assert g["decisiones_humanas_estimadas"] == {"sin_agrupar": 8, "con_grupos_fuertes": 5, "con_grupos_extendidos": 4} and "no son identidades verificadas" in g["aviso"]
    by = _by_key(r)
    assert len({by[k]["grupo"] for k in rosa}) == 1 and by[rosa[0]]["grupo_fuerte"] and by[alone]["grupo"] is None and len({by[k]["grupo"] for k in shared}) == 1
    assert by[homonym[0]]["grupo"] == by[homonym[1]]["grupo"] and by[homonym[0]]["grupo"] is not None and by[homonym[0]]["grupo_fuerte"] is None          # agrupados solo por el nombre: tentativo y no fuerte


def test_a_shared_phone_between_different_names_is_flagged_as_mixed_identities_and_phone_only():
    w = World()
    a = w.doc(_ext(phone="+54 9 11 5555-0301", alts=[("Pedro Ruiz", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    b = w.doc(_ext(phone="+54 9 11 5555-0301", alts=[("Luis Soto", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    r = _report(w)
    g = r["aggregate"]["agrupaciones_tentativas"]
    assert g["grupos_fuertes"] == 0 and g["grupos_extendidos"] == 1 and g["grupos_con_identidades_mezcladas"] == 1 and g["enlaces_solo_celular"] == 1
    assert _by_key(r)[a]["grupo"] == _by_key(r)[b]["grupo"] and _by_key(r)[a]["grupo_fuerte"] is None


def test_documents_matching_a_profile_created_by_the_same_batch_are_tagged():
    w = World()
    pid = w.profile("Irene", "Costa", email="irene.costa@sintetico.test", from_run=True)
    key = w.doc(_ext(emails=["irene.costa@sintetico.test", "irene.otro@sintetico.test"], alts=[("Irene Costa", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    r = _report(w)
    d = _by_key(r)[key]
    assert d["categoria"] == rec.LINK and "PERFIL_CREADO_POR_ESTE_LOTE" in d["riesgos"] and r["aggregate"]["posible_vinculacion"]["a_perfiles_creados_por_este_lote"] == 1 and d["perfiles"][0]["de_este_lote"] is True and d["perfiles"][0]["profile_id"] == pid


# ═══════════════════ matriz, oportunidades, privacidad y solo lectura ═══════════════════
def test_the_matrix_and_the_prioritized_opportunities_add_up():
    w = World()
    w.profile("Ana", "Lopez", email="ana.alt@sintetico.test")
    w.doc(_ext(failure="NO_TEXT", rule="R6", state="MISSING"))
    w.doc(_ext(emails=["ana.otro@sintetico.test", "ana.alt@sintetico.test"], alts=[("Ana Lopez", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.doc(_ext(rule="R6", state="MISSING", flags=["NAME_MISSING"]))
    agg = _report(w)["aggregate"]
    total = sum(sum(counts.values()) for counts in agg["matriz_motivo_x_categoria"].values())
    assert total == agg["excepciones_analizadas"] == 4 and sum(agg["categorias"].values()) == 4
    assert agg["matriz_motivo_x_categoria"]["READ_FAILURE"][rec.EXTRACTION] == 1 and agg["matriz_motivo_x_categoria"]["MULTIPLE_EMAILS"][rec.LINK] == 1
    ops = {o["prioridad"]: o for o in agg["oportunidades_priorizadas"]}
    assert ops[1]["documentos"] == 1 and ops[1]["decisiones_estimadas"] == 1 and ops[4]["documentos"] == 1 and ops[6]["documentos"] == 1 and ops[7]["documentos"] == 1
    assert [o["prioridad"] for o in agg["oportunidades_priorizadas"]] == [1, 2, 3, 4, 5, 6, 7] and any("verifica" in x.lower() for x in agg["limitaciones"])


def _synthetic_pii_world() -> World:
    w = World()
    w.profile("Valeria", "Ibarra", email="ibarra.v@sintetico.test", phone="5491155550001")
    w.doc(_ext(emails=["ibarra.v@sintetico.test"], phone="5491155550001", alts=[("Valeria Ibarra", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
    w.doc(_ext(phone="5491155550001", alts=[("Valeria Ibarra", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    return w


def test_the_aggregate_report_has_no_personal_data_and_no_opaque_ids_and_the_detail_has_no_personal_data():
    w = _synthetic_pii_world()
    r = _report(w)
    text = json.dumps(r["aggregate"], ensure_ascii=False)
    for private in PII + tuple(k for k, _g in w.docs):
        assert private not in text, private
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)                                                      # ni profile_id: solo en el detalle protegido
    detail = json.dumps(r["detail"], ensure_ascii=False)
    for private in PII:
        assert private not in detail, private
    assert all(re.fullmatch(r"[0-9a-f]{16}", d["content_key"]) for d in r["detail"]) and any(d["perfiles"] for d in r["detail"])


def _files_digest() -> dict:
    return {p.relative_to(config.BASE_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(config.BASE_DIR).rglob("*")) if p.is_file()}


def test_building_the_report_changes_nothing_in_the_manifest_ledger_staging_or_bank():
    w = _synthetic_pii_world()
    w.build(ledgers={w.docs[0][0]: {"action": "MANUAL_LINK", "state": "DONE"}})
    before = _files_digest()
    rec.build_report(rec.load_inputs(RUN))
    rec.build_report(rec.load_inputs(RUN))
    assert _files_digest() == before


def test_the_module_imports_and_calls_only_read_functions():
    source = Path(rec.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    for forbidden in ("put_group", "put_plan", "put_ledger", "put_index", "append_audit", "append_verdict", "update_run", "create_run", "delete_", "write_through", "_write(", "profile_repository.create",
                      "profile_repository.update", "update_profile", "complete_identity", "link_existing", "dismiss(", "retry(", "reopen(", "open(", ".write_text", "unlink", "push_path", "download"):
        assert forbidden not in code, forbidden


def test_it_refuses_a_run_that_is_not_an_import_without_plan_or_without_staging():
    with pytest.raises(mf.ManifestNotFoundError):
        rec.load_inputs(RUN)
    mf.create_run(RUN, {"kind": pilot.KIND_PILOT, "staging": {"state": "PRESENT"}})
    with pytest.raises(rec.RecoverabilityError) as e:
        rec.load_inputs(RUN)
    assert e.value.code == "NOT_AN_IMPORT_RUN"
    mf.update_run(RUN, lambda d: d.update({"kind": pilot.KIND_IMPORT}))
    with pytest.raises(rec.RecoverabilityError) as e:
        rec.load_inputs(RUN)
    assert e.value.code == "NO_PLAN"
    mf.put_plan(RUN, {"summary": {}, "decisions": {}, "clusters": {}})
    mf.update_run(RUN, lambda d: d.update({"staging": {"state": "DISCARDED"}}))
    with pytest.raises(rec.RecoverabilityError) as e:
        rec.load_inputs(RUN)
    assert e.value.code == "STAGING_NOT_AVAILABLE"
