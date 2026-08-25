"""ODOO — CONTRATO DE TRANSPORTE REUSABLE (owner performance gate).

Antes cada RPC hacia un `httpx.post(...)` suelto: conexion y handshake TLS
nuevos por llamada. Medido contra el Odoo real, el 74% de cada round-trip era
abrir la conexion, y un endpoint de Creadas son 9 RPCs.

Estos tests fijan el CONTRATO, no milisegundos: un pool por proceso, una
autenticacion, y un cierre limpio. Sin red — el transporte se sustituye por un
doble que cuenta lo que importa.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from webapp.services import odoo_client as oc

_FUENTE = (Path(__file__).resolve().parents[2] / "webapp" / "services" / "odoo_client.py").read_text(encoding="utf-8")


def _sin_comentarios(src: str) -> str:
    """Los comentarios de este modulo NOMBRAN el patron viejo a proposito
    (explican por que se fue). Contarlos como codigo daria un falso positivo."""
    return re.sub(r"#[^\n]*", "", src)


# ── Doble de transporte ────────────────────────────────────────────────────
class _Respuesta:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _TransporteFalso:
    """Cuenta instancias, llamadas y cierres. Reemplaza a `httpx.Client`."""

    creados = 0
    lock = threading.Lock()

    def __init__(self, *_a, **kwargs):
        type(self).creados += 1
        self.kwargs = kwargs
        self.is_closed = False
        self.cierres = 0
        self.autenticaciones = 0
        self.ejecuciones = 0
        self.demora_auth = 0.0

    def post(self, _url, json=None, **_kw):
        params = (json or {}).get("params", {})
        if params.get("service") == "common" and params.get("method") == "authenticate":
            with type(self).lock:
                self.autenticaciones += 1
            if self.demora_auth:
                time.sleep(self.demora_auth)      # ensancha la ventana de carrera
            return _Respuesta({"result": 7})
        with type(self).lock:
            self.ejecuciones += 1
        return _Respuesta({"result": []})

    def close(self):
        self.cierres += 1
        self.is_closed = True


@pytest.fixture
def transporte(monkeypatch):
    """Deja el modulo en estado limpio, instala el doble, y limpia despues."""
    oc.close_transport()
    _TransporteFalso.creados = 0
    monkeypatch.setattr(oc, "httpx", type("_M", (), {
        "Client": _TransporteFalso,
        "Timeout": oc.httpx.Timeout,
        "Limits": oc.httpx.Limits,
        "HTTPError": oc.httpx.HTTPError,
    }))
    monkeypatch.setattr(oc.config, "ODOO_URL", "https://odoo.example")
    monkeypatch.setattr(oc.config, "ODOO_DB", "db-de-prueba")
    monkeypatch.setattr(oc.config, "ODOO_USERNAME", "svc@example")
    monkeypatch.setattr(oc.config, "ODOO_API_KEY", "clave-secreta-de-prueba")
    yield
    oc.close_transport()


# ── A. el RPC pasa por el cliente reusable, no por httpx.post ──────────────

def test_a_rpc_no_usa_httpx_post_suelto(transporte, monkeypatch):
    """El seam de comportamiento: si algun RPC siguiera usando el post suelto,
    esta bomba explota. No se inspecciona nada privado."""
    def bomba(*_a, **_k):
        raise AssertionError("un RPC uso httpx.post suelto en vez del cliente del proceso")

    monkeypatch.setattr(oc.httpx, "post", bomba, raising=False)
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])


def test_a_el_modulo_ya_no_construye_requests_sueltas(transporte):
    """Y el codigo tampoco lo menciona (los comentarios si, a proposito)."""
    codigo = _sin_comentarios(_FUENTE)
    assert "httpx.post(" not in codigo
    assert "_http().post(" in codigo


# ── B/C. un pool por PROCESO, no por cliente ni por request ────────────────

def test_b_varios_rpc_reusan_el_mismo_transporte(transporte):
    cli = oc.OdooClient()
    for _ in range(5):
        cli.search_read("crm.stage", [], fields=["id"])
    assert _TransporteFalso.creados == 1


def test_c_varios_wrappers_no_crean_cada_uno_su_pool(transporte):
    """Este es EL invariante del increment: `CommercialService()` construye un
    `OdooClient` por request. Si el pool colgara del wrapper, cada request
    volveria a abrir conexiones y el defecto seguiria intacto."""
    for _ in range(4):
        oc.OdooClient().search_read("crm.lead", [], fields=["id"])
    assert _TransporteFalso.creados == 1
    assert oc._http() is oc._http()


def test_c_el_pool_tiene_limites_y_timeouts_finitos(transporte):
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    kw = oc._http().kwargs
    assert kw["limits"].max_keepalive_connections > 0, "sin keep-alive no hay reuso"
    t = kw["timeout"]
    assert t.connect and t.read, "los timeouts tienen que ser finitos y explicitos"
    assert "verify" not in kw, "no se toca la verificacion TLS para ganar velocidad"


# ── D/E. una sola autenticacion, sin estampida ─────────────────────────────

def test_d_el_primer_uso_concurrente_autentica_exactamente_una_vez(transporte):
    """Ocho hilos entrando con el uid sin resolver. Sin la doble verificacion
    bajo lock, cada uno dispararia su propio authenticate."""
    oc._http()
    oc._http().demora_auth = 0.05          # ensancha la ventana de carrera
    listos, errores = [], []

    def tocar():
        try:
            listos.append(oc.OdooClient().uid)
        except Exception as exc:            # pragma: no cover
            errores.append(exc)

    hilos = [threading.Thread(target=tocar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert errores == []
    assert listos == [7] * 8, "todos tienen que ver el MISMO uid"
    assert oc._http().autenticaciones == 1


def test_e_las_llamadas_siguientes_reusan_el_uid(transporte):
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    assert oc._http().autenticaciones == 1
    for _ in range(3):
        oc.OdooClient().search_read("crm.tag", [], fields=["id"])   # wrappers NUEVOS
    assert oc._http().autenticaciones == 1, "un wrapper nuevo no puede re-autenticar"
    assert oc._http().ejecuciones == 4


def test_e_el_uid_no_se_reusa_entre_configuraciones_distintas(transporte, monkeypatch):
    """La cache va por (url, db, usuario): cambiar de instancia no puede
    heredar el uid de la anterior."""
    oc.OdooClient().uid
    monkeypatch.setattr(oc.config, "ODOO_DB", "otra-db")
    oc.OdooClient().uid
    assert oc._http().autenticaciones == 2
    assert len(oc._uid_cache) == 2


# ── F/G. vida del pool: sobrevive requests, muere una sola vez ─────────────

def test_f_el_transporte_sobrevive_a_una_request_que_termina(transporte):
    """Terminar una request NO puede cerrar el pool: no le pertenece."""
    primero = oc._http()
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    del_wrapper = oc.OdooClient()
    del_wrapper.search_read("crm.tag", [], fields=["id"])
    del del_wrapper
    assert oc._http() is primero
    assert primero.is_closed is False


def test_g_el_shutdown_cierra_exactamente_una_vez(transporte):
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    cliente = oc._http()
    oc.close_transport()
    oc.close_transport()                    # idempotente
    assert cliente.cierres == 1
    assert cliente.is_closed is True


def test_g_despues_del_shutdown_el_pool_se_reconstruye(transporte):
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    oc.close_transport()
    oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    assert _TransporteFalso.creados == 2, "tras cerrar, el siguiente uso abre uno nuevo"


def test_g_el_lifespan_de_la_app_es_quien_cierra():
    """El cierre cuelga del shutdown del PROCESO. Si esto se moviera a una
    dependencia por request, el pool moriria en cada request."""
    main = (Path(__file__).resolve().parents[2] / "webapp" / "main.py").read_text(encoding="utf-8")
    assert "odoo_client.close_transport()" in main
    lifespan = main[main.index("async def lifespan("):main.index("app = FastAPI(")]
    assert "close_transport()" in lifespan


# ── H. ninguna credencial en errores ───────────────────────────────────────

def test_h_los_errores_del_transporte_no_filtran_credenciales(transporte, monkeypatch):
    secretos = ["clave-secreta-de-prueba", "db-de-prueba", "svc@example"]

    def explota(*_a, **_k):
        raise oc.httpx.HTTPError("boom")

    monkeypatch.setattr(oc._http(), "post", explota)
    with pytest.raises(oc.OdooTransportError) as info:
        oc.OdooClient().search_read("crm.stage", [], fields=["id"])
    texto = f"{info.value} {info.value.stage} {repr(info.value)}"
    for secreto in secretos:
        assert secreto not in texto


def test_h_el_transporte_no_registra_nada(transporte):
    """Nada de logging nuevo en el camino del RPC: la unica forma segura de
    garantizar que no se escriba un payload con credenciales es no escribir."""
    codigo = _sin_comentarios(_FUENTE)
    for ruido in ("logging.", "print(", "logger"):
        assert ruido not in codigo, f"el cliente Odoo no deberia {ruido}"
