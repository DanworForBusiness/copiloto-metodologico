"""
test_commercial_licensing_profile.py — Account Licensing Profile (ver
HANDOFF — Commercial Licensing, owner ticket "Final Business Semantics
Realignment"). `CommercialService.get_licensing_profile` vía
`FakeOdooClient` — CI NUNCA depende de Odoo real.

Universo ÚNICO y PERMANENTE (owner §3/§4): toda cuenta canónica con
`Es Cliente?=Sí` — punto. Ya NO deriva de oportunidades/tags en absoluto,
así que `_build_licensing_profile` ya NO toca `crm.lead`/`crm.stage`/
`crm.tag` — el fixture de este archivo por eso no necesita `LEADS`/`STAGES`/
`TAGS` salvo en los dos tests que prueban explícitamente que la presencia de
oportunidades/tags NO tiene efecto (§6/§16.Q).

Universo de cuentas del fixture (`_licensing_client`):

  100 Cuenta Si          — Es Cliente?=Sí, CERO oportunidades
  101 Cuenta No          — Es Cliente?=No
  102 Cuenta Ex          — Es Cliente?=Ex
  103 Cuenta Blank       — sin property Es Cliente? seteada
  104 Cuenta Si Con NN   — Es Cliente?=Sí + 1 opp activa con tag New Name
  105 Cuenta Si Con BI   — Es Cliente?=Sí + 1 opp activa con tag Base Instalada
  106 Contacto           — persona, commercial_partner_id=100 (colapsa a 100)
  107 Cuenta Perfil      — Es Cliente?=Sí + TODAS las properties de
                           licenciamiento seteadas (adapter end-to-end)
  108 Cuenta Metadata Drift — Es Cliente?=Sí + property técnica presente
                           pero con `string` distinto al esperado (guard)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.webapp._commercial_fake_odoo import FakeOdooClient
from webapp.services import commercial_service as svc_module
from webapp.services.commercial_service import (
    CommercialConfigurationError,
    CommercialDataError,
    CommercialService,
)

_COMMERCIAL_SERVICE_PY_SRC = (
    Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_service.py"
).read_text(encoding="utf-8")

STAGES = [
    {"id": 1, "name": "0 Prospecto"},
    {"id": 2, "name": "1 Contacto"},
]
TAGS = [
    {"id": 10, "name": "New Name"},
    {"id": 11, "name": "Base Instalada BI"},
]

_ES_CLIENTE_PROP = "2397af606d6cf8a3"
_PARTNER_LIC_PROP = "85e69c44e445398f"
_AE_SAP_PROP = "e85a4881542a93b0"
_ARQ_PROP = "8bcae38dc5e849eb"
_MONTO_PROP = "4495d8705b9b212c"
_VENC_PROP = "c084bf66dd492576"
_TIPO_CONTRATO_PROP = "148e42f95b7b9dc8"
_SEGMENTO_PROP = "dd859ad74685c28e"


def _es_cliente(value_key: str) -> dict:
    return {"name": _ES_CLIENTE_PROP, "string": "Es Cliente?", "type": "selection", "value": value_key,
            "selection": [["si_key", "Sí"], ["no_key", "No"], ["ex_key", "Ex"]]}


PARTNERS = [
    {"id": 100, "name": "Cuenta Si", "properties": [_es_cliente("si_key")]},
    {"id": 101, "name": "Cuenta No", "properties": [_es_cliente("no_key")]},
    {"id": 102, "name": "Cuenta Ex", "properties": [_es_cliente("ex_key")]},
    {"id": 103, "name": "Cuenta Blank", "properties": []},
    {"id": 104, "name": "Cuenta Si Con NN", "properties": [_es_cliente("si_key")]},
    {"id": 105, "name": "Cuenta Si Con BI", "properties": [_es_cliente("si_key")]},
    {"id": 106, "name": "Contacto", "commercial_partner_id": 100, "properties": [_es_cliente("si_key")]},
    {"id": 107, "name": "Cuenta Perfil", "user_id": [60, "Juan Manuel Melgarejo"],
     "currency_id": [1, "USD"],
     "properties": [
         _es_cliente("si_key"),
         {"name": _PARTNER_LIC_PROP, "string": "Partner de Licencia", "type": "selection",
          "value": "sk", "selection": [["sk", "05 - SOFTTEK"]]},
         {"name": _AE_SAP_PROP, "string": "AE SAP", "type": "selection",
          "value": "ae", "selection": [["ae", "CF - BI - Alejandro Nuñez"]]},
         {"name": _ARQ_PROP, "string": "Arquitectura Actual", "type": "tags",
          # 3 elementos por tag (key, label, color) — MISMA forma que Odoo
          # real (descubierto en vivo: `dict()` sobre pares de 2 revienta
          # contra el `tags` real, que trae un color de más).
          "value": ["sac", "bw"], "tags": [["sac", "SAC", 3], ["bw", "BW", 5]]},
         {"name": _MONTO_PROP, "string": "Monto Contrato", "type": "monetary",
          "value": 15000, "currency_field": "currency_id"},
         {"name": _VENC_PROP, "string": "Vencimiento Contrato", "type": "date", "value": "2027-03-15"},
         {"name": _TIPO_CONTRATO_PROP, "string": "Tipo de Contrato SAP", "type": "selection",
          "value": "ci", "selection": [["ci", "Contrato Individual"]]},
         {"name": _SEGMENTO_PROP, "string": "Segmento de la Cuenta", "type": "selection",
          "value": "corp", "selection": [["corp", "Corporate"]]},
     ]},
    {"id": 108, "name": "Cuenta Metadata Drift", "properties": [
        _es_cliente("si_key"),
        {"name": _MONTO_PROP, "string": "Monto Contrato (renombrada)", "type": "monetary",
         "value": 999, "currency_field": "currency_id"},
    ]},
]

# Sólo usados por los tests que prueban explícitamente que oportunidades/
# tags NO tienen efecto sobre el universo del Perfil (§16.Q).
LEADS = [
    {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [104, "Cuenta Si Con NN"]},
    {"id": 2, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [11],
     "partner_id": [105, "Cuenta Si Con BI"]},
    # Oportunidad de un CONTACTO (106) cuya empresa es 100 — nunca debe
    # generar una fila propia, ni siquiera sumar una SEGUNDA fila para 100
    # (que ya entra por su propia property Es Cliente?=Sí).
    {"id": 3, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [106, "Contacto"]},
]


def _client(**overrides) -> FakeOdooClient:
    kwargs = {"stages": STAGES, "tags": TAGS, "leads": LEADS, "partners": PARTNERS}
    kwargs.update(overrides)
    return FakeOdooClient(**kwargs)


def _names(accounts: list[dict]) -> set[str]:
    return {a["name"] for a in accounts}


# ============================================================================
# Universo — Es Cliente?=Sí, punto. Nunca deriva de oportunidades/tags.
# ============================================================================

def test_includes_es_cliente_si_account_with_zero_opportunities():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Si" in _names(profile["accounts"])
    svc_module.clear_cache()


def test_excludes_es_cliente_no_account():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta No" not in _names(profile["accounts"])
    svc_module.clear_cache()


def test_excludes_es_cliente_ex_account():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Ex" not in _names(profile["accounts"])
    svc_module.clear_cache()


def test_excludes_blank_es_cliente_property_account():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Blank" not in _names(profile["accounts"])
    svc_module.clear_cache()


def test_es_cliente_si_account_with_new_name_tagged_opportunity_still_included():
    # Owner §6/§16.Q: la presencia/tag de una oportunidad NO tiene efecto
    # sobre la elegibilidad — la cuenta entra SOLO por Es Cliente?=Sí.
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Si Con NN" in _names(profile["accounts"])
    svc_module.clear_cache()


def test_es_cliente_si_account_with_base_instalada_tagged_opportunity_still_included():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Si Con BI" in _names(profile["accounts"])
    svc_module.clear_cache()


def test_opportunity_licenciamiento_value_has_no_effect_on_profile_universe():
    # Owner §4: "Licenciamiento is an opportunity-level concept and does not
    # define the Profile universe" — probado quitando TODAS las
    # oportunidades del fixture: el universo del Perfil no cambia un ápice.
    svc_module.clear_cache()
    with_opps = CommercialService(_client()).get_licensing_profile("todos")
    without_opps = CommercialService(_client(leads=[])).get_licensing_profile("todos")
    assert _names(with_opps["accounts"]) == _names(without_opps["accounts"])
    svc_module.clear_cache()


# ============================================================================
# Tipo (Todos/New Name/Base instalada) NO afecta el Perfil (owner §6)
# ============================================================================

def test_todos_nn_bi_return_the_identical_account_set():
    svc_module.clear_cache()
    client = _client()
    todos = CommercialService(client).get_licensing_profile("todos")
    nn = CommercialService(client).get_licensing_profile("nn")
    bi = CommercialService(client).get_licensing_profile("bi")
    assert _names(todos["accounts"]) == _names(nn["accounts"]) == _names(bi["accounts"])
    assert todos["total"] == nn["total"] == bi["total"]
    svc_module.clear_cache()


def test_switching_tipo_does_not_change_the_base_profile_account_set():
    # Mismo test que arriba, desde el ángulo del owner ticket (§16.S): un
    # cambio de Tipo global NUNCA redefine QUÉ cuentas son elegibles.
    svc_module.clear_cache()
    client = _client()
    for tipo in ("todos", "nn", "bi", "todos"):
        profile = CommercialService(client).get_licensing_profile(tipo)
        assert _names(profile["accounts"]) == {
            "Cuenta Si", "Cuenta Si Con NN", "Cuenta Si Con BI", "Cuenta Perfil", "Cuenta Metadata Drift",
        }
    svc_module.clear_cache()


# ============================================================================
# Grano de cuenta — commercial_partner_id, sin duplicados
# ============================================================================

def test_contact_opportunity_collapses_into_its_company_never_a_standalone_row():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    names = _names(profile["accounts"])
    assert "Contacto" not in names, "un contacto nunca es una fila propia"
    assert "Cuenta Si" in names
    ids = [a["id"] for a in profile["accounts"] if a["name"] == "Cuenta Si"]
    assert ids == [100], "el contacto (106) colapsó a la empresa 100, sin duplicar la fila"
    svc_module.clear_cache()


def test_one_row_per_account_even_when_es_cliente_si_is_set_on_a_contact():
    # `partners` acotado a 100 (empresa) + 106 (contacto, Es Cliente?=Sí
    # propio) — sin las demás cuentas del fixture general, para aislar el
    # caso: la property "Es Cliente?" en el CONTACTO también debe resolver
    # a la empresa, nunca duplicar la fila.
    svc_module.clear_cache()
    contact_only_client = _client(
        leads=[],
        partners=[p for p in PARTNERS if p["id"] in (100, 106)],
    )
    profile = CommercialService(contact_only_client).get_licensing_profile("todos")
    assert profile["total"] == 1
    assert profile["accounts"][0]["name"] == "Cuenta Si"
    svc_module.clear_cache()


# ============================================================================
# Adapter tipado — technical name + label/type guard
# ============================================================================

def test_property_adapter_resolves_all_seven_typed_fields():
    # `arquitectura_actual` acá viaja con tags de TRES elementos
    # (key/label/color, ver fixture `PARTNERS[107]`) — MISMA forma que Odoo
    # real (regresión: encontrado en vivo, `_tags_value` reventaba con pares
    # de 2 asumidos).
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    account = next(a for a in profile["accounts"] if a["name"] == "Cuenta Perfil")
    assert account["partner_de_licencia"] == "05 - SOFTTEK"
    assert account["ae_sap"] == "CF - BI - Alejandro Nuñez"
    assert account["arquitectura_actual"] == ["SAC", "BW"]
    assert account["monto_contrato"] == 15000
    assert account["currency"] == "USD"
    assert account["vencimiento_contrato"] == "2027-03-15"
    assert account["tipo_contrato_sap"] == "Contrato Individual"
    assert account["segmento_cuenta"] == "Corporate"
    assert account["seller"] == "Juan Manuel Melgarejo"
    svc_module.clear_cache()


def test_tags_value_handles_three_element_odoo_tuples_directly():
    # Unit-level, aislado del resto del pipeline: prueba DIRECTO
    # `_tags_value` contra la forma real de Odoo (key, label, color) —
    # regresión puntual del bug encontrado en vivo (`dict()` sobre la lista
    # cruda reventaba con "sequence element has length 3; 2 is required").
    from webapp.services.commercial_service import _tags_value
    props = [{"name": _ARQ_PROP, "string": "Arquitectura Actual", "type": "tags",
              "value": ["a", "b"], "tags": [["a", "Alpha", 1], ["b", "Beta", 7], ["c", "Gamma", 2]]}]
    assert _tags_value(props, _ARQ_PROP, "Arquitectura Actual") == ["Alpha", "Beta"]


def test_missing_property_resolves_to_none_not_a_crash():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    account = next(a for a in profile["accounts"] if a["name"] == "Cuenta Si")
    assert account["partner_de_licencia"] is None
    assert account["monto_contrato"] is None
    assert account["arquitectura_actual"] == []
    svc_module.clear_cache()


def test_metadata_drift_is_treated_as_absent_never_misinterpreted():
    # La property técnica EXISTE (mismo `name`) pero su `string` no coincide
    # con el esperado — owner: "fail observably", nunca interpretar
    # metadata incompatible como si fuera la correcta.
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    account = next(a for a in profile["accounts"] if a["name"] == "Cuenta Metadata Drift")
    assert account["monto_contrato"] is None, "string distinto -> tratado como ausente, nunca 999"
    svc_module.clear_cache()


def test_property_lookup_is_by_technical_name_not_by_label_alone():
    src = _COMMERCIAL_SERVICE_PY_SRC.split("def _find_partner_property(", 1)[1].split("\n\n\n", 1)[0]
    assert 'prop.get("name") != technical_name' in src
    assert 'prop.get("string")' in src  # el label sigue siendo un GUARD, no la clave primaria


# ============================================================================
# Batching — cero N+1, y ya NO toca crm.lead/crm.stage/crm.tag
# ============================================================================

def test_licensing_profile_issues_a_bounded_number_of_odoo_calls():
    svc_module.clear_cache()
    client = _client()
    CommercialService(client).get_licensing_profile("todos")
    models_called = [call[0] for call in client.calls]
    partner_calls = [m for m in models_called if m == "res.partner"]
    # A lo sumo DOS reads de res.partner (batch principal + segunda pasada
    # acotada de empresas huérfanas) — nunca uno por cuenta.
    assert len(partner_calls) <= 2, f"se esperaban <=2 lecturas de res.partner, hubo {len(partner_calls)}"
    svc_module.clear_cache()


def test_licensing_profile_no_longer_reads_opportunities_or_tags():
    # Owner "Final Business Semantics Realignment" §12: "Do not infer
    # client status from opportunity tags... presence of opportunities...
    # Base instalada" — el universo es puramente account-native ahora, así
    # que `crm.lead`/`crm.stage`/`crm.tag` NUNCA deberían tocarse.
    svc_module.clear_cache()
    client = _client()
    CommercialService(client).get_licensing_profile("todos")
    models_called = {call[0] for call in client.calls}
    assert "crm.lead" not in models_called
    assert "crm.stage" not in models_called
    assert "crm.tag" not in models_called
    svc_module.clear_cache()


def test_no_second_properties_parser_was_introduced():
    # Reusa `_partner_ids_by_cliente_value` LITERAL para "Es Cliente?" — cero
    # segunda implementación de esa regla.
    body = _COMMERCIAL_SERVICE_PY_SRC.split("def _build_licensing_profile(", 1)[1].split("\n    def ", 1)[0]
    assert "self._partner_ids_by_cliente_value(self._CLIENTE_SI_LABELS)" in body
    assert "_CLIENTE_SI_LABELS" in body
    assert '"si cliente"' not in body.lower()  # sin un segundo set de labels ad-hoc


# ============================================================================
# Endpoint / config / falla — MISMO contrato fail-soft que el resto
# ============================================================================

def test_get_licensing_profile_requires_configuration():
    with pytest.raises(CommercialConfigurationError):
        CommercialService(_client(configured=False)).get_licensing_profile("todos")


def test_get_licensing_profile_wraps_transport_failure():
    svc_module.clear_cache()
    client = _client()
    client.raise_on("res.partner", stage="execute")
    with pytest.raises(CommercialDataError):
        CommercialService(client).get_licensing_profile("todos")
    svc_module.clear_cache()


def test_invalid_tipo_falls_back_to_todos():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("not-a-real-tipo")
    assert profile["total"] == len(
        CommercialService(_client()).get_licensing_profile("todos")["accounts"]
    )
    svc_module.clear_cache()


def test_empty_es_cliente_universe_returns_zero_accounts_for_all_tipos():
    svc_module.clear_cache()
    client = _client(leads=[], partners=[])
    for tipo in ("todos", "nn", "bi"):
        profile = CommercialService(client).get_licensing_profile(tipo)
        assert profile == {"accounts": [], "total": 0}
    svc_module.clear_cache()


def test_licensing_profile_endpoint_is_independent_from_opportunities_detail():
    router_src = (
        Path(__file__).resolve().parents[2] / "webapp" / "routers" / "commercial.py"
    ).read_text(encoding="utf-8")
    assert router_src.count('@router.get("/comercial/data/licensing-profile"') == 1
    endpoint_body = router_src.split(
        '@router.get("/comercial/data/licensing-profile"', 1)[1].split("\n\n\n", 1)[0]
    assert "get_licensing_profile" in endpoint_body
    assert "get_opportunities_detail" not in endpoint_body


def test_licensing_profile_never_writes_and_reads_only_res_partner():
    block = _COMMERCIAL_SERVICE_PY_SRC.split("def _build_licensing_profile(", 1)[1].split(
        "\n    def get_licensing_profile", 1)[0]
    for banned in (".create(", ".write(", ".unlink(", "execute_kw"):
        assert banned not in block, banned
    assert '"res.partner"' in block
    for banned_model in ('"crm.lead"', '"crm.stage"', '"crm.tag"'):
        assert banned_model not in block, banned_model
