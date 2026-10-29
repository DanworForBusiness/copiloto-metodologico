# Presentaciones (Gestión Comercial) — Handoff maestro

Documento de transferencia para el siguiente agente. Cubre **V1** (publicable), **V2.0 workspace** y **V2.1 panel de miniaturas** (implementados en la misma rama) y el **plan aprobado de V2.2+**.
La guía técnica detallada es [`COMMERCIAL_PRESENTATIONS.md`](COMMERCIAL_PRESENTATIONS.md) (contratos, rutas, campos, limitaciones); este archivo es el mapa de estado, decisiones y continuidad.
No existía un handoff aparte de Presentaciones antes de este: el registro previo era esa guía más la memoria del agente; este documento lo consolida.

## 1. Estado técnico

| | |
|---|---|
| Rama | `feat/commercial-presentations-v1` (base `origin/main` `3b996fb`, que no avanzó: sin divergencia) |
| HEAD del PR | el commit que agrega este archivo (solo documentación; ver `git rev-parse HEAD` de la rama) · último commit de código: `a04750b` |
| Commits propios | 19 (V1 y su pulido, copias cerradas, reveal, encabezado, V2.0, V2.1 y este handoff; ver `git log origin/main..HEAD`) |
| PR | contra `main`, **sin merge ni deploy**; URL y descripción en GitHub (copia local con los valores finales: `_handoffs/PRESENTACIONES_V1_20261028/HANDOFF_MAESTRO.md`) |
| CI | lo dispara el push sobre el HEAD; su estado se registra en la copia local y en el PR. **No se da por aprobado mientras esté pendiente.** |
| Suite local pristine sobre el HEAD | corrida en segundo plano al publicar; resultado exacto en la copia local y en el PR. **Pendiente = no aprobada.** |
| Árbol | limpio salvo `human-capital/` y `reporting_cache/` (sin trackear; **ajenos/runtime, no tocar, no commitear**) |
| Stash ajeno | `stash@{0}` «datasphere A04 unrelated — preserve, do NOT mix into CCHH closure»: **preservar** |

Regla de entrega permanente: `origin/main` = producción (push a `main` auto-despliega a DigitalOcean). Nada se mergea ni se despliega sin autorización explícita del owner.

## 2. Arquitectura: archivos y rutas

| Pieza | Archivo |
|---|---|
| Servicio (registro, validadores, persistencia, render context, componentes propios) | `webapp/services/commercial_presentations.py` |
| Router HTTP (R1–R14) | `webapp/routers/commercial_presentations.py` |
| Documento inmersivo (Jinja, 12 `<section>`) | `webapp/templates/presentations/vanture-bdc.html` |
| CSS / JS del documento (compartidos por todas las instancias) | `content_packs/datasphere_core/assets/presentations/vanture-bdc/vanture.css`, `vanture.js` |
| Seed del template y variantes de add-ons | `…/vanture-bdc/vanture-bdc.yaml`, `addons.yaml` (assets en `…/img/`) |
| Catálogo (Presentaciones + Componentes reutilizables) | `webapp/templates/commercial/presentations.html`, `presentation_detail.html` |
| Editor (workspace V2.0 + panel V2.1) | `webapp/templates/commercial/presentation_edit.html` |
| Diálogos de componentes | `webapp/templates/partials/commercial_component_dialogs.html` |
| JS de página (catálogo, diálogos, editor, panel) | `webapp/static/commercial_presentations.js` |
| CSS de página | `webapp/static/commercial.css` (+ `tools.css`: scrollbar del Hub extendido a `html.content-doc` y `.cp-slides`) |
| Guía técnica | `docs/COMMERCIAL_PRESENTATIONS.md` |

**Rutas** (snapshot de rutas = **273**, `tests/route_surface.json`; **no se agregaron rutas en V2**: `thumbs=1` es un parámetro de consulta de R1):

| | Ruta | Permiso |
|---|---|---|
| R1 | `GET /presentaciones/{id}` (`?embed=1`, `&edit=1`, `&thumbs=1`) | sesión + autorizado del Hub (no exige Comercial) |
| R2 | `GET /comercial/presentaciones` | Comercial |
| R3 | `GET /comercial/presentaciones/{id}` | Comercial |
| R4 | `POST …/{id}/copiar` | Comercial |
| R5 | `GET …/{id}/editar` (workspace) | Comercial + poder editar esa instancia |
| R6 | `POST …/{id}` (campos, `removed_slides`, `blocks`) | ídem R5 |
| R7 | `POST …/{id}/eliminar` | responsable o `APP_OWNER`; nunca la semilla |
| R8/R10 | `GET /presentaciones/{id}/logo` · `/qr` | contrato de lectura de R1 |
| R9 | `POST …/{id}/qr` | ídem R6 |
| R11–R14 | `GET /comercial/componentes/vista` · `POST …/duplicar` · `POST …/{id}` · `POST …/{id}/eliminar` | Comercial + visibilidad / responsable o `CONTENT_OWNER` |

Orden de enforcement de R6 (autoritativo): 401 → 403 no Comercial → 404 → 403 no puede editarla (incluye seeds) → 403 origen → 400 → 422 → 409 revisión (sin escribir) → 503 persistencia (con compensación).

## 3. Modelo de datos

**Instancia** (`commercial_strategy_runtime/presentations/<uuid>.json`; el seed `vanture-bdc` NO se guarda: sale del YAML):
`schema_version`, `presentation_id`, `template_id`, `template_version`, `revision`, `title`, `content` (los **206 campos** `VANTURE_FIELDS`, `kind` line|paragraph, solo texto plano), `removed_slides` (ids opcional), `blocks` (desvíos del ocupante por defecto de cada región), `qr_sha`/`logo_sha` (+ archivos `<id>.qr.png`, `<id>.logo.png`), `owner_id`, `created_by_*`, `updated_*`, `copied_from`. Las claves nuevas son **aditivas**: ausente = comportamiento anterior, sin subir `template_version`.
**Componente propio** (`…/presentations/components/<uuid>.json`): `component`, `name`, `description`, `params` (**`null` en un componente cerrado**), `revision`, `owner_id`, `based_on`, auditoría. Privado de su responsable y `CONTENT_OWNER`.
**Snapshot de uso** dentro de `blocks[region]`: `{component, variant: "own:<uuid>@<rev>", params, from:{id,revision,name}}`. Es independiente: editar o borrar el componente no altera lo ya insertado; el render lee **solo** el snapshot.
**Límites:** 200 instancias, 200 componentes (30 por usuario), título ≤120, logo PNG ≤1 MB ≤1,5 MP con alfa.

## 4. Registro de componentes

`COMPONENTS`: `layer-stack` (variantes `bdc-architecture`, `knowledge-core-stack`; **con contrato de parámetros** `validate_layer_stack_params`: textos y tonos editables, estructura fija), `ecosystem-flow` y `logo-marquee` (**cerrados**: contenido fijo del renderer). `REGIONS`: 9 (`s2…s9.body`, `s12.bleed`), 5 nacen vacías. `PARAM_CONTRACTS = {"layer-stack": …}`.
**Duplicar no depende de que el renderer sea editable**: un cerrado se duplica como copia propia con `params: null` (nombre y descripción editables; parámetros enviados → 422). **Copia viva:** no congela el contenido visual ni guarda HTML/CSS/JS; si cambia la definición institucional, cambian la copia y sus usos. Los institucionales son inmutables desde la UI (sin Editar/Eliminar, tampoco para `APP_OWNER`).

## 5. Persistencia, permisos y assets

* Modos `read-only | local | remote` (`DANWOR_PERSISTENCE`); en `remote`, push síncrono a Spaces con compensación (`RemoteOutcomeUnknown`, `DurablePushFailed`). **Prerrequisito de despliegue:** `SPACES_PERSIST_PATHS` debe incluir `commercial_strategy_runtime` y `DANWOR_PERSISTENCE=remote`.
* Permisos (decisiones vigentes del owner): editar = responsable o `CONTENT_OWNER`; `CONTENT_OWNER` edita pero **no elimina** lo ajeno; **`APP_OWNER` no edita contenido ajeno** por ser administrador (sí elimina copias ajenas); componentes propios solo responsable + `CONTENT_OWNER`. Sin roles, variables ni allowlists nuevos en toda la rama.
* Assets compartidos por contenido hash (`css_url`/`js_url` con `?v=`); QR y logo propios por instancia con validador PNG propio (**no hay Pillow** en el proyecto).

## 6. Producto V1 (qué hace)

* **Catálogo** de Presentaciones y **Componentes reutilizables** (colapsado por defecto), mismo contrato de columnas, fecha de liberación `29-10-2026` para lo institucional, responsables «Juan Manuel Melgarejo» / «Danwor» (solo rótulos), sin badge «Institucional».
* **Edición inline** sobre el documento real (206 campos), bloques gobernados, ✕ para quitar/restaurar slides con numeración dinámica (**aprobada, no volver a plantearla**), copia independiente y eliminación.
* **Add-ons:** componentes por región (quitar/restaurar/reemplazar/agregar), QR intercambiable, snapshots, variantes editables de `layer-stack`, **copias cerradas** de Flow y Marquee.
* **Vista previa de componentes** a ancho de referencia 1260 px escalada; scrollbar del Hub en el documento embebido (`html.content-doc`).
* **Portada:** órbita gris suave solo en claro, punch «7 de 10» ×0,83, único glow de fondo el de la portada, encabezado fijo transparente en reposo (oscuro) / 80 % (claro) con fade conjunto de fondo y línea en 1 s (`--cp-topbar-rest`), pie sin la frase bajo los logos y aviso ancho.
* **Reveal definitivo:** contrato del Hub `.content-reveal`/`.is-visible` (IntersectionObserver equivalente al del Copiloto, **una sola vez por visita**), sin `view()` compitiendo; hero, órbitas, conectores, marquee y progreso intactos; edición, previews y `prefers-reduced-motion` siempre visibles; anclas revelan el destino. Mediciones: opacidad 50 % con el elemento a 753 px (Copiloto 772, guía 702; antes 870); 209/209 bloques elegibles dentro de una unidad; 84/84 unidades con entrada perceptible.

## 7. V2.0 y V2.1 (implementados en esta rama)

* **V2.0 workspace** (`cp-workspace`): editor sin `commercial_nav`/handle (grilla de una columna, tarjeta a todo el ancho/alto), barra superior conservada, **encabezado compacto** (`← Volver a Presentaciones` · «Editando» + título + (i) de visibilidad del enlace · Cancelar · «Sin guardar» · Guardar). «Volver» y «Cancelar» comparten la confirmación «Descartar cambios». Catálogo, detalle y lectura intactos.
* **V2.1 panel** (`aside.cp-slides`): lista desde el DOM del editor, número superpuesto en la miniatura, activa dorada / hover blanco, navegación sin rebotes (`navTarget`), **miniaturas reales con UN iframe adicional** (`?embed=1&thumbs=1`, sin JS ni animaciones, 12 slides 1440×810 con paso fijo 930 px), fallback textual (móvil / fallo), `+ Nueva diapositiva` como placeholder deshabilitado. Detalle en la guía técnica.

## 8. Pruebas, evidencia y limitaciones

**Tests focalizados:** `pytest -q tests/webapp/test_commercial_presentations*.py tests/test_route_contract.py` → **655 passed**. Archivos: `…_access`, `_addons`, `_catalog`, `_catalog_polish`, `_closed_components`, `_components`, `_edit_coverage`, `_logo`, `_persistence`, `_render`, `_slides_panel`, `_workspace` (+ contrato de rutas).
**Gates E2E con navegador real** (Playwright; claro/oscuro; 1440/1024/390 px): catálogo 68/68 · eliminar 13/13 · componentes 54/54 · add-ons 84/84 · edición 34/34 · tarjetas 18/18 · detalle 22/22 · copias cerradas 43/43 · pulido 97/97 (servidor limpio) · encabezado 44/44 · pie 16/16 · reveal 75/75 · workspace 97/97 · panel de miniaturas 124/124. Dos gates de conteo/estado (`gate_polish`) dan 95/97 si corren tras otro en el MISMO servidor (estado acumulado: artefacto conocido); con servidor limpio dan 97/97.
Los scripts (no versionados, ~80 KB) se conservaron en `_handoffs/PRESENTACIONES_V1_20261028/gates/` junto a `run_qa_pres.py` (servidor QA con directorio temporal), `fresh.sh` y `bump.sh`; tienen rutas de la sesión original y hay que adaptarlas. Evidencia visual en `_handoffs/PRESENTACIONES_V1_20261028/{componentes,reveal,workspace,…}`.

**Suite completa (pristine = `git archive` del SHA + `pytest -q` sin variables ambientales; PATH con `git` Y Node):**

| SHA | Resultado | Valor |
|---|---|---|
| `5d2a55b` | 1 fallo conocido · 11 435 pasados · 29 omitidos | válida |
| `4a36ec4` | 2 fallos (conocido + `git` ausente del PATH) · 11 356 · 119 omitidos | **inválida** (PATH sin `git`) |
| `73783fe` | 1 fallo conocido · 11 400 · 119 omitidos | **inconclusa**: sin Node en el PATH se omiten ~90 tests dependientes de Node |
| `0b5eafb` | 2 fallos: el conocido + **uno nuevo** `tests/webapp/test_hub_tile_info.py::test_the_affordance_lives_in_shared_css_so_the_primitive_is_hub_reusable` (V2.0 agregó a `commercial.css` una regla sobre `.activity-kpi__info`, primitivo que debe vivir solo en `tools.css`) · 11 502 pasados · 29 omitidos | **válida** (git y Node en el PATH). El fallo nuevo se corrigió en el commit siguiente (quitar la regla); sobre ese SHA no hay corrida local completa: la cubre el CI |

**Fallo conocido, clasificado aparte:** `tests/test_portability_scoring_v2.py::test_the_35_flows_match_the_calibrated_v2_results` — el `catalog_local` git-ignorado de esta máquina tiene 34 flujos y el test espera 35. Ambiental, preexistente, **no relacionado con Presentaciones**; no se modificó el test ni `catalog_local`. Cualquier otro fallo es nuevo y atribuible a la rama hasta demostrar lo contrario.

**Lecciones de QA (costaron tiempo):** (1) Playwright headless usa `--hide-scrollbars`: los gates del panel se corren con `ignore_default_args=["--hide-scrollbars"]` o no se ven las barras reales; (2) los listeners zombi de Windows bloquean puertos tras matar un servidor QA: `bump.sh` cambia de puerto; (3) los gates sensibles al conteo (catálogo, eliminar, componentes) necesitan servidor **limpio**; (4) heredocs con comillas rompen el shell: usar la herramienta Write; los parches preservan CRLF; (5) `scrollTo` programático respeta `scroll-behavior: smooth`: usar `behavior: 'instant'` al medir; (6) medir el CSS real, no suponer anchos (barras de scroll); (7) **los tests focalizados de Presentaciones no cubren contratos transversales del Hub** (p. ej. `test_hub_tile_info`: ninguna vertical estila el primitivo `(i)`): tras tocar CSS/JS compartido o `commercial.css`, correr además todos los tests que inspeccionan esos archivos (`grep -rl "commercial.css\|tools.css" tests`); en el checkout de trabajo fallan solo por archivos locales git-ignorados `test_commercial_strategy::test_next_steps_has_sixteen_rows` (`strategy.yaml` local de 14 tareas) y `test_datasphere_assessment::test_form_fields_labels_placeholders_and_margins`; en una exportación limpia pasan.

## 9. Decisiones pendientes (NO implementadas)

1. **Edición editorial institucional por `CONTENT_OWNER` mediante overrides** (corregir textos comerciales de la presentación institucional sin tocar código ni crear copia). Discovery hecho, sin implementar: reutilizar edición inline + revisiones + persistencia con una capa de overrides sobre el seed (clave por campo, revisión propia, restaurar al original, reconciliación con el seed en cada deploy), permiso exacto solo `CONTENT_OWNER` (no `APP_OWNER`), definición visual intacta, trazabilidad y restauración.
2. **Edición estructural de cajas/capas** de `layer-stack`: hoy solo textos y tonos (`_stack_shape` fija la estructura).
3. **Sharing de componentes propios:** hoy privados (responsable + `CONTENT_OWNER`).
4. **Enlaces compartidos con tema inicial (`?theme=dark|light`)** — discovery hecho (con navegador real), sin implementar. Estado actual: R1 **no interpreta `theme`** (el servidor rinde `<html class="content-theme-…">` desde la cookie `dw-content-theme`: `routers/commercial_presentations.py`, `presentation_shared_view`); `?theme=dark` abierto por un destinatario claro se ignora. Precedencia medida: clase del servidor (cookie) **>** `localStorage` (`hub_theme.js::current()` prefiere la clase ya presente), por lo que el arranque del cliente (`vanture.js` → `dwTheme.apply(current())`) solo reaplica lo que decidió el servidor y un tema impuesto en el servidor sobreviviría sin flash. Otros actores: el selector del documento (`set()`) **persiste** la preferencia global (cookie + `localStorage`); el mensaje `danwor-theme` del Hub solo **aplica** (no persiste); el evento `storage` de otra pestaña **sí** cambia una pestaña ya abierta. El enlace se genera en `_share_path()` (`/presentaciones/{id}`) y «Copiar enlace» lo completa con `location.origin` en `commercial_presentations.js` (catálogo y detalle). Precedente en el Hub: Academy ya arma sus enlaces standalone con `theme=<tema actual>` y las guías aceptan `?theme=` inicial.
   **Corrección mínima propuesta (sin infraestructura nueva, reutilizando el mecanismo):** (a) R1 acepta `theme` solo si es `light|dark` **y la página no es un embed** (los iframes del Hub deben seguir la preferencia del Hub); un valor inválido se ignora (cae a la cookie); no toca cookie ni `localStorage`; (b) «Copiar enlace» agrega `?theme=<tema vigente del autor>`; (c) pruebas: precedencia con recarga, navegación directa, destinatario con el tema contrario, selector, inválidos, embed intacto. Verificado con una simulación en navegador real (el servidor rinde `<html>` con el tema del parámetro): cookie y preferencia del destinatario quedan intactas al cargar, recarga y URL conservan el tema, el selector sigue funcionando. Decisiones del owner: si el selector debe seguir **persistiendo** la preferencia global (hoy sí) o ser solo de sesión mientras hay override; y si ignorar el evento `storage` de otras pestañas en un documento con override (hoy lo cambia). Galaxia/Classic es una dimensión independiente (dato de la instancia, no de la URL). Sonda reutilizable: `_handoffs/PRESENTACIONES_V1_20261028/gates/theme_discovery.py`.

## 10. Presentaciones V2 — plan aprobado

Orden aprobado: **workspace ✅ → panel con miniaturas ✅ → Classic con presets → imagen propia → nueva diapositiva.**

* **V2.2 Galaxia / Classic.** `style: "galaxia"|"classic"` (enum, ausente = galaxia) en la instancia; `data-ps-style` en `<html>`, **distinto** del tema claro/oscuro (`content-theme-*`). Classic = tokens + portada editorial en `vanture.css`, sin duplicar Vanture ni sus 12 slides; los componentes se re-tematizan por tokens (30 de 378 estilos inline tienen color fijo: riesgo). Portada con **presets institucionales** (`assets/presentations/vanture-bdc/covers/` + registro YAML). El estilo se elige al copiar y en el encabezado del editor; la semilla es siempre Galaxia. Gates: paridad byte a byte de Galaxia, add-ons y componentes en ambos estilos, copias heredan estilo.
* **V2.3 Portada propia.** `cover: {kind:"preset"|"upload", …}`; subida con el patrón del QR (GET sirve, POST sube con compensación); **solo PNG** (`validate_png` con límites propios: ≤3 MB, ≤4 MP, sin alfa obligatorio); JPEG/WebP exigirían validador nuevo (no hay Pillow).
* **V2.4 Nueva diapositiva gobernada.** Decisión D5: **no** convertir las 12 slides en macros genéricas; evaluar un **manifiesto ordenado** (`slide_order`/`custom_slides`) que preserve el render institucional e intercale layouts nuevos (inicial `title-body`: kicker numerado, título, descripción, región de add-ons). Regiones dinámicas `x<id>.body`; `NUMBERED_SLIDES` → orden por instancia (`render_context` ya calcula `slide_no/next_of/nav_of`); riel, `nextlink` (22 condicionales + 10 hardcodeados hoy) y anclas desde el manifiesto; quitar/restaurar consistente con las incorporadas; copia hereda. Clave `slides` en R6. Es el cambio de mayor riesgo.
* **Compatibilidad:** claves aditivas, instancias V1 cargan sin migración; contratos pinneados que se tocan: `NUMBERED_SLIDES`, `REGIONS`, snapshot de rutas; copias y persistencia remota deben heredar `style`, `cover` y slides.

## 11. Reglas permanentes del owner

No push/PR/merge/deploy sin autorización explícita · REUSE → EXTEND MÍNIMAMENTE → CREATE SOLO SI NO EXISTE · pantallas con primitivos del Hub, nunca CSS propio · verificar Git y trabajo ajeno antes de modificar; `git add` con paths explícitos (nunca `-A`) · un solo directorio de desarrollo, sin worktrees salvo autorización · no declarar verde lo que falló; el fallo conocido de portabilidad se clasifica aparte · evidencia visual en claro/oscuro y desktop/mobile.

## 12. Riesgos y bloqueantes reales

* CI y la corrida pristine del HEAD: ver sección 1 (si alguna sigue pendiente al leer esto, no está aprobada).
* Despliegue: `SPACES_PERSIST_PATHS` + `DANWOR_PERSISTENCE=remote` antes de producción.
* `gh` no está instalado en esta máquina: el PR se crea por API/URL; el seguimiento de CI es por las herramientas del escritorio.
* V2.4 (nueva diapositiva) toca contratos pinneados: exige gate de paridad byte a byte de Galaxia.

## 13. Primer prompt recomendado para el nuevo agente

> Retomás **Presentaciones (Gestión Comercial)** de Danwor Hub. Leé primero `docs/COMMERCIAL_PRESENTATIONS_HANDOFF.md` y luego `docs/COMMERCIAL_PRESENTATIONS.md`. Verificá `git status`, la rama `feat/commercial-presentations-v1`, el estado del PR y del CI; preservá íntegramente `human-capital/`, `reporting_cache/` y los stashes. **No hagas push, merge ni deploy sin mi autorización explícita.** Primera tarea: confirmar el estado del PR y del CI sobre el HEAD y, si hay una regresión atribuible a la rama, corregir solo lo necesario. Después, y solo con mi GO, implementá **V2.2 (estilo Galaxia/Classic con portada por presets)** siguiendo la sección 10 del handoff: REUSE → EXTEND MÍNIMAMENTE, sin duplicar Vanture, preservando Galaxia byte a byte, con tests focalizados, E2E en claro/oscuro y desktop/mobile (barras de scroll reales) y un commit local separado.
