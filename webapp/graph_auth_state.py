"""
graph_auth_state.py — AUTORIDAD ÚNICA sobre una distinción que el Hub venía
perdiendo: cuándo una falla de Graph es de AUTORIZACIÓN y cuándo es de FUENTE.

Clasificación: PLATFORM / GRAPH AUTH STATE. NO importa nada de ``webapp``
(hoja pura, cualquiera puede usarla sin riesgo de ciclo). NO habla HTTP, NO
conoce routers, NO decide copy.

EL DEFECTO QUE CIERRA (auditoría cross-vertical, owner 2026-08-23): la sesión
del Hub vive 8 h (cookie JWT, ``SESSION_MAX_AGE``) pero el access token de
Graph vive ~60-90 min, se adquiere UNA sola vez en el login interactivo y no
tiene renovación posible (no se pide ``offline_access``, no hay refresh
token). O sea: hay una ventana de horas en las que la sesión es válida y la
autorización delegada ya murió.

En esa ventana Graph responde 401 y el Hub lo presentaba como
``source_error`` — el MISMO estado que una caída real de SharePoint. El
usuario leía "No pudimos cargar desde SharePoint", que es falso y además no
sugiere la acción que sí resuelve. Terminó descubriendo por prueba y error
que salir y volver a entrar lo arreglaba.

CONTRATO: 401 (y sólo 401) es ``auth_required``.

Deliberadamente NO se incluye 403. Un 403 de Graph con token delegado
significa scope/consentimiento insuficiente, y eso NO lo arregla renovar la
autorización — necesita consentimiento de administrador. Remediación
distinta ⇒ estado distinto. Meterlo acá le prometería al usuario una acción
que no lo va a desbloquear.

`source_state` existe porque las dos verticales que consumen Graph ya tienen
su propio vocabulario EN PRODUCCIÓN para "falla de fuente": Reporting usa
``source_error``, Staffing usa ``graph_error``. Renombrar cualquiera de los
dos rompería sus templates y sus tests, y no es lo que este corrective viene
a resolver: la decisión que se comparte es la de AUTORIZACIÓN, no el nombre
del otro estado.
"""
from __future__ import annotations

GRAPH_AUTH_REQUIRED = "auth_required"
GRAPH_SOURCE_ERROR = "source_error"


def graph_error_state(status_code: int | None, *, source_state: str = GRAPH_SOURCE_ERROR) -> str:
    """`auth_required` si la autorización delegada ya no sirve; si no,
    `source_state` (el vocabulario de falla de fuente de quien llama).

    `status_code` puede ser `None` — una falla de transporte sin respuesta
    HTTP (DNS, timeout) NO es un problema de autorización.
    """
    return GRAPH_AUTH_REQUIRED if status_code == 401 else source_state
