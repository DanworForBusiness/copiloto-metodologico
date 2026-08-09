from pathlib import Path
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default

# App identity
APP_TITLE    = _env("DANWOR_APP_TITLE", "Copiloto Metodológico | Arquitecto Danwor")
APP_SUBTITLE = _env("DANWOR_APP_SUBTITLE", "SAP Datasphere · BDC-Ready · Inteligencia metodológica")
LOGO_URL     = _env("DANWOR_LOGO_URL", "https://certificaciones.greatplacetowork.com.ar/hubfs/Danwor%20LOGO.png")

# App identity — workspace Operaciones (mismo patrón que APP_TITLE/APP_SUBTITLE
# arriba; NO se reutilizan esas mismas variables porque un solo proceso sirve
# ambos workspaces — no hay "config activo" que mutar por request, ver
# webapp/workspaces.py y platform_topbar.html).
OPERATIONS_APP_TITLE    = _env("DANWOR_OPERATIONS_APP_TITLE", "Gestión Inteligente · Operaciones")
OPERATIONS_APP_SUBTITLE = _env("DANWOR_OPERATIONS_APP_SUBTITLE", "Analítica y gestión operativa | Danwor Transversal")

# Paths
BASE_DIR          = Path(__file__).parent
CONTENT_PACKS_DIR = BASE_DIR / "content_packs"
CONTENT_PACK      = _env("DANWOR_CONTENT_PACK", "datasphere_core")
# La resolución de content/trees/assets/templates vive en core.content_registry
# (basada en content_packs/, con cadena de herencia). Las viejas vars
# CONTENT_DIR/TREES_DIR/ASSETS_DIR/TEMPLATES_DIR/LOGO_* se retiraron: no las
# importaba nadie (la webapp resuelve por pack; las tools offline usan sus rutas).
CASE_LIBRARY_DIR  = BASE_DIR / "case_library"
FEEDBACK_LOG_DIR  = BASE_DIR / "feedback_log"
ACTIVITY_LOG_DIR  = BASE_DIR / "activity_log"

# ── Persistencia en DO Spaces ────────────────────────────────────────────────
# App Platform corre sobre un FS efímero: en cada deploy/reciclado se borra todo
# lo que el copiloto genera (catálogo, casos, feedback, adopción, analítica). Este
# bloque configura el respaldo/restore contra un bucket S3-compatible (DO Spaces).
# Si faltan credenciales, TODO queda deshabilitado (no-op) → dev local sin cambios.
# Las CLAVES son secretas: definir SPACES_KEY/SPACES_SECRET por entorno, nunca acá.
SPACES_ENDPOINT   = _env("SPACES_ENDPOINT", "")          # https://atl1.digitaloceanspaces.com (sin el bucket)
SPACES_REGION     = _env("SPACES_REGION", "atl1")
SPACES_BUCKET     = _env("SPACES_BUCKET", "")            # danwor-copiloto-memory
SPACES_KEY        = _env("SPACES_KEY", "")
SPACES_SECRET     = _env("SPACES_SECRET", "")
SPACES_PREFIX     = _env("SPACES_PREFIX", "").strip("/")  # opcional, p.ej. "prod" para separar ambientes
SPACES_SYNC_INTERVAL = int(_env("SPACES_SYNC_INTERVAL", "120"))  # segundos entre mirror periódico
# Dirs runtime a persistir (relativas a BASE_DIR). El catálogo puede vivir en otra
# ruta vía DANWOR_CATALOG_DIR; el remote_store lo resuelve en runtime. (Se parsea
# inline porque _csv_env se define más abajo en este módulo.)
PERSIST_PATHS     = [p.strip() for p in _env(
    "SPACES_PERSIST_PATHS", "catalog_local,case_library,feedback_log,activity_log,reports"
).split(",") if p.strip()]
PERSIST_ENABLED   = bool(SPACES_ENDPOINT and SPACES_BUCKET and SPACES_KEY and SPACES_SECRET)

# LLM
MODEL        = _env("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_EXECUTION_MODE = _env("DANWOR_EXECUTION_MODE", "API Real")

# Feature flags
# DEV/PRD controla la visibilidad operativa; la ejecucion conversacional se
# define por DANWOR_EXECUTION_MODE (API Real por defecto).
STATUS       = _env("DANWOR_STATUS", "DEV").upper()
DIAGNOSTIC_MODE = _env("DANWOR_DIAGNOSTIC_MODE", "false").lower() in {"1", "true", "yes", "on"}

# Usuario(s) con permiso operativo para ver el modo diagnóstico aun en PRD.
# Acepta alias sin dominio ("joconnor") o mails completos separados por coma.
APP_OWNER    = _env("APP_OWNER", "joconnor")

# Presentation
# Cantidad mínima de dígitos para la numeración global de secciones.
# 2 => [01], [02]. 3 => [001], [002].
SECTION_NUMBER_WIDTH = int(_env("DANWOR_SECTION_NUMBER_WIDTH", "2"))

# Largo máximo del texto visible en botones {{support}}.
# El texto completo se conserva como tooltip para no perder contexto.
SUPPORT_LINK_LENGTH = int(_env("SUPPORT_LINK_LENGTH", "15"))

# IA completa por identidad lógica de contenido.
# Acepta:
# - "*" para habilitar todos los módulos/apéndices del pack activo
# - IDs lógicos ("01", "02", "A05") independientes del nombre físico
# - stems exactos para compatibilidad hacia atrás
def _csv_env(name: str, default: str) -> list[str]:
    raw = _env(name, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


_IA_ENABLED_ENV = os.getenv("DANWOR_IA_ENABLED_MODULES", "").strip()
IA_ENABLED_MODULES: list[str] = _csv_env(
    "DANWOR_IA_ENABLED_MODULES",
    "01,02,03,04,05,A01,A02,A03,A04,A05,A06",
)
IA_DISABLED_MODULES: list[str] = _csv_env("DANWOR_IA_DISABLED_MODULES", "00")


def _module_identity(stem: str) -> str:
    """Extrae la identidad lógica del módulo/apéndice: 01, 02, A05, etc."""
    match = re.match(r"^(A\d+|\d+)(?:_|$)", (stem or "").strip(), flags=re.I)
    return match.group(1).upper() if match else ""


def ia_enabled_for(stem: str) -> bool:
    """Devuelve si un módulo/apéndice tiene IA habilitada para el pack activo."""
    clean_stem = (stem or "").strip()
    module_id = _module_identity(clean_stem)
    if _IA_ENABLED_ENV:
        enabled = {item.strip().upper() for item in IA_ENABLED_MODULES}
    else:
        # El pack puede cambiar durante la sesion sin recargar config.py.
        from core.content_registry import active_content_pack
        enabled = {"*"} if active_content_pack() == "bdc_next" else {
            "01", "02", "03", "04", "05", "A01", "A02", "A03", "A04", "A05", "A06"
        }
    disabled = {item.strip().upper() for item in IA_DISABLED_MODULES}

    if clean_stem.upper() in disabled or module_id in disabled:
        return False
    if "*" in enabled:
        return True
    return clean_stem.upper() in enabled or module_id in enabled

# Roadmap items
ROADMAP_ITEMS = [
    ("Prueba de Concepto",   True,  "🚀", "Modo upskill con IA: decidir mejor, aprender más rápido y evitar errores comunes."),
    ("Herramienta interna",  True,  "🧰", "Extensión a todos los módulos metodológicos para uso interno de consultores."),
    ("Comercial & pre-venta",False, "💼", "Discovery de retroalimentación técnica y acelerador de propuestas."),
    ("Certificaciones SAP",  False, "🎓", "Plataforma de entrenamiento y evaluación técnica."),
    ("Módulo RDS",           False, "📦", "Framework para industrialización de Rapid Deployment Solutions."),
]

# Azure AD — OAuth corporativo (Microsoft Entra ID)
# NOTA: client_id y tenant_id NO son secretos — son identificadores públicos del
# flujo OAuth public-client (viajan al browser igual). No se usa client_secret
# (token delegado del usuario). Por eso es seguro dejar defaults; overridear por
# entorno solo para apuntar a otro tenant/app.
AZURE_CLIENT_ID  = _env("AZURE_CLIENT_ID", "Ad98b5cd-71d0-451f-8d70-e8581a4788fa")
AZURE_TENANT_ID  = _env("AZURE_TENANT_ID", "f287b953-5428-4a5e-ae17-bd9c4cfaedbe")
TITLE_CONTEXT    = _env("DANWOR_TITLE_CONTEXT", "ON").upper() # ON vs OFF → el asistente usa el jobTitle como contexto

# Control de acceso adicional a la autenticación corporativa.
# "ALL" permite cualquier usuario autenticado del tenant configurado.
# En otro caso, usar mails separados por coma:
# ALLOWED_USERS="usuario1,usuario2" asume @danwor.com.
# También acepta mails completos si algún caso lo requiere.
ALLOWED_USERS    = _env("ALLOWED_USERS", "ALL")
# Perfil de SOLO LECTURA (trainees/juniors que entran por Danwor Academy y
# profundizan en el contenido técnico, pero SIN interacción IA hasta que los
# seniors validen el copiloto). Estos usuarios navegan y leen todo el contenido,
# pero tienen la IA apagada (sin asistente ni análisis de sección → sin consumo
# de tokens). Mismo formato que ALLOWED_USERS: "" = nadie es solo-lectura;
# "ALL" = cualquier autenticado que NO esté en ALLOWED_USERS es solo-lectura;
# o CSV de mails/alias. ALLOWED_USERS (perfil full) SIEMPRE gana si el usuario
# está en ambas listas. Para el lanzamiento masivo: vaciar ACADEMY_USERS y/o
# pasar los usuarios a ALLOWED_USERS.
ACADEMY_USERS    = _env("ACADEMY_USERS", "")
# Cierre del dialog de invitación (/desafio) cuando la app corre enmascarada en
# el frame de danwor.com: la cruz navega la ventana completa (target=_top) a esta
# URL "limpia" para que el refresh no vuelva a pasar por /desafio. Sin frame
# (host directo o local), el dialog se cierra en el lugar y esta URL no se usa.
DESAFIO_CLOSE_URL = _env("DESAFIO_CLOSE_URL", "https://danwor.com.ar/copiloto/")
AUTH_DEFAULT_DOMAIN = _env("AUTH_DEFAULT_DOMAIN", "danwor.com").lower().lstrip("@")

# Secreto para firmar la cookie de sesión de la webapp (FastAPI). En producción
# definir SESSION_SECRET por entorno; el default solo sirve para desarrollo.
SESSION_SECRET   = _env("SESSION_SECRET", "danwor-copiloto-dev-secret-cambiar-en-produccion")
SESSION_MAX_AGE  = int(_env("SESSION_MAX_AGE", "28800"))  # 8 horas en segundos
# Atributos de la cookie de sesión. Cuando la app va embebida en un iframe de otro
# dominio (p. ej. danwor.com/copiloto sobre el host de DigitalOcean), la cookie es
# "de tercera parte": debe ser SameSite=None; Secure para viajar. En local (http)
# se deja Lax (default). En producción: SESSION_COOKIE_SAMESITE=none + SESSION_COOKIE_SECURE=true.
SESSION_COOKIE_SAMESITE = _env("SESSION_COOKIE_SAMESITE", "lax").lower()
SESSION_COOKIE_SECURE   = _env("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}

# Logging liviano de adopción inicial.
# OFF: no escribe nada. ON/TRUE/1/YES: registra eventos JSONL diarios. VERBOSE regostra también el detalle de cada request (para debug de adopción).
LOG_ACTIVITY = _env("LOG_ACTIVITY", "VERBOSE").upper()

# SharePoint / Graph — Casos SAP internos.
# Se leen con token delegado del usuario autenticado. No se usa client_secret.
SHAREPOINT_CASES_ENABLED = _env("SHAREPOINT_CASES_ENABLED", "ON").upper()
SHAREPOINT_CASES_SITE_ID = _env(
    "SHAREPOINT_CASES_SITE_ID",
    "danwor.sharepoint.com,87f47e3b-f792-4537-bcac-8a26d5488789,616ef0c6-fda4-427e-92fb-5f8470732185",
)
SHAREPOINT_CASES_LIST_ID = _env(
    "SHAREPOINT_CASES_LIST_ID",
    "973e8de0-35e2-4ba1-950b-34eaf88348fd",
)
SHAREPOINT_CASES_TOP_K = int(_env("SHAREPOINT_CASES_TOP_K", "5"))
SHAREPOINT_CASES_MIN_SCORE = float(_env("SHAREPOINT_CASES_MIN_SCORE", "6"))
SHAREPOINT_CASES_CACHE_TTL = int(_env("SHAREPOINT_CASES_CACHE_TTL", "300"))

# SharePoint / Graph — Catálogo CSN (PILOTO, no productivo).
# Carpeta "CSN" con los exports CDS crudos por cliente/dominio, leída con el
# token delegado del usuario (mismo scope Sites.Read.All que los casos). Es el
# link de compartir de la carpeta; el generador lo resuelve vía Graph /shares.
SHAREPOINT_CATALOG_CSN_URL = _env(
    "SHAREPOINT_CATALOG_CSN_URL",
    "https://danwor.sharepoint.com/:f:/s/PrcticaAnalytics/IgDMABujBXLDSIAVxSj6GI9LAdb4FvQjQckGYhQvuc-tIIg?e=PEazSD",
)

# SharePoint / Graph — matriz de roles de seguridad (archivo Excel independiente).
# Se descarga al staging temporal como <cliente>/Seguridad/*.xlsx para que el
# generador de seguridad la procese junto con DAC/linaje sin mover el activo.
SHAREPOINT_SECURITY_ROLE_MATRIX_URL = _env(
    "SHAREPOINT_SECURITY_ROLE_MATRIX_URL",
    "https://danwor.sharepoint.com/:x:/s/PrcticaAnalytics/IQC_KaECHiU5TrOjJBpDhkC7AeH7NJUQhq3AACTGOSpltG8?e=n6CNhy",
)
SHAREPOINT_SECURITY_ROLE_MATRIX_CLIENT = _env("SHAREPOINT_SECURITY_ROLE_MATRIX_CLIENT", "Danone")

# SharePoint / Graph — Staffing (Operations, Batch 2 de aceleración post-Fase 2).
# Sitio "Gestión": Asignaciones por proyecto / Proyectos / Colaboradores.
# El site ID NO está establecido en este repo/HANDOFF — a diferencia de
# SHAREPOINT_CASES_SITE_ID/SHAREPOINT_CATALOG_CSN_URL arriba, que sí tienen un
# valor real conocido, este queda sin default: webapp/services/
# staffing_repository.py falla explícitamente si se intenta usar sin
# configurar. Prerequisito de despliegue pendiente antes de Batch 3 (ver
# HANDOFF, Batch 2).
SHAREPOINT_GESTION_SITE_ID = _env("SHAREPOINT_GESTION_SITE_ID", "").strip()
# Los 3 list ID sí fueron provistos como referencia conocida al autorizar este
# batch — KNOWN FROM LEGACY SOURCE CONFIGURATION (evidencia de Power Query
# legado), NO todavía LIVE GRAPH VALIDATED en este entorno. No aparecían
# antes en repo/HANDOFF (verificado, sin contradicción) — quedan como
# default configurable, pendientes de confirmar contra una lectura real de
# Graph (ver HANDOFF, Batch 2, prerequisito de validación).
SHAREPOINT_ASIGNACIONES_LIST_ID = _env(
    "SHAREPOINT_ASIGNACIONES_LIST_ID", "c7c96b06-b32d-4386-8228-dd8e1f9926b0",
).strip()
SHAREPOINT_PROYECTOS_LIST_ID = _env(
    "SHAREPOINT_PROYECTOS_LIST_ID", "efa2e279-593e-4321-867f-1ff982d800f6",
).strip()
SHAREPOINT_COLABORADORES_LIST_ID = _env(
    "SHAREPOINT_COLABORADORES_LIST_ID", "6e512cd3-394a-461f-abcd-6613f2c21113",
).strip()

# Internal names de columna Graph para las 3 listas de Staffing. A
# diferencia de los list ID arriba, estos NO tienen ningún valor conocido —
# ni de Power Query legado ni de ningún otro lugar del repo/HANDOFF — así
# que NO llevan default (corrective pass, Batch 2): los nombres que se
# usaban antes (ColaboradorLookupId, ProyectoLookupId, Asignacion, Estado,
# FechaInicio, FechaFin, y Title para nombre de proyecto/colaborador) eran
# una inferencia estilística sobre la convención de otras listas
# (sharepoint_cases.py), NUNCA confirmada contra el sitio Gestión real —
# quedan removidos como default de producción. Vacíos hasta que alguien con
# acceso al sitio los confirme (Graph Explorer / `$select` sobre
# .../lists/{id}/columns) y los configure. La app NO falla al importar por
# esto — solo webapp/services/staffing_repository.py falla explícito si se
# intenta LEER Staffing sin esta configuración (ver Batch 2 en HANDOFF).
SHAREPOINT_ASIGNACIONES_FIELD_COLABORADOR_LOOKUP = _env(
    "SHAREPOINT_ASIGNACIONES_FIELD_COLABORADOR_LOOKUP", "",
).strip()
SHAREPOINT_ASIGNACIONES_FIELD_PROYECTO_LOOKUP = _env(
    "SHAREPOINT_ASIGNACIONES_FIELD_PROYECTO_LOOKUP", "",
).strip()
SHAREPOINT_ASIGNACIONES_FIELD_ALLOCATION = _env(
    "SHAREPOINT_ASIGNACIONES_FIELD_ALLOCATION", "",
).strip()
SHAREPOINT_ASIGNACIONES_FIELD_STATUS = _env(
    "SHAREPOINT_ASIGNACIONES_FIELD_STATUS", "",
).strip()
SHAREPOINT_ASIGNACIONES_FIELD_START_DATE = _env(
    "SHAREPOINT_ASIGNACIONES_FIELD_START_DATE", "",
).strip()
SHAREPOINT_ASIGNACIONES_FIELD_END_DATE = _env(
    "SHAREPOINT_ASIGNACIONES_FIELD_END_DATE", "",
).strip()
SHAREPOINT_PROYECTOS_FIELD_TITLE = _env("SHAREPOINT_PROYECTOS_FIELD_TITLE", "").strip()
SHAREPOINT_COLABORADORES_FIELD_TITLE = _env("SHAREPOINT_COLABORADORES_FIELD_TITLE", "").strip()

# Detección de sección por texto (Nivel 2 de _find_matching_section)
# MIN_SECTION_WORDS  — mínimo de palabras significativas de la pregunta que deben
#                      aparecer en el cuerpo de la sección para considerar match.
#                      Subir para reducir falsos positivos.
# SECTION_STOPWORDS  — palabras que NO cuentan como señal de relevancia porque
#                      aparecen en casi todas las secciones del módulo.
MIN_SECTION_WORDS  = 3
SECTION_STOPWORDS  = {
    "datasphere", "sap", "datos", "dato", "sistema", "sistemas",
    "modulo", "seccion", "integracion", "flujo", "flujos",
}
