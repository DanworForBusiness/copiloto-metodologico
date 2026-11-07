"""
test_assessment_report_space_provenance.py — badge neutro «Space: <nombre>» en la cabecera del informe de Assessment (PROCEDENCIA).

  * ad hoc: el Space VALIDADO de la ejecución, guardado como metadato del resultado temporal; no entra al perfil, a su firma ni al Juicio;
  * Catálogo: SOLO si el ledger de publicación registra una activación de ese modelo (evidencia persistida); si no, no se muestra
    (nunca se infiere del contexto del usuario, de otros manifiestos ni de nombres);
  * procedencia, evidencia metodológica y Juicio quedan separados: dos ejecuciones del mismo modelo desde Spaces distintos muestran cada
    una su Space con el MISMO perfil y el MISMO Juicio.
"""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from tests.webapp import test_catalog_assessment_routes as cat
from tests.webapp import test_datasphere_assessment as ds
from tests.webapp.test_catalog_assessment_routes import world  # noqa: F401 — fixture
from tests.webapp.test_datasphere_assessment import fake_export, owner  # noqa: F401 — fixtures
from tools.catalog import publication_ledger as ledger_mod
from webapp.services import assessment_service as asvc
from webapp.services import datasphere_assessment_service as dsvc


def _space_badge(html: str):
    badge = BeautifulSoup(html, "html.parser").select_one(".asr-hero [data-asr-space]")
    return badge.get_text(" ", strip=True) if badge is not None else None


def _adhoc_run(c, space: str):
    r = ds._post(c, data={**ds.VALID, "space": space})
    rid = ds._rid(r)
    return rid, f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}?pack=datasphere_core"


# ── ad hoc ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_two_runs_of_the_same_model_show_their_own_space_without_changing_results(owner, fake_export):  # noqa: F811
    rid_a, url_a = _adhoc_run(owner, "CALIDAD")
    rid_b, url_b = _adhoc_run(owner, "PRODUCCION")
    assert _space_badge(owner.get(url_a).text) == "Space: CALIDAD"
    assert _space_badge(owner.get(url_b).text) == "Space: PRODUCCION"
    rec_a, rec_b = dsvc.load_result(rid_a), dsvc.load_result(rid_b)
    assert rec_a["space"] == "CALIDAD" and rec_b["space"] == "PRODUCCION"
    assert rec_a["profile"] == rec_b["profile"]                                         # misma evidencia -> mismo perfil (el Space no entra)
    assert asvc.provisional_judgment(rec_a["profile"]) == asvc.provisional_judgment(rec_b["profile"])   # mismo Juicio
    assert "CALIDAD" not in json.dumps(rec_a["profile"]) and "PRODUCCION" not in json.dumps(rec_b["profile"])
    exported = owner.get(f"/modules/{ds.STEM}/datasphere-assessment/result/{rid_a}/export.json").text
    assert "CALIDAD" not in exported                                                    # el export canónico no lleva procedencia


def test_adhoc_result_without_space_metadata_shows_no_badge(owner, fake_export):  # noqa: F811
    rid, url = _adhoc_run(owner, "CALIDAD")
    path = dsvc._dirs()[1] / f"{rid}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("space")                                                                 # resultado guardado antes de esta mejora
    path.write_text(json.dumps(record), encoding="utf-8")
    assert _space_badge(owner.get(url).text) is None


# ── Catálogo ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _write_ledger(*activations):
    ledger: dict = {}
    for i, (space, root) in enumerate(activations, start=1):
        ledger = ledger_mod.build(ledger, operation_id=f"op{i}", activated_at=f"2026-11-0{i}T00:00:00+00:00", client_dir="Acme", space=space,
                                  root_object=root, root_type="analytic-model", extra={}, file_digests={}, counts={})
    ledger_mod.write(asvc.catalog_dir(), "Acme", ledger)


def _catalog_html() -> str:
    c = cat.client_for()
    cat._post(c)
    return c.get("/catalog/flow/assessment?flow=acme-4am", headers=cat.HX).text


def test_catalog_shows_the_space_of_the_last_registered_activation(world):  # noqa: F811
    _write_ledger(("CALIDAD", "4AM_ACME"), ("PRODUCCION", "4AM_OTHER"), ("PRODUCCION", "4AM_ACME"))
    assert _space_badge(_catalog_html()) == "Space: PRODUCCION"


def test_catalog_without_reliable_evidence_shows_no_space(world):  # noqa: F811
    assert _space_badge(_catalog_html()) is None                                        # sin ledger
    _write_ledger(("CALIDAD", "4AM_OTHER"))
    assert _space_badge(_catalog_html()) is None                                        # el ledger no registra ESTE modelo
    ledger_mod.ledger_path(asvc.catalog_dir(), "Acme").write_text("{no es json", encoding="utf-8")
    assert _space_badge(_catalog_html()) is None                                        # ledger ilegible: nunca se infiere


def test_catalog_space_never_changes_the_governed_result(world):  # noqa: F811
    html_without = _catalog_html()
    env_before = json.dumps(asvc._load_envelope("Acme", "4AM_ACME"), sort_keys=True)
    _write_ledger(("CALIDAD", "4AM_ACME"))
    html_with = _catalog_html()
    assert json.dumps(asvc._load_envelope("Acme", "4AM_ACME"), sort_keys=True) == env_before   # sobre gobernado intacto
    level = lambda h: BeautifulSoup(h, "html.parser").select_one(".asr-hero [data-asr-level]")    # noqa: E731
    assert (level(html_without) is None) == (level(html_with) is None)
    if level(html_with) is not None:
        assert level(html_with)["data-asr-level"] == level(html_without)["data-asr-level"]


# ── «Evaluar la versión actual»: del Catálogo al Assessment ad hoc, sin publicar ────────────────────────────────────
def test_catalog_report_links_to_the_adhoc_assessment_of_the_same_model(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dsvc, "can_execute", lambda request: True)
    link = BeautifulSoup(_catalog_html(), "html.parser").select_one("[data-asr-diagnostic]")
    assert link is not None and link["href"] == dsvc.catalog_prefill_url("04_Modelado_y_Desarrollo", "datasphere_core", "Acme", "4AM_ACME")
    monkeypatch.setattr(dsvc, "can_execute", lambda request: False)                       # sin acceso a la herramienta: sin enlace
    assert BeautifulSoup(_catalog_html(), "html.parser").select_one("[data-asr-diagnostic]") is None


def test_the_tool_is_prefilled_with_the_model_and_never_with_a_space(owner, fake_export):  # noqa: F811
    ds._post(owner, data={**ds.VALID, "space": "CALIDAD"})                               # contexto previo de la sesión con un Space
    html = owner.get(f"/modules/{ds.STEM}?pack=datasphere_core&tool=assessment&client=Acme&root=4AM_OTRO").text
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("#dsa-form")
    assert form.select_one('[name="datasphere_root_object"]')["value"] == "4AM_OTRO" and form.select_one('[name="client"]')["value"] == "Acme"
    assert (form.select_one('[name="space"]').get("value") or "") == ""                 # el Space lo elige el consultor
    bad = owner.get(f"/modules/{ds.STEM}?pack=datasphere_core&tool=assessment&client=Acme&root=%3Cscript%3E").text
    assert BeautifulSoup(bad, "html.parser").select_one('#dsa-form [name="datasphere_root_object"]')["value"] != "<script>"   # inválido: no precarga
