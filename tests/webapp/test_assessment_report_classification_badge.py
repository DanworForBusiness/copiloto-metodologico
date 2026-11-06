"""
test_assessment_report_classification_badge.py — todo informe de assessment expone su clasificación global (Adopción metodológica).

  * materializado (Catálogo): badge en la cabecera con el Juicio PERSISTIDO del sobre;
  * ad hoc (Herramientas de Diagnóstico): Juicio PROVISIONAL derivado con el motor canónico sobre el perfil de ESA ejecución, sin leer el Catálogo
    ni el registro de decisiones gobernadas; rotulado «provisional»;
  * badge y sección [02] salen de la MISMA vista (`presenter.judgment_view`); sin Juicio válido no hay badge.

Fixtures sintéticas (pool `write_inbound`); el motor de assessment y el de Juicio son los reales salvo donde se fija un Juicio para un caso puntual.
"""
from __future__ import annotations

import json
import re

import pytest
from bs4 import BeautifulSoup

from tests.webapp import test_catalog_assessment_routes as cat
from tests.webapp import test_datasphere_assessment as ds
from tests.webapp.test_catalog_assessment_routes import world  # noqa: F401 — fixture
from tests.webapp.test_datasphere_assessment import fake_export, owner  # noqa: F401 — fixtures
from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION, evaluate_judgment
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as asvc
from webapp.services import datasphere_assessment_service as dsvc


def _determined(base: dict, level: str) -> dict:
    return {**base, "status": "DETERMINED", "level": level, "not_determinable_reason": None}


def _not_determinable(base: dict) -> dict:
    return {**base, "status": "NOT_DETERMINABLE", "level": None, "not_determinable_reason": "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL",
            "level_rule": "LVL-05"}


def _badge(html: str):
    soup = BeautifulSoup(html, "html.parser")
    return soup, soup.select_one(".asr-hero [data-asr-level]")


def _closing_title(soup) -> str:
    section = soup.select_one("#asr-juicio")
    return section.select(".callout")[-1].select_one(".callout__title, strong, h4, h3, p").get_text(" ", strip=True) if section else ""


def _assert_badge_matches_section(html: str, *, provisional: bool):
    soup, badge = _badge(html)
    assert badge is not None
    label = badge.get_text(" ", strip=True)
    level_label = label.rsplit("·", 1)[1].strip()
    assert label == f"Adopción metodológica{' provisional' if provisional else ''} · {level_label}"
    assert badge.name == "span" and badge.has_attr("data-asr-provisional") is provisional
    section = soup.select_one("#asr-juicio")
    assert section is not None and f"· {level_label}" in section.get_text(" ", strip=True)       # mismo nivel en el cierre de [02]
    return badge, level_label, section


# ── Materializado (Catálogo) ──────────────────────────────────────────────────

def _catalog_report_with(judgment_fn):
    c = cat.client_for()
    cat._post(c)
    env = json.loads(json.dumps(asvc._load_envelope("Acme", "4AM_ACME")))
    env["judgment"] = judgment_fn(env["judgment"])
    asvc._store_envelope(env)
    asvc.reset_memory_cache()
    return c.get("/catalog/flow/assessment?flow=acme-4am", headers=cat.HX).text


@pytest.mark.parametrize("level,label,tone", [("PARCIAL", "Parcial", "parcial"), ("ADECUADA", "Adecuada", None), ("BUENA", "Buena", None)])
def test_materialized_report_shows_the_persisted_level_in_the_header(world, level, label, tone):  # noqa: F811
    html = _catalog_report_with(lambda j: _determined(j, level))
    badge, level_label, _ = _assert_badge_matches_section(html, provisional=False)
    assert level_label == label and badge["data-asr-level"] == level
    expected_tone = presenter.status_tone(level)
    assert f"catalog-card__verdict--{expected_tone}" in badge["class"]
    if tone:
        assert expected_tone == tone
    assert "Clasificación provisional" not in html


def test_materialized_not_determinable_is_explicit(world):  # noqa: F811
    html = _catalog_report_with(_not_determinable)
    badge, level_label, section = _assert_badge_matches_section(html, provisional=False)
    assert level_label == "No determinable" and badge["data-asr-level"] == "NOT_DETERMINABLE"
    assert "catalog-card__verdict--neutra" in badge["class"]


def test_without_an_assessment_there_is_no_classification_badge(world):  # noqa: F811
    html = cat.client_for().get("/catalog/flow/assessment?flow=acme-4am", headers=cat.HX).text      # NOT_YET_ASSESSED
    assert _badge(html)[1] is None and 'id="asr-juicio"' not in html


# ── Ad hoc (Herramientas de Diagnóstico) ──────────────────────────────────────

def _adhoc(c):
    rid = ds._rid(ds._post(c))
    return rid, f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}?pack=datasphere_core"


def test_adhoc_report_shows_its_own_provisional_judgment_from_the_canonical_engine(owner, fake_export, monkeypatch):  # noqa: F811
    rid, url = _adhoc(owner)
    profile = dsvc.load_result(rid)["profile"]
    official = presenter.judgment_view(evaluate_judgment(profile, ruleset=CURRENT_RULESET_VERSION, ratified_contracts=[]))

    def no_catalog(*a, **k):
        raise AssertionError("el informe ad hoc no lee el Catálogo")

    def no_registry(*a, **k):
        raise AssertionError("el informe ad hoc no lee el registro de decisiones gobernadas")
    from webapp.services import governed_decisions_store, judgment_decisions
    monkeypatch.setattr(asvc, "_load_envelope", no_catalog)
    monkeypatch.setattr(governed_decisions_store, "load_events", no_registry)
    monkeypatch.setattr(judgment_decisions, "take_snapshot", no_registry)
    html = owner.get(url).text
    badge, level_label, section = _assert_badge_matches_section(html, provisional=True)
    assert level_label == official["level_label"] and f"catalog-card__verdict--{official['tone']}" in badge["class"]
    text = section.get_text(" ", strip=True)
    assert "Clasificación provisional" in text
    assert "No incorpora las ratificaciones de gobierno" in text and "aunque el modelo no haya cambiado" in text   # puede diferir del materializado
    assert "Se calcula con las reglas vigentes al abrir este informe" in text and official["ruleset_version"] in text   # limitación: ruleset vigente
    assert "Lectura de la adopción provisional" in section.get_text(" ", strip=True)


@pytest.mark.parametrize("make,label", [(lambda j: _determined(j, "PARCIAL"), "Parcial"), (lambda j: _determined(j, "ADECUADA"), "Adecuada"),
                                        (lambda j: _determined(j, "BUENA"), "Buena"), (_not_determinable, "No determinable")])
def test_adhoc_report_renders_each_official_outcome(owner, fake_export, monkeypatch, make, label):  # noqa: F811
    rid, url = _adhoc(owner)
    real = asvc._derive_judgment
    monkeypatch.setattr(asvc, "_derive_judgment", lambda profile, ratified=None: make(real(profile, ratified)))
    _badge_el, level_label, _ = _assert_badge_matches_section(owner.get(url).text, provisional=True)
    assert level_label == label


def test_adhoc_judgment_may_differ_from_the_published_one_and_never_borrows_it(owner, fake_export, monkeypatch):  # noqa: F811
    """El Catálogo publicó ADECUADA para el mismo modelo; la ejecución ad hoc produce PARCIAL: cada informe muestra el suyo."""
    rid, url = _adhoc(owner)
    published = _determined({}, "ADECUADA")
    monkeypatch.setattr(asvc, "_persisted_judgment", lambda env: published)
    real = asvc._derive_judgment
    monkeypatch.setattr(asvc, "_derive_judgment", lambda profile, ratified=None: _determined(real(profile, ratified), "PARCIAL"))
    _, level_label, _ = _assert_badge_matches_section(owner.get(url).text, provisional=True)
    assert level_label == "Parcial"


def test_adhoc_judgment_is_stable_across_reopenings(owner, fake_export):  # noqa: F811
    rid, url = _adhoc(owner)
    first, second = (_badge(owner.get(url).text)[1].get_text(" ", strip=True) for _ in range(2))
    assert first == second
    record = dsvc.load_result(rid)
    assert "judgment" not in record                                         # nada nuevo persistido en el resultado ad hoc


def test_adhoc_engine_failure_shows_no_misleading_badge(owner, fake_export, monkeypatch):  # noqa: F811
    rid, url = _adhoc(owner)

    def boom(profile, ratified=None):
        raise ValueError("perfil no juzgable")
    monkeypatch.setattr(asvc, "_derive_judgment", boom)
    html = owner.get(url).text
    assert _badge(html)[1] is None and 'id="asr-juicio"' not in html
    assert re.search(r"<h1 class=\"asr-title\">", html)                     # el resto del informe sigue


def test_adhoc_report_writes_nothing_to_catalog_spaces_or_governed_records(owner, fake_export, monkeypatch):  # noqa: F811
    """Abrir el informe ad hoc (y exportarlo) solo LEE: ninguna escritura al Catálogo, a Spaces, a sobres gobernados ni al resultado ad hoc."""
    rid, url = _adhoc(owner)
    from core import remote_store
    calls: list[str] = []
    for name in ("mirror_up", "push_path", "delete_path"):
        monkeypatch.setattr(remote_store, name, lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr(asvc, "_store_envelope", lambda *a, **k: calls.append("_store_envelope"))
    monkeypatch.setattr(dsvc, "save_result", lambda *a, **k: calls.append("save_result"), raising=False)
    before = json.dumps(dsvc.load_result(rid), sort_keys=True)
    assert _badge(owner.get(url).text)[1] is not None
    for ext in ("json", "md"):
        assert owner.get(f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}/export.{ext}").status_code == 200
    assert calls == []
    assert json.dumps(dsvc.load_result(rid), sort_keys=True) == before          # el resultado ad hoc queda byte a byte igual


def test_materialized_report_is_untouched_by_the_provisional_path(world, monkeypatch):  # noqa: F811
    """El informe materializado sigue mostrando el Juicio PERSISTIDO del sobre: nunca llama a ``provisional_judgment`` ni se rotula provisional."""
    def forbidden(*a, **k):
        raise AssertionError("el materializado no usa el Juicio provisional")
    monkeypatch.setattr(asvc, "provisional_judgment", forbidden)
    html = _catalog_report_with(lambda j: _determined(j, "ADECUADA"))
    badge, level_label, _ = _assert_badge_matches_section(html, provisional=False)
    assert level_label == "Adecuada" and "provisional" not in badge.get_text(" ", strip=True)
