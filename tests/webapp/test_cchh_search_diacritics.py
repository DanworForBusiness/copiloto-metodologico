"""Hotfix CCHH (punto 10) — la búsqueda libre ignora mayúsculas, minúsculas y diacríticos (`Diaz` ≡ `Díaz`, `n` ≡ `ñ`) mediante UNA normalización específica de búsqueda (`search_text.fold`). La identidad (`_normalize_text`: duplicados e igualdad
exacta de filtros) NO cambia; los valores persistidos y mostrados tampoco. El navegador (`dwSearchFold`) usa los MISMOS pasos: un test corre ambas implementaciones sobre el mismo corpus. Datos sintéticos."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from webapp.services import entrevista_repository, human_capital_service as svc, profile_repository, search_text
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401

APP_JS = Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js"
CORPUS = ["Díaz", "DIAZ", "  díaz  ", "Ñandú", "NUÑEZ", "José María", "Gómez", "Ünal", "Çelik", "ﬁne", "İstanbul", "é", "Zoë  \t Brontë", "", "Straße", "日本語", "Ángel  Pérez  Núñez"]


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _profile(first, last, **extra) -> dict:
    return profile_repository.create({"first_name": first, "last_name": last, "email": f"{first}.{last}@danwor.com".lower().replace(" ", ""), **extra})


def _names(c, **params) -> list[str]:
    html = c.get("/cchh/candidatos", params=params, headers={"HX-Request": "true"}).text
    return re.findall(r'<span class="hc-candidate-name dw-idcell__name">([^<]+)</span>', html)


# ═══════════════════ la normalización ═══════════════════
@pytest.mark.parametrize("a,b", [("Diaz", "Díaz"), ("Jose", "José"), ("Maria", "María"), ("Gomez", "Gómez"), ("nunez", "NUÑEZ"), ("Nandu", "ñandú"), ("diaz", "DÍAZ")])
def test_accent_case_and_enye_variants_are_equivalent_for_search(a, b):
    assert search_text.fold(a) == search_text.fold(b)


def test_the_identity_normalization_is_untouched_and_still_distinguishes_accents():
    assert svc._normalize_text("Díaz") != svc._normalize_text("Diaz") and svc._normalize_text("  Díaz  Ruiz ") == "díaz ruiz"
    a = {"first_name": "Jose", "last_name": "Diaz"}
    b = {"first_name": "José", "last_name": "Díaz"}
    assert svc.find_duplicate(a, [b]) is None and svc.find_duplicate(b, [b]) is b          # la deduplicación por nombre sigue siendo exacta
    assert svc.find_duplicate({"email": "Ana@Danwor.com"}, [{"email": "ana@danwor.com"}]) is not None and svc.find_duplicate({"email": "ánã@danwor.com"}, [{"email": "ana@danwor.com"}]) is None


def test_backend_and_browser_normalizations_are_identical():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no disponible")
    source = APP_JS.read_text(encoding="utf-8")
    code = source[source.index("// <dw-search-fold>"):source.index("// </dw-search-fold>")]
    script = code + "\nconst corpus = JSON.parse(require('fs').readFileSync(0, 'utf8'));\nprocess.stdout.write(JSON.stringify(corpus.map(dwSearchFold)));"
    out = subprocess.run([node, "-e", script], input=json.dumps(CORPUS), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [search_text.fold(v) for v in CORPUS]


# ═══════════════════ Banco de CVs ═══════════════════
def test_banco_search_finds_both_spellings_in_either_direction(c):
    _profile("Ana", "Diaz"), _profile("Luis", "Díaz"), _profile("Eva", "DÍAZ"), _profile("Otro", "Perez")
    for q in ("Diaz", "Díaz", "díaz", "DIAZ"):
        assert sorted(_names(c, q=q)) == ["Ana Diaz", "Eva DÍAZ", "Luis Díaz"], q
    assert _names(c, q="Diazz") == []                                                     # no es una búsqueda difusa


@pytest.mark.parametrize("typed,first,last", [("Jose", "José", "Test"), ("José", "Jose", "Test"), ("Maria", "María", "Test"), ("Gomez", "Pablo", "Gómez"), ("Nuñez", "Pablo", "Nunez"), ("Nunez", "Pablo", "Ñuñez")])
def test_banco_search_equivalences_by_first_or_last_name(c, typed, first, last):
    _profile(first, last), _profile("Distinto", "Apellido")
    assert _names(c, q=typed) == [f"{first} {last}"]


def test_banco_search_covers_full_name_email_skills_and_location(c):
    _profile("José", "Gómez", profile_skills="Programación, Análisis", location="Córdoba")
    _profile("Otra", "Persona", profile_skills="Excel", location="Salta")
    assert _names(c, q="jose gomez") == ["José Gómez"]                                  # nombre completo
    assert _names(c, q="programacion") == ["José Gómez"] and _names(c, q="ANALISIS") == ["José Gómez"]          # skills
    assert _names(c, q="cordoba") == ["José Gómez"] and _names(c, q="Córdoba") == ["José Gómez"]               # ubicación
    assert _names(c, q="jose.gomez@") == ["José Gómez"]                                  # email (ya sin tildes, igual sigue buscable)
    assert _names(c, location="cordoba") == ["José Gómez"] and _names(c, location="CÓRDOBA") == ["José Gómez"]  # filtro de ubicación (texto libre)


def test_banco_search_keeps_exact_filters_sort_and_pagination(c):
    for i, (first, last, seniority) in enumerate([("Ana", "Díaz", "SR"), ("Beto", "Diaz", "SSR"), ("Carla", "DÍAZ", "SR"), ("Diego", "Díaz", "SR"), ("Elena", "Perez", "SR")]):
        _profile(first, last, seniority=seniority, location="Córdoba")
    assert sorted(_names(c, q="diaz", seniority="SR")) == ["Ana Díaz", "Carla DÍAZ", "Diego Díaz"]               # AND con el filtro exacto
    assert _names(c, q="diaz", seniority="SR", sort="nombre", dir="desc") == ["Diego Díaz", "Carla DÍAZ", "Ana Díaz"]   # orden ANTES de paginar
    page1 = _names(c, q="diaz", sort="nombre", dir="asc", page_size="25", page="1")
    assert page1 == ["Ana Díaz", "Beto Diaz", "Carla DÍAZ", "Diego Díaz"]
    html = c.get("/cchh/candidatos", params={"q": "díaz", "location": "cordoba", "page_size": "25"}, headers={"HX-Request": "true"}).text
    assert re.search(r"4 candidatos", html)                                                                   # el total filtrado refleja ambas variantes
    assert 'value="díaz"' in html                                                                              # lo tipeado se muestra tal cual (la normalización no se ve)


def test_search_never_changes_persisted_or_displayed_values(c):
    p = _profile("José", "Ñuñez", location="Córdoba")
    before = json.dumps(profile_repository.get(p["profile_id"]), sort_keys=True)
    assert _names(c, q="jose nunez") == ["José Ñuñez"]                                                          # se muestra el valor real, no el normalizado
    assert json.dumps(profile_repository.get(p["profile_id"]), sort_keys=True) == before


def test_the_add_candidate_picker_ships_the_folded_haystack_and_the_browser_folds_the_query(c):
    bid = svc.create_busqueda({"nombre_puesto": "Puesto", "fecha_apertura": "2026-10-01"}, actor=CCHH_EMAIL)["busqueda_id"]
    _profile("José", "Díaz")
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert 'data-search="josé' not in page and re.search(r'data-search="[^"]*jose diaz', page)                    # el haystack del servidor llega plegado
    assert "window.dwSearchFold" in page                                                                       # y el navegador pliega la consulta con la MISMA función (equivalencia probada arriba)
    assert "function dwSearchFold" in APP_JS.read_text(encoding="utf-8")


# ═══════════════════ otras búsquedas libres de CCHH (mismo mecanismo) ═══════════════════
def test_entrevistas_onboarding_and_publicaciones_searches_ignore_diacritics():
    entrevistas = [{"profile": {"first_name": "José", "last_name": "Díaz"}, "origen": "LinkedIn", "puesto_mostrado": "Analista de Gestión", "area": "SAP", "fecha_entrevista": "2026-10-01"},
                   {"profile": {"first_name": "Otro", "last_name": "Perez"}, "origen": "Referido", "puesto_mostrado": "QA", "area": "Cloud", "fecha_entrevista": "2026-10-02"}]
    assert [r["profile"]["last_name"] for r in svc.filter_entrevistas(entrevistas, q="jose diaz")] == ["Díaz"]
    assert [r["profile"]["last_name"] for r in svc.filter_entrevistas(entrevistas, q="GESTIÓN")] == ["Díaz"] and [r["profile"]["last_name"] for r in svc.filter_entrevistas(entrevistas, q="gestion")] == ["Díaz"]
    assert len(svc.filter_entrevistas(entrevistas, q="gestion", area="SAP")) == 1 and svc.filter_entrevistas(entrevistas, q="gestion", area="Cloud") == []      # combina con filtros
    onboardings = [{"profile": {"first_name": "María", "last_name": "Núñez"}, "puesto_mostrado": "Líder técnico", "area": "SAP", "fecha_ingreso": "2026-10-05", "progress": {"hechos": 1, "total": 17, "porcentaje": 6}},
                   {"profile": {"first_name": "Otra", "last_name": "Persona"}, "puesto_mostrado": "QA", "area": "SAP", "fecha_ingreso": "2026-10-06", "progress": {"hechos": 0, "total": 17, "porcentaje": 0}}]
    assert [r["profile"]["last_name"] for r in svc.filter_onboardings(onboardings, q="maria nunez")] == ["Núñez"] and len(svc.filter_onboardings(onboardings, q="LIDER TECNICO")) == 1
    pubs = [{"posicion": "Analista de Gestión", "canal": "LinkedIn", "periodo": "2026-Q4"}, {"posicion": "QA", "canal": "Referidos", "periodo": "2026-Q4"}]
    assert [r["posicion"] for r in svc.filter_publicaciones(pubs, q="gestion")] == ["Analista de Gestión"] and [r["posicion"] for r in svc.filter_publicaciones(pubs, q="Gestión", canal="LinkedIn")] == ["Analista de Gestión"]


def test_operations_busquedas_search_uses_the_same_normalization(c, operations_users):
    from webapp.routers import operations_busquedas as ob
    assert ob._fold("Gestión Ñandú") == search_text.fold("Gestión Ñandú") == "gestion nandu"
