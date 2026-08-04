"""Red de seguridad del ruteo conversacional (orquestación).

Batería de preguntas reales (obvias, capciosas y cross-module) con la ruta
esperada. Blinda que:
  - los casos que funcionan sigan funcionando (secciones, how-to, cross-module),
  - los casos rectificados no vuelvan a romperse (naming, conceptuales, gate de
    assets genéricos/troubleshooting, coberturas cross-module).

`expect`: substring que DEBE aparecer en el mensaje o en alguna acción del route
          (o "LLM" si se espera fallback al LLM).
`bad`:    substrings que NO deben ENCABEZAR el mensaje (recurso tangencial como
          titular en vez de la sección precisa).
"""
import pytest

from webapp.services.content_service import load_module
from webapp.services.assistant_routing import AssistantRoutingService

PACK = "datasphere_core"

# (módulo activo, pregunta, expect, [bad_headline])
CASES = [
    # naming — antes devolvía SAP Help genérico / templates tangenciales
    ("04_Modelado_y_Desarrollo", "como nombro un objeto en datasphere?", "convencion-de-nombres", ["SAP Help oficial"]),
    ("04_Modelado_y_Desarrollo", "convencion de nombres", "convencion-de-nombres", ["Template CDS"]),
    ("04_Modelado_y_Desarrollo", "que prefijo uso para una vista?", "convencion-de-nombres", ["Template CDS"]),
    ("04_Modelado_y_Desarrollo", "como nombrar una tabla remota?", "convencion-de-nombres", ["Particionar"]),
    ("04_Modelado_y_Desarrollo", "nomenclatura de artefactos", "convencion-de-nombres", []),
    ("05_Reporting_y_Consumo", "como nombro un objeto?", "convencion-de-nombres", []),
    # how-to con intención — deben seguir liderando con el recurso
    ("04_Modelado_y_Desarrollo", "como creo una vista CDS?", "cds_extractor_template", []),
    ("03_Integracion_y_Estrategia_de_Datos", "template CDS para extraccion", "extraccion-via-api-corporativa", []),
    ("01_Arquitectura_y_Conectividad", "como configuro el DP agent?", "Instalar y Configurar DP Agent", []),
    ("05_Reporting_y_Consumo", "como conecto SAC a datasphere?", "Conectar SAC", []),
    ("05_Reporting_y_Consumo", "como conecto power bi?", "Power BI", []),
    # conceptuales / comparativas — la sección lidera, el asset queda relacionado
    ("03_Integracion_y_Estrategia_de_Datos", "que es un data product?", "contrato-de-datos-vs-data-product", ["Nota SAP"]),
    ("01_Arquitectura_y_Conectividad", "que es un data product?", "datos-como-producto", ["Nota SAP"]),
    ("02_Seguridad_y_Gobernanza", "que es DAC?", "seguridad-de-datos", ["Template"]),
    ("05_Reporting_y_Consumo", "power bi vs SAC", "estrategia-de-reporting", ["API metadata", "acelerar relevamiento"]),
    ("03_Integracion_y_Estrategia_de_Datos", "diferencia entre federacion y persistencia", "federacion-vs-persistencia", []),
    # secciones y cross-module
    ("04_Modelado_y_Desarrollo", "que capas hay en el modelado?", "evolucion-de-arquitectura-por-capas", []),
    ("01_Arquitectura_y_Conectividad", "como transporto objetos entre espacios?", "espacios-transportes", []),
    ("01_Arquitectura_y_Conectividad", "cuantos espacios necesito?", "espacios-transportes", []),
    ("01_Arquitectura_y_Conectividad", "1-tier o 2-tier?", "espacios-transportes", []),
    ("02_Seguridad_y_Gobernanza", "que paradigmas arquitectonicos hay?", "paradigmas-como-diseno-arquitectonico", []),
    # coberturas antes ausentes (caían a LLM / recurso tangencial)
    ("01_Arquitectura_y_Conectividad", "que es HDLF?", "coexistencia-de-paradigmas", []),
    ("03_Integracion_y_Estrategia_de_Datos", "como creo un espacio?", "espacios-transportes", []),
    ("04_Modelado_y_Desarrollo", "como conecto una fuente no SAP?", "01_Arquitectura", []),
    ("05_Reporting_y_Consumo", "como expongo datos para consumo?", "05_Reporting", ["DATA_NOT_READY", "Troubleshooting"]),
    # corrida adversarial — sinónimos/fraseos de naming y decisión
    ("04_Modelado_y_Desarrollo", "como llamo a una tabla local", "convencion-de-nombres", []),
    ("04_Modelado_y_Desarrollo", "sufijo para power bi", "convencion-de-nombres", ["Conectar Power BI"]),
    # naming de artefacto específico: la intención de naming manda sobre el objeto
    ("04_Modelado_y_Desarrollo", "prefijo de un data flow", "convencion-de-nombres", []),
    ("04_Modelado_y_Desarrollo", "como nombro un replication flow?", "convencion-de-nombres", []),
    ("04_Modelado_y_Desarrollo", "como nombro una task chain?", "convencion-de-nombres", []),
    ("03_Integracion_y_Estrategia_de_Datos", "federo o persisto?", "federacion-vs-persistencia", []),
    # "modelo analítico" NO debe enganchar el Template de Seguridad (m02) por
    # compartir la palabra "modelo"; naming → Convención de Nombres.
    ("04_Modelado_y_Desarrollo", "como llamo al modelo analitico?", "convencion-de-nombres", ["Seguridad"]),
    ("05_Reporting_y_Consumo", "como llamo al modelo analitico?", "convencion-de-nombres", ["Seguridad"]),
    # out-of-scope debe seguir cayendo a LLM (no forzar curaduría)
    ("04_Modelado_y_Desarrollo", "receta de pizza", "LLM", []),
    ("01_Arquitectura_y_Conectividad", "cual es el precio de datasphere", "LLM", []),
]

_docs = {}
def _doc(stem):
    if stem not in _docs:
        _docs[stem] = load_module(stem, PACK)
    return _docs[stem]


def test_task_chains_naming_has_no_cds_noise():
    """Una consulta de naming sobre task chains no debe colgar el Template CDS
    (tangencial, matchea solo por la palabra 'convención') como recurso relacionado."""
    route = AssistantRoutingService().route(
        _doc("04_Modelado_y_Desarrollo"), "cual es la convencion para task chains?", PACK
    )
    assert route is not None
    assert "convencion-de-nombres" in " ".join(a.url for a in (route.actions or ()))
    assert "Template CDS" not in route.message


@pytest.mark.parametrize("stem,question,expect,bad", CASES)
def test_routing_battery(stem, question, expect, bad):
    route = AssistantRoutingService().route(_doc(stem), question, PACK)
    urls = " ".join(a.url for a in (route.actions or ())) if route else ""
    msg = route.message if route else ""
    haystack = msg + " " + urls

    if expect == "LLM":
        assert route is None, f"esperaba LLM fallback, obtuve: {msg}"
        return

    assert route is not None, f"esperaba ruteo a '{expect}', obtuve LLM fallback"
    assert expect in haystack, f"no llega a '{expect}'. msg={msg} urls={urls}"

    # el asset tangencial no debe ENCABEZAR (aparecer como relacionado está OK)
    if bad and msg.startswith("Encontré un recurso curado"):
        for b in bad:
            assert b.lower() not in msg.lower(), f"titular flojo '{b}' en: {msg}"
