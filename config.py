from pathlib import Path
import os

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

# Paths
BASE_DIR          = Path(__file__).parent
CONTENT_DIR       = BASE_DIR / "content"
TREES_DIR         = BASE_DIR / "trees"
ASSETS_DIR        = BASE_DIR / "assets"
LOGO_ANIMATED     = ASSETS_DIR / "danwor-animated-logo.html"
LOGO_DANCING      = ASSETS_DIR / "danwor-animated-dancing.html"
CASE_LIBRARY_DIR  = BASE_DIR / "case_library"
FEEDBACK_LOG_DIR  = BASE_DIR / "feedback_log"

# LLM
MODEL        = _env("OPENAI_MODEL", "gpt-4o-mini")

# Feature flags
# "DEV"  → modo simulación por defecto, controles de debug visibles
# "PRD"  → API Real forzada, controles de debug ocultos
STATUS       = _env("DANWOR_STATUS", "DEV").upper()

# Presentation
# Cantidad mínima de dígitos para la numeración global de secciones.
# 2 => [01], [02]. 3 => [001], [002].
SECTION_NUMBER_WIDTH = int(_env("DANWOR_SECTION_NUMBER_WIDTH", "2"))

# IA completa habilitada sólo para estos stems (PoC → Herramienta interna)
IA_ENABLED_MODULES: list[str] = [
    "01_Arquitectura_y_Conectividad",
    "02_Seguridad_y_Gobernanza",
    "03_Integracion_y_Estrategia_de_Datos",
    "04_Modelado_y_Desarrollo",
    "05_Reporting_y_Consumo",
    "06_Troubleshooting_y_Performance",
    "08_Modernizacion_BW_Greenfield_BDC",
    "10_Fundamentos_EA_y_Paradigmas",
]

# Roadmap items
ROADMAP_ITEMS = [
    ("Prueba de Concepto",   True,  "🚀", "Modo upskill con IA: decidir mejor, aprender más rápido y evitar errores comunes."),
    ("Herramienta interna",  True,  "🧰", "Extensión a todos los módulos metodológicos para uso interno de consultores."),
    ("Comercial & pre-venta",False, "💼", "Discovery de retroalimentación técnica y acelerador de propuestas."),
    ("Certificaciones SAP",  False, "🎓", "Plataforma de entrenamiento y evaluación técnica."),
    ("Módulo RDS",           False, "📦", "Framework para industrialización de Rapid Deployment Solutions."),
]

# Azure AD — OAuth corporativo (Microsoft Entra ID)
AZURE_CLIENT_ID  = _env("AZURE_CLIENT_ID", "Ad98b5cd-71d0-451f-8d70-e8581a4788fa")
AZURE_TENANT_ID  = _env("AZURE_TENANT_ID", "f287b953-5428-4a5e-ae17-bd9c4cfaedbe")
TITLE_CONTEXT    = _env("DANWOR_TITLE_CONTEXT", "ON").upper() # ON vs OFF → el asistente usa el jobTitle como contexto

# Control de acceso adicional a la autenticación corporativa.
# "ALL" permite cualquier usuario autenticado del tenant configurado.
# En otro caso, usar mails separados por coma:
# ALLOWED_USERS="usuario1@danwor.com,usuario2@danwor.com"
ALLOWED_USERS    = _env("ALLOWED_USERS", "ALL")

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
