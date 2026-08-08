"""
copilot_runtime.py — Composición de estado mutable de proceso del Copiloto
(Fase 2J-A).

Dueño explícito de los dos únicos objetos creados por ``webapp/main.py`` que
requieren identidad compartida entre requests:

- ``store`` (WizardStore): runs de árboles de decisión, en memoria.
- ``assistant_store`` (AssistantConversationStore): conversaciones y replies
  del asistente, en memoria.

Extraído de ``webapp/main.py`` como relocación pura de la construcción —
mismos constructores, mismas semánticas de proceso-local/en-memoria/sin TTL
que ya existían. NO agrega persistencia, Redis, base de datos, TTL, estado
distribuido, lifecycle de startup/shutdown, service locator, registry, DI ni
``app.state``.

Clasificación: COPILOT / PROCESS-LIFETIME STATE COMPOSITION. NOT PLATFORM /
SHARED — `store` y `assistant_store` son conceptos de negocio del Copiloto
(árboles de decisión, conversaciones del asistente); un futuro workspace de
Operaciones/Staffing no reutilizaría estas INSTANCIAS. Si algún día necesita
estado mutable propio, podría reutilizar el PATRÓN (un módulo chico de
composición que construye y re-exporta sus propias instancias) — no este
módulo ni estos objetos.

Explícitamente NO incluye (permanecen donde están, son stateless):
``section_analysis_service``, ``assistant_chat_service``,
``assistant_routing_service``, ``assistant_feedback_repository``.

NO importa ``webapp.main``. NO define rutas. NO renderiza templates. NO
toca Catalog ni M365/Graph.
"""
from __future__ import annotations

from webapp.services.case_library import CaseLibraryRepository
from webapp.services.wizard_service import WizardStore
from webapp.services.assistant_service import AssistantConversationStore

store = WizardStore(case_repository=CaseLibraryRepository())
assistant_store = AssistantConversationStore()
