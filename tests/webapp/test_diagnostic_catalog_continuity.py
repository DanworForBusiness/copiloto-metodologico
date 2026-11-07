"""
test_diagnostic_catalog_continuity.py — «Regenerar Catálogo» desde un Assessment ad hoc reciente: REVISIÓN de solo lectura y operación gobernada realmente disponible.

  * el ícono abre la revisión del resultado (GET, sin escrituras); autorización y propiedad del resultado como el resto de la herramienta;
  * la operación se decide por evidencia persistida: sin entrada en el Catálogo -> incorporar (publicar); publicado desde el MISMO Space -> actualizar (publicar);
    sin Space registrado -> publicar con la guarda del pool decidiendo; publicado desde OTRO Space -> bloqueado (no se ofrece); resultado sin Space -> bloqueado;
  * la única acción es ir al formulario de PUBLICACIÓN precargado con cliente, modelo y el Space DEL RESULTADO (nunca credenciales); el perfil temporal no sustituye nada.
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


def _record(space="CALIDAD", am="4AM_ACME", client="Acme"):
    return {"result_id": "r" * 32, "client_display": client, "terminal_am": am, "space": space, "profile": None}


def _ledger(space):
    led = ledger_mod.build({}, operation_id="op1", activated_at="2026-11-01T00:00:00+00:00", client_dir="Acme", space=space, root_object="4AM_ACME",
                           root_type="analytic-model", extra={}, file_digests={}, counts={})
    ledger_mod.write(asvc.catalog_dir(), "Acme", led)


# ── decisión de la operación ─────────────────────────────────────────────────────────────────────────────────────
def test_model_not_in_catalog_is_incorporated_by_publishing(world):  # noqa: F811
    assert dsvc.catalog_continuity(_record(am="4AM_NUEVO"))["operation"] == dsvc.OP_INCORPORATE


def test_model_in_catalog_without_recorded_space_lets_the_pool_guard_decide(world):  # noqa: F811
    r = dsvc.catalog_continuity(_record())
    assert r["operation"] == dsvc.OP_UPDATE_UNVERIFIED_SPACE and r["catalog"]["published_space"] is None
    assert any("no tiene registrado" in d for d in r["differences"])


def test_same_space_updates_and_other_space_is_blocked(world):  # noqa: F811
    _ledger("CALIDAD")
    assert dsvc.catalog_continuity(_record(space="CALIDAD"))["operation"] == dsvc.OP_UPDATE_SAME_SPACE
    _ledger("PRODUCCION")
    r = dsvc.catalog_continuity(_record(space="CALIDAD"))
    assert r["operation"] == dsvc.OP_BLOCKED_OTHER_SPACE and r["operation"] not in dsvc.PUBLISHABLE_OPERATIONS
    assert any("PRODUCCION" in d and "CALIDAD" in d for d in r["differences"])


def test_result_without_space_is_blocked(world):  # noqa: F811
    assert dsvc.catalog_continuity(_record(space=""))["operation"] == dsvc.OP_BLOCKED_NO_SPACE


# ── recorrido: ícono -> revisión -> formulario de publicación precargado (sin escrituras) ─────────────────────────────
def test_recent_tile_offers_the_review_and_it_leads_to_a_prefilled_publication(owner, fake_export):  # noqa: F811
    r = ds._post(owner, data={**ds.VALID, "space": "CALIDAD"})
    rid = ds._rid(r)
    panel = BeautifulSoup(owner.get(f"/modules/{ds.STEM}?pack=datasphere_core&tool=assessment").text, "html.parser")
    icon = panel.select_one("[data-dsa-catalog]")
    assert icon is not None and icon["href"].startswith(f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}/catalog")
    assert "Regenerar Catálogo" in icon.get_text(" ", strip=True)
    before = json.dumps(dsvc.load_result(rid), sort_keys=True)
    review = BeautifulSoup(owner.get(icon["href"]).text, "html.parser")
    main = review.select_one("[data-dsa-catalog-review]")
    assert main["data-operation"] == dsvc.OP_INCORPORATE                                    # Acme/4AM_ACME no está en el Catálogo de este entorno
    assert review.select_one("[data-review-space]").get_text(strip=True) == "CALIDAD"
    link = review.select_one("[data-review-publish]")
    assert link is not None and link["href"] == dsvc.publication_prefill_url(ds.STEM, "datasphere_core", "Acme", "4AM_ACME", "CALIDAD")
    assert json.dumps(dsvc.load_result(rid), sort_keys=True) == before                      # la revisión no toca el resultado
    form = BeautifulSoup(owner.get(link["href"]).text, "html.parser").select_one("#dsp-form")
    assert form.select_one('[name="space"]')["value"] == "CALIDAD" and form.select_one('[name="datasphere_root_object"]')["value"] == "4AM_ACME"
    assert form.select_one('[name="client"]')["value"] == "Acme" and (form.select_one('[name="datasphere_client_secret"]').get("value") or "") == ""


def test_review_of_someone_elses_result_is_not_found(owner, fake_export):  # noqa: F811
    rid = ds._rid(ds._post(owner, data={**ds.VALID, "space": "CALIDAD"}))
    assert ds._client("otro@danwor.com").get(f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}/catalog").status_code in (403, 404)
