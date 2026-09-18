"""
test_commercial_licensing_profile.py — Account Licensing Profile (ver
HANDOFF — Commercial Licensing). `CommercialService.get_licensing_profile`
vía `FakeOdooClient` — CI NUNCA depende de Odoo real.

Etapas: 1="0 Prospecto" (prospecto), 2="1 Contacto" (activa), 3="4 Propuesta"
(activa). Tags: 10="New Name" (NN), 11="Base Instalada BI" (BI, requiere
AMBOS substrings "base"+"instal", ver `_nn_bi_tags`).

Universo de cuentas del fixture (`_licensing_client`) — una por escenario del
owner GO §3/§6, nunca reutilizada entre casos para que cada aserción señale
exactamente la regla que prueba:

  100 Cuenta NN         — 1 opp activa con tag NN, sin Es Cliente?
  101 Cuenta BI         — 1 opp activa con tag BI, sin Es Cliente?
  102 Cuenta Both       — 1 opp activa con AMBOS tags
  103 Cuenta Neither    — 1 opp activa SIN ningún tag
  104 Cuenta Dormida Si — Es Cliente?=Sí, CERO oportunidades (whitespace)
  105 Cuenta EsNo ConNN — Es Cliente?=No, 1 opp activa con tag NN
  106 Cuenta EsSi ConBI — Es Cliente?=Sí, 1 opp activa con tag BI
  107 Cuenta Ex         — Es Cliente?=Ex, CERO oportunidades
  108 Cuenta Blank      — sin property Es Cliente? seteada, CERO oportunidades
  109 Contacto          — persona, commercial_partner_id=100 (colapsa a 100)
  110 Cuenta Perfil     — 1 opp activa CON tag NN + TODAS las properties de
                          licenciamiento seteadas (adapter end-to-end)
  111 Cuenta Metadata Drift — property técnica presente pero con `string`
                          distinto al esperado (guard de metadata)
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
from webapp.services.odoo_client import OdooTransportError

_COMMERCIAL_SERVICE_PY_SRC = (
    Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_service.py"
).read_text(encoding="utf-8")

STAGES = [
    {"id": 1, "name": "0 Prospecto"},
    {"id": 2, "name": "1 Contacto"},
    {"id": 3, "name": "4 Propuesta"},
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
    {"id": 100, "name": "Cuenta NN", "properties": []},
    {"id": 101, "name": "Cuenta BI", "properties": []},
    {"id": 102, "name": "Cuenta Both", "properties": []},
    {"id": 103, "name": "Cuenta Neither", "properties": []},
    {"id": 104, "name": "Cuenta Dormida Si", "properties": [_es_cliente("si_key")]},
    {"id": 105, "name": "Cuenta EsNo ConNN", "properties": [_es_cliente("no_key")]},
    {"id": 106, "name": "Cuenta EsSi ConBI", "properties": [_es_cliente("si_key")]},
    {"id": 107, "name": "Cuenta Ex", "properties": [_es_cliente("ex_key")]},
    {"id": 108, "name": "Cuenta Blank", "properties": []},
    {"id": 109, "name": "Contacto", "commercial_partner_id": 100, "properties": []},
    {"id": 110, "name": "Cuenta Perfil", "user_id": [60, "Juan Manuel Melgarejo"],
     "currency_id": [1, "USD"],
     "properties": [
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
    {"id": 111, "name": "Cuenta Metadata Drift", "properties": [
        {"name": _MONTO_PROP, "string": "Monto Contrato (renombrada)", "type": "monetary",
         "value": 999, "currency_field": "currency_id"},
    ]},
]

LEADS = [
    {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [100, "Cuenta NN"]},
    {"id": 2, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [11],
     "partner_id": [101, "Cuenta BI"]},
    {"id": 3, "type": "opportunity", "active": True, "stage_id": 3, "tag_ids": [10, 11],
     "partner_id": [102, "Cuenta Both"]},
    {"id": 4, "type": "opportunity", "active": True, "stage_id": 1, "tag_ids": [],
     "partner_id": [103, "Cuenta Neither"]},
    {"id": 5, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [105, "Cuenta EsNo ConNN"]},
    {"id": 6, "type": "opportunity", "active": True, "stage_id": 3, "tag_ids": [11],
     "partner_id": [106, "Cuenta EsSi ConBI"]},
    # Oportunidad de un CONTACTO (109) cuya empresa es 100 — debe colapsar,
    # nunca aparecer como cuenta propia.
    {"id": 7, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [109, "Contacto"]},
    {"id": 8, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
     "partner_id": [110, "Cuenta Perfil"]},
    # Perdida/inactiva — fuera del universo 0-8 activo, nunca debe colar una
    # cuenta al perfil por sí sola.
    {"id": 9, "type": "opportunity", "active": False, "stage_id": 2, "tag_ids": [10],
     "partner_id": [103, "Cuenta Neither"]},
]


def _client(**overrides) -> FakeOdooClient:
    kwargs = {"stages": STAGES, "tags": TAGS, "leads": LEADS, "partners": PARTNERS}
    kwargs.update(overrides)
    return FakeOdooClient(**kwargs)


def _names(accounts: list[dict]) -> set[str]:
    return {a["name"] for a in accounts}


# ============================================================================
# TODOS — unión (A) pipeline activo + (B) Es Cliente?=Sí dormidas
# ============================================================================

def test_todos_includes_every_account_with_active_pipeline():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    names = _names(profile["accounts"])
    for expected in ("Cuenta NN", "Cuenta BI", "Cuenta Both", "Cuenta Neither",
                      "Cuenta EsNo ConNN", "Cuenta EsSi ConBI", "Cuenta Perfil"):
        assert expected in names, expected
    svc_module.clear_cache()


def test_todos_includes_dormant_es_cliente_si_account_with_zero_pipeline():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Dormida Si" in _names(profile["accounts"])
    svc_module.clear_cache()


def test_todos_excludes_dormant_ex_no_pipeline_account():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Ex" not in _names(profile["accounts"])
    svc_module.clear_cache()


def test_todos_excludes_dormant_blank_property_account():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    assert "Cuenta Blank" not in _names(profile["accounts"])
    svc_module.clear_cache()


def test_todos_excludes_accounts_with_only_inactive_opportunities():
    # La cuenta 103 tiene UNA opp activa (entra por eso) y una INACTIVA
    # (perdida) — la inactiva sola nunca colaría una cuenta si no fuera por
    # la activa. `partners=[]` acá aísla el caso: sin ninguna property
    # "Es Cliente?" en el universo, el ÚNICO camino de entrada a Todos es
    # pipeline activo — así que este test prueba EXCLUSIVAMENTE la regla de
    # `active=True` en el dominio de oportunidades, sin que el componente
    # (B) (dormidas Es Cliente?=Sí) lo contamine.
    svc_module.clear_cache()
    only_inactive_client = _client(leads=[l for l in LEADS if l["id"] == 9], partners=[])
    profile = CommercialService(only_inactive_client).get_licensing_profile("todos")
    assert profile["total"] == 0, "una oportunidad SOLO inactiva no genera ninguna cuenta"
    svc_module.clear_cache()


# ============================================================================
# NEW NAME — SOLO tag, nunca Es Cliente?
# ============================================================================

def test_new_name_includes_nn_tagged_account():
    svc_module.clear_cache()
    nn = CommercialService(_client()).get_licensing_profile("nn")
    assert "Cuenta NN" in _names(nn["accounts"])
    svc_module.clear_cache()


def test_new_name_excludes_bi_only_account():
    svc_module.clear_cache()
    nn = CommercialService(_client()).get_licensing_profile("nn")
    assert "Cuenta BI" not in _names(nn["accounts"])
    svc_module.clear_cache()


def test_new_name_excludes_neither_tag_account():
    svc_module.clear_cache()
    nn = CommercialService(_client()).get_licensing_profile("nn")
    assert "Cuenta Neither" not in _names(nn["accounts"])
    svc_module.clear_cache()


def test_new_name_includes_both_tag_account():
    svc_module.clear_cache()
    nn = CommercialService(_client()).get_licensing_profile("nn")
    assert "Cuenta Both" in _names(nn["accounts"])
    svc_module.clear_cache()


def test_es_cliente_no_does_not_prevent_new_name_membership():
    # Owner §9 — el hallazgo central del audit: Es Cliente?=No NO implica
    # "no es New Name". La cuenta 105 tiene el tag NN y debe aparecer en NN
    # pese a que su property de cuenta diga "No".
    svc_module.clear_cache()
    nn = CommercialService(_client()).get_licensing_profile("nn")
    assert "Cuenta EsNo ConNN" in _names(nn["accounts"])
    svc_module.clear_cache()


# ============================================================================
# BASE INSTALADA — SOLO tag, nunca Es Cliente?
# ============================================================================

def test_base_instalada_includes_bi_tagged_account():
    svc_module.clear_cache()
    bi = CommercialService(_client()).get_licensing_profile("bi")
    assert "Cuenta BI" in _names(bi["accounts"])
    svc_module.clear_cache()


def test_base_instalada_excludes_nn_only_account():
    svc_module.clear_cache()
    bi = CommercialService(_client()).get_licensing_profile("bi")
    assert "Cuenta NN" not in _names(bi["accounts"])
    svc_module.clear_cache()


def test_base_instalada_excludes_neither_tag_account():
    svc_module.clear_cache()
    bi = CommercialService(_client()).get_licensing_profile("bi")
    assert "Cuenta Neither" not in _names(bi["accounts"])
    svc_module.clear_cache()


def test_base_instalada_includes_both_tag_account():
    svc_module.clear_cache()
    bi = CommercialService(_client()).get_licensing_profile("bi")
    assert "Cuenta Both" in _names(bi["accounts"])
    svc_module.clear_cache()


def test_es_cliente_si_does_not_imply_base_instalada_membership():
    # Owner §9, la mitad simétrica: Es Cliente?=Sí NO implica "es Base
    # Instalada" para una cuenta SIN pipeline (104, dormida) — sólo entra a
    # Todos (componente B), nunca a BI (que es puramente tag-derivado sobre
    # oportunidades reales).
    svc_module.clear_cache()
    bi = CommercialService(_client()).get_licensing_profile("bi")
    assert "Cuenta Dormida Si" not in _names(bi["accounts"])
    # Y la que SÍ tiene el tag BI entra por el tag, no por la property.
    assert "Cuenta EsSi ConBI" in _names(bi["accounts"])
    svc_module.clear_cache()


# ============================================================================
# Grano de cuenta — commercial_partner_id, sin duplicados
# ============================================================================

def test_contact_opportunity_collapses_into_its_company_never_a_standalone_row():
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("todos")
    names = _names(profile["accounts"])
    assert "Contacto" not in names, "un contacto nunca es una fila propia"
    assert "Cuenta NN" in names
    ids = [a["id"] for a in profile["accounts"] if a["name"] == "Cuenta NN"]
    assert ids == [100], "la oportunidad del contacto se resolvió a la empresa 100"
    svc_module.clear_cache()


def test_one_row_per_account_even_with_multiple_opportunities():
    # `partners=[p for p in PARTNERS if p["id"] == 100]` aísla el caso —
    # sin las cuentas dormidas Es Cliente?=Sí del fixture general, así que
    # el único camino a Todos es la MISMA cuenta 100, dos veces.
    svc_module.clear_cache()
    two_opps_same_account = _client(
        leads=[
            {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
             "partner_id": [100, "Cuenta NN"]},
            {"id": 2, "type": "opportunity", "active": True, "stage_id": 3, "tag_ids": [10],
             "partner_id": [100, "Cuenta NN"]},
        ],
        partners=[p for p in PARTNERS if p["id"] == 100],
    )
    profile = CommercialService(two_opps_same_account).get_licensing_profile("todos")
    assert profile["total"] == 1
    assert profile["accounts"][0]["name"] == "Cuenta NN"
    svc_module.clear_cache()


# ============================================================================
# Adapter tipado — technical name + label/type guard
# ============================================================================

def test_property_adapter_resolves_all_seven_typed_fields():
    # `arquitectura_actual` acá viaja con tags de TRES elementos
    # (key/label/color, ver fixture `PARTNERS[110]`) — MISMA forma que Odoo
    # real (regresión: encontrado en vivo, `_tags_value` reventaba con pares
    # de 2 asumidos).
    svc_module.clear_cache()
    profile = CommercialService(_client()).get_licensing_profile("nn")
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
    profile = CommercialService(_client()).get_licensing_profile("nn")
    account = next(a for a in profile["accounts"] if a["name"] == "Cuenta NN")
    assert account["partner_de_licencia"] is None
    assert account["monto_contrato"] is None
    assert account["arquitectura_actual"] == []
    svc_module.clear_cache()


def test_metadata_drift_is_treated_as_absent_never_misinterpreted():
    # La property técnica EXISTE (mismo `name`) pero su `string` no coincide
    # con el esperado — owner §10: "fail observably / emit a controlled
    # degraded state", nunca interpretar metadata incompatible como si
    # fuera la correcta.
    svc_module.clear_cache()
    drift_client = _client(leads=[
        {"id": 1, "type": "opportunity", "active": True, "stage_id": 2, "tag_ids": [10],
         "partner_id": [111, "Cuenta Metadata Drift"]},
    ])
    profile = CommercialService(drift_client).get_licensing_profile("nn")
    account = next(a for a in profile["accounts"] if a["name"] == "Cuenta Metadata Drift")
    assert account["monto_contrato"] is None, "string distinto -> tratado como ausente, nunca 999"
    svc_module.clear_cache()


def test_property_lookup_is_by_technical_name_not_by_label_alone():
    src = _COMMERCIAL_SERVICE_PY_SRC.split("def _find_partner_property(", 1)[1].split("\n\n\n", 1)[0]
    assert 'prop.get("name") != technical_name' in src
    assert 'prop.get("string")' in src  # el label sigue siendo un GUARD, no la clave primaria


# ============================================================================
# Batching — cero N+1
# ============================================================================

def test_licensing_profile_issues_a_bounded_number_of_odoo_calls():
    svc_module.clear_cache()
    client = _client()
    CommercialService(client).get_licensing_profile("todos")
    models_called = [call[0] for call in client.calls]
    partner_calls = [m for m in models_called if m == "res.partner"]
    # UN read de opciones (stages) + UN read de leads + a lo sumo DOS reads
    # de res.partner (batch principal + segunda pasada acotada de empresas
    # huérfanas) — nunca uno por cuenta (13 cuentas en el fixture).
    assert len(partner_calls) <= 2, f"se esperaban <=2 lecturas de res.partner, hubo {len(partner_calls)}"
    assert models_called.count("crm.lead") == 1
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
    client.raise_on("crm.lead", stage="execute")
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


def test_licensing_profile_endpoint_is_independent_from_opportunities_detail():
    router_src = (
        Path(__file__).resolve().parents[2] / "webapp" / "routers" / "commercial.py"
    ).read_text(encoding="utf-8")
    assert router_src.count('@router.get("/comercial/data/licensing-profile"') == 1
    endpoint_body = router_src.split(
        '@router.get("/comercial/data/licensing-profile"', 1)[1].split("\n\n\n", 1)[0]
    assert "get_licensing_profile" in endpoint_body
    assert "get_opportunities_detail" not in endpoint_body


def test_licensing_profile_never_writes_and_reads_only_known_models():
    block = _COMMERCIAL_SERVICE_PY_SRC.split("def _build_licensing_profile(", 1)[1].split(
        "\n    def get_licensing_profile", 1)[0]
    for banned in (".create(", ".write(", ".unlink(", "execute_kw"):
        assert banned not in block, banned
    # `crm.tag` se resuelve vía `self._nn_bi_tags()` (llamada, no literal
    # inline acá) — MISMO criterio que Forecast/Prospectos/Detalle, nunca
    # una segunda resolución de tags.
    for model in ('"res.partner"', '"crm.lead"', '"crm.stage"'):
        assert model in block, model
    assert "self._nn_bi_tags()" in block
