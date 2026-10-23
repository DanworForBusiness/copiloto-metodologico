"""Hotfix de normalización de nombres de archivo (extractor v3): prefijos documentales, títulos y tokens de borde ambiguos («RC»), nombres compuestos, nombres cortos legítimos, iniciales y partículas; sin cambios en
la corroboración por email ni en las reglas R1–R6. Todo con datos sintéticos."""
from __future__ import annotations

import itertools
import json

import pytest

from tests.webapp.cv_docs import make_pdf
from webapp.services import cv_extract as x

PAD = ["11 5555 1111"]


def doc(filename, emails, lines=("Nacional Experimental",)):
    return x.extract(filename, make_pdf([*lines, *PAD, *emails]))


def alt(result, tokens):
    return next(a for a in result["name_decision"]["alternatives"] if a["tokens"] == tokens)


# ═══════════════════ caso reportado: «RC Eliana Ojeda» ═══════════════════
def test_rc_is_an_ambiguous_edge_token_never_dropped_silently():
    r = doc("RC Eliana Ojeda.pdf", ["eliana.ojeda@gmail.com"])
    assert (r["first_name"], r["last_name"]) == ("Eliana", "Ojeda")                                          # la variante normalizada, no «Rc Eliana Ojeda»
    d = r["name_decision"]
    assert (d["rule"], d["state"]) == ("R2", "CORROBORATED") and r["extractor_version"] == 3                  # la regla y el estado no cambian
    assert x.NAME_EDGE_TOKEN_UNEXPLAINED in r["blocking"] and r["state"] == "NEEDS_REVIEW"                    # bloqueante: el owner revisa
    edge = alt(r, "Eliana Ojeda")["edge_token"]
    assert edge == {"token": "Rc", "position": "START", "original": "Rc Eliana Ojeda", "variant": "Eliana Ojeda", "kind": "AMBIGUOUS"}      # el valor original se conserva


@pytest.mark.parametrize("filename, first, last", [("RC_Eliana_Ojeda_CV.pdf", "Eliana", "Ojeda"), ("rc eliana ojeda.pdf", "Eliana", "Ojeda"), ("Eliana Ojeda RC.pdf", "Eliana", "Ojeda"), ("RC - Eliana Ojeda (2).pdf", "Eliana", "Ojeda")])
def test_rc_variants_in_the_filename_offer_the_normalized_name_and_require_review(filename, first, last):
    r = doc(filename, ["eliana.ojeda@gmail.com"])
    assert (r["first_name"].capitalize(), r["last_name"].capitalize()) == (first, last) and x.NAME_EDGE_TOKEN_UNEXPLAINED in r["blocking"]      # un archivo en minúsculas conserva sus minúsculas (comportamiento previo)
    assert next(a for a in r["name_decision"]["alternatives"] if "edge_token" in a)["edge_token"]["token"].lower() == "rc"


def test_without_a_strong_email_rc_changes_nothing_and_is_not_offered_as_a_variant():
    r = doc("RC Eliana Ojeda.pdf", ["eojeda@gmail.com"])                                                      # email débil: sin corroboración ⇒ el comportamiento previo (conflicto), sin variante
    assert r["first_name"] is None and (r["name_decision"]["rule"], r["name_decision"]["state"]) == ("R4", "CONFLICT") and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"]
    assert "edge_token" not in alt(r, "Rc Eliana Ojeda")
    r2 = doc("RC Eliana Ojeda.pdf", ["info@empresa.com"])
    assert r2["first_name"] is None and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r2["flags"]


def test_rc_inside_a_real_name_in_the_middle_is_not_touched():
    r = doc("Eliana Rc Ojeda.pdf", ["eliana.ojeda@gmail.com"])
    assert (r["first_name"], r["last_name"]) == ("Eliana", "Rc Ojeda") and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"]


def test_a_text_candidate_with_rc_is_reviewed_the_same_way():
    r = x.extract("documento.pdf", make_pdf(["RC Eliana Ojeda", *PAD, "eliana.ojeda@gmail.com"]))
    assert (r["first_name"], r["last_name"]) == ("Eliana", "Ojeda") and x.NAME_EDGE_TOKEN_UNEXPLAINED in r["blocking"]


# ═══════════════════ prefijos documentales y títulos reconocibles: se quitan, con rastro ═══════════════════
@pytest.mark.parametrize("filename, original, stripped_token, kind", [
    ("Dr Juan Perez.pdf", "Dr Juan Perez", "Dr", "TITLE"), ("Dra. Maria Gomez.pdf", "Dra Maria Gomez", "Dra", "TITLE"), ("Lic_Maria_Gomez.pdf", "Lic Maria Gomez", "Lic", "TITLE"),
    ("Ing Pedro Diaz CV.pdf", "Ing Pedro Diaz", "Ing", "TITLE"), ("HV Juan Perez.pdf", "HV Juan Perez", "HV", "DOCUMENT"), ("Juan Perez Mg.pdf", "Juan Perez Mg", "Mg", "TITLE"),
])
def test_recognizable_prefixes_and_titles_are_removed_from_the_proposal_keeping_the_original(filename, original, stripped_token, kind):
    email = {"Dr Juan Perez.pdf": "juan.perez", "Dra. Maria Gomez.pdf": "maria.gomez", "Lic_Maria_Gomez.pdf": "maria.gomez", "Ing Pedro Diaz CV.pdf": "pedro.diaz", "HV Juan Perez.pdf": "juan.perez", "Juan Perez Mg.pdf": "juan.perez"}[filename]
    r = doc(filename, [f"{email}@gmail.com"])
    name = f"{r['first_name']} {r['last_name']}"
    assert stripped_token.lower() not in name.lower().split() and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"]           # ya no queda en la propuesta y no hace falta revisión por el token
    norm = next(a for a in r["name_decision"]["alternatives"] if a["tokens"] == name)["normalization"]
    assert norm["original"] == original.replace(".", "")                                                                       # el valor original (sin el ruido de archivo, p. ej. «CV») queda en el rastro
    assert norm["stripped"][0]["token"].strip(".").lower() == stripped_token.lower() and norm["stripped"][0]["kind"] == kind
    assert (r["name_decision"]["rule"], r["name_decision"]["state"]) == ("R2", "CORROBORATED") and r["name_corroborated_by_email"] is True


def test_titles_are_only_removed_at_the_edges_and_only_while_two_words_remain():
    assert x._name_from_segment("Juan Dr Perez")["tokens"] == ["Juan", "Dr", "Perez"]                           # medio: no se toca
    assert x._name_from_segment("Dr Perez")["tokens"] == ["Dr", "Perez"]                                          # quedarían menos de 2 palabras
    cand = x._name_from_segment("Dr Lic Juan Perez")
    assert cand["tokens"] == ["Juan", "Perez"] and [s["token"] for s in cand["normalization"]["stripped"]] == ["Dr", "Lic"]
    assert x._name_from_segment("Ing Juan Perez")["normalization"]["original"] == "Ing Juan Perez"


# ═══════════════════ nombres compuestos, cortos legítimos, iniciales y partículas ═══════════════════
@pytest.mark.parametrize("filename, email, first, last", [
    ("Maria Jose Fernandez.pdf", "maria.fernandez", "Maria", "Jose Fernandez"),
    ("Juan de la Cruz.pdf", "juan.cruz", "Juan de", "la Cruz"),
    ("Ana Maria Lopez.pdf", "ana.lopez", "Ana", "Maria Lopez"),
    ("Ana Gomez.pdf", "agomez", "Ana", "Gomez"),
    ("Eva Ruiz.pdf", "eva.ruiz", "Eva", "Ruiz"),
])
def test_compound_names_and_short_given_names_explained_by_the_email_are_kept_whole(filename, email, first, last):
    r = doc(filename, [f"{email}@gmail.com"])
    assert (r["first_name"], r["last_name"]) == (first, last) and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"] and "edge_token" not in json.dumps(r)


def test_short_legitimate_names_are_preserved_not_stripped():
    explained = doc("Li Wei Chen.pdf", ["li.wei.chen@gmail.com"])                                               # «Li» es un componente exacto del email: está explicado
    assert (explained["first_name"], explained["last_name"]) == ("Li", "Wei Chen") and x.NAME_EDGE_TOKEN_UNEXPLAINED not in explained["flags"]
    unexplained = doc("Li Wei Chen.pdf", ["wei.chen@gmail.com"])                                                  # no explicado: se conserva igual y se pide revisión (nunca se descarta)
    assert unexplained["first_name"] == "Li" and x.NAME_EDGE_TOKEN_UNEXPLAINED in unexplained["blocking"]
    edge = alt(unexplained, "Li Wei Chen")["edge_token"]
    assert edge["kind"] == "UNEXPLAINED" and edge["token"] == "Li" and edge["variant"] == "Wei Chen" and edge["original"] == "Li Wei Chen"
    for short in ("Wu", "Ng", "Xu", "Jo"):
        assert x._name_from_segment(f"{short} Maria Lopez")["tokens"][0] == short                                  # no están en la lista cerrada: nunca se quitan


def test_the_unexplained_edge_rule_only_applies_to_file_only_identities_corroborated_only_by_email():
    r = x.extract("Li Wei Chen.pdf", make_pdf(["Li Wei Chen", *PAD, "wei.chen@gmail.com"]))                       # el texto coincide con el archivo: la corroboración no depende solo del email
    assert x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"] and r["first_name"] == "Li"
    labeled = x.extract("doc.pdf", make_pdf(["Nombre: Li Wei Chen", *PAD, "wei.chen@gmail.com"]))                  # texto con etiqueta
    assert x.NAME_EDGE_TOKEN_UNEXPLAINED not in labeled["flags"]
    text_only = x.extract("doc.pdf", make_pdf(["Li Wei Chen", *PAD, "wei.chen@gmail.com"]))                       # proviene del texto, no del archivo
    assert text_only["first_name"] == "Li" and x.NAME_EDGE_TOKEN_UNEXPLAINED not in text_only["flags"]


def test_initials_and_particles_are_preserved_and_never_trigger_the_edge_rule():
    r = doc("Juan P Gomez.pdf", ["jgomez@gmail.com"])
    assert (r["first_name"], r["last_name"]) == ("Juan P.", "Gomez") and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"] and r["name_decision"]["rule"] == "R2"
    r2 = x.extract("doc.pdf", make_pdf(["J. Perez Gomez", *PAD, "j.perez.gomez@gmail.com"]))
    assert (r2["first_name"], r2["last_name"]) == ("J.", "Perez Gomez") and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r2["flags"]
    assert x._name_from_segment("Maria de los Angeles Paz") is None or x._name_from_segment("Juan de la Cruz")["tokens"] == ["Juan", "de", "la", "Cruz"]
    assert x._name_from_segment("de Juan Cruz") is None and x._name_from_segment("Analista a Cargo") is None and x._name_from_segment("Gerente A Cargo") is None     # conectores de una letra no son iniciales


# ═══════════════════ sin efecto sobre la corroboración por email ni sobre R1–R6 ═══════════════════
def _old_email_strong(core_tokens, emails):
    """Referencia: el criterio de la versión 2 (antes de `email_explained`), para probar que el hotfix no lo cambia."""
    toks = [x.fold(t) for t in core_tokens if len(x.fold(t)) >= 2]
    for email in emails:
        local, parts = x._local_part(email)
        if not local or local in x.ROLE_MAILBOXES or any(p in x.ROLE_MAILBOXES for p in parts):
            continue
        if len({t for t in toks if len(t) >= 3 and t in local}) >= 2:
            return True
        for i, t in enumerate(toks):
            if len(t) < 4 or t not in local:
                continue
            initials = "".join(other[0] for j, other in enumerate(toks) if j != i)
            if initials and (local.startswith(initials + t) or local.endswith(t + initials)):
                return True
    return False


NAMES = [["Eliana", "Ojeda"], ["Rc", "Eliana", "Ojeda"], ["Juan", "Carlos", "Perez"], ["Li", "Wei"], ["Ana", "Li"], ["Maria", "Jose", "Fernandez"], ["Wu", "Chen", "Mario"], ["Ana", "Gomez"]]
EMAILS = [["eliana.ojeda@x.com"], ["eojeda@x.com"], ["jcperez@x.com"], ["jperez@x.com"], ["li.wei@x.com"], ["ana.li@x.com"], ["maria.fernandez@x.com"], ["info@x.com"], ["ventas.perez@x.com"], ["agomez@x.com"], ["wu.chen.mario@x.com"], ["rcojeda@x.com"], []]


@pytest.mark.parametrize("tokens, emails", list(itertools.product(NAMES, EMAILS)), ids=lambda v: "-".join(v) if isinstance(v, list) and v and "@" not in v[0] else (v[0] if v else "sin-email"))
def test_email_corroboration_is_exactly_the_same_as_before(tokens, emails):
    assert x.email_strong(tokens, emails) == _old_email_strong(tokens, emails)


@pytest.mark.parametrize("filename, lines, emails, rule, state, first, last", [
    ("Eliana Ojeda.pdf", ["Nacional Experimental"], ["eliana.ojeda@x.com"], "R2", "CORROBORATED", "Eliana", "Ojeda"),
    ("Eliana Ojeda.pdf", ["Eliana Ojeda"], ["eliana.ojeda@x.com"], "R1", "CORROBORATED", "Eliana", "Ojeda"),
    ("doc.pdf", ["Nombre: Eliana Ojeda"], ["info@x.com"], "R3", "DECLARED", "Eliana", "Ojeda"),
    ("doc.pdf", ["Eliana Ojeda"], ["info@x.com"], "R5", "UNCORROBORATED", None, None),
    ("CV Roberto Diaz.pdf", ["Sofia Martinez"], ["sofia@x.com"], "R4", "CONFLICT", None, None),
    ("doc.pdf", ["Experiencia Laboral"], [], "R6", "MISSING", None, None),
    ("Eliana Ojeda.pdf", ["Eliana Ruiz"], ["eliana@x.com"], "R5", "PARTIAL", None, None),
])
def test_rules_r1_to_r6_keep_their_outcome(filename, lines, emails, rule, state, first, last):
    r = doc(filename, emails, lines=lines)
    assert (r["name_decision"]["rule"], r["name_decision"]["state"], r["first_name"], r["last_name"]) == (rule, state, first, last)
    assert x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"]


def test_the_edge_review_never_changes_rule_or_state_only_adds_the_flag_and_the_variant():
    plain = doc("Eliana Ojeda.pdf", ["eliana.ojeda@gmail.com"])
    ambiguous = doc("RC Eliana Ojeda.pdf", ["eliana.ojeda@gmail.com"])
    unexplained = doc("Li Wei Chen.pdf", ["wei.chen@gmail.com"])
    for r in (plain, ambiguous, unexplained):
        assert (r["name_decision"]["rule"], r["name_decision"]["state"]) == ("R2", "CORROBORATED")
    assert (ambiguous["first_name"], ambiguous["last_name"]) == (plain["first_name"], plain["last_name"])
    assert set(ambiguous["flags"]) - set(plain["flags"]) == {x.NAME_EDGE_TOKEN_UNEXPLAINED}


# ═══════════════════ reevaluación offline con datos persistidos ═══════════════════
def stored_v1(emails, text_name="Nacional Experimental"):
    return {"first_name": "Nacional", "last_name": "Experimental", "email": emails[0] if len(emails) == 1 else None, "phone": None, "phone_digits": None, "flags": ["NAME_SINGLE_SOURCE"],
            "candidates": {"emails": emails, "phones": [], "names": {"text": text_name, "filename": "Rc Eliana Ojeda"}}}


def test_the_normalized_variant_is_computed_offline_from_the_stored_filename_and_emails():
    r = x.rearbitrate(stored_v1(["eliana.ojeda@gmail.com"]), "RC Eliana Ojeda.pdf")                             # el nombre de archivo (metadato del grupo) y los emails persistidos alcanzan
    assert (r["first_name"], r["last_name"]) == ("Eliana", "Ojeda") and x.NAME_EDGE_TOKEN_UNEXPLAINED in r["blocking"] and r["extractor_version"] == 3
    assert alt(r, "Eliana Ojeda")["edge_token"]["original"] == "Rc Eliana Ojeda" and "OFFLINE_REEVALUATION" in r["name_decision"]["limits"]
    again = x.rearbitrate(stored_v1(["eliana.ojeda@gmail.com"]), "RC Eliana Ojeda.pdf")
    assert json.dumps(again, sort_keys=True) == json.dumps(r, sort_keys=True)                                      # determinista


def test_offline_reevaluation_without_stored_emails_cannot_corroborate_and_says_so():
    r = x.rearbitrate(stored_v1([]), "RC Eliana Ojeda.pdf")
    assert r["first_name"] is None and x.NAME_EDGE_TOKEN_UNEXPLAINED not in r["flags"] and "OFFLINE_REEVALUATION" in r["name_decision"]["limits"]


# ═══════════════════ diagnóstico de tokens de borde (solo nombres de archivo) ═══════════════════
def test_edge_token_diagnostic_reports_frequent_short_tokens_without_names():
    names = ["RC Ana Gomez.pdf", "RC Luis Paz.pdf", "RC_Eva_Sol.pdf", "Dr Mario Diaz.pdf", "Dr Rosa Vega.pdf", "Dr Pedro Cruz.pdf", "Lucia Fernandez Mg.pdf", "Jose Lopez.pdf",
             "Julia Ramos.docx", "CV Pablo Mora.pdf", "Ana Torres.pdf", "Ana Rios.pdf", "Ana Lima.pdf", "x.pdf", "solo.pdf"]
    rep = x.filename_edge_tokens(names)
    tokens = {(t["token"], t["position"]): t for t in rep["tokens"]}
    assert tokens[("RC", "START")]["files"] == 3 and tokens[("RC", "START")]["classification"] == "AMBIGUOUS"
    assert tokens[("DR", "START")]["files"] == 3 and tokens[("DR", "START")]["classification"] == "TITLE"
    assert tokens[("ANA", "START")]["files"] == 3 and tokens[("ANA", "START")]["classification"] == "UNKNOWN"
    assert ("MG", "END") not in tokens and ("CV", "START") not in tokens                                            # una sola vez (< min) / ruido ya filtrado
    assert rep["considered_files"] == 13 and tokens[("RC", "START")]["share_pct"] == round(100 * 3 / 13, 1)
    blob = json.dumps(rep, ensure_ascii=False)
    assert not [n for n in ("Gomez", "Paz", "Sol", "Diaz", "Vega", "Cruz", "Fernandez", "Lopez", "Ramos", "Mora", "Torres", "Rios", "Lima", "pdf") if n.lower() in blob.lower()]    # solo tokens de borde, nunca un nombre completo ni un archivo


def test_edge_token_diagnostic_is_bounded_and_ignores_long_tokens_particles_and_digits():
    names = [f"Alejandro Perez {i}.pdf" for i in range(5)] + [f"Alejandro de {i}.pdf" for i in range(5)] + [f"Mariana Lopez.pdf"] * 4
    rep = x.filename_edge_tokens(names, min_files=3, max_len=4)
    assert rep["tokens"] == [] and rep["considered_files"] == 14                                                     # largos, con dígitos o partículas: nada que informar
    many = [f"T{chr(97 + i)}{chr(97 + j)} Persona Uno.pdf" for i in range(6) for j in range(6) for _ in range(3)]
    assert len(x.filename_edge_tokens(many, limit=5)["tokens"]) == 5
