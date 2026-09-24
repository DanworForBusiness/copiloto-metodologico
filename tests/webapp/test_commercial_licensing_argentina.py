"""
test_commercial_licensing_argentina.py — Commercial Licensing, Director
Requests §4: el universo base del Perfil de licenciamiento pasa a ser
`Es Cliente?=Sí AND País=Argentina` (antes sólo `Es Cliente?=Sí`).
Country CANÓNICO (`res.partner.country_id` → `res.country.code`), nunca
matching de texto libre, nunca derivado de un contacto hijo arbitrario —
ver `CommercialService._argentina_country_id`/`_build_licensing_profile`.

Fixture PROPIO y acotado (no reusa el de test_commercial_licensing_profile.py,
que ya cubre el resto de las dimensiones con TODAS las cuentas en
Argentina) — acá el foco es EXCLUSIVAMENTE la dimensión país.

`svc_module.clear_cache()` antes/después de cada test (MISMO patrón ya
establecido en test_commercial_licensing_profile.py): `get_licensing_profile`
cachea por una key FIJA (`"commercial:licensing-profile"`), independiente
del `FakeOdooClient` instanciado — sin esto, un test vería el resultado
cacheado de OTRO test con datos distintos."""
from __future__ import annotations

from tests.webapp._commercial_fake_odoo import FakeOdooClient
from webapp.services import commercial_service as svc_module
from webapp.services.commercial_service import CommercialService

_ES_CLIENTE_PROP = "2397af606d6cf8a3"


def _es_cliente_si() -> dict:
    return {"name": _ES_CLIENTE_PROP, "string": "Es Cliente?", "type": "selection", "value": "si_key",
            "selection": [["si_key", "Sí"], ["no_key", "No"], ["ex_key", "Ex"]]}


def _es_cliente_no() -> dict:
    return {"name": _ES_CLIENTE_PROP, "string": "Es Cliente?", "type": "selection", "value": "no_key",
            "selection": [["si_key", "Sí"], ["no_key", "No"], ["ex_key", "Ex"]]}


AR = [9, "Argentina"]
BR = [23, "Brasil"]


def _names(accounts: list[dict]) -> set[str]:
    return {a["name"] for a in accounts}


def _profile(partners, tipo="todos", **overrides) -> dict:
    kwargs = {"stages": [], "tags": [], "leads": [], "partners": partners}
    kwargs.update(overrides)
    svc_module.clear_cache()
    try:
        return CommercialService(FakeOdooClient(**kwargs)).get_licensing_profile(tipo)
    finally:
        svc_module.clear_cache()


# ============================================================================
# J/K/L/M — elegibilidad de país, composición con Es Cliente?
# ============================================================================

def test_j_client_and_argentina_is_included():
    partners = [{"id": 1, "name": "Cliente AR", "country_id": AR, "properties": [_es_cliente_si()]}]
    assert _names(_profile(partners)["accounts"]) == {"Cliente AR"}


def test_k_client_and_non_argentina_is_excluded():
    partners = [{"id": 1, "name": "Cliente BR", "country_id": BR, "properties": [_es_cliente_si()]}]
    assert _profile(partners)["accounts"] == []


def test_l_client_with_blank_country_is_excluded():
    partners = [{"id": 1, "name": "Cliente Sin País", "properties": [_es_cliente_si()]}]
    assert _profile(partners)["accounts"] == []


def test_m_non_client_in_argentina_is_excluded():
    partners = [{"id": 1, "name": "No Cliente AR", "country_id": AR, "properties": [_es_cliente_no()]}]
    assert _profile(partners)["accounts"] == []


def test_mixed_universe_only_client_and_argentina_survives():
    partners = [
        {"id": 1, "name": "Cliente AR", "country_id": AR, "properties": [_es_cliente_si()]},
        {"id": 2, "name": "Cliente BR", "country_id": BR, "properties": [_es_cliente_si()]},
        {"id": 3, "name": "No Cliente AR", "country_id": AR, "properties": [_es_cliente_no()]},
        {"id": 4, "name": "Cliente Sin País", "properties": [_es_cliente_si()]},
    ]
    assert _names(_profile(partners)["accounts"]) == {"Cliente AR"}


# ============================================================================
# N — país de la cuenta CANÓNICA, nunca de un contacto hijo arbitrario
# ============================================================================

def test_n_uses_canonical_company_country_not_child_contact_country():
    # El CONTACTO (id=2) tiene su propio country_id=Brasil (un empleado que
    # viaja/vive afuera, por ejemplo) pero su EMPRESA (id=1,
    # commercial_partner_id de destino) es Argentina — la cuenta debe
    # incluirse (país de la empresa), y el país "propio" del contacto nunca
    # se lee para esta decisión.
    partners = [
        {"id": 1, "name": "Empresa AR", "country_id": AR, "properties": [_es_cliente_si()]},
        {"id": 2, "name": "Contacto BR viajero", "commercial_partner_id": 1, "country_id": BR,
         "properties": [_es_cliente_si()]},
    ]
    assert _names(_profile(partners)["accounts"]) == {"Empresa AR"}


def test_n_child_contact_alone_resolves_to_companys_country():
    # Sin la empresa duplicada en Es Cliente?=Sí — sólo el contacto trae la
    # property, resuelve a account_id=1 (su empresa), y el país que decide
    # es el de la empresa (Argentina), no el que el contacto pudiera tener.
    partners = [
        {"id": 1, "name": "Empresa AR", "country_id": AR, "properties": []},
        {"id": 2, "name": "Contacto", "commercial_partner_id": 1, "country_id": BR,
         "properties": [_es_cliente_si()]},
    ]
    assert _names(_profile(partners)["accounts"]) == {"Empresa AR"}


# ============================================================================
# O — cero N+1: una sola consulta a res.country para TODO el batch,
# nunca una por cuenta.
# ============================================================================

def test_o_single_res_country_query_regardless_of_account_count():
    partners = [
        {"id": i, "name": f"Cliente AR {i}", "country_id": AR, "properties": [_es_cliente_si()]}
        for i in range(1, 21)
    ]
    client = FakeOdooClient(stages=[], tags=[], leads=[], partners=partners)
    svc_module.clear_cache()
    try:
        CommercialService(client).get_licensing_profile()
    finally:
        svc_module.clear_cache()
    country_calls = [c for c in client.calls if c[0] == "res.country"]
    assert len(country_calls) == 1


def test_o_no_res_country_query_when_es_cliente_universe_already_empty():
    # Atajo ya existente (universo Es Cliente?=Sí vacío -> perfil vacío sin
    # tocar res.partner en absoluto) — confirmarlo también evita gastar la
    # consulta de país cuando no hace falta.
    client = FakeOdooClient(stages=[], tags=[], leads=[], partners=[])
    svc_module.clear_cache()
    try:
        CommercialService(client).get_licensing_profile()
    finally:
        svc_module.clear_cache()
    assert not any(c[0] == "res.country" for c in client.calls)


# ============================================================================
# Fail-closed: "AR" ausente en esta instancia de Odoo -> universo vacío,
# nunca "incluye todo por las dudas".
# ============================================================================

def test_missing_ar_country_in_this_odoo_instance_yields_empty_profile():
    partners = [{"id": 1, "name": "Cliente AR", "country_id": AR, "properties": [_es_cliente_si()]}]
    assert _profile(partners, countries=[])["accounts"] == []


# ============================================================================
# tipo (Todos/NN/BI) sigue sin afectar el universo del Perfil — MISMO
# criterio ya establecido, ahora también verificado con la dimensión país
# de por medio.
# ============================================================================

def test_tipo_independent_of_new_country_filter():
    partners = [{"id": 1, "name": "Cliente AR", "country_id": AR, "properties": [_es_cliente_si()]}]
    kwargs = {"stages": [], "tags": [], "leads": [], "partners": partners}
    svc_module.clear_cache()
    try:
        svc = CommercialService(FakeOdooClient(**kwargs))
        todos = svc.get_licensing_profile("todos")
        nn = svc.get_licensing_profile("nn")
        bi = svc.get_licensing_profile("bi")
    finally:
        svc_module.clear_cache()
    assert todos == nn == bi
