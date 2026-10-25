"""Índice temporal de candidatos (`cv_history_candidate_index`): EQUIVALENCIA EXACTA con `cv_reconcile.reconcile_with_profiles` sobre identidades ambiguas (homónimos, emails alternativos, celulares compartidos, nombres invertidos,
iniciales, parecidos, nombres sin palabras útiles, perfiles dados de baja y más coincidencias que el tope). Todo sintético y en memoria; el índice es por request y no comparte estado."""
from __future__ import annotations

import copy
import random
import re
from difflib import SequenceMatcher
from pathlib import Path

import pytest

import config
from tests.test_cv_history_recoverability import RUN, World, _corroborated_world, _ext
from webapp.services import cv_history_candidate_index as cidx, cv_history_manifest as mf, cv_history_recoverability as rec, cv_reconcile, profile_repository
from webapp.services.cv_history_candidate_index import CandidateIndex

FIRSTS = ["Ana", "Ana María", "José", "Jose", "J.", "J", "Juan", "Jonathan", "Jonatan", "María", "Maria", "Carlos", "Cárlos", "", None, "A", "Luis Miguel"]
LASTS = ["Pérez", "Perez", "PEREZ", "García", "Garcia", "Gómez", "Gomez Lopez", "López", "Lopez", "O'Connor", "", None, "B", "Pérez Gómez", "Sosa"]
PHONES = [None, "", "+54 9 11 5555-0001", "01155550001", "5491155550001", "11 5555 0001", "5491155550002", "12345", "+54 9 11 5555-0003", "15 5555 0003"]
EMAILS = [None, "", "ana@sintetico.test", " ANA@sintetico.test ", "jose.perez@sintetico.test", "otro@sintetico.test", "j.perez@sintetico.test"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)


def _people(rng: random.Random, n: int, *, with_id: bool) -> list[dict]:
    out = []
    for i in range(n):
        p = {"first_name": rng.choice(FIRSTS), "last_name": rng.choice(LASTS), "email": rng.choice(EMAILS)}
        phone = rng.choice(PHONES)
        if rng.random() < .5:
            p["phone"] = phone
        else:
            p["phone_digits"] = re.sub(r"\D", "", phone or "") or None
        if with_id:
            p["profile_id"] = f"{i:08d}-0000-0000-0000-000000000000"
            if rng.random() < .15:
                p["deleted_at"] = "2026-01-01T00:00:00Z"
        out.append(p)
    return out


def _derived(rng: random.Random, profiles: list[dict]) -> dict:
    """Una persona que se parece a un perfil de una de las formas ambiguas que importan (misma identidad en otra forma, homónimo, solo email, solo celular...)."""
    base = rng.choice(profiles)
    first, last = base.get("first_name"), base.get("last_name")
    kind = rng.choice(["exact", "swapped", "initial", "email_only", "phone_only", "shuffled_tokens", "typo"])
    if kind == "swapped":
        return {"first_name": last, "last_name": first}
    if kind == "initial":
        return {"first_name": (first or "")[:1], "last_name": last}
    if kind == "email_only":
        return {"first_name": None, "last_name": None, "email": (base.get("email") or "").upper()}
    if kind == "phone_only":
        return {"first_name": None, "last_name": None, "phone": base.get("phone") or base.get("phone_digits")}
    if kind == "shuffled_tokens":
        return {"first_name": " ".join(reversed(f"{first or ''} {last or ''}".split())), "last_name": ""}
    if kind == "typo":
        return {"first_name": (first or "")[:-1] + "x", "last_name": last}
    return {"first_name": first, "last_name": last, "email": base.get("email"), "phone": base.get("phone")}


@pytest.mark.parametrize("seed", range(6))
def test_the_indexed_result_is_identical_to_the_full_comparison_on_ambiguous_identities(seed):
    rng = random.Random(seed)
    profiles = _people(rng, 70, with_id=True)
    index = CandidateIndex(profiles)
    seen_signals: set[str] = set()
    persons = _people(rng, 60, with_id=False) + [_derived(rng, profiles) for _ in range(120)]
    for person in persons:
        full = cv_reconcile.reconcile_with_profiles(person, profiles)
        assert index.reconcile(person) == full, person                                                                   # misma lista, mismo orden, mismo tope
        seen_signals.update(s for m in full for s in m["signals"])
    assert seen_signals >= {cv_reconcile.EMAIL_EXACT, cv_reconcile.PHONE_MATCH, cv_reconcile.NAME_EXACT, cv_reconcile.NAME_SWAPPED, cv_reconcile.NAME_TOKENS_EQUAL, cv_reconcile.INITIAL_LAST}   # el corpus ejercita cada señal


def test_the_corpus_also_exercises_name_similar_and_the_cap_of_five_matches():
    profiles = [{"profile_id": f"{i:08d}", "first_name": "Maria", "last_name": "Gomez", "email": f"m{i}@sintetico.test"} for i in range(8)]                    # 8 homónimos: el tope de 5 recorta
    profiles += [{"profile_id": "typo-1", "first_name": "Jonathan", "last_name": "Sosa"}, {"profile_id": "typo-2", "first_name": "Katherine", "last_name": "Smith"}]
    index = CandidateIndex(profiles)
    homonym = {"first_name": "María", "last_name": "Gómez"}
    assert len(index.reconcile(homonym)) == 5 and index.reconcile(homonym) == cv_reconcile.reconcile_with_profiles(homonym, profiles)
    for person, signal in (({"first_name": "Jonathan", "last_name": "Soza"}, cv_reconcile.NAME_SIMILAR), ({"first_name": "Catherine", "last_name": "Smith"}, cv_reconcile.NAME_SIMILAR)):
        full = cv_reconcile.reconcile_with_profiles(person, profiles)
        assert index.reconcile(person) == full and any(signal in m["signals"] for m in full), person


@pytest.mark.parametrize("person, expected_signal", [
    ({"first_name": "Jose", "last_name": "Perez"}, cv_reconcile.NAME_EXACT),                                                # homónimo con tilde y mayúsculas
    ({"first_name": "Perez", "last_name": "Jose"}, cv_reconcile.NAME_SWAPPED),                                              # nombre invertido
    ({"first_name": "María José", "last_name": "Gómez"}, cv_reconcile.NAME_TOKENS_EQUAL),                                  # mismas palabras en otro orden
    ({"first_name": "J.", "last_name": "Pérez"}, cv_reconcile.INITIAL_LAST),                                                # inicial + apellido
    ({"first_name": None, "last_name": None, "email": " JOSE.PEREZ@sintetico.test "}, cv_reconcile.EMAIL_EXACT),            # email alternativo (variante solo-email)
    ({"first_name": None, "last_name": None, "phone": "+54 9 11 5555-0003"}, cv_reconcile.PHONE_MATCH),                    # celular compartido con otro formato
    ({"first_name": "Jonathan", "last_name": "Soza"}, cv_reconcile.NAME_SIMILAR),                                           # parecido (typo)
])
def test_each_signal_family_keeps_its_candidates_in_the_index(person, expected_signal):
    profiles = [
        {"profile_id": "p1", "first_name": "José", "last_name": "PÉREZ", "email": "jose.perez@sintetico.test", "phone": "5491155550001"},
        {"profile_id": "p2", "first_name": "Jose", "last_name": "Perez", "email": "otro@sintetico.test", "phone": "01155550003"},          # homónimo con otros datos
        {"profile_id": "p3", "first_name": "José María", "last_name": "Gómez", "email": None, "phone_digits": "5491155550003"},          # celular compartido con p2
        {"profile_id": "p4", "first_name": "Jonathan", "last_name": "Sosa", "deleted_at": "2026-02-02T00:00:00Z"},                       # dado de baja: también se informa, marcado
        {"profile_id": "p5", "first_name": "A", "last_name": "B"},                                                                    # sin palabras útiles
        {"profile_id": "p6", "first_name": None, "last_name": None, "email": "ana@sintetico.test"},                                   # sin nombre
    ]
    index = CandidateIndex(profiles)
    full = cv_reconcile.reconcile_with_profiles(person, profiles)
    assert index.reconcile(person) == full
    assert any(expected_signal in m["signals"] for m in full), (person, full)                                                       # el caso realmente produce la señal que dice probar


def test_names_without_useful_words_and_empty_people_match_exactly_like_the_full_comparison():
    profiles = [{"profile_id": "a", "first_name": "A", "last_name": "B"}, {"profile_id": "b", "first_name": "B", "last_name": "A"}, {"profile_id": "c", "first_name": "", "last_name": ""}, {"profile_id": "d", "first_name": None, "last_name": "Soto"}]
    index = CandidateIndex(profiles)
    for person in ({"first_name": "A", "last_name": "B"}, {"first_name": "", "last_name": ""}, {}, {"first_name": None, "last_name": "Soto"}, {"first_name": "X", "last_name": "Y"}):
        assert index.reconcile(person) == cv_reconcile.reconcile_with_profiles(person, profiles), person
    assert CandidateIndex([]).reconcile({"first_name": "Ana", "last_name": "Soto"}) == []


def test_the_similarity_bounds_never_discard_a_pair_that_difflib_would_accept():
    rng = random.Random(5)
    alphabet = "abcde fgh"
    names = ["".join(rng.choice(alphabet) for _ in range(rng.randint(2, 18))).strip() or "x" for _ in range(220)]
    profiles = [{"profile_id": f"{i:04d}", "first_name": n, "last_name": ""} for i, n in enumerate(names)]
    index = CandidateIndex(profiles)
    accepted = 0
    for q in names[:70]:
        full_q = cv_reconcile._full(q, "")
        kept = {p["profile_id"] for p in index.candidates({"first_name": q, "last_name": ""})}
        for p in profiles:
            if full_q.strip() and cv_reconcile._full(p["first_name"], "").strip() and SequenceMatcher(None, full_q, cv_reconcile._full(p["first_name"], "")).ratio() >= cv_reconcile.SIMILARITY_THRESHOLD:
                accepted += 1
                assert p["profile_id"] in kept, (q, p["first_name"])
    assert accepted > 50                                                                                                # el corpus tiene muchos pares aceptados: la prueba no es vacía


# ═══════════════════ la clasificación completa no cambia ═══════════════════
def _ambiguous_world() -> World:
    w = World()
    w.profile("Valeria", "Ibarra", email="ibarra.v@sintetico.test", phone="5491155550001")
    w.profile("Valeria", "Ibarra", email="valeria.ibarra@otro.test", phone="5491155550009")                                  # homónimo del anterior
    w.profile("Rosa", "Vega", phone="5491155550002")
    w.profile("Mario", "Vega", phone="+54 9 11 5555-0002")                                                                   # celular compartido
    w.profile("Pablo", "Rios", email="pablo.rios@sintetico.test", deleted=True)
    w.profile("Hugo", "Sosa", email="hugo.sosa@sintetico.test")
    w.doc(_ext(emails=["ibarra.v@sintetico.test", "valeria.ibarra@otro.test"], phone="5491155550001", alts=[("Valeria Ibarra", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))   # homónimos + emails alternativos
    w.doc(_ext(phone="5491155550002", alts=[("Rosa Vega", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))                                  # celular compartido
    w.doc(_ext(phone="5491155550002", alts=[("Mario Vega", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
    w.doc(_ext(rule="R4", state="CONFLICT", alts=[("Pablo Rios", "DECLARED"), ("Mario Vega", "DECLARED")], flags=["NAME_CONFLICT", "EMAIL_MISSING"]))
    w.doc(_ext(emails=["x@sintetico.test", "y@sintetico.test"], alts=[("Elena Rojas", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
    w.doc(_ext(emails=["hugo.sosa@sintetico.test", "otro.hugo@sintetico.test"], alts=[("Hugo Sosa", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))                                                      # un único perfil coincidente
    w.doc(_ext(phone="5491155550009", alts=[("Elena Rojas", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))                                       # celular de un perfil con OTRO nombre: solo celular
    w.doc(_ext(rule="R6", state="MISSING", flags=["NAME_MISSING"]))
    w.doc(_ext(failure="NO_TEXT", rule="R6", state="MISSING"))
    return w


def _all_classifications(index_for) -> list:
    inputs = rec.load_inputs(RUN)
    live = [p for p in inputs["profiles"] if not p.get("deleted_at")]
    index = index_for(live)
    return [rec.classify(row, inputs["groups"].get(row["content_key"]), inputs["ledgers"].get(row["content_key"]), live, RUN, index) for row in inputs["rows"]]


def test_classification_with_the_index_is_identical_to_the_full_classification_on_ambiguous_batches():
    w = _ambiguous_world()
    w.build()
    full = _all_classifications(lambda live: None)
    fast = _all_classifications(lambda live: CandidateIndex(live))
    assert fast == full and len(full) == 9
    cats = {d["categoria"] for d, _ in full}
    assert {rec.LINK, rec.ASSISTED, rec.CONFLICT} <= cats and any(d["riesgos"] for d, _ in full)                       # el lote realmente mezcla categorías y riesgos


def test_classification_with_the_index_is_identical_on_the_24_corroborated_distribution():
    w, _private = _corroborated_world()
    w.profile("Abel", "Aguirre", email="abel.aguirre@sintetico.test")
    w.profile("Bruno", "Aguirre")
    w.build()
    assert _all_classifications(lambda live: CandidateIndex(live)) == _all_classifications(lambda live: None)


def test_the_index_is_per_request_and_shares_no_state():
    profiles = [{"profile_id": "p1", "first_name": "Ana", "last_name": "Soto", "email": "ana@sintetico.test"}]
    before = copy.deepcopy(profiles)
    first, second = CandidateIndex(profiles), CandidateIndex([])
    assert first.reconcile({"email": "ana@sintetico.test"})[0]["profile_id"] == "p1" and second.reconcile({"email": "ana@sintetico.test"}) == [] and profiles == before    # no muta los perfiles ni filtra a otra instancia
    profiles.append({"profile_id": "p2", "first_name": "Ana", "last_name": "Soto", "email": "ana2@sintetico.test"})
    assert [m["profile_id"] for m in first.reconcile({"first_name": "Ana", "last_name": "Soto"})] == ["p1"]                                                              # una copia de la lista: no sigue al original
    source = Path(cidx.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    for forbidden in ("open(", "lru_cache", "functools", "global ", "@cache", "json", "write", "sqlite", "pickle", "requests", "urllib"):
        assert forbidden not in code, forbidden
    assert not [n for n, v in vars(cidx).items() if isinstance(v, (dict, list, set)) and not n.startswith("__")]                                                          # sin estado mutable a nivel de módulo


def test_the_review_service_builds_one_new_index_per_call_and_keeps_every_review_identical(monkeypatch):
    from webapp.services import cv_history_import_exec as ex, cv_history_review as review
    w = _ambiguous_world()
    w.build()
    built = []
    real = review.CandidateIndex
    monkeypatch.setattr(review, "CandidateIndex", lambda live: (built.append(live), real(live))[1])
    rows = ex.exception_details(RUN, ex.exceptions_overview(RUN)["rows"])
    indexed = [r["review"] for r in review.annotate(RUN, copy.deepcopy(rows))]
    review.annotate(RUN, copy.deepcopy(rows))
    assert len(built) == 2 and built[0] is not built[1]                                                                  # un índice por llamada, nunca compartido
    monkeypatch.setattr(review, "CandidateIndex", lambda live: type("Full", (), {"reconcile": staticmethod(lambda person: cv_reconcile.reconcile_with_profiles(person, live))})())
    full = [r["review"] for r in review.annotate(RUN, copy.deepcopy(rows))]
    assert indexed == full and all(indexed)
    calls = []
    monkeypatch.setattr(review, "CandidateIndex", real)
    monkeypatch.setattr(review.rec, "classify", lambda *a, **k: (calls.append(1), (_ for _ in ()).throw(RuntimeError("x")))[1])
    assert review.annotate(RUN, []) == [] and calls == []                                                                # sin filas abiertas no se construye ni se clasifica nada
