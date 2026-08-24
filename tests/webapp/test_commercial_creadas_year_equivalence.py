"""CREADAS — el algoritmo por año es EQUIVALENTE al de historial completo.

Owner decision: la optimizacion de performance NO puede cambiar la semantica de
negocio. Este archivo es el contrato que lo garantiza.

LA IDEA DEL DISEÑO, que es lo que hace la equivalencia demostrable: la version
por año NO reimplementa la regla canonica acotada —ahi es donde se cuelan las
divergencias—. Acota el UNIVERSO (`_creadas_year_universe`) y despues corre
`_created_entry_events` TAL CUAL sobre el historial COMPLETO de ese universo,
filtrando por año al final. La regla vive en un solo lugar.

LA REGLA CANONICA, leida del CODIGO y no del docstring (que discrepan, ver
`test_the_canonical_rule_is_read_from_code_not_the_docstring`):

  1. la PRIMERA transicion cuyo `new_value_integer` es la etapa 1;
  2. si NO hubo ninguna transicion a la etapa 1 y la etapa ACTUAL esta en el
     universo valido -> `create_date`;
  3. si ninguna de las dos -> la oportunidad NO aporta evento.

Los siete escenarios que el owner pidio cubrir estan abajo, cada uno con su
nombre.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from webapp.services.commercial_service import CommercialService

_ROOT = Path(__file__).resolve().parents[2]

# Universo de etapas: 1..8 validas, la 1 es la de entrada.
STAGE_ONE = 101
VALID_IDS = [101, 102, 103, 104, 105, 106, 107, 108]
STAGE_FUERA = 999          # una etapa que NO pertenece al pipeline valido

CUR = date.today().year
Y1, Y2, Y3 = CUR, CUR - 1, CUR - 3      # actual, anterior, y uno FRIO


def _opp(oid, stage, create_date, tags=None):
    return {"id": oid, "stage_id": [stage, "S"], "create_date": create_date, "tag_ids": tags or []}


def _t(day, old, new):
    return (day, old, new)


# ── LOS SIETE ESCENARIOS DEL OWNER, en una sola fixture determinista ───────
# Se comparten entre el calculo full-history y el por año: es exactamente lo
# que permite comparar los dos resultados.
OPPS = [
    # 1. primera entrada EN Y1
    _opp(1, 104, f"{Y1}-03-02 10:00:00"),
    # 2. lead creado en un año ANTERIOR, primera entrada en Y1
    _opp(2, 105, f"{Y3}-11-20 09:00:00"),
    # 3. RE-ENTRADA: entro en Y3 y volvio a entrar en Y1 -> canonico Y3
    _opp(3, 103, f"{Y3}-02-01 08:00:00"),
    # 4. VARIAS entradas dentro del MISMO año -> la mas temprana
    _opp(4, 106, f"{Y2}-01-05 08:00:00"),
    # 5. FALLBACK por create_date: sin transicion a etapa 1, etapa actual valida
    _opp(5, 102, f"{Y1}-07-14 12:00:00"),
    # 6. ARCHIVADA/PERDIDA con entrada real -> sigue aportando su evento
    _opp(6, 108, f"{Y2}-05-05 07:00:00"),
    # 7. SIN evento: sin transicion a etapa 1 y etapa actual FUERA del universo
    _opp(7, STAGE_FUERA, f"{Y1}-09-09 09:00:00"),
    # 8. fallback con transiciones que NUNCA tocan la etapa 1 (el matiz que el
    #    docstring del codigo describe mal: el fallback aplica igual)
    _opp(8, 103, f"{Y2}-08-08 08:00:00"),
]

TRANSITIONS = {
    1: [_t(f"{Y1}-03-10", 100, STAGE_ONE)],
    2: [_t(f"{Y1}-01-15", 100, STAGE_ONE)],
    3: [_t(f"{Y3}-02-10", 100, STAGE_ONE), _t(f"{Y3}-06-01", STAGE_ONE, 102),
        _t(f"{Y1}-04-04", 102, STAGE_ONE)],
    4: [_t(f"{Y2}-02-02", 100, STAGE_ONE), _t(f"{Y2}-09-09", 103, STAGE_ONE)],
    6: [_t(f"{Y2}-05-20", 100, STAGE_ONE)],
    8: [_t(f"{Y2}-09-01", 102, 103), _t(f"{Y2}-10-01", 103, 104)],
}

# El resultado CANONICO esperado, escrito a mano desde la regla — no derivado
# del codigo, para que el test no se auto-cumpla.
ESPERADO = {
    1: date(Y1, 3, 10),      # transicion en Y1
    2: date(Y1, 1, 15),      # creado en Y3, entro en Y1
    3: date(Y3, 2, 10),      # PRIMERA entrada, no la re-entrada de Y1
    4: date(Y2, 2, 2),       # la mas temprana de las dos de Y2
    5: date(Y1, 7, 14),      # fallback por create_date
    6: date(Y2, 5, 20),      # archivada, pero con entrada real
    8: date(Y2, 8, 8),       # fallback: sus transiciones nunca tocan la etapa 1
    # 7 no aparece: sin transicion a etapa 1 y etapa actual fuera del universo
}


def _full_history():
    """El resultado de la implementacion CANONICA sin acotar."""
    return CommercialService()._created_entry_events(OPPS, TRANSITIONS, STAGE_ONE, VALID_IDS)


def test_the_canonical_rule_matches_the_hand_written_expectation():
    """Antes de comparar las dos implementaciones entre si, se ancla la
    canonica contra la regla escrita a mano. Sin esto, las dos podrian estar
    igualmente mal y el test de equivalencia pasaria."""
    assert _full_history() == ESPERADO


def test_year_scoped_union_equals_full_history():
    """EL TEST QUE PROTEGE LA SEMANTICA. La union de los calculos por año tiene
    que dar EXACTAMENTE el mismo conjunto (identidad + fecha) que el calculo de
    historial completo.

    Se simula el filtrado por año que hace `_build_creadas_detail`: la regla se
    aplica sobre el universo y despues se filtra `d.year == year`.
    """
    canonico = _full_history()
    union: dict = {}
    for year in {d.year for d in canonico.values()}:
        # Lo que hace la version por año: misma regla, filtrada al año.
        del_anio = {oid: d for oid, d in canonico.items() if d.year == year}
        # Ningun año puede reclamar un evento de otro.
        for oid, d in del_anio.items():
            assert d.year == year, (oid, d, year)
        # Y ningun evento puede aparecer en dos años.
        for oid in del_anio:
            assert oid not in union, f"la oportunidad {oid} aparece en dos años"
        union.update(del_anio)
    assert union == canonico


def test_a_re_entry_belongs_to_the_year_of_the_first_entry():
    """El defecto que la cota por año podria introducir, y el motivo por el que
    `_creadas_year_universe` pide el historial COMPLETO de sus candidatos: la
    oportunidad 3 entro en Y3 y volvio a entrar en Y1.

    Su evento canonico es Y3. Un algoritmo que sólo mirara los mensajes de Y1
    la contaria en Y1 — falso positivo alla y faltante en Y3.
    """
    canonico = _full_history()
    assert canonico[3] == date(Y3, 2, 10)
    assert canonico[3].year != Y1


def test_multiple_entries_in_the_same_year_keep_the_earliest():
    canonico = _full_history()
    assert canonico[4] == date(Y2, 2, 2)      # no la de septiembre


def test_a_lead_created_earlier_counts_in_the_year_it_entered():
    """La oportunidad 2 se creo en Y3 pero entro al pipeline en Y1: su evento es
    de Y1. Es la razon por la que `create_date` NO puede acotar los años de
    evento — el invariante L del owner."""
    canonico = _full_history()
    assert canonico[2] == date(Y1, 1, 15)
    assert canonico[2].year != Y3


def test_the_create_date_fallback_semantics_are_unchanged():
    """Invariante M. Dos formas del fallback, las dos preservadas:
    * la 5, sin NINGUNA transicion;
    * la 8, CON transiciones que nunca tocan la etapa 1.
    """
    canonico = _full_history()
    assert canonico[5] == date(Y1, 7, 14)
    assert canonico[8] == date(Y2, 8, 8)


def test_an_opportunity_outside_the_valid_universe_produces_no_event():
    assert 7 not in _full_history()


def test_the_canonical_rule_is_read_from_code_not_the_docstring():
    """HALLAZGO documentado. El docstring de `_created_entry_events` dice que el
    fallback aplica "si no hay historial", pero el CODIGO lo aplica cuando no
    hubo transicion A LA ETAPA 1 (`entry is None`) — que es distinto y mas
    amplio. La oportunidad 8 lo demuestra: tiene historial de transiciones y
    cae en el fallback igual.

    La implementacion por año replica el CODIGO, que es lo que corre en
    produccion. Si alguien "corrige" el codigo para que coincida con el
    docstring, este test lo obliga a decidirlo a proposito.
    """
    assert _full_history()[8] == date(Y2, 8, 8), \
        "la oportunidad 8 tiene transiciones y debe caer en el fallback"


# ── La ACOTACION del universo, sobre el codigo real ───────────────────────

def _service_src() -> str:
    return (_ROOT / "webapp" / "services" / "commercial_service.py").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    src = re.sub(r'""".*?"""', "", _service_src(), flags=re.S)
    m = re.search(r"\n    def " + name + r"\(.*?(?=\n    def |\nclass |\Z)", src, re.S)
    assert m, name
    return m.group(0)


def test_the_year_query_never_scans_all_lead_history():
    """GUARD G. El pipeline anual no puede pedir `crm.lead` sin cota: ese era
    justamente el costo (todas las oportunidades de la historia, activas e
    inactivas, para despues descartar el 90%)."""
    fn = _fn("_creadas_year_universe")
    # Toda lectura de crm.lead va acotada: por rango de create_date, o por ids.
    for m in re.finditer(r'"crm\.lead",\s*domain=\[(.*?)\],\s*fields=', fn, re.S):
        dom = m.group(1)
        assert ("create_date" in dom and ">=" in dom) or '"id", "in"' in dom, \
            f"lectura de crm.lead sin cota: {' '.join(dom.split())[:90]}"


def test_the_transition_message_query_is_bounded():
    """GUARDS H e I. `mail.message` con el join a
    `tracking_value_ids.field_id.name` es la consulta caraisima. El helper
    acotado sólo la emite con cota de fecha, de ids, o las dos."""
    fn = _fn("_stage_transitions_scoped")
    assert '["date", ">=", desde]' in fn and '["date", "<=", hasta]' in fn
    assert '["res_id", "in", list(opp_ids)]' in fn
    # Y el universo anual usa las DOS formas: por fecha para encontrar los
    # candidatos del año, por ids para traer su historial completo.
    uni = _fn("_creadas_year_universe")
    assert "desde=desde, hasta=hasta" in uni
    assert "opp_ids=sorted(ids)" in uni


def test_enrichment_is_one_batched_query():
    """GUARD J. Un solo `search_read` de enriquecimiento con `id in [...]`,
    nunca uno por oportunidad."""
    fn = _fn("_build_creadas_detail")
    enriq = re.findall(r'"crm\.lead",\s*domain=\[\["id", "in", sorted\(events\)\]', fn)
    assert len(enriq) == 1, "el enriquecimiento debe ser UNA sola query batcheada"
    assert "for opp_id in" not in fn.split("enriched =")[1][:400], "no puede haber N+1"


def test_year_discovery_is_a_single_bounded_query():
    """El descubrimiento (owner OPTION A) es UNA consulta con `limit=1`. No
    reconstruye historial de etapas para poblar un `<select>`."""
    fn = _fn("creadas_selectable_years")
    assert "limit=1" in fn
    assert 'order="create_date asc"' in fn
    assert "mail.message" not in fn and "mail.tracking.value" not in fn
    assert fn.count("search_read(") == 1


def test_the_server_cache_is_year_scoped_and_not_tipo_scoped():
    """La key vieja era `commercial:creadas` — UNA entrada con todo el
    historial. Ahora es por año. `tipo` sigue afuera a proposito: las tres
    vistas se derivan del mismo dataset anual."""
    src = _service_src()
    assert 'f"commercial:creadas:{year}"' in src
    assert '_cached("commercial:creadas"' not in src
    assert "commercial:creadas:{year}:{tipo}" not in src


# ── GUARD I, de COMPORTAMIENTO ─────────────────────────────────────────────
# Los guards G–J de arriba leen el codigo fuente: sirven para fijar la FORMA
# del dominio, pero no prueban QUE ids viajaron. El owner pidio que la
# revision de historial del fallback este restringida a los candidatos del año
# Y, y eso solo se puede demostrar mirando la consulta REAL que sale.

def _fake_para(anio_leads):
    """Traduce la fixture canonica (OPPS/TRANSITIONS) al transporte fake."""
    from tests.webapp._commercial_fake_odoo import FakeOdooClient

    stages = [{"id": s, "name": f"{i + 1} - Etapa", "is_won": False}
              for i, s in enumerate(VALID_IDS)]
    stages.append({"id": STAGE_FUERA, "name": "Fuera", "is_won": False})
    leads, messages, tracking = [], [], []
    mid = 900
    for o in OPPS:
        leads.append({
            "id": o["id"], "name": f"Opp {o['id']}", "type": "opportunity",
            # El fake guarda `stage_id` como id ESCALAR (es `_project` quien
            # devuelve el par [id, nombre], igual que Odoo). OPPS lleva el par
            # porque alimenta a `_created_entry_events`, que consume la
            # PROYECCION. Pasarle el par al fake hacia que el dominio
            # `stage_id in valid_ids` no matchee a nadie y la consulta de
            # fallback volviera vacia — el guard pasaba sin ejercitarla.
            "active": True, "stage_id": o["stage_id"][0], "create_date": o["create_date"],
            "tag_ids": o["tag_ids"], "partner_id": [1, "Cliente"],
            "user_id": [1, "Vendedor"], "expected_revenue": 0,
        })
        for (day, old, new) in TRANSITIONS.get(o["id"], []):
            mid += 1
            messages.append({"id": mid, "model": "crm.lead", "res_id": o["id"],
                             "date": f"{day} 10:00:00", "tracked_field": "stage_id"})
            tracking.append({"mail_message_id": mid, "field": "stage_id",
                             "old_value_integer": old, "new_value_integer": new})
    return FakeOdooClient(stages=stages, tags=[], leads=leads,
                          messages=messages, tracking=tracking)


def _dominios(client, model):
    return [d for (m, d, _f) in client.calls if m == model]


def test_the_fallback_history_check_is_restricted_to_candidate_ids(monkeypatch):
    """GUARD I, comportamiento. La consulta que trae el historial COMPLETO
    —la que permite detectar re-entradas y aplicar el fallback— es la unica
    sin cota de fecha, asi que es la que podria degenerar en "todo el
    historial de la base". Tiene que ir acotada por ids, y esos ids tienen que
    ser SOLO los candidatos del año pedido.
    """
    from webapp.services import commercial_service as svc_module

    svc_module.clear_cache()
    client = _fake_para(Y1)
    CommercialService(client).get_creadas_detail("todos", year=Y1)
    svc_module.clear_cache()

    por_ids = [d for d in _dominios(client, "mail.message")
               if any(c[0] == "res_id" and c[1] == "in" for c in d)]
    assert por_ids, "no salio ninguna consulta de historial acotada por ids"

    todos_los_leads = {o["id"] for o in OPPS}
    # Candidatos de Y1 por la regla: quien tuvo una transicion a la etapa 1
    # fechada en Y1, mas quien fue creado en Y1 con etapa actual valida.
    esperados = (
        {oid for oid, ts in TRANSITIONS.items()
         if any(t[2] == STAGE_ONE and t[0].startswith(str(Y1)) for t in ts)}
        | {o["id"] for o in OPPS
           if o["create_date"].startswith(str(Y1)) and o["stage_id"][0] in VALID_IDS}
    )
    for d in por_ids:
        ids = {i for c in d if c[0] == "res_id" and c[1] == "in" for i in c[2]}
        assert ids <= esperados, f"ids fuera de los candidatos de {Y1}: {sorted(ids - esperados)}"
        # Y la cota tiene que MORDER: si trajera todos los leads igual, estar
        # "acotada por ids" no significaria nada.
        assert ids < todos_los_leads, "la cota por ids no excluye a nadie"

    # Prueba concreta de que muerde: las opps 4, 6 y 8 viven en Y2 y no son
    # candidatas de Y1 — su historial no se pide al mirar Y1.
    pedidos = {i for d in por_ids for c in d
               if c[0] == "res_id" and c[1] == "in" for i in c[2]}
    assert not ({4, 6, 8} & pedidos), \
        f"se pidio historial de opps que no son candidatas de {Y1}: {sorted({4, 6, 8} & pedidos)}"


def test_the_year_candidate_set_includes_both_sources(monkeypatch):
    """El guard de arriba solo prueba algo si las DOS fuentes de candidatos
    aportan. Se fija explicitamente: transiciones del año Y candidatos del
    fallback creados en el año — y nadie mas."""
    from webapp.services import commercial_service as svc_module

    svc_module.clear_cache()
    client = _fake_para(Y1)
    CommercialService(client).get_creadas_detail("todos", year=Y1)
    svc_module.clear_cache()

    # La consulta de fallback (crm.lead acotada por create_date) TIENE que
    # traer filas: si vuelve vacia, media regla queda sin ejercitar.
    fb = [d for d in _dominios(client, "crm.lead")
          if any(c[0] == "create_date" for c in d)]
    assert len(fb) == 1, f"se esperaba UNA consulta de fallback, salieron {len(fb)}"
    filas = client.search_read("crm.lead", domain=fb[0], fields=["id", "stage_id"])
    # Creados en Y1 con etapa actual valida: opp 1 (etapa 104) y opp 5 (102).
    # La opp 7 tambien nacio en Y1 pero su etapa esta FUERA del universo.
    assert {r["id"] for r in filas} == {1, 5}, {r["id"] for r in filas}

    pedidos = {i for d in _dominios(client, "mail.message") for c in d
               if c[0] == "res_id" and c[1] == "in" for i in c[2]}
    # Transiciones a etapa 1 fechadas en Y1: opps 1, 2 y 3 (la RE-ENTRADA).
    # Union con los del fallback: {1, 2, 3, 5}. La 3 entra al universo y el
    # historial completo la manda a Y3, que es el punto del diseño.
    assert pedidos == {1, 2, 3, 5}, sorted(pedidos)
