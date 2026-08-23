"""
safe_redirect.py — AUTORIDAD ÚNICA sobre `next`: qué destino de redirect
post-login se acepta.

Clasificación: PLATFORM / REDIRECT SAFETY. NO importa nada de ``webapp`` (para
que cualquier consumidor pueda usarlo sin arrastrar dependencias ni riesgo de
ciclo). NO contiene rutas, servicios ni access policy. NO decide a dónde va el
usuario: sólo dice si un destino propuesto es interno.

Existía la MISMA expresión copiada en tres lugares
(``routers/landing.py``, y dos veces en ``routers/platform.py``):

    next if next.startswith("/") and not next.startswith("//") else ""

Tres copias de una regla de seguridad son tres oportunidades de divergir, y de
hecho la regla estaba INCOMPLETA en las tres. Medido en el browser, con
``a.href`` sobre un documento del Hub, estos destinos la pasaban y aun así
resolvían a un host ajeno:

    "/\\evil.example"        ->  http://evil.example/
    "/\\\\evil.example"      ->  http://evil.example/
    "/<TAB>/evil.example"    ->  http://evil.example/
    "/<LF>/evil.example"     ->  http://evil.example/

Las dos causas son de PARSEO de URL, no de nuestro código:

  1. En URLs de esquema "special" (http/https) el browser trata ``\`` como
     ``/``, así que ``/\evil.example`` es ``//evil.example``: un host.
  2. El browser BORRA tab/LF/CR que aparezcan DENTRO de la URL antes de
     interpretarla, y recorta controles/espacios del principio. Es decir:
     ``/<TAB>/evil.example`` también termina siendo ``//evil.example``.

De ahí la forma de este helper: no alcanza con mirar la cadena literal, hay que
mirar la cadena que el BROWSER va a interpretar. Y lo que se devuelve es
exactamente eso — se valida y se emite la misma cadena, nunca una y se usa otra.

Contrato deliberadamente CONSERVADOR: se acepta un path propio del Hub
(``/comercial``, ``/reporting?x=1``, ``/modules/foo?pack=bar``) y se rechaza
todo lo demás. Cualquier ``\`` se rechaza de plano, incluso dentro del query
string, donde el browser NO lo normalizaría: ninguna ruta ni parámetro real del
Hub lleva backslash, así que el costo es nulo y evita tener que razonar caso por
caso sobre dónde normaliza cada browser. Un destino rechazado no es un error:
el caller cae en su propio default (ver los tres consumidores).
"""
from __future__ import annotations

import re

# Lo que el browser BORRA dentro de la URL.
_URL_STRIPPED = re.compile(r"[\t\n\r]")
# Lo que el browser RECORTA al principio (controles C0 y espacio).
_LEADING_JUNK = re.compile(r"^[\x00-\x20]+")


def safe_internal_next(value: str | None) -> str:
    """El destino interno equivalente a `value`, o `""` si no es interno.

    Interno = un path del propio Hub: arranca con UNA barra y no con dos.
    `""` (y `None`) devuelven `""`, o sea "no hay destino": es el mismo
    resultado que un destino rechazado, y los callers ya lo tratan igual
    (`safe_internal_next(next) or default_home`).
    """
    probe = _LEADING_JUNK.sub("", _URL_STRIPPED.sub("", value or ""))
    if "\\" in probe:
        return ""
    if not probe.startswith("/") or probe.startswith("//"):
        return ""
    return probe
