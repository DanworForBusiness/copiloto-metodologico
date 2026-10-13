"""4B.0 — Resolvedor de identidad de un modelo (módulo puro). El mundo sintético reproduce la ESTRUCTURA del Catálogo real: títulos anidados en el mismo cliente, dominios compartidos,
títulos que difieren del dominio, siglas cortas que son identidad y el mismo título en dos clientes."""
from __future__ import annotations

import pytest

from webapp.services.assistant_entry_resolver import AMBIGUOUS, MULTIPLE, NONE, UNIQUE, Identity, resolve

BIO, KEN, LOM, PAM = "Biogenesis", "Kensing", "Loma", "Pampa"
CLIENTS = (BIO, KEN, LOM, PAM)
LOBS = ("Ventas", "Stock / Inventario", "Finanzas")


def _i(eid, client, title, tech="", dom=""):
    return Identity(eid, client, title, tech, dom)


WORLD = [
    _i("bio-sell", BIO, "Sell Out", "AM_SELL_OUT", "Sell Out"),
    _i("bio-hoj", BIO, "Sell Out Hojobar", "AM_SELL_OUT_HOJOBAR", "Sell Out Hojobar"),
    _i("bio-hojnc", BIO, "Sell out Hojobar NC", "AM_SELL_OUT_HOJOBAR_NC", "Sell out Hojobar NC"),
    _i("bio-mp", BIO, "Reserva MP", "AM_RESERVA_MP", "AM_RESERVA_MP"),
    _i("bio-mat", BIO, "Reserva de Material", "AM_RESERVA_MATERIAL", "Reserva de Material"),
    _i("bio-ocu", BIO, "Ocupación de Almacenes", "AM_OCUPACION_ALMACENES", "Stock / Inventario"),
    _i("bio-ocu3", BIO, "Ocupación de almacenes de terceros", "AM_OCUPACION_ALMACENES_TERCERO", "Stock / Inventario"),
    _i("bio-lista", BIO, "Lista Precios", "AM_Lista_Precios", "AM_Lista_Precios"),
    _i("bio-vtas", BIO, "Modelo Analítico Base Ventas", "AM_BASE_VENTAS", "Ventas"),
    _i("bio-desc", BIO, "Descuentos Comerciales", "AM_DescuentosComerciales", "Ventas"),
    _i("ken-dd", KEN, "Delivery Document", "4AM_SND_DELIVERYDOCUMENT", "Delivery Document"),
    _i("ken-ddi", KEN, "Delivery Document Item", "4AM_SND_DELIVERYDOCUMENTITEM", "Delivery Document Item"),
    _i("ken-gl", KEN, "Raw Data of G/L Account Line Item", "4AM_FNC_GLACCOUNTLINEITRAWDATA", "Raw Data of G/L Account Line Item"),
    _i("lom-stock", LOM, "Stock", "4MA_STOCK", "Stock / Inventario"),
    _i("pam-stock", PAM, "Stock", "4AM_STOCK_PAM", "Stock / Inventario"),                       # el MISMO título en dos clientes
    _i("pam-pagos", PAM, "Modelo analítico trazabilidad de Pagos - Factura - Compra", "4AM_PUR_PagoFacturaCompra", "Compras"),
    _i("bio-na", BIO, "", "", "Stock / Inventario"),                                             # sin identidad terminal: ninguna clave de título
]


def r(q):
    return resolve(q, WORLD, CLIENTS, LOBS)


def ids(res):
    return [m.entry_id for m in res.matches]


# ── identidad por título ────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("question,expected", [
    ("¿Qué casos a revisar tiene Delivery Document?", "ken-dd"),
    ("la confianza de Delivery Document Item", "ken-ddi"),                       # el nombre completo más largo gana
    ("qué sabés de lista precios", "bio-lista"),
    ("la cobertura de Reserva MP", "bio-mp"),                                    # la sigla «MP» es identidad (no se descarta por corta)
    ("la cobertura de reserva de material", "bio-mat"),
    ("¿y Ocupación de Almacenes?", "bio-ocu"),
    ("Ocupación de almacenes de terceros", "bio-ocu3"),
    ("sell out hojobar nc", "bio-hojnc"),
    ("sell out hojobar", "bio-hoj"),
    ("sell out", "bio-sell"),
    ("modelo analítico trazabilidad de pagos - factura - compra", "pam-pagos"),
    ("Raw Data of G/L Account Line Item", "ken-gl"),
])
def test_title_resolves_unique(question, expected):
    res = r(question)
    assert (res.status, ids(res)) == (UNIQUE, [expected]), res


def test_nested_titles_exact_name_wins_but_prefix_alone_stays_the_shorter_model():
    assert ids(r("Delivery Document")) == ["ken-dd"] and ids(r("Delivery Document Item")) == ["ken-ddi"]


def test_accents_and_case_do_not_matter():
    assert ids(r("OCUPACION DE ALMACENES DE TERCEROS")) == ["bio-ocu3"]


# ── identificador técnico y dominio propio ──────────────────────────────────────────────────────────────────────────────
def test_technical_id_is_a_secondary_key():
    assert ids(r("AM_RESERVA_MP")) == ["bio-mp"] and ids(r("4AM_SND_DELIVERYDOCUMENTITEM")) == ["ken-ddi"]


def test_own_domain_is_a_secondary_key_when_unique():
    world = [_i("a", BIO, "Nombre visible", "AM_X", "Dominio Propio"), _i("b", BIO, "Otro", "AM_Y", "Dominio Propio Largo")]
    assert ids(resolve("el dominio propio", world, CLIENTS, ())) == ["a"]         # el más largo no aplica: «dominio propio largo» no está en la pregunta


def test_shared_domain_alone_never_identifies():
    for q in ("el de stock inventario", "stock / inventario", "ventas"):
        assert r(q).status == NONE, q


# ── el cliente solo desambigua ──────────────────────────────────────────────────────────────────────────────────────────────
def test_same_title_in_two_clients_is_ambiguous_without_client_and_unique_with_it():
    res = r("la confianza de Stock")
    assert (res.status, sorted(ids(res))) == (AMBIGUOUS, ["lom-stock", "pam-stock"])
    assert ids(r("la confianza de Stock de Loma")) == ["lom-stock"] and r("la confianza de Stock de Loma").status == UNIQUE
    assert ids(r("la confianza de Stock de Pampa")) == ["pam-stock"]


def test_client_alone_never_selects_a_model():
    for q in ("qué sabés de Pampa", "el modelo de Kensing", "Biogenesis", "qué sabés de Loma"):                # Loma tiene UN solo modelo: aun así el cliente solo no lo selecciona
        assert r(q).status == NONE and r(q).matches == (), q


def test_named_client_without_that_title_is_none_not_another_clients_model():
    res = r("el stock de Kensing")
    assert res.status == NONE and res.clients == (KEN,)


def test_two_clients_named_are_reported_and_both_are_in_scope():
    res = r("Stock de Loma y Pampa")
    assert res.clients == (LOM, PAM) and sorted(ids(res)) == ["lom-stock", "pam-stock"] and res.status == AMBIGUOUS


# ── alcance (línea de negocio / cliente) ≠ nombre de modelo ─────────────────────────────────────────────────────────────────
def test_line_of_business_name_is_scope_not_a_model_name():
    world = WORLD + [_i("fin", BIO, "Ventas", "AM_VENTAS", "Ventas")]               # un título que coincide con una línea de negocio
    assert resolve("las ventas de Biogenesis", world, CLIENTS, LOBS).status == NONE
    assert ids(resolve("la confianza de «Ventas»", world, CLIENTS, LOBS)) == ["fin"]


def test_stock_title_inside_the_stock_inventory_line_is_not_a_model_name():
    assert r("los modelos de Stock / Inventario").status == NONE


# ── comillas ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_quoted_name_must_equal_a_key():
    assert ids(r("¿qué sabés de «Sell Out»?")) == ["bio-sell"]                       # exacto: no arrastra los «Sell Out Hojobar»
    assert r("¿qué sabés de «Sell»?").status == NONE and r("¿qué sabés de «Sell»?").quoted


def test_quoted_shared_domain_is_ambiguous_with_all_its_models():
    res = r("la confianza de «Stock / Inventario»")
    assert res.status == AMBIGUOUS and res.quoted and sorted(ids(res)) == ["bio-na", "bio-ocu", "bio-ocu3", "lom-stock", "pam-stock"]


def test_quoted_unknown_name_is_none_and_marked_quoted():
    res = r("la confianza de «Inexistente»")
    assert res.status == NONE and res.quoted


# ── sin fuzzy, varios modelos, sin identidad ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("question", ["delivery doc", "lista de precios", "reservas mp", "sellout", "oculpacion de almacenes", "los modelos con adopción buena", ""])
def test_no_fuzzy_or_partial_matching(question):
    assert r(question).status == NONE, question


def test_two_distinct_models_named_is_multiple():
    res = r("compará Delivery Document con Reserva MP")
    assert res.status == MULTIPLE and sorted(ids(res)) == ["bio-mp", "ken-dd"]


def test_entry_without_terminal_identity_has_no_title_key():
    assert "bio-na" not in ids(r("flujo sin modelo terminal"))


def test_resolution_never_returns_an_identity_that_is_not_in_the_universe():
    for q in ("Sell Out", "Stock", "Delivery Document", "Reserva MP", "x"):
        assert set(ids(r(q))) <= {w.entry_id for w in WORLD}
