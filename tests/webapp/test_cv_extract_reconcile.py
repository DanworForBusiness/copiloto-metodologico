"""Extracción LOCAL de datos de contacto de CVs (PDF/DOCX reales generados) y conciliación de identidad SIN fusión. Todo con documentos de prueba; ningún servicio externo."""
from __future__ import annotations

import json

import pytest

from tests.webapp.cv_docs import make_blank_pdf, make_docx, make_encrypted_pdf, make_pdf, make_zip_bomb_docx
from webapp.services import cv_extract as x, cv_reconcile as rc

BODY_MARK = "Responsable de la migración del data warehouse corporativo hacia Datasphere"


def pdf(lines, name="CV.pdf"):
    return x.extract(name, make_pdf(lines))


# ═══════════════════ extracción: documentos completos ═══════════════════
def test_pdf_with_clear_header_yields_the_four_fields_with_evidence():
    r = pdf(["Juan Pérez Gómez", "Consultor SAP Datasphere", "Cel: +54 9 11 5555-1234 | juan.perez@gmail.com", "DNI 35.123.456  CUIT 20-35123456-7", "2015 - 2020 Empresa Uno", BODY_MARK],
            "CV Juan Perez Gomez.pdf")
    assert (r["first_name"], r["last_name"], r["email"], r["phone"]) == ("Juan", "Pérez Gómez", "juan.perez@gmail.com", "+54 9 11 5555-1234")
    assert r["evidence"] == {"email": "text", "phone": "text", "name": ["text", "filename"]} and r["name_corroborated_by_email"] is True
    assert r["blocking"] == [] and r["advisory"] == [x.NAME_SPLIT_AMBIGUOUS] and r["state"] == "COMPLETE"            # 3 palabras: la partición es una suposición, se avisa pero no bloquea


def test_docx_reads_contact_data_from_the_header_part():
    r = x.extract("María Fernández CV.docx", make_docx([BODY_MARK], header=["María Fernández", "WhatsApp +54 11 4444 5678", "maria.fernandez@empresa.com.ar"]))
    assert (r["first_name"], r["last_name"], r["email"]) == ("María", "Fernández", "maria.fernandez@empresa.com.ar") and r["phone"].startswith("+54 11 4444")
    assert r["state"] == "COMPLETE"


def test_dni_cuit_and_year_ranges_are_never_taken_as_phones():
    r = pdf(["Ana López", "ana.lopez@mail.com", "DNI: 35.123.456", "CUIT 27-35123456-4", "Período 2012 - 2018", "Matrícula 12345678901234567890"])
    assert r["phone"] is None and x.PHONE_MISSING in r["flags"] and x.PHONE_MISSING in r["advisory"] and r["blocking"] == []                  # sin celular no bloquea


def test_comma_form_and_uppercase_names_are_split_with_certainty():
    r = pdf(["PÉREZ, Juan Carlos", "11 4444 5555", "jcperez@mail.com"], "x.pdf")
    assert (r["first_name"], r["last_name"]) == ("Juan Carlos", "Pérez") and x.NAME_SPLIT_AMBIGUOUS not in r["flags"]
    r2 = pdf(["LUCIA GOMEZ", "lucia.gomez@mail.com"], "cv.pdf")
    assert (r2["first_name"], r2["last_name"]) == ("Lucia", "Gomez")


# ═══════════════════ extracción: ambigüedad ⇒ revisión (nunca se adivina) ═══════════════════
def test_several_emails_or_phones_block_and_list_candidates_without_choosing_the_email():
    r = pdf(["Pablo Ruiz", "pablo.ruiz@a.com y pruiz@b.com", "Cel 11 5555 1111", "Tel 11 4444 2222"])
    assert r["email"] is None and x.EMAIL_MULTIPLE in r["blocking"] and r["candidates"]["emails"] == ["pablo.ruiz@a.com", "pruiz@b.com"]
    assert x.PHONE_MULTIPLE in r["blocking"] and len(r["candidates"]["phones"]) == 2 and r["phone"]                      # el celular sugerido es el de mayor puntaje, igual va a revisión
    assert r["state"] == "NEEDS_REVIEW"


def test_name_conflict_between_text_and_filename_blocks():
    r = pdf(["Sofía Martínez", "sofia@mail.com", "11 5555 2222"], "CV Roberto Díaz.pdf")
    assert x.NAME_CONFLICT in r["blocking"] and r["first_name"] == "Sofía" and r["candidates"]["names"] == {"text": "Sofía Martínez", "filename": "Roberto Díaz"}


def test_filename_only_name_has_an_uncertain_order_and_no_name_at_all_blocks():
    r = x.extract("Perez_Juan_CV.pdf", make_pdf(["Experiencia laboral", "juan.perez@mail.com", "11 5555 3333"]))
    assert x.NAME_ORDER_UNCERTAIN in r["blocking"] and r["evidence"]["name"] == ["filename"]
    r2 = x.extract("documento.pdf", make_pdf(["Experiencia laboral", "Habilidades", "x@y.com"]))
    assert x.NAME_MISSING in r2["blocking"] and r2["first_name"] is None


def test_single_source_name_is_blocking_unless_the_email_corroborates_it():
    solo = x.extract("documento.pdf", make_pdf(["Diego Torres", "Consultor", "contacto@empresa.com", "11 5555 4444"]))
    assert x.NAME_SINGLE_SOURCE in solo["blocking"]
    corroborated = x.extract("documento.pdf", make_pdf(["Diego Torres", "Consultor", "dtorres@empresa.com", "11 5555 4444"]))
    assert x.NAME_SINGLE_SOURCE not in corroborated["flags"] and corroborated["name_corroborated_by_email"] and corroborated["state"] == "COMPLETE"


def test_missing_email_blocks():
    r = pdf(["Laura Sosa", "Cel 11 5555 6666"], "CV Laura Sosa.pdf")
    assert x.EMAIL_MISSING in r["blocking"] and r["state"] == "NEEDS_REVIEW"


# ═══════════════════ fallas de lectura ═══════════════════
@pytest.mark.parametrize("name, build, code", [
    pytest.param("escaneado.pdf", make_blank_pdf, x.NO_TEXT, id="escaneado"),
    pytest.param("cifrado.pdf", make_encrypted_pdf, x.PDF_ENCRYPTED, id="cifrado"),
    pytest.param("viejo.doc", lambda: bytes.fromhex("d0cf11e0a1b11ae1") + b"0" * 100, x.FORMAT_UNSUPPORTED, id="doc-binario"),
    pytest.param("bomba.docx", make_zip_bomb_docx, x.EXTRACTION_ERROR, id="zip-bomb"),
    pytest.param("xxe.docx", lambda: make_docx([], extra_document_xml='<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "boom">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>'), x.EXTRACTION_ERROR, id="doctype-entidades"),
    pytest.param("roto.docx", lambda: b"PK" + bytes([3, 4]) + b" esto no es un zip", x.EXTRACTION_ERROR, id="zip-roto"),
])
def test_unreadable_documents_return_a_stable_failure_code_without_raising(name, build, code):
    r = x.extract(name, build())
    assert r["failure"] == code and r["blocking"] == [code] and r["state"] == "NEEDS_REVIEW" and r["first_name"] is None


def test_a_corrupt_pdf_is_a_failure_not_a_crash_and_never_echoes_library_messages():
    r = x.extract("roto.pdf", b"%PDF-1.4\n" + b"basura " * 50)
    assert r["failure"] in (x.EXTRACTION_ERROR, x.NO_TEXT) and set(r) >= {"failure", "blocking"} and "basura" not in json.dumps(r)


def test_the_cv_text_is_never_part_of_the_result():
    r = pdf(["Juan Pérez Gómez", "juan.perez@gmail.com", "Cel +54 9 11 5555-1234", BODY_MARK, "Segunda frase confidencial del currículum"])
    blob = json.dumps(r, ensure_ascii=False)
    assert "Datasphere" not in blob and "confidencial" not in blob and "migración" not in blob


def test_extraction_is_bounded_in_pages_and_characters():
    assert x.MAX_PDF_PAGES == 6 and x.MAX_TEXT_CHARS == 40000 and x.DOCX_MAX_UNCOMPRESSED == 20 * 1024 * 1024


# ═══════════════════ conciliación (sin fusión) ═══════════════════
PROFILES = [
    {"profile_id": "p-email", "first_name": "Lucía", "last_name": "Gómez", "email": "Lucia.Gomez@Mail.com", "phone": None},
    {"profile_id": "p-phone", "first_name": "Gustavo", "last_name": "Ríos", "email": None, "phone": "+54 9 11 5555-9999"},
    {"profile_id": "p-swap", "first_name": "Ruiz", "last_name": "Pablo", "email": None, "phone": None},
    {"profile_id": "p-tokens", "first_name": "Ana María", "last_name": "López", "email": None, "phone": None},
    {"profile_id": "p-sim", "first_name": "Estefanía", "last_name": "Fernández", "email": None, "phone": None},
    {"profile_id": "p-init", "first_name": "Marcelo", "last_name": "Vega", "email": None, "phone": None},
    {"profile_id": "p-del", "first_name": "Nora", "last_name": "Paz", "email": "nora@mail.com", "phone": None, "deleted_at": "2026-01-01T00:00:00Z"},
    {"profile_id": "p-none", "first_name": "Zoe", "last_name": "Zeta", "email": "zoe@mail.com", "phone": None},
]


def match(first, last, email=None, phone=None):
    return {m["profile_id"]: m for m in rc.reconcile_with_profiles({"first_name": first, "last_name": last, "email": email, "phone": phone}, PROFILES)}


def test_exact_email_is_strong_even_with_a_different_name_and_is_case_insensitive():
    m = match("Otro", "Nombre", email="lucia.gomez@mail.com")
    assert m["p-email"]["strength"] == "STRONG" and m["p-email"]["signals"] == [rc.EMAIL_EXACT]


def test_phone_plus_name_is_strong_and_phone_alone_is_only_probable():
    assert match("Gustavo", "Rios", phone="11 5555-9999")["p-phone"]["strength"] == "STRONG"           # tolera prefijos y acentos
    assert match("Otra", "Persona", phone="011 15 5555 9999")["p-phone"]["strength"] == "PROBABLE"


def test_name_variants_are_probable_or_weak_never_strong():
    assert match("Pablo", "Ruiz")["p-swap"]["signals"] == [rc.NAME_SWAPPED] and match("Pablo", "Ruiz")["p-swap"]["strength"] == "PROBABLE"
    assert match("Ana", "López María")["p-tokens"]["signals"] == [rc.NAME_TOKENS_EQUAL]                  # mismas palabras en otro orden/partición
    assert match("Ana María", "López")["p-tokens"]["signals"] == [rc.NAME_EXACT]
    assert match("Estefania", "Fernandes")["p-sim"]["strength"] == "WEAK" and rc.NAME_SIMILAR in match("Estefania", "Fernandes")["p-sim"]["signals"]
    assert match("Martín", "Vega")["p-init"]["signals"] == [rc.INITIAL_LAST] or match("Martín", "Vega")["p-init"]["strength"] == "WEAK"


def test_deleted_profiles_are_reported_and_unrelated_ones_do_not_appear():
    m = match("Nora", "Paz", email="nora@mail.com")
    assert m["p-del"]["deleted"] is True and "p-none" not in m and "p-email" not in m


def test_reconciliation_only_suggests_it_returns_candidates_and_mutates_nothing():
    before = json.dumps(PROFILES, sort_keys=True)
    fields = {"first_name": "Lucía", "last_name": "Gómez", "email": "lucia.gomez@mail.com", "phone": None}
    out = rc.reconcile_with_profiles(fields, PROFILES)
    assert isinstance(out, list) and set(out[0]) == {"profile_id", "strength", "signals", "deleted"} and json.dumps(PROFILES, sort_keys=True) == before
    assert len(rc.reconcile_with_profiles(fields, PROFILES * 4)) <= rc.MAX_MATCHES


def test_in_run_clusters_suggest_the_same_person_across_documents_without_merging():
    records = {"k1": {"first_name": "Juan", "last_name": "Pérez", "email": "jp@mail.com"}, "k2": {"first_name": "Juan", "last_name": "Perez", "email": "jp@mail.com"},
               "k3": {"first_name": "Otra", "last_name": "Persona", "email": None}}
    out = rc.cluster_in_run(records)
    assert [c["key"] for c in out["k1"]] == ["k2"] and out["k1"][0]["strength"] == "STRONG" and out["k3"] == [] and set(records) == {"k1", "k2", "k3"}
