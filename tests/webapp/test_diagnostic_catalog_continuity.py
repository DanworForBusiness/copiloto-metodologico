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


def _readable_empty_catalog(monkeypatch):
    """Catálogo LEGIBLE sin entradas (el modelo no está publicado): distinto de un Catálogo que no se pudo cargar."""
    from tools.catalog import catalog_service
    from webapp.services import catalog_reads
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (catalog_service, []))


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
def test_recent_tile_offers_the_review_and_it_leads_to_a_prefilled_publication(owner, fake_export, monkeypatch):  # noqa: F811
    _readable_empty_catalog(monkeypatch)
    r = ds._post(owner, data={**ds.VALID, "space": "CALIDAD"})
    rid = ds._rid(r)
    panel = BeautifulSoup(owner.get(f"/modules/{ds.STEM}?pack=datasphere_core&tool=assessment").text, "html.parser")
    icon = panel.select_one("[data-dsa-catalog]")
    assert icon is not None and icon["href"].startswith(f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}/catalog")
    assert "Regenerar Catálogo" in icon.get_text(" ", strip=True)
    before = json.dumps(dsvc.load_result(rid), sort_keys=True)
    review = BeautifulSoup(owner.get(icon["href"]).text, "html.parser")
    main = review.select_one("[data-dsa-catalog-review]")
    assert main["data-operation"] == dsvc.OP_INCORPORATE                                    # Catálogo legible sin este modelo
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


# ── Catálogo ilegible o identidad no resuelta: nunca «no existe», nunca publicar ─────────────────────────────────────
def test_unreadable_catalog_never_claims_absence_nor_offers_publishing(owner, fake_export, monkeypatch):  # noqa: F811
    from webapp.services import catalog_reads
    rid = ds._rid(ds._post(owner, data={**ds.VALID, "space": "CALIDAD"}))
    url = f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}/catalog?pack=datasphere_core"
    for broken in (lambda: (None, []), lambda: (_ for _ in ()).throw(OSError("catálogo ilegible"))):
        monkeypatch.setattr(catalog_reads, "_catalog_entries", broken)
        r = dsvc.catalog_continuity(dsvc.load_result(rid))
        assert r["operation"] == dsvc.OP_BLOCKED_CATALOG_UNVERIFIED and r["operation"] not in dsvc.PUBLISHABLE_OPERATIONS and r["catalog_unavailable"]
        page = BeautifulSoup(owner.get(url).text, "html.parser")
        assert page.select_one("[data-review-publish]") is None
        assert page.select_one("[data-review-catalog-model]").get_text(strip=True) == "No verificable"


def test_unresolvable_catalog_identity_is_not_verifiable(world, monkeypatch):  # noqa: F811
    real = asvc.resolve_entries
    monkeypatch.setattr(asvc, "resolve_entries", lambda es: {k: asvc.Resolution("POOL_LOAD_ERROR", v.client) for k, v in real(es).items()})
    assert dsvc.catalog_continuity(_record(am="4AM_NUEVO"))["operation"] == dsvc.OP_BLOCKED_CATALOG_UNVERIFIED


def test_publishable_review_states_available_unverified_and_transactional_confirmation(owner, fake_export, monkeypatch):  # noqa: F811
    _readable_empty_catalog(monkeypatch)
    rid = ds._rid(ds._post(owner, data={**ds.VALID, "space": "CALIDAD"}))
    page = BeautifulSoup(owner.get(f"/modules/{ds.STEM}/datasphere-assessment/result/{rid}/catalog?pack=datasphere_core").text, "html.parser")
    assert page.select_one("[data-review-available]") and page.select_one("[data-review-confirmation]")
    assert "todos" in page.select_one("[data-review-not-verified]").get_text(" ", strip=True)     # la guarda revisa el modelo Y sus dependencias
