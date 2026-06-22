# Auditoría de residuos legacy

Fecha: 2026-06-21  
Alcance: `ArquitectoDanwor-poc`, runtime FastAPI, runtime Streamlit heredado,
herramientas offline y content packs.

## Resumen ejecutivo

FastAPI ya resuelve todos sus módulos, apéndices, árboles, embeds y templates
locales desde `content_packs/`. En las pruebas de resolución efectiva hubo **0
referencias legacy sobre 16 recursos editoriales inspeccionados**.

Sin embargo, las carpetas raíz `content/`, `assets/`, `trees/` y `templates/`
todavía no deben borrarse directamente porque:

1. `core/content_registry.py` las agrega como fallback para todos los packs.
2. `build_analysis_prompt.py`, `propose_content_updates.py` y
   `propose_tree_updates.py` todavía apuntan directamente a carpetas raíz.
3. El renderer Streamlit conserva una resolución directa de
   `BASE_DIR/templates` para assets descargables.
4. El proyecto todavía conserva funcionalidades no portadas a FastAPI:
   assessment, analítica administrativa, SharePoint cases y parte del logging.

La limpieza debe ejecutarse por fases. Primero se retiran los consumidores y el
fallback; después se borran las copias.

## Fuente de verdad efectiva

### Datasphere MVP

- 6/6 módulos resueltos desde `content_packs/datasphere_mvp/content`.
- 5/5 apéndices resueltos desde `content_packs/datasphere_mvp/content/apendices`.
- 13/13 árboles resueltos desde `content_packs/datasphere_mvp/trees`.
- Todos los embeds y el template CDS resuelven dentro del pack.

### BDC Next

- 2/2 módulos y 1/1 apéndice resueltos desde `content_packs/bdc_next/content`.
- Los tres embeds heredados resuelven desde el pack padre `datasphere_mvp`.
- `content_mode: replace` evita mezclar Markdown Datasphere con BDC.

### Riesgo detectado en BDC

Los árboles sí se heredan desde Datasphere. Además,
`WizardStore.trees_for_module()` acepta coincidencia por prefijo numérico. Como
resultado, `01_Estrategia_y_Readiness_BDC` recibe incorrectamente el árbol
`m01_espacios_topologia` del módulo Datasphere 01.

Recomendación: resolver árboles por `links.module` exacto y declarar herencia de
árboles explícita por pack. No usar el número visible como identidad lógica.

## Duplicados legacy

### `content/`

- 6 módulos y 5 apéndices tienen equivalente en `datasphere_mvp`.
- 6 archivos son idénticos.
- 5 archivos ya divergen del pack activo.
- Las cuatro imágenes de `content/img/` no tienen referencias en el repositorio.

Conclusión: `content/` ya es una segunda fuente de verdad y se está quedando
obsoleta. Debe retirarse tras migrar los tres scripts offline y eliminar el
fallback del registry.

### `assets/`

- 15/15 archivos legacy tienen copia en el pack.
- 14 son idénticos.
- `danwor-animated-dancing.html` difiere y la versión activa es la del pack.

Conclusión: la carpeta raíz es redundante para FastAPI y para los packs
instalados.

### `trees/`

- 12/12 árboles legacy son idénticos a sus equivalentes del pack.
- El pack contiene además `02_seguridad_dac.yaml`.

Conclusión: la carpeta raíz es una copia completa y desactualizable.

### `templates/`

- `cds_extractor_template.abap` es idéntico al template del pack.

Conclusión: duplicado, pero el renderer Streamlit todavía accede a la ruta raíz.

## Candidatos de eliminación inmediata

Estos archivos no tienen consumidores detectados y no participan de FastAPI:

- `core/logo.py`: módulo huérfano; abre un asset raíz inexistente.
- `engine/markdown_tools.py`: módulo huérfano; además importa `utils`, módulo que
  no existe en la raíz.
- `content/img/00_cover.png`.
- `content/img/00_cover_old.png`.
- `content/img/00_roadmap.png`.
- `content/img/03_arquitectura_integracion.png`.
- `backend/`: sólo contiene `__pycache__` de una implementación ya retirada; no
  hay fuentes versionadas.

Assets sin referencias detectadas dentro del pack:

- `content_packs/datasphere_mvp/assets/danwor-animated-header.html`.
- `content_packs/datasphere_mvp/assets/danwor-animated-logo.html` (sólo lo
  menciona el módulo huérfano).
- `content_packs/datasphere_mvp/assets/danwor-arquitectura-referencia-aws (1).html`.
- `content_packs/datasphere_mvp/assets/danwor-convencion-nombres.html`.
- `content_packs/datasphere_mvp/assets/danwor-estrategia-reporting.html`.

Antes de borrar assets editoriales conviene confirmar que no estén reservados
para secciones todavía no publicadas.

## Componentes que no usa FastAPI, pero aún preservan funcionalidad

No deben considerarse residuos mientras siga vigente el requisito de no perder
funcionalidad:

- `app.py`, `auth.py`, `ui/` y `.streamlit/config.toml`: aplicación Streamlit.
- `core/content.py`, `core/state.py`, `core/llm.py`, `core/activity.py` y
  `core/sharepoint_cases.py`: adapters y runtime Streamlit.
- `engine/tree_engine.py`, `engine/tree_platform.py` y `engine/tree_registry.py`:
  motor original y red de caracterización.
- `assessment/`, `core/assessment_engine.py` y `ui/assessment_panel.py`:
  assessment aún no portado.
- `analyze_cases.py`, `analyze_feedback.py`, `propose_*`, `pipeline/` y
  `reports/`: herramientas administrativas/offline aún no portadas.
- Tests golden de render y routing: siguen siendo la red de seguridad de paridad.

## Residuos de configuración y documentación

- `requirements.txt` mezcla dependencias FastAPI y Streamlit. Debe dividirse en
  runtime web, legacy y desarrollo.
- `README.md` todavía presenta Streamlit como aplicación principal.
- `BETA_ARCHITECTURE.md` afirma que `core/corpus.py` importa Streamlit; ya no es
  cierto.
- `webapp/services/section_analysis.py` todavía busca API keys en
  `.streamlit/secrets.toml` como fallback. Debe quedar sólo entorno/secret store.
- `config.py` conserva fallbacks directos a las cuatro carpetas raíz.
- `webapp/static/app.css` no está sin uso, pero contiene estilos iniciales
  solapados por `layout.css` y `tools.css`; requiere consolidación, no borrado.
- `case_library/` y `reports/` mezclan seed/conocimiento con outputs de runtime.
  Conviene separar datos versionados de datos generados.

## Hallazgo funcional corregido durante la auditoría

`core/routing_scorers._find_matching_asset()` importaba el parser desde
`core.content` (Streamlit). En FastAPI ese import fallaba silenciosamente y
deshabilitaba los assets locales. Ahora usa `core.content_metadata`, que es puro,
y tiene prueba de regresión para el template CDS del módulo 04.

## Plan de retiro seguro

### Fase 1: cortar doble fuente de verdad

1. Migrar los tres scripts offline a `content_registry` y al pack explícito.
2. Migrar la descarga Streamlit de templates a `resolve_pack_file()`.
3. Eliminar el fallback `BASE_DIR/kind` de `roots_for_kind()`.
4. Hacer que un pack inexistente falle al iniciar en vez de caer a carpetas raíz.
5. Agregar tests que exijan que toda resolución quede dentro de `content_packs/`.

### Fase 2: borrar duplicados raíz

Eliminar `content/`, `assets/`, `trees/` y `templates/` completos. Ejecutar suite
web, routing, render, árboles y herramientas offline.

### Fase 3: retirar Streamlit

Sólo después de portar o retirar explícitamente assessment, SharePoint,
analítica y administración:

1. Eliminar `app.py`, `auth.py`, `ui/`, `.streamlit/` y adapters Streamlit.
2. Eliminar dependencias `streamlit` y `streamlit-msal` del runtime.
3. Retirar tests legacy únicamente cuando exista cobertura equivalente FastAPI.

## Estado de pruebas al auditar

- Suite FastAPI y contratos puros usados durante la refactorización: verde.
- Suite completa: **158 passed, 10 failed, 2 skipped**.
- Los 10 fallos son snapshots/goldens legacy desactualizados frente a cambios
  editoriales actuales (títulos, emojis y render), no errores de import o rutas.
- No se actualizaron goldens automáticamente: deben revisarse y aprobarse como
  cambios editoriales intencionales.
