# Copiloto Metodologico | Arquitecto Danwor

Aplicacion Streamlit para navegar contenido metodologico, arboles de decision, assessment tecnico y asistencia con IA para SAP Datasphere / BDC.

## Requisitos

- Python 3.11+
- Acceso a Microsoft Entra ID para login corporativo
- `OPENAI_API_KEY` si se usa el modo API Real

## Instalacion

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuracion

Copiar `.env.example` a `.env` o definir variables de entorno equivalentes antes de iniciar la app.

Variables principales:

- `DANWOR_STATUS`: `DEV` usa simulacion por defecto y muestra herramientas internas; `PRD` fuerza API Real.
- `OPENAI_API_KEY`: clave requerida para llamadas reales a OpenAI.
- `OPENAI_MODEL`: modelo usado por el gateway LLM.
- `AZURE_CLIENT_ID` y `AZURE_TENANT_ID`: datos de la app registrada en Microsoft Entra ID.
- `DANWOR_TITLE_CONTEXT`: `ON` agrega el `jobTitle` del usuario como contexto del asistente.

## Ejecucion

```powershell
streamlit run app.py
```

## Estructura

- `app.py`: orquestador principal.
- `config.py`: rutas, feature flags y configuracion por entorno.
- `core/`: estado, contenido, prompts y gateway LLM.
- `engine/`: carga y render de arboles de decision.
- `ui/`: paneles visuales de Streamlit.
- `content/`: modulos Markdown.
- `trees/`: arboles YAML.
- `assessment/`: evaluaciones tecnicas.

## Mantenimiento

No versionar `.env`, `.streamlit/secrets.toml`, archivos `.zip`, `feedback_log/` ni `__pycache__/`.

## Contrato de contenido

Los módulos Markdown son la fuente metodológica y semántica del copiloto. Los
HTML embebidos son assets visuales.

Usar los tags así:

- `{{section_meta: keywords="..."}}`: keywords breves para encontrar una sección.
- `{{section_id: 06}}`: tag legacy. La numeración visible ya no lo respeta; se
  calcula automáticamente desde `00_Intro` en adelante. El ancho se controla con
  `SECTION_NUMBER_WIDTH` en `config.py` (`2` => `[01]`, `3` => `[001]`).
- `{{semantic_summary}}...{{/semantic_summary}}`: resumen invisible en pantalla,
  usado por el LLM cuando una imagen o HTML contiene conocimiento técnico.
- `{{section_mode: visual_only}}`: contenido institucional/introductorio que no
  debe participar en el flujo IA.
- `{{html_embed: ../assets/archivo.html height=660}}`: render visual embebido.

Usar frontmatter así:

- `scope`: alcance temático del módulo. Entra en conversación libre y acciones.
- `out_of_scope_redirect`: keywords para derivar consultas a otro módulo.
- `intent_assets`: activos concretos que el asistente puede ofrecer si la consulta
  menciona una intención compatible.
- `guardrails`: posturas normativas Danwor que el LLM debe respetar. Sirven para
  evitar que anti-patterns se presenten como alternativas viables.

Regla práctica: si el visual enseña una decisión técnica, debe tener
`semantic_summary`; si es onboarding institucional, puede ser `visual_only`.
Si una corrección de feedback cambia la postura metodológica de un módulo,
primero evaluar si corresponde actualizar `scope` o `guardrails` antes de tocar
prompts globales.
