# HANDOFF — Funcionalidad "Catálogos Danwor" del Copiloto Metodológico Danwor

> **Documento de traspaso compartido entre asistentes (Claude ↔ Codex/GPT).**
> Ver "Protocolo de handoff" y "Bitácora" al final. Antes de trabajar: leé la
> bitácora para saber en qué estado quedó todo y quién tocó qué último.
>
> **📍 Estado al 2026-07-24 (Codex, última revisión):** la funcionalidad dejó de ser
> solo "Catálogo CDS". Hoy conviven catálogos determinísticos para **dominios/CSN**,
> **código CDS/API**, **fuentes de datos/conexiones/espacios** y **seguridad/DAC**.
> El panel admin ya habla de **Catálogos técnicos**. La regeneración sigue siendo
> piloto/local, incremental por manifest, y preserva curaduría manual del senior.
> Última línea funcional validada: seguridad DAC con tile clickeable, criterios,
> resumen cuando exista tag manual, detalle central y linaje consumidor real.

Ya conocés el marco metodológico Danwor y la arquitectura del copiloto (FastAPI +
Jinja2 SSR + HTMX + Alpine, pack activo `datasphere_core`). Este archivo documenta
la familia de **Catálogos Danwor**: contenido técnico generado desde evidencias
crudas y consumido por el copiloto como fuente determinística.

## ⚠️ Restricción firme: PILOTO / NO-PUSH
Piloto en localhost; **NO se pushea a producción** hasta nueva orden. Al commitear,
EXCLUIR explícitamente (nunca `git add -A`):
- `tools/catalog/` (generador, servicio, este HANDOFF)
- Cambios de catálogo en `webapp/main.py`, `webapp/services/{assistant_service,admin_tools,content_service}.py`
- `webapp/templates/partials/catalog_*.html`, `webapp/templates/catalog_flow.html`, `webapp/templates/catalog_cds.html`, tab Catálogo en `module.html`, botón en `partials/admin_panel.html`
- Datos runtime `catalog_local/APIs/`, `catalog_inbound/APIs/` (código CDS, git-excluidos)
- `webapp/static/tools.css` (`.catalog-*`/`.ln-*`/`.lineage-*`), `webapp/static/app.js` (handlers del catálogo)
- Datos runtime git-excluidos/efímeros: `catalog_local/`, `catalog_inbound/`
- Ninguna key (OpenAI/Graph) se pushea.

## Qué hace
Genera y consume catálogos en Markdown a partir de evidencias técnicas crudas:
exports CSN/CDS, código CDS/API, espacios, conexiones y seguridad/DAC. El objetivo
es que el copiloto priorice activos internos determinísticos antes de caer en IA:
dominios reutilizables, fuentes de datos disponibles por cliente, configuración de
espacios/conexiones, artefactos DAC y vistas consumidoras. Dos consumos: (a)
asistente (grounding), (b) pestaña **Catálogo** determinística (sin IA), con dropdown
contextual y detalle en el panel central.

## Arquitectura
- **Generador** `tools/catalog/generate_catalog.py`: lee `<Cliente>/<Dominio>/*.json` (+`.txt` SQL preview), reconstruye linaje, infiere capa/artefacto/uso §28 (módulo 04), evalúa semáforo, emite `flujo_<dominio>.md` + `vistas/<NOMBRE>.md`. Frontmatter partido `auto:` (regenerable) / `manual:` (senior; se preserva). Entrypoints: `process_folder`, `generate_all`, `fetch_csn_folder`.
- **Consumo** `tools/catalog/catalog_service.py`: `load_catalog` → `CatalogEntry`; `search_catalog`, `domains`, `flows_for_domain`, `find_flow`, `format_catalog_for_prompt`.
- **Webapp** `webapp/main.py`: `_catalog_entries`/`_catalog_domains` (releen fresco, sin cache); endpoints `GET /catalog/domain`, `GET /catalog/flow?cliente&dominio&pack&assoc&frag`, `GET /catalog/cds?name&cliente&dominio&pack`, `POST /admin/catalog/generate`. Detalle: `_catalog_flow_view(entry, show_assoc, pack)` → `(sections, lineage_svg)`; `_catalog_lineage_svg(nodes, show_assoc)` → `(html, keep)`; `_catalog_layer_index`, `_CATALOG_LAYERS`.
- **Código CDS (extractores)** `webapp/main.py`: `_catalog_apis_dir()` (`catalog_local/APIs`), `_cds_name_from_origen("ABAP_CDS.X$E")→"X"`, `_catalog_cds_link(origen)` (nombre solo si `APIs/<X>.md` existe, si no ""), `_catalog_cds_view(name)`→`(meta,title,intro,sections)`. Botón inyectado en `_catalog_flow_view`. Generador: `fetch_csn_folder` baja `.md`; `_sync_apis(inbound,out)` copia `APIs/` (o `CDS/`) a `catalog_local/APIs/` (lo llama `generate_all`).
- **Grounding** `assistant_service._catalog_grounding()`; **botón admin** `admin_tools.run_catalog_generation(graph_token)`.
- **Templates** `partials/`: `catalog_tab.html`, `catalog_flows.html`, `catalog_flow_content.html`, `catalog_lineage.html`, `catalog_cds_content.html`; páginas completas `catalog_flow.html`, `catalog_cds.html`.
- **Render**: el detalle reusa el pipeline de módulos → `content_service.render_sections(body, pack, stem, base)`. Contenido en markdown; render lo resuelve el copiloto (no se replica lógica visual).
- **Datos**: `catalog_local/` (salida, git-excluida, efímera), `catalog_inbound/` (STUBS de dev, NO fieles a la real), SharePoint CSN (`config.SHAREPOINT_CATALOG_CSN_URL`).

## Reglas de diseño (NO romper — todo dinámico, cero nombres hardcodeados)
1. **Dominio = del CONTENIDO, no de la carpeta.** CSN real es 1-nivel → dominio "General" → se INFIERE: `_infer_domain()` matchea labels/nombres de artefactos de consumo (cubo/hecho) vs `_DOMAIN_VOCAB` (Stock/Inventario unificados; Ventas; Compras; Finanzas; Producción). Override `manual.dominio`. `catalog_service` usa `manual.dominio or auto.dominio`.
2. **Capa** (`_capa_of`/`_catalog_layer_index`): (a) prefijo §28 dígito 1-4; (b) `origen_remoto`→Ingesta; (c) `modeling_pattern`/`data_category`: DIMENSION→Armonización, FACT→Propagación, CUBE→Consumo; (d) prefijo AM/EM→Consumo, V*→Propagación; (e) default **Ingesta** (NO Consumo).
3. **Artefacto (tipo)** = `OTROS[prefijo]` o `kind_label`; patrón crudo va en fila "Patrón de modelado".
4. **Directo vs Asociación**: entidad ANALÍTICA (hecho/dim/cubo) → directo = solo su base transformacional; DEGRADA a asociación dims/textos/crudos (solo degrada, nunca promueve). Vista relacional (DATA_STRUCTURE) → todo FROM/JOIN directo. Directo se extrae SOLO de `SELECT.from`/JOIN (`_from_sources`), no columnas/mixins. En generador (`load_folder`) y espejado en runtime (`_catalog_flow_view._split_up`).
5. **Diagrama SVG** (`_catalog_lineage_svg`): se ancla en el **OUTPUT** del flujo (capa más profunda CON aristas directas). `keep` = **ancestros DIRIGIDOS** del output (directo; + asociación si ON) → excluye ramas laterales y sistema aislado (SAP.TIME, DAC…). Devuelve `(html, keep)`. Layout **ALAP** (columna = distancia al sink); cajas ancho-por-contenido; nodos = anclas `<a href="#slug">`.
6. **Toggle "Incluir asociaciones"** (global): OFF = espina directa; ON = linaje completo del output. Afecta el **diagrama Y las secciones** (con OFF no se muestra el detalle de artefactos fuera del core). Re-renderiza el **detalle completo** vía HTMX (`?assoc=0|1`, target `#catalog-detail-slot`).
7. **Secciones**: título `{dw-black}Descripción · {dw-gold}NOMBRE` (o `NOMBRE · NOMBRE` sin descripción); tablas Resumen + Medidas + Atributos (Tipo/Long./Clave 🔑/Relación→dim). Slug = `slugify(plano sin tonos)`; links y nodos apuntan al mismo slug; scroll a sección con handler delegado en `app.js`.
8. **UX detalle**: carga en slot `#catalog-detail-slot` dentro de `#content`; se oculta el módulo con clase `catalog-detail-open` (no se destruye). "← Volver" = `dwCatalogBack()`. Overscroll "scroll para continuar" anulado con el detalle abierto.
9. **Botón "Abrir CDS View"** (auto-vínculo robusto, sin hardcode): origen `ABAP_CDS.<X>$…` → `<X>`; el botón se renderiza **solo si `APIs/<X>.md` existe** (sin archivo = sin botón, sin link muerto ni error de consola). Reusa `.support-link` (theme-aware, sin CSS nuevo); `hx-get="/catalog/cds?…"` → swap en `#catalog-detail-slot`; "← Volver al flujo" re-abre el flujo. El `.md` de código: frontmatter `cds_view`/`tabla_base`/`extraccion` + `# Título` + `_intro_` + `## Código` ` ```abap `. **Tolerancia**: `_catalog_cds_view` auto-cierra un fence impar (si el autor olvidó el ` ``` ` de cierre). Biblioteca **compartida y agnóstica de cliente** en `APIs/` (una copia por extractor).

## Operativo (importante)
- Server SIN `--reload` (`.claude/launch.json`, uvicorn `webapp.main:app` :8082). **Cambios `.py` → reiniciar server** (css/js/templates y `.md` de `catalog_local` recargan solos; `_catalog_entries` relee fresco). La acción admin de regeneración recarga `generate_catalog` antes de ejecutar, pero cambios en `admin_tools.py` o `webapp/main.py` siguen requiriendo reinicio. **Reiniciar el server desloguea la sesión del preview.**
- Regenerar: Herramientas de gestión → modo diagnóstico ON → botón "Regenerar catálogo" (logueado, manda `dw-graph-token`). Lee SharePoint CSN o, sin token, `catalog_inbound/`. Token Graph de corta vida: **HTTP 401 = vencido → re-loguear**, regenerar enseguida.
- `generate_all` usa manifest local incremental (`catalog_local/.catalog_manifest.json`): regenera solo flujos con fuentes cambiadas y fuerza full regen si cambia el hash del generador. NO limpia stale → limpiar `catalog_local` a mano si cambia la estructura o desaparecen flujos.
- Tests: `.venv/Scripts/python.exe -m pytest -q` (139 verdes). Usar `PYTHONIOENCODING=utf-8` para prints con emoji (Windows).
- Verificar en browser: module 04 → pestaña Catálogo → dominio "Stock / Inventario" → ficha de cliente → detalle (diagrama + toggle + secciones).

## Pendientes / ideas (charladas, sin implementar)
1. **Ancla explícita en la UI**: resaltar/nombrar el nodo output en el diagrama (hoy implícita = output). Ofrecido, sin hacer.
2. **Re-anclar el linaje en un artefacto elegido** (hoy fijo en output): selector "Ver desde: [artefacto]" o click-en-nodo. El usuario dijo que la vista actual alcanza por ahora.
3. **Membership de dataflows** (`DF_*` → target): sin resolver → fuentes paralelas (p. ej. `2VS_MBEW_R3_S4`, desvío 🔴 doble fuente ECC/S4 en Loma) quedan dead-end y no se conectan al linaje. Mejora en `load_folder`.
4. Persistencia durable de `catalog_local` en DigitalOcean (fs efímera); pasar token Graph de sesión al consumo SharePoint del asistente; segmentación/profundidad para flujos grandes (Loma 150 artefactos).

---

## Protocolo de handoff
Este archivo lo mantienen los dos asistentes. Cuando termines una sesión de trabajo:
1. Agregá una entrada al final de la **Bitácora** (fecha, quién, qué cambiaste, estado, tests).
2. Si cambiaste una regla de diseño o la arquitectura, actualizá la sección de arriba correspondiente.
3. Dejá anotado lo que quedó a medias o pendiente para que el otro retome sin fricción.

## Bitácora

### 2026-07-21 — Claude (traspaso a Codex hasta el viernes 4PM)
**Estado: todo lo de abajo funciona y está verificado en los 3 clientes reales (Biogenesis CDS 🟢, Danone BW Bridge 🟡, Loma ABAP 🟠). 139 tests verdes. Server corriendo :8082 (recién reiniciado → re-login).**

Implementado y validado con el usuario (Joe) en esta sesión:
- Detalle del flujo renderizado con el pipeline de módulos (hero + diagrama + secciones con tablas), no lógica visual replicada.
- Dominio inferido del contenido (Stock/Inventario unificados); dropdown ya no muestra "General".
- Capa por `modeling_pattern` (dimensión→Armonización, hecho→Propagación, cubo→Consumo); artefacto = tipo §28 real; patrón crudo en su fila.
- Tabla de **campos** por artefacto (Campo/Descripción/Tipo/Long./Clave 🔑/Medida ∑ o Relación→dim), desde el CSN.
- Headers de sección `{dw-black}Descripción · {dw-gold}NOMBRE`.
- **Diagrama de linaje SVG**: layout ALAP (columna = distancia al output, no por capa), cajas ancho-por-contenido, anclado en el OUTPUT del flujo, `keep` = ancestros dirigidos. Directo vs asociación (solo degradar). Coincide con Datasphere: OFF = espina directa (Biogenesis 8, Loma 19, Danone 6), ON = linaje completo del output (Biogenesis 35).
- **Toggle "Incluir asociaciones"** afecta diagrama **y** secciones (re-render del detalle completo vía HTMX). Última mejora aplicada y verificada (OFF=8 secciones, ON=35 en Biogenesis).
- Fix de nodos flotantes en Loma (sistema SAP.TIME/DAC y rama paralela 2VS_MBEW_R3_S4 fuera del OFF).

Aprendizaje clave confirmado con Joe: **el linaje de Datasphere depende del artefacto-ancla**; para un flujo, el ancla correcta es su OUTPUT/sink (modelo analítico), y desde ahí el linaje aguas arriba es el flujo completo.

Lo último que quedó en el aire (no bloqueante): Joe preguntó cómo elegir el artefacto-ancla dinámicamente (re-anclar) — quedó DIFERIDO ("la vista actual alcanza por ahora"). Ver Pendientes #1/#2. También Pendiente #3 (dataflows) explica por qué 2VS_MBEW_R3_S4 no se conecta.

Último fix de la sesión: los números del nodo del SVG y de la sección estaban
desincronizados (nodo numeraba por orden completo, sección por orden filtrado por el
toggle). Se extrajo `_catalog_flow_keep(nodes, show_assoc)`; `_catalog_lineage_svg`
ahora recibe `keep` y devuelve solo html; `_catalog_flow_view` numera nodos Y
secciones por la posición en `shown_order` (orden visible) → coinciden y se adaptan
al toggle. Verificado (Biogenesis OFF=8, ON=35, cero mismatches). Server 3fa4e04e.

Polish visual (para igualar 100% a los módulos):
- `.catalog-lineage` ahora espeja `.section-index` ("EN ESTE MÓDULO"): `padding:28px 0 36px` + `border-bottom:1px solid var(--line)` (la "línea de cierre" que faltaba) + figcaption `.72rem` uppercase soft (no mono). La línea de apertura ya la da el `border-bottom` del `.module-hero`.
- Hero: combiné el subtítulo redundante — antes `Mirada Danwor · plena` (hero-meta) + `🟢 Referencia Danwor — reutilizable…` (línea aparte). Ahora una sola línea en el meta: `<span>{icon} {apto}</span><span>{n_art} artefactos</span>` → "🟢 Referencia Danwor — reutilizable (atender notas menores) · 35 artefactos". Se quitó `catalog-detail__apto`.
Solo template+CSS (no requiere reiniciar server ni regenerar). Verificado por render del partial. 139 verdes. (Joe estimó la feature ~85%.)
- La "línea de cierre" del linaje: CAUSA REAL (tras dos intentos fallidos) = COLOR, no caché ni `<figure>`. En light theme la app NO ajusta `--line` (queda casi invisible sobre #f7f7f4); las líneas visibles (hero/section-index/content-section) usan `.content-theme-light … { border-color:#d9d9d3 }` (layout.css:116). Mi borde usaba `var(--line)` y no está en esa lista → invisible en claro. FIX: separador como elemento en el template `<div class="catalog-flow-sep" style="border-top:1px solid rgba(128,128,128,.32)">` (gris semitransparente que se ve en AMBOS temas), inline y en el template (HTMX-fresh) → se ve al re-abrir el detalle sin recargar CSS. (Si se quisiera perfección de tema: usar el override de border-color por `.content-theme-light`/dark en CSS, pero requiere que recargue tools.css.)

### 2026-07-22 — Claude (botón "Abrir CDS View" + biblioteca APIs/)
**Estado: implementado y verificado a nivel Python/render (el browser está deslogueado y no puedo ingresar credenciales → no hay screenshot; falta que Joe lo clickee logueado). Server :8082 reiniciado (serverId 9498ad9c), sin errores de arranque.**

Nueva feature: acceso al **código de las CDS views (extractores corporativos)** desde el detalle del flujo, con auto-vínculo.
- **Ubicación en SharePoint**: Joe subió los `.md` de código a una carpeta compartida a nivel raíz de CSN llamada **`APIs/`** (no `CDS/`), agnóstica de cliente (una copia por extractor). `_sync_apis` en el generador tolera `APIs/` **o** `CDS/`.
- **Estructura del `.md`** (frontmatter liviano + código): `cds_view`, `tabla_base`, `extraccion` + `# Título` + `_intro_` + `## Código` + fence ` ```abap `. OJO: el ejemplo de Joe vino **sin cerrar el fence** (un solo ` ``` `) → agregué auto-cierre tolerante en `_catalog_cds_view` (`if body.count("```") % 2: body += "```"`) para que igual renderice como bloque de código. Ideal que el `.md` traiga el cierre, pero ya no rompe si falta.
- **Auto-vínculo (robusto, sin hardcode)**: `_cds_name_from_origen("ABAP_CDS.ZCDS_DSP_DIM_MCH1$E") → "ZCDS_DSP_DIM_MCH1"` (saca prefijo `ABAP_CDS.` y sufijo `$…`). `_catalog_cds_link(origen)` devuelve el nombre **solo si `APIs/<X>.md` existe** → si no hay archivo, el botón NO se renderiza (nada de link muerto ni error de consola). Solo aplica a orígenes `ABAP_CDS.*` (ABAPTABLES/BW Bridge/sin origen → sin botón, correcto).
- **UI**: en `_catalog_flow_view` se inyecta, tras el header de la sección del artefacto, un `<div class="support-links catalog-cds-launch"><a class="support-link catalog-cds-link" hx-get="/catalog/cds?name&cliente&dominio&pack" hx-target="#catalog-detail-slot" hx-swap="innerHTML" @click.prevent>Abrir CDS View ‹/›</a></div>`. Reusa el estilo `.support-link` existente (ya theme-aware, layout.css:130) → **sin CSS nuevo**.
- **Endpoint** `GET /catalog/cds?name&cliente&dominio&pack&(HX)`: renderiza `APIs/<name>.md` con `render_sections` (mismo pipeline). Templates nuevos `partials/catalog_cds_content.html` (hero: título + intro + `cds_view`/`tabla_base`/`extraccion`; "← Volver al flujo" re-abre el flujo por HTMX con cliente+dominio) y `catalog_cds.html` (shell, navegación directa). Guardas: traversal (`..`/`/`), archivo ausente → 404.
- **Generador**: `fetch_csn_folder` ahora baja también `.md` (antes solo `.json`/`.txt`); `generate_all` llama `_sync_apis(inbound, out)` → copia `APIs/`(o `CDS/`)`*.md` a `catalog_local/APIs/`. Al regenerar desde SharePoint con token, los 4 extractores de Biogenesis quedarán disponibles y los demás artefactos `ABAP_CDS.*` con archivo tendrán su botón automáticamente.

Verificado (Python/TestClient): derivación de nombre OK; botón aparece **solo** en `ZCDS_DSP_DIM_MCH1` (único `.md` local) y no en el resto; CDS view renderiza `<pre><code class="language-abap">` (tras el auto-cierre); partial y shell renderizan sin error; `/catalog/cds` 307→login sin sesión (routing OK). **Pendiente de Joe**: regenerar desde SharePoint (trae los 4 APIs) y clickear el botón logueado para el OK visual. Localmente copié el ejemplo a `catalog_local/APIs/` y `catalog_inbound/APIs/` para test.

Codex: cuando avances, dejá tu entrada acá abajo con lo que hiciste, para que Claude retome el viernes.
### 2026-07-22 — Codex (regeneración incremental del catálogo)
**Estado: implementado y validado con prueba temporal; no se ejecutó regeneración real sobre datos del usuario en esta sesión.**

Cambios:
- `tools/catalog/generate_catalog.py`: `generate_all()` ahora usa un manifest local (`catalog_local/.catalog_manifest.json`) por flujo/dominio. Calcula firma de los `.json`/`.txt` fuente y evita reprocesar carpetas sin cambios si la salida esperada existe.
- El manifest guarda también el SHA-256 del propio generador. Si cambia `generate_catalog.py`, se invalida el cache incremental y se regeneran todos los flujos para no conservar markdown viejo con reglas metodológicas anteriores.
- `_sync_apis()` sigue corriendo siempre; es barato y mantiene sincronizados los `.md` de APIs/CDS compartidos.
- `webapp/services/admin_tools.py` ya recarga `generate_catalog` antes de regenerar, así el botón admin toma cambios del generador sin depender de un import cacheado.

Alcance / límites de esta fase:
- La descarga desde SharePoint todavía es completa; esta fase optimiza la regeneración local del catálogo, no el fetch Graph.
- No hay limpieza automática de salidas stale si desaparece un flujo o cambia la estructura de carpetas; sigue siendo manual por ahora.
- Si se quiere una fase 2, el siguiente paso natural es usar metadata Graph (`eTag`/`lastModifiedDateTime`) para evitar descargar archivos sin cambios desde SharePoint.

Tests:
- `py_compile` OK para `tools/catalog/generate_catalog.py` y `webapp/services/admin_tools.py`.
- Prueba temporal OK: primera ejecución `1 flujo generado`, segunda ejecución `0 generados / 1 sin cambios` usando carpetas temporales y `process_folder` simulado.


### 2026-07-22 — Codex (catálogo piloto de espacios y conexiones)
**Estado: implementado y validado con ejemplos locales de Biogenesis; no se pushe? ni se commite?.**

Cambios:
- Nuevo `tools/catalog/generate_space_catalog.py`: lee exports JSON de `Espacios/` y `Conexiones/` por cliente y genera markdown operativo en `catalog_local/arquitectura/<Cliente>/`:
  - `arquitectura_conectividad.md` como ?ndice de espacios.
  - `espacios/<SPACE>.md` con propiedades del espacio, conexiones y lectura Danwor.
  - `conexiones/<SPACE>__<CONN>.md` con detalle t?cnico, `ConnectionInfo` y capacidades declaradas.
- `tools/catalog/generate_catalog.py`: el generador CSN ignora carpetas `APIs/`, `CDS/`, `Espacios/` y `Conexiones/` para no confundir JSON operativos con flujos CSN.
- `webapp/services/admin_tools.py`: el bot?n de regeneraci?n del cat?logo ejecuta primero el generador CDS/CSN y luego el generador de espacios/conexiones. Si este ?ltimo falla, reporta `[WARN]` y no bloquea el cat?logo CDS.

Validaci?n:
- Probado contra `E:\Joe\Descargas\Datasphere\Biogenesis`: `2 espacio(s), 13 conexi?n(es)`.
- Preview temporal OK para `CALIDAD`, `DESARROLLO` y conexi?n `CALIDAD__BW_DS`.
- Encoding revisado: sin `?` rotos en textos humanos de los archivos tocados.
- Tests: `.venv/Scripts/python.exe -m pytest -q` ? 139 passed.

Pendiente sugerido:
- Revisar con Joe si el cat?logo de espacios/conexiones debe tener una vista HTMX propia o quedar inicialmente como markdown generado navegable desde el cat?logo/gesti?n.
- En una fase posterior, sumar incrementalidad espec?fica para `Espacios/Conexiones` si el volumen crece.


### 2026-07-22 — Codex (manifest incremental para espacios/conexiones)
**Estado: implementado y validado; no se pushe? ni se commite?.**

Cambios:
- `tools/catalog/generate_space_catalog.py` ahora usa el mismo `catalog_local/.catalog_manifest.json` del cat?logo principal.
- El manifest preserva bloques separados:
  - `flows` / `generator_sha256` para CSN/CDS.
  - `spaces_connections` / `spaces_generator_sha256` para `Espacios/Conexiones`.
- `tools/catalog/generate_catalog.py` ahora preserva claves ajenas al guardar su manifest, para no borrar el bloque de espacios/conexiones cuando corre el generador CSN.
- La firma de espacios/conexiones considera archivos `.json` bajo `Espacios/` y `Conexiones/` por cliente. Si no cambian y las salidas existen, no reescribe los `.md`.

Validaci?n:
- Prueba local con `E:\Joe\Descargas\Datasphere\Biogenesis`: primera corrida gener? `1 cliente`, segunda corrida report? `0 cliente(s), 1 sin cambios`.
- `py_compile` OK.
- Tests: `.venv/Scripts/python.exe -m pytest -q` ? 139 passed.


### 2026-07-23 - Codex (catalogo conexiones: semaforo, features y alertas)
**Estado: implementado en piloto local; no se commiteo ni se pusheo.**

Cambios principales:
- `tools/catalog/catalog_service.py`: agrupacion de conexiones por cliente/tipo con escenario principal y alertas para escenarios no sincronizados.
- Semaforo de tiles de conexion:
  - `plena`: escenario consistente y todas las features posibles disponibles.
  - `buena`: escenario consistente pero con features posibles no disponibles.
  - `parcial`: inconsistencias entre espacios/escenarios o conectividad operativa insuficiente.
- Features ahora distinguen posible vs disponible:
  - Data Flows / Replication Flows requieren Cloud Connector para mostrarse habilitadas.
  - Remote Tables requiere DP Agent, salvo provisioning distinto de `dpAgent`.
- `webapp/templates/partials/catalog_connections.html`: agrega `Ver features`, `Ver conexiones` y `Ver alertas` dentro del tile.
- `webapp/static/tools.css`: estilos para chips de features, pills DP Agent/Cloud Connector y alertas.
- Correccion de mojibake introducido en catalogo de conexiones (`conexion`, `Si`, separadores) sin tocar el contenido historico del handoff.

Validaciones:
- `py_compile` OK para catalog_service.py y servicios web relacionados.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 151 warnings.

Notas operativas:
- El server corre sin reload; para ver cambios Python hay que reiniciar el server.
- Mantener PILOTO local: no push a produccion hasta nueva orden.
- No commitear runtime/data: `catalog_local/`, `catalog_inbound/`, reportes/logs/case_library/feedback si aplican.

Pendiente visual/funcional para revisar con Joe:
- Validar en UI modulo 01 > Catalogo > conexiones: MSSQL debe consolidar el escenario principal y mostrar alerta para la conexion desalineada.
- Validar SAP S/4HANA OP: debe conservar features disponibles y businessName consolidado.
- Discutir si conviene refactor posterior para que `{{support}}` del catalogo use exactamente el renderer core en vez de aproximacion visual.

### 2026-07-23 - Codex (catalogo conexiones: detalle central con SVG)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `tools/catalog/catalog_service.py`: nuevo contrato `connection_client_detail(...)` y helper publico `connection_feature_states(...)` para reutilizar la logica ya validada de clientes/tipos/features sin duplicar criterios en la vista.
- `webapp/main.py`: nuevo endpoint HTMX `/catalog/connections/detail` y renderer central para conexiones:
  - Hero `Catalogo Danwor - <cliente>` / `Conexiones & Espacios`.
  - SVG `Arquitectura & Conectividad` con origenes/conexiones numerados y anclas a sus secciones.
  - Toggle `Incluir todas las conexiones` para mostrar conexiones no seleccionadas en tono atenuado.
  - Secciones tecnicas generadas con `render_sections(...)`, heredando headers/tablas del core.
- `webapp/templates/partials/catalog_connection_content.html`: nueva plantilla de detalle central.
- `webapp/templates/partials/catalog_connections.html`: los tiles de conexion ahora abren el detalle central en `#catalog-detail-slot`; los `details` internos frenan propagacion para no abrir el tile por accidente.
- `webapp/static/tools.css`: estilos minimos para el SVG de conexiones, caja Datasphere, espacios, nodos atenuados y detalle de tablas.

Validaciones:
- Prueba local de servicio: Biogenesis / SAPS4HANAOP => 2 conexiones, espacios CALIDAD/DESARROLLO, verdict `plena`; con toggle => 10 conexiones del cliente.
- Prueba de render helper: 2 secciones + SVG generado correctamente.
- `py_compile` OK.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

Pendiente sugerido:
- Validar visualmente en UI modulo 01 > Catalogo > conexion: alineacion del SVG y comportamiento del toggle con panel derecho abierto/cerrado.
- Revisar holisticamente el uso de `{{support}}` en catalogo vs renderer core cuando se retome ese frente.

### 2026-07-23 - Codex (catalogo conexiones: agrupacion conceptual en SVG y detalle)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/main.py`: el SVG de Conexiones & Espacios ahora agrupa una misma conexion conceptual por `type_id + connection + businessName`, mostrando un unico origen con flechas a todos los espacios aplicables.
- El label visible del origen queda como `ID - nombre funcional` en terminos visuales (`ID · nombre` en codigo), sin sumar el typeId como texto visible del nodo.
- Los espacios del box Datasphere mantienen el titulo `Espacios` alineado a la derecha, pero cada tile de espacio vuelve a alinear su texto a la izquierda.
- El detalle central ya no genera una seccion por espacio cuando el origen fue agrupado: genera una seccion por origen conceptual y dentro lista cada espacio como subseccion (`### DESARROLLO`, `### CALIDAD`, etc.). Esto mantiene sincronizados los numeros del SVG con las secciones.
- Orden logico de espacios mediante `_catalog_space_sort_key`: Desarrollo -> Calidad/QA -> Produccion/PRD -> resto.
- `webapp/templates/partials/catalog_connection_content.html`: el meta de referencia usa un punto CSS semaforizado en lugar de emoji, evitando mojibake.
- `webapp/static/tools.css`: estilos `catalog-meta-dot*` y menor protagonismo para `conn-ds-title`.

Validacion:
- `py_compile` OK para `webapp/main.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

Pendiente sugerido:
- Revisar con Joe el layout interno de la seccion agrupada para hacer mas amable la lectura de multiples espacios bajo una misma conexion.
- Mantener pendiente la discusion holistica sobre reutilizar el renderer core de `{{support}}` en catalogo.


### 2026-07-23 - Codex (catalogo conexiones: table_group transversal y detalle por origen)
**Estado: implementado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/services/content_service.py`: se agrego la convencion markdown transversal `{{table_group: Titulo}}` dentro de tablas. El renderer la convierte a una fila HTML con `colspan`, clase `table-group-row`, sin exigir HTML en el contenido ni en los generadores.
- `webapp/static/tools.css`: estilos theme-aware para `.methodology tr.table-group-row td`, reutilizables en cualquier tabla markdown del core o catalogo.
- `webapp/main.py`: el detalle de Conexiones & Espacios ahora agrega un subtitulo `>` por origen agrupado y ordena cada espacio con `###`. Las tablas pasan a usar grupos `Identificacion`, `Conectividad` y `Agente`, y las primeras filas quedan `Business Name` y `Technical Name`.

Validacion:
- `py_compile` OK para `webapp/main.py` y `webapp/services/content_service.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

Pendiente sugerido:
- Validar visualmente el detalle Biogenesis / SAP S/4HANA OP con panel abierto/cerrado.
- Definir con Joe si se incorporan mas campos de los JSON crudos bajo estos grupos.

### 2026-07-23 - Codex (catalogo conexiones: tabla comparativa por espacios)
**Estado: implementado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/main.py`: el detalle agrupado de Conexiones & Espacios dejo de repetir una tabla por espacio. Ahora genera una unica tabla comparativa por origen, con columna `Propiedad` y una columna por cada espacio (`DESARROLLO`, `CALIDAD`, etc.).
- La tabla mantiene los grupos semanticos `{{table_group: Identificacion}}`, `{{table_group: Conectividad}}` y `{{table_group: Agente}}`, por lo que el render transversal sigue aplicando `colspan` y separadores visuales.
- `webapp/static/tools.css`: las tablas del detalle de conexiones usan `table-layout: fixed` y wrap defensivo para que las columnas tengan el mismo ancho y el contenido se adapte dentro de cada celda.

Validacion:
- `py_compile` OK para `webapp/main.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

Nota UX:
- Esta estructura simplifica la comparacion linea por linea entre espacios para una misma conexion/origen, en lugar de obligar a leer bloques duplicados.

### 2026-07-23 - Codex (catalogo conexiones: columnas equivalentes en tablas agrupadas)
**Estado: implementado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/services/content_service.py`: las tablas markdown que contienen filas `{{table_group: ...}}` ahora reciben automaticamente clase `table-fixed-columns` y un `colgroup` con anchos equivalentes calculados segun la cantidad de columnas del header. Esto evita que la primera columna (`Propiedad`) quede mas angosta que las columnas de espacios.
- `webapp/static/tools.css`: se agrego el estilo compartido para `table.table-fixed-columns`, con `table-layout: fixed`, `width: 100%` y wrap defensivo por celda.

Validacion:
- `py_compile` OK para `webapp/services/content_service.py` y `webapp/main.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

Nota UX:
- El ajuste aplica transversalmente a cualquier tabla markdown que use `{{table_group}}`, no solo al catalogo de conexiones.

### 2026-07-23 - Codex (catalogo conexiones: tabla simple vs comparativa)
**Estado: implementado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/main.py`: el detalle de Conexiones & Espacios ahora distingue entre origenes con una sola conexion/espacio y origenes con multiples espacios.
- Si el origen tiene multiples espacios, mantiene el subtitulo narrativo y la tabla comparativa `Propiedad | <Espacio 1> | <Espacio 2> | ...`.
- Si el origen tiene un unico espacio, omite el subtitulo `El origen ... conecta contra ...` y renderiza la tabla simple `Propiedad | Valor`, como el comportamiento previo.

Validacion:
- `py_compile` OK para `webapp/main.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

### 2026-07-23 - Codex (catalogo conexiones: alcance final de detalle + pending UI semaforo)
**Estado: implementado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/main.py`: el detalle de Conexiones & Espacios quedo con el alcance final solicitado para cada origen/conexion:
  - Identificacion: Business Name, Technical Name.
  - Conectividad: Connection type, Application Server, System Number, Mandante, ID, Idioma.
  - Cloud Connector: Configurado, Location ID, Virtual Destination, Virtual Host, Virtual Port.
  - Autenticacion: Tipo.
  - Remote Tables: Data Provisioning, Data Access, Data Provisioning Agent, Estado, Streaming Read.
  - Replication Flows: Fast Serialization.
  - Features: Remote tables, Data flows, Replication Flows, Model Import.
- La tabla sigue usando `{{table_group: ...}}`, por lo que mantiene separadores transversales y columnas equivalentes.

Validacion:
- `py_compile` OK para `webapp/main.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 153 warnings.

Pendiente inmediato:
- Estandarizar la presentacion visual del veredicto entre catalogo de dominios y catalogo de origenes/conexiones: misma secuencia `semaforo + PLENA/BUENA/PARCIAL + texto descriptivo + cantidad`.

### 2026-07-23 - Codex (catalogo: veredicto visual unificado dominios/conexiones)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/main.py`: se agregaron filtros Jinja compartidos para normalizar el veredicto del catalogo (`plena`, `buena`, `parcial`) como clase CSS, label uppercase y texto descriptivo.
- `webapp/templates/partials/catalog_flows.html`: los tiles de dominios ahora usan la misma estructura visual que conexiones: semaforo CSS + cliente + badge `PLENA/BUENA/PARCIAL` + texto descriptivo/cantidad.
- `webapp/templates/partials/catalog_connections.html`: los tiles de origenes/conexiones adoptan el mismo diccionario visual y separan estado, descripcion y cantidad.
- `webapp/templates/partials/catalog_flow_content.html` y `catalog_connection_content.html`: el hero/meta del detalle central queda alineado en secuencia: semaforo + `PLENA/BUENA/PARCIAL` + descripcion + cantidad.
- `webapp/static/tools.css`: se ajustaron estilos compartidos para badge de veredicto, punto semaforizado y linea de veredicto reutilizable.

Validacion:
- `py_compile` OK para `webapp/main.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK desde el repo real: 139 passed, 153 warnings.

Pendiente sugerido:
- Validar visualmente en UI que dominios y origenes/conexiones se vean alineados en claro/oscuro y con panel derecho abierto/cerrado.

### 2026-07-23 - Codex (catalogo conexiones: MSSQL database/gateway + auth_type)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `tools/catalog/generate_space_catalog.py`: el generador ahora materializa secciones `Database`, `Authentication` y `gateway` desde el JSON crudo de conexiones. La deteccion de `database` exige un bloque tipo dict y la de `auth_type` exige un valor escalar no vacio, evitando falsos positivos.
- `tools/catalog/catalog_service.py`: el parser del catalogo lee la seccion `Database` y la fusiona en `info`, para que el renderer pueda resolver `pds_server_name`, `pds_port_number`, `pds_database_name` y `version` en conexiones MSSQL.
- `webapp/main.py`: el detalle MSSQL ahora muestra `Servidor`, `Puerto`, `Nombre de base de datos`, `Version`, `Location ID` desde `cloudConnectorLocation` y `Virtual Destination = Derivar de conectividad` cuando `virtualHost/virtualPort` derivan de `pds_server_name/pds_port_number`.
- `webapp/main.py`: en conexiones SAP, `Autenticacion > Tipo` queda tomado del tag real `auth_type` cuando existe, en lugar de caer en `technicaluser` o `-`.

Validacion:
- Pruebas sinteticas de generacion/parsing para MSSQL `database`, `Authentication.auth_type` y `gateway` OK.
- `.venv/Scripts/python.exe -m pytest -q` OK desde el repo real: 139 passed, 153 warnings.
- Validado visualmente por Joe: MSSQL ya muestra servidor/puerto/base/version, `Location ID` y `Virtual Destination`; SAP ya muestra `Autenticacion > Tipo`.

Operativo:
- Regenerar catalogo desde SharePoint cuando cambien los JSON crudos para que los `.md` incorporen las nuevas secciones.
- Reiniciar server si hay cambios `.py`, porque el piloto corre sin `--reload`.

Pendiente sugerido:
- Revisar otros tipos de conexion cuando haya ejemplos reales de JSON para evitar inferencias locales por tipo.
### 2026-07-23 - Codex (catalogo: cumplimiento contextual + CDS code feedback/copy)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `webapp/main.py`: se agrego `_catalog_compliance_intro_md(...)` para repetir contexto debajo de `## Cumplimiento` en catalogo de dominios y en Conexiones & Espacios. El texto usa el render existente de subtitulo markdown (`> ...`) y explicita la clasificacion `PLENA/BUENA/PARCIAL · referencia Danwor` para que el veredicto siga visible al final del detalle.
- `webapp/main.py`: el endpoint `/catalog/cds` ahora pasa `catalog_kind="cds"` y `catalog_ref=<nombre CDS>` para que el feedback de cierre tenga referencia util.
- `webapp/templates/partials/catalog_cds_content.html`: la pagina de codigo CDS incluye `{% include "partials/catalog_feedback.html" %}` al cierre, alineada con dominios y conexiones.
- `webapp/services/content_service.py` y `webapp/static/app.js`: el boton nativo de copia de codigo queda con tooltip/aria `Copiar código` / `Código copiado` sin mojibake.
- `webapp/main.py`: se corrigio mojibake en el subtitulo de Cumplimiento (`por qué`, `catálogo`, separador `·`).

Validacion:
- `py_compile` OK para `webapp/main.py` y `webapp/services/content_service.py`.
- `git diff --check` OK para los archivos tocados durante la correccion CDS/copy.

Operativo:
- Reiniciar server para tomar cambios `.py`; refrescar navegador para tomar `app.js` actualizado.

Pendiente sugerido:
- Validar visualmente en UI el cierre de feedback en `/catalog/cds` y el subtitulo de `Cumplimiento` en dominios/conexiones en theme claro/oscuro.

### 2026-07-24 - Codex (catalogo seguridad: exclusion dominio + resumen DAC)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `tools/catalog/generate_catalog.py`: `Seguridad` queda excluida de `CATALOG_IGNORED_DIRS`, igual que `Espacios` y `Conexiones`, para que no aparezca como dominio del catalogo de modelos.
- `tools/catalog/generate_security_catalog.py`: el catalogo DAC ahora puede leer un resumen manual desde JSON usando `@Danwor.catalog.summary` como tag recomendado. Tambien tolera `@Danwor.catalog.securitySummary`, `@DataWarehouse.businessDefinition.summary` y `@DataWarehouse.businessDefinition.description` como fallback.
- `tools/catalog/generate_security_catalog.py`: el resumen se materializa en frontmatter `auto.summary` del `.md` generado.
- `tools/catalog/catalog_service.py`: `CatalogSecurityEntry` expone `summary` y lo incluye en el texto de busqueda interno.
- `webapp/templates/partials/catalog_security.html`: el tile DAC mueve `Espacio/Espacios` dentro del expandible `Ver criterios` como pie final, y agrega un segundo expandible `Ver resumen` cuando existe `summary`.
- `webapp/templates/partials/catalog_security.html`: se corrigio mojibake real del template (`Elegi`/separadores) usando escritura UTF-8 controlada.

Patron operativo:
- Cuando se agregue un nuevo directorio de entrada que NO represente dominios/flows del catalogo principal (ej. `Seguridad`, `Espacios`, `Conexiones`), debe agregarse a `CATALOG_IGNORED_DIRS` en `generate_catalog.py` y procesarse con un generador dedicado. Asi evitamos que el catalogo de dominios contamine sus tiles con fuentes tecnicas auxiliares.
- Para DAC, si se quiere mostrar el resumen en el tile, agregar el texto al JSON con el tag recomendado `@Danwor.catalog.summary` y regenerar el catalogo.

Validacion:
- `py_compile` OK para `tools/catalog/generate_catalog.py`, `tools/catalog/generate_security_catalog.py` y `tools/catalog/catalog_service.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 157 warnings.

### 2026-07-24 - Codex (catalogo seguridad: tile DAC + detalle central con linaje)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Cambios:
- `tools/catalog/generate_security_catalog.py`: el frontmatter `auto` de seguridad ahora incluye `lineage_views` con pares `space/view` derivados del CSV/JSON de linaje. Esto evita que la UI tenga que raspar tablas markdown para dibujar el flujo.
- `tools/catalog/catalog_service.py`: `CatalogSecurityEntry` expone `lineage_views`; si un `.md` viejo no trae frontmatter nuevo, intenta reconstruirlo desde la tabla markdown de `Uso / Linaje` como fallback liviano.
- `webapp/templates/partials/catalog_security.html`: se corrigió el `+ + VER CRITERIOS`; el texto del summary vuelve a ser solo `VER CRITERIOS` / `VER RESUMEN` porque el `+` lo agrega CSS. El tile completo es la acción clickeable para abrir el detalle central; no debe existir botón separado `Abrir detalle` en este catálogo.
- `webapp/main.py`: el endpoint `/catalog/security/detail` acepta `lineage=0/1` y genera un SVG liviano de seguridad DAC: `Entidad de permisos -> DAC -> vistas consumidoras`.
- `webapp/templates/partials/catalog_security_content.html`: el detalle central de seguridad ahora muestra el bloque de linaje antes del articulo y agrega checkbox `Incluir linaje` para mostrar/ocultar vistas consumidoras referenciales.
- `webapp/static/tools.css`: se agregaron estilos minimos para la accion explicita del tile y subtitulos del SVG, reutilizando `catalog-lineage` / `lineage-svg` existentes.

Validacion:
- `py_compile` OK para `webapp/main.py`, `tools/catalog/generate_security_catalog.py` y `tools/catalog/catalog_service.py`.
- `.venv/Scripts/python.exe -m pytest -q` OK: 139 passed, 157 warnings.

Operativo:
- Para que `lineage_views` quede en los `.md`, regenerar catalogo despues de subir/actualizar CSV/JSON de Seguridad en SharePoint. Los `.md` viejos siguen funcionando por fallback, pero el contrato correcto es frontmatter `auto.lineage_views`.
- Reiniciar server si hay cambios `.py`, porque el piloto corre sin `--reload`.

### 2026-07-24 - Codex (handoff: estado actual consolidado)
**Estado: documentación saneada; no se commiteó ni se pusheó.**

Aclaraciones consolidadas:
- El alcance vigente es **Catálogos Danwor**, no únicamente Catálogo CDS.
- `Regenerar catálogo` en el panel admin debe entenderse como regeneración de catálogos técnicos desde SharePoint/local: dominios/CSN, APIs/CDS, espacios, conexiones y seguridad/DAC.
- En Seguridad/DAC el tile es clickeable completo. No hay botón separado `Abrir detalle`; los únicos expandibles del tile son `Ver criterios` y `Ver resumen` cuando corresponde.
- El linaje DAC consumidor correcto viene de `auto.lineage_views`; para `.md` viejos el servicio puede reconstruirlo desde la tabla `Uso / Linaje` como fallback.
- Mantener como regla: cualquier nuevo directorio técnico que no represente dominios/flows del catálogo principal debe agregarse a `CATALOG_IGNORED_DIRS` y procesarse con generador dedicado.

Validación:
- Revisión editorial del HANDOFF en UTF-8. No se ejecutaron tests porque no hubo cambios de código.


### 2026-07-24 - Codex (catalogo seguridad: descarga CSV y cierre linaje DAC)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Problema detectado:
- El generador de seguridad/DAC funcionaba correctamente con la carpeta local `E:\Joe\Descargas\Datasphere\Danone\Seguridad`, pero al regenerar desde SharePoint el tile quedaba como `BUENA` y `0 vistas consumidoras`.
- Causa raiz: `fetch_csn_folder(...)` en `tools/catalog/generate_catalog.py` solo descargaba `.json`, `.txt` y `.md`. El linaje DAC estaba en `lineage_DAC_SND_PBI.csv`, por lo tanto nunca llegaba al generador cuando la fuente era SharePoint.

Cambios:
- `tools/catalog/generate_catalog.py`: la descarga Graph/SharePoint ahora incluye tambien `.csv` junto con `.json`, `.txt` y `.md`.
- Validado contra archivos locales reales: `data.json` y `lineage_DAC_SND_PBI.csv` con columnas `SPACE_ID, VIEW_NAME` generan `lineage_views` con 13 vistas consumidoras, `consumers: 13`, `spaces: [GOVERNED_BW_BRIDGE]` y veredicto `plena`.
- El markdown generado de DAC ya contiene los cambios esperados de tabla/detalle: `Owner`, `Estructura`, `Identificador`, `Entidad de Permisos`, grupo `Criterios` y campos gobernados separados por linea.

Operativo:
- Reiniciar server si hubo cambios `.py`.
- Ejecutar `Regenerar catalogo` desde el panel admin. La salida deberia incluir mas archivos descargados si SharePoint trae CSV y una linea `Catalogo de seguridad regenerado...`.
- Si el tile sigue sin resumen, revisar que el JSON DAC tenga un tag manual soportado. Recomendado: `@Danwor.catalog.summary` dentro de `definitions.<DAC_NAME>`.

Validacion:
- `py_compile` OK para `tools/catalog/generate_catalog.py`, `tools/catalog/generate_security_catalog.py` y `webapp/services/admin_tools.py`.
- No se corrio suite completa de tests en este cierre.

### 2026-07-24 - Codex (proximo intento: catalogo seguridad de roles desde matriz Excel)
**Estado: intencion registrada; no implementado aun.**

Pedido de Joe para retomar si la sesion queda interrumpida:
- Incorporar al catalogo de seguridad una segunda familia de evidencia: matriz de roles/permisos desde `DSP_Matriz_Seguridad_Template.xlsx`, ubicada inicialmente en `E:\Joe\Descargas\Datasphere\Danone\Seguridad` y luego en SharePoint.
- Objetivo: asi como el generador convierte CSV/JSON de linaje DAC en `lineage_views`, crear un parser/generador para convertir la matriz de roles a JSON/markdown interpretable por el catalogo.
- La salida esperada debe identificar roles globales, roles de alcance y permisos en estructura JSON/markdown, manteniendo el patron de generadores dedicados y preservacion de metadata manual cuando aplique.

Propuesta de UX/contrato:
- En la pestaña Catalogo, reemplazar el dropdown unico `Criterio de seguridad` por un selector con dos opciones de alto nivel:
  1. `Seguridad de Roles` -> procesa/renderiza la nueva matriz de roles.
  2. `Seguridad de Datos` -> procesa/renderiza DACs como `DAC por Area de Ventas` y futuros criterios DAC.
- `Seguridad de Datos` debe conservar el comportamiento actual: tiles DAC, criterios, resumen, detalle central, entidad de permisos, DAC y linaje consumidor opcional.
- `Seguridad de Roles` debe seguir el mismo lenguaje visual del catalogo: tile clickeable, expandibles `+ VER ...`, detalle central y seccion `Cumplimiento` si hay reglas de ponderacion.

Formato recomendado:
- Preferir `.xlsx` si la matriz tiene varias hojas, celdas agrupadas, encabezados multinivel o estructura rica. Para parser Python, usar `openpyxl` si ya esta disponible o agregar dependencia solo si se valida con Joe.
- Preferir `.csv` solo si Joe exporta una hoja plana unica con columnas claras. Es mas simple, pero pierde contexto si la matriz depende de varias pestañas o agrupaciones visuales.
- Si se mantiene `.xlsx`, documentar el contrato minimo: nombres de hojas, fila de headers, columnas obligatorias y como distinguir rol global vs rol de alcance vs permiso.

Pendientes antes de implementar:
- Revisar el workbook real `DSP_Matriz_Seguridad_Template.xlsx` y confirmar hojas/columnas.
- Definir si el generador emite un `.json` intermedio dentro de `catalog_local/seguridad/<cliente>/` o si genera markdown directo con frontmatter `auto` + `manual` como el resto. Recomendacion inicial: generar ambos si aporta trazabilidad, pero mantener como fuente consumible principal el markdown/frontmatter.
- Asegurar que `Seguridad` siga excluido del catalogo principal de dominios (`CATALOG_IGNORED_DIRS`) y sea procesado solo por generadores dedicados.

### 2026-07-24 - Codex (avance piloto: Seguridad de Roles desde XLSX)
**Estado: primera pasada implementada en código y validada con salida temporal; no se commiteó ni se pusheó.**

Cambios realizados:
- `tools/catalog/generate_security_catalog.py` ahora contempla `.xlsx` como fuente de seguridad junto con `.json` y `.csv`.
- Se agregó un parser XLSX read-only basado en OpenXML/stdlib para `DSP_Matriz_Seguridad_Template.xlsx` sin depender de `openpyxl`.
- El parser lee:
  - hoja `Cover` como índice maestro de roles.
  - hojas `GRC_*` como roles globales.
  - hojas `SRC_*` como roles de alcance.
  - grillas de permisos marcados con `X` para armar una matriz resumida de privilegios.
- El generador emite un markdown adicional bajo `catalog_local/seguridad/<cliente>/Seguridad_de_Roles__<archivo>.md` con frontmatter `auto.tipo: security` y `auto.family: roles`.
- `tools/catalog/catalog_service.py` extiende `CatalogSecurityEntry` con `family`, conteos de roles/permisos y `source_file`, preservando DAC como `family=data` por defecto.
- `webapp/templates/partials/catalog_security.html` muestra tiles de Roles con conteos (`roles`, `privilegios`, `roles globales`, `roles de alcance`) y mantiene DAC con identificador/entidad de permisos.
- `webapp/templates/partials/catalog_security_content.html` oculta el linaje DAC cuando `entry.family == roles`; el detalle central queda basado en el markdown renderizado.

Validación realizada:
- Generación temporal contra `E:\Joe\Descargas\Datasphere\Danone\Seguridad` sin pisar `catalog_local` real.
- Resultado temporal: `Danone: 1 criterio(s) de seguridad` + `Danone: 1 matriz(es) de roles`.
- Servicio carga ambas entradas: `DAC por Area de Ventas` (`family=data`, 13 vistas) y `Seguridad de Roles` (`family=roles`, 12 roles, 80 privilegios).
- `py_compile` OK para `generate_security_catalog.py`, `catalog_service.py` y `webapp/main.py`.
- No se corrió suite completa `pytest` en esta pasada.

Pendiente / siguiente paso recomendado:
- Regenerar catálogo real desde el panel admin o CLI para materializar el `.md` de roles en `catalog_local`.
- Reiniciar server (corre sin `--reload`).
- Probar módulo 02 > Catálogo: debería aparecer `Seguridad de Roles` como opción/criterio junto con `DAC por Area de Ventas`.
- UX pendiente: Joe pidió idealmente dos opciones de alto nivel: `Seguridad de Roles` y `Seguridad de Datos`. La primera pasada lo deja como entrada del dropdown existente para validar datos sin rediseñar todavía el selector.
- Si se confirma el modelo, refactorizar el selector de seguridad para separar familia (`Roles` vs `Datos`) y luego mostrar tiles correspondientes.



### 2026-07-24 - Codex (cierre UX: selector Seguridad por familia)
**Estado: implementado y validado en piloto local; no se commite? ni se pushe?.**

Aclaraci?n de Joe:
- El dropdown de Seguridad no debe listar criterios DAC directamente.
- Debe tener dos opciones funcionales fijas:
  1. `Seguridad de Roles`
  2. `Seguridad de Datos`
- `Seguridad de Roles` muestra las tiles generadas desde la matriz de roles.
- `Seguridad de Datos` muestra directamente todas las tiles DAC disponibles, sin un segundo selector de criterio.

Cambios:
- `tools/catalog/catalog_service.py`: `security_families(...)` qued? como contrato fijo de UI (`roles`, `data`) y `security_for_family(...)` filtra las entradas generadas por familia.
- `webapp/main.py`: el contexto del cat?logo usa `catalog_security_families`; el endpoint `/catalog/security` recibe `family` y renderiza la familia seleccionada.
- `webapp/templates/partials/catalog_tab.html`: el selector ahora muestra `Cat?logo de seguridad` con `Seguridad de Roles` y `Seguridad de Datos`.
- `webapp/templates/partials/catalog_security.html`: el estado vac?o se expresa por familia (`Sin artefactos detectados para ...`).

Validaci?n:
- `py_compile` OK para `tools/catalog/catalog_service.py`, `webapp/main.py`, `webapp/services/admin_tools.py`, `tools/catalog/generate_catalog.py` y `tools/catalog/generate_security_catalog.py`.
- Validaci?n r?pida del servicio: `security_families(...)` devuelve exactamente `Seguridad de Roles` y `Seguridad de Datos`; con el cat?logo local actual hay 1 entrada `roles` y 1 entrada `data`.

Operativo:
- Reiniciar server para tomar cambios `.py`.
- Regenerar cat?logo solo si cambian las fuentes SharePoint/XLSX/CSV/JSON; este cambio de selector no requiere regeneraci?n por s? mismo.


### 2026-07-24 - Codex (roles: matriz visual por rol)
**Estado: implementado y validado en piloto local; no se commite? ni se pushe?.**

Pedido de Joe:
- La secci?n `[04] Permisos ? Matriz resumida` del cat?logo `Seguridad de Roles` no era legible: aplanaba una matriz que el Excel ya expresa muy bien.
- La salida deb?a capitalizar el formato original: objetos de autorizaci?n como filas, privilegios como columnas y una marca en la intersecci?n donde el Excel trae `X`.
- No conviene poner todas las hojas dentro de una ?nica secci?n resumida; cada rol debe tener su propia secci?n del cat?logo.

Cambios:
- `tools/catalog/generate_security_catalog.py`:
  - Se elimin? la salida `Permisos ? Matriz resumida` en el markdown generado.
  - `Roles Globales` y `Roles de Alcance` quedan como ?ndices/res?menes navegables: cada `Role ID` linkea a su secci?n propia.
  - Cada rol (`GRC_*` y `SRC_*`) genera una secci?n `## <Role ID> ? Matriz de permisos`.
  - Cada secci?n de rol renderiza una tabla matricial: `Objeto de autorizaci?n` + columnas de privilegios (`Create`, `Read`, `Update`, `Delete`, etc.) y `?` donde el Excel ten?a `X`.
  - Se agreg? exclusi?n de archivos temporales de Office (`~$*.xlsx`) para evitar que una sesi?n abierta de Excel rompa la regeneraci?n.
  - Los caracteres sensibles (`?`, `?`, acentos) quedaron emitidos mediante escapes Unicode en el c?digo fuente para evitar mojibake al editar desde PowerShell.

Validaci?n:
- `py_compile` OK para `tools/catalog/generate_security_catalog.py`.
- Generaci?n temporal contra `E:\Joe\Descargas\Datasphere\Danone\Seguridad` OK.
- Verificaci?n por Python del markdown temporal: contiene `?`, `?`, `autorizaci?n` y `cat?logo` reales en UTF-8.
- Suite completa ejecutada desde `C:\Users\Joe\ArquitectoDanwor`: `139 passed, 157 warnings`.

Operativo:
- Para verlo en UI real: reiniciar server y regenerar cat?logo desde Herramientas de Gesti?n o CLI.
- Si Excel est? abierto localmente, el lock file `~$...xlsx` ya se ignora.


### 2026-07-24 - Codex (cierre roles: índice, template y secciones útiles)
**Estado: implementado y validado en piloto local; no se commiteó ni se pusheó.**

Pedido de Joe cerrado en esta pasada:
- En el catálogo `Seguridad de Roles`, los roles sin matriz/permisos de detalle ya no generan secciones propias vacías o irrelevantes.
- Antes del primer bloque de contenido se agrega un índice tipo `EN ESTE MÓDULO`, con numeración y links directos a las secciones de roles que sí tienen detalle.
- En `Resumen · Roles`, el `Archivo fuente` apunta al template SharePoint `DSP_Matriz_Seguridad_Template.xlsx`.
- El header del markdown generado incluye `{{support: Matriz template="https://danwor.sharepoint.com/:x:/s/PrcticaAnalytics/IQC_KaECHiU5TrOjJBpDhkC7AeH7NJUQhq3AACTGOSpltG8?e=B9uJub"}}` para abrir la matriz desde un botón de soporte.
- El generador quedó escrito con escapes Unicode en las cadenas sensibles para evitar mojibake al editar desde PowerShell.

Archivos tocados:
- `tools/catalog/generate_security_catalog.py`

Validación:
- `py_compile` OK para `tools/catalog/generate_security_catalog.py`.
- Generación temporal OK contra `E:\Joe\Descargas\Datasphere\Danone\Seguridad`.
- El markdown temporal contiene `EN ESTE MÓDULO`, botón `Matriz template`, link al archivo fuente y no contiene `Matriz resumida`.
- Chequeo UTF-8 OK: sin carácter de reemplazo, acentos correctos y checks `✓` generados.
- Suite completa: `139 passed, 157 warnings`.

Operativo para ver el cambio en UI:
- Regenerar catálogo real desde Herramientas de Gestión o por CLI.
- Reiniciar server porque corre sin `--reload`.

Nota para Claude:
- El historial reciente del `HANDOFF.md` tiene mojibake en entradas anteriores por ediciones previas desde consola. Esta entrada queda como referencia limpia del estado final de roles.
- No reescribir automáticamente todo el handoff sin hablarlo con Joe, para no perder trazabilidad de la bitácora.

### 2026-07-24 - Codex (fix roles: support y link externo del template)
**Estado: implementado y validado en piloto local; no se commiteo ni se pusheo.**

Problema detectado por Joe:
- El link del `Archivo fuente` abr?a dentro de la misma app en vez de nueva pesta?a.
- El bot?n `{{support}}` `Matriz template` no se ve?a en el header del cat?logo de roles.

Causa:
- El renderer core de `{{support}}` solo lo toma como control de encabezado cuando el tag aparece al inicio de la secci?n, antes del heading. El generador lo estaba emitiendo debajo del `#`.
- El link de `Archivo fuente` estaba emitido como markdown link simple, sin control expl?cito de `target`.

Cambio:
- `tools/catalog/generate_security_catalog.py` ahora emite `{{support: Matriz template="..."}}` antes del `# {dw-black}Seguridad de Roles ...`, respetando el contrato core del renderer.
- `Archivo fuente` ahora se emite como `<a href="..." target="_blank" rel="noopener noreferrer">DSP_Matriz_Seguridad_Template.xlsx</a>`.
- Se agreg? `import html` para escapar la URL del link expl?cito.

Validaci?n:
- `py_compile` OK.
- Generaci?n temporal OK contra `E:\Joe\Descargas\Datasphere\Danone\Seguridad`.
- Validaci?n directa con `_extract_heading_supports(...)`: genera `<div class="support-links heading-supports">... target="_blank" ... Matriz template ...</div>`.
- Suite completa: `139 passed, 157 warnings`.

Operativo:
- Para ver el fix en UI hay que regenerar el cat?logo real y reiniciar server. El cambio vive en el generador; los `.md` ya generados no se corrigen solos.

### 2026-07-24 — Claude (fix: regeneración incremental regeneraba TODO)
**Estado: bug encontrado y corregido; verificado por simulación end-to-end; 139 tests verdes. No commiteado ni pusheado.**

Síntoma (reportado por Joe): regeneró todo, cambió UN solo archivo, regeneró de nuevo y **volvió a generar todo** (`0 sin cambios` en los tres catálogos: dominios, espacios/conexiones, seguridad).

Causa raíz: la **firma por fuente incluía `mtime_ns`**. `fetch_csn_folder` descarga SharePoint a un **temp nuevo cada vez** → cada archivo recién bajado tiene un mtime distinto, aunque el contenido (sha256) sea idéntico → la firma nunca matcheaba → `force`/regen siempre. Estaba en los TRES generadores:
- `generate_catalog.py::_catalog_leaf_signature` (línea ~766)
- `generate_space_catalog.py::_client_source_signature` (~131)
- `generate_security_catalog.py::_source_signature` (~287)

Fix: saqué `mtime_ns` de las tres firmas. La identidad estable de un archivo descargado = `sha256` (contenido) + ruta relativa (a `inbound_root`, estable pese al temp cambiante). `size` queda (es content-derived, inofensivo).

Verificado por simulación (dos temp distintos con el mismo contenido + un cambio selectivo): 1ª corrida = 2 generados; 2ª (re-descarga, mismo contenido, mtimes nuevos) = **0 generados / 2 sin cambios**; 3ª (cambio SOLO Danone) = **1 generado (Danone) / 1 sin cambios**. Exactamente el comportamiento incremental esperado.

OJO operativo para Joe: como CAMBIÓ el generador, su `generator_sha256` cambia → la **próxima** regeneración fuerza un full-rebuild UNA vez (esperado). De la segunda regeneración en adelante, lo que no cambió se saltea. `admin_tools` ya hace `importlib.reload` de los tres generadores, así que no hace falta reiniciar el server para tomar el fix — con regenerar alcanza.

RELACIONADO — Fase 2 HECHA (ver entrada "descarga incremental" más abajo): el incremental de arriba solo salteaba la GENERACIÓN local (barata); el cuello real era la DESCARGA de SharePoint. Ya se implementó la descarga condicional por `cTag`/`eTag` + caché persistente.

### 2026-07-24 — Claude (fix: Seguridad de Datos — DAC duplicado + linaje cruzado)
**Estado: dos bugs corregidos y verificados contra la fuente real de Danone; 139 tests verdes. No commiteado ni pusheado. Requiere REGENERAR para que se vea (el cambio vive en el generador; los `.md` ya generados no se corrigen solos).**

Síntomas (reportados por Joe): (1) el tile `Danone | DAC_D2I_EXTEND` aparecía DUPLICADO en Seguridad de Datos; (2) `DAC_D2I_EXTEND` mostraba 13 vistas de linaje cuando NO existe ningún `.csv` con ese nombre — el único, `lineage_DAC_SND_PBI.csv`, debía aplicar SOLO a `DAC_SND_PBI`.

Diagnóstico: en `Danone/Seguridad` hay 2 DAC reales (`DAC_D2I_EXTEND`, `DAC_SND_PBI`), un `lineage_DAC_SND_PBI.csv` (13 filas) y un `data.json` (source_file `data.csv`, 13 filas, SIN nombre de DAC — export genérico redundante).
- Bug linaje (`load_security`): `lineage_rows` se leía para TODA la carpeta (`_read_lineage_rows`) y se asignaba a CADA DAC → los 13 de SND se pegaban también a D2I.
- Bug duplicado (`write_security_catalog`): no limpia `.md` obsoletos. El label del DAC cambió con el tiempo ("DAC por Sociedad" → "Permission Entity…"), el generador escribió el `.md` con el slug nuevo y dejó el viejo (`dac_por_sociedad__dac_d2i_extend.md`) → el loader lo cargaba como segundo tile.

Fix (`generate_security_catalog.py`):
- Linaje POR DAC: `_read_lineage_sources` (devuelve `[(stem, rows)]`) + `_lineage_belongs_to(stem, dac)` (regla por nombre: `lineage_<DAC>` → `<DAC>`; un `data.json`/`data.csv` sin nombre de DAC NO se ata a ninguno) + `_lineage_for_dac`. En `load_security`, cada artefacto recibe solo su linaje.
- Limpieza de stale: `write_security_catalog` borra los `.md` de DAC que ya no correspondan al set actual (NO toca `Seguridad_de_Roles__*`), y reporta cuántos obsoletos eliminó.

Verificado contra `E:\Joe\Descargas\Datasphere\Danone\Seguridad`: `load_security` → 2 DACs; `DAC_D2I_EXTEND` = **0 vistas**, `DAC_SND_PBI` = **13 vistas**. `write_security_catalog` con un stale sembrado → lo elimina (`1 obsoleto(s) eliminado(s)`) y preserva el `.md` de roles. 139 verdes.

Nota para Joe: al regenerar, el catálogo de seguridad se rehace (cambió el generador) → desaparece el tile duplicado y el linaje de D2I queda vacío (correcto). Regla a futuro: si un DAC tiene consumidores, subir su `lineage_<DAC>.csv` con ESE nombre técnico; los exports genéricos tipo `data.csv` ya no se atan a nada.

### 2026-07-24 — Claude (fase 2: descarga incremental SharePoint por cTag + caché)
**Estado: implementado y verificado con Graph simulado; 139 tests verdes. No commiteado ni pusheado.**

Problema (Joe): el incremental de generación no aceleraba nada — la DESCARGA de SharePoint seguía siendo completa cada vez (una GET por archivo vía Graph), y eso es el cuello de botella real (parsear JSON→md es barato).

Fix (`generate_catalog.py` + `admin_tools.py`):
- **Caché persistente** de crudos: `admin_tools` ya no baja a un `mkdtemp` que borra; usa `catalog_local/.csn_cache` (env `DANWOR_CATALOG_CSN_CACHE` para override). Los generadores corren sobre ese caché. Es runtime (no se commitea); en DO es efímero → tras redeploy la 1ª regen re-baja todo (aceptable).
- **Manifest de descargas** `.csn_cache/.download_manifest` (SIN extensión .json para que los generadores no lo levanten como fuente): `{"folder": {relpath: tag}, "shared": {relpath: tag}}`. `tag = _remote_tag(item)` = `cTag` (cambia con contenido) → `eTag` → `size:lastModifiedDateTime`.
- `fetch_csn_folder` condicional: recorre el árbol Graph; por archivo, si el crudo está en caché y el tag no cambió → NO baja (reusa). Si no hay downloadUrl pero hay copia, la conserva. Al final PRUNE de los que desaparecieron en SharePoint. Devuelve "X descargado(s), Y sin cambios (caché), Z eliminado(s)".
- `fetch_shared_file` (xlsx de roles) también condicional vía `cache_root` (bloque `shared` del manifest).

Verificado con Graph mockeado (2 archivos): 1ª (caché vacío)=2 descargados; 2ª (sin cambios)=**0 descargados, 2 de caché**; 3ª (cambia cTag de uno)=**1 descargado, 1 de caché**; 4ª (borro uno remoto)=**0 descargados, 1 de caché, 1 eliminado** (caché queda con el correcto). 139 verdes.

Para Joe: la 1ª "Regenerar" tras este cambio baja todo (caché vacío) — normal. De ahí en más, la descarga (y la generación) solo tocan lo que cambió en SharePoint → mucho más rápido. Verás en la salida "N descargado(s), M sin cambios (caché)". OJO: la línea de descarga ahora dice "X descargado(s)…" en vez de "N archivo(s) descargado(s)".

### 2026-07-24 — Claude (UX: contador de catálogos en header + fuentes colapsables)
**Estado: implementado y verificado por render/tests; 139 verdes. No commiteado ni pusheado.**

Dos mejoras de UX pedidas por Joe:

1. **Contador de catálogos en el header del módulo** (`module.html`): junto a "N secciones | N árboles asociados | N diagnóstico asociado" ahora se muestra, ENTRE árboles y diagnóstico, "N catálogo(s) disponible(s)". El conteo es por módulo, igual que el contenido de la pestaña Catálogo: 01 → orígenes de conexión (`catalog_connection_types`), 02 → artefactos de seguridad (nuevo `catalog_security_count` = `len(_catalog_security_entries()[1])`, porque `catalog_security_families` es un contrato fijo de 2 y no refleja lo generado), resto → dominios (`catalog_domains`). Se gatea con `catalog_domains` (misma condición que el botón de la pestaña) y solo se muestra si el conteo > 0. Nuevo context var `catalog_security_count` en el route del módulo (`main.py`).

2. **Fuentes colapsables en los tiles del módulo 03** (`catalog_flows.html`): el módulo 03 reusa el catálogo de dominios con foco "fuentes"; la lista de orígenes CDS podía hacer el tile muy largo. Ahora "Fuentes:" es un `<details class="catalog-card__notes">` (misma clase/estilo que "+ VER NOTAS" → el CSS agrega el "+ "/"− " y uppercase) con `<summary>Ver fuentes (N)</summary>` → se ve **"+ VER FUENTES (N)"**, colapsado por defecto. Sin CSS nuevo. El `onclick="event.stopPropagation()"` evita que abrir/cerrar dispare la navegación del tile.

Verificado: render del partial (fuentes = details con "Ver fuentes (3)"); lógica del header en los 4 módulos (01=5, 02=3, 03/04=1, singular/plural y gate OK); server arranca sin errores; 139 tests verdes. Falta OK visual de Joe (logueado).

Correcciones (mismo día, feedback de Joe sobre módulo 02 Seguridad):
- Header módulo 02 imprimía 3 (artefactos DAC+roles) pero la tab muestra 2 (familias Roles/Datos). Cambiado a `catalog_security_families|length` para que coincida con la tab. Quité el context var `catalog_security_count` (ya no se usa).
- El detalle de **Seguridad de Roles** mostraba "0 vistas consumidoras" en el meta del hero (texto genérico que solo aplica a DAC/Datos). `catalog_security_content.html`: el meta ahora es por familia → roles muestra "N roles · M privilegios" (`role_count`/`permission_count`), datos mantiene "N vistas consumidoras". La figura de linaje DAC ya estaba gateada por `family != roles`. Verificado por render de ambas familias.


### 2026-07-24 — Claude (fix: tipos/longitudes en Analytic Model + ocultar Catálogo en módulo 05)
**Estado: implementado y verificado contra datos reales (Biogenesis); 139 tests verdes. No commiteado ni pusheado. Requiere REGENERAR para el fix de tipos (vive en el generador).**

1. **Tipos/longitudes faltantes en AM_BASE_STOCKS** (reportado por Joe): el Analytic Model (cubo) mostraba todo "—" en tipo/longitud. Causa: en el CSN de un cubo, `elements.<medida>` solo trae `@EndUserText.label` + `@AnalyticsDetails.measureType` (SIN type inline); el tipo real está en la entidad FUENTE que el `query.SELECT.columns` referencia (p. ej. `AM_BASE_STOCKS.Stock_Libre_Utilizacion` → ref `["VF_BASE_STOCK","Stock_Libre_Utilizacion"]`, que sí tiene `cds.Decimal 17,3` inline). El generador nunca seguía ese ref. Fix en `generate_catalog.py::load_folder`: se guardan `raw_elems` (elements crudos por entidad) + `col_refs` (alias→ref de `query.SELECT.columns`); post-pass `_inherit_typelen` que, para cada campo sin tipo, sigue la cadena de refs hasta hallar un tipo inline. Verificado: medidas → Decimal 17,3; claves de dimensión (LGORT/CHARG/…) → String con longitud. (Quedan en "—" 4 atributos de navegación vía asociación `_MATNR` — su ref apunta a un mixin/asociación, no a una entidad; caso menor, sin regresión.)

2. **Módulo 05 (Reporting y Consumo) sin tab Catálogo** (pedido de Joe): hoy el 05 caería en el branch "Dominio", que no aplica ahí → mostraba "basura". Se ocultó la pestaña hasta armar su catálogo propio. `module.html`: `{% set show_catalog = catalog_domains and número != "05" %}` y se gatea con `show_catalog` el botón del tab, el panel y el contador del header. Simple, sin variables nuevas en el backend. Verificado: 01/03/04 muestran tab, 05 no.

### 2026-07-24 — Claude (descarte de campos residuales sin tipo)
**Estado: implementado y verificado; 139 tests verdes; catálogo regenerado desde caché. No commiteado ni pusheado.**

Seguimiento del fix de tipos: en AM_BASE_STOCKS quedaban 4 atributos residuales (`MTART_Nmero_de_mater`, `SPART_...`, `MVGR1_...`, `MVGR2_...`) sin tipo ni longitud. Son proyecciones de navegación que referencian un mixin/asociación (no una entidad), no parte real del modelo. A pedido de Joe se descartan. `load_folder` (post-pass de herencia de tipo): tras intentar resolver, se filtran de `ent.fields` los campos que quedaron sin tipo NI longitud, SOLO en entidades con `col_refs` (query-projected: cubos/AM/vistas) y preservando siempre `key`/`medida`/`assoc` (claves, medidas y relaciones a dimensión nunca se caen). Verificado: AM_BASE_STOCKS Atributos 13→9 (todos con tipo), Medidas 6 (Decimal 17,3), MATNR/WERKS conservan Relación→dim; VF/2VR/VD sin typeless. Regenerados los 3 clientes; solo refrescar.

### 2026-07-24 — Claude (safety-check: labels i18n sin resolver)
**Estado: implementado y verificado contra Danone (BWBRIDGE); 139 tests verdes; catálogo regenerado. No commiteado ni pusheado.**

Bug (reportado por Joe): algunos exports (p. ej. `Remote.BWBRIDGE.*`) no resuelven la traducción de `@EndUserText.label` y dejan un placeholder i18n como texto — `{i18n>Remote.BWBRIDGE.ZIM_V01@ENDUSERTEXT.LABEL}` — que terminaba impreso como descripción (heading `{i18n>...} · NOMBRE` y en la columna Descripción de campos).

Fix en DOS capas: (1) generador `generate_catalog.py::_clean_label(raw, fallback)` — detecta el placeholder (`{i18n>`, `@enduserext.label`, o `{...@...}`) y cae al fallback (nombre técnico); aplicado al label de entidad (`load_folder`) y al de campo (`_extract_elements`). (2) runtime `main.py::_catalog_clean_label` (gemelo) usado en `_catalog_flow_view` al leer `auto.label`, para cubrir `.md` viejos sin regenerar. Verificado: `Remote.BWBRIDGE.0VENDOR` → label = nombre técnico, campos con label placeholder → "—"; ningún `.md` de Danone contiene `i18n>`. Regenerados los 3 clientes; server reiniciado.

### 2026-07-24 — Claude (labels i18n: RESOLVER, no descartar)
**Estado: implementado y verificado (Danone BWBRIDGE); 139 verdes; 3 clientes regenerados. No pusheado.**

Corrección al fix anterior (Joe): el placeholder `{i18n>Remote.BWBRIDGE.ZIM_V01@ENDUSERTEXT.LABEL}` NO es basura a descartar — es una CLAVE de la tabla i18n del propio CSN (`top-level i18n = {idioma:{clave:texto}}`, es/en) que mapea a la descripción real. Descartarlo perdía labels válidos. Fix en `generate_catalog.py`: se mergea `i18n_map` de todos los archivos (español preferido: se itera en→es) y `_resolve_label(raw, fallback, i18n_map)` resuelve `{i18n>clave}`→texto; si la clave no está, cae al nombre técnico (entidad) o vacío (campo→"—"). Post-pass sobre entidad + campos. Reemplaza al `_clean_label` anterior. Verificado: `Remote.BWBRIDGE.ZIM_V01` → "MM-IM: Gestion de Inventarios y Stock"; `0CPNOITEMS` → "Cantidad de actividades (movimientos)". `main.py::_catalog_clean_label` queda como safety-net de último recurso para .md viejos (runtime no tiene el i18n_map).

### 2026-07-24 — Claude (SVG de Seguridad de Roles: cobertura capa × entorno)
**Estado: implementado y verificado (estructura/tests); 139 verdes; Danone regenerado. Falta OK visual final de Joe. No pusheado.**

Nuevo SVG para el detalle de **Seguridad de Roles** (family=roles), hermano de los otros del catálogo. Bandas horizontales = capas de Datasphere (Integración/Modelado/Consumo/Gobierno y Administración, derivadas de la función del rol); columnas = entornos DEV/QA/PRD. Cada rol es un recuadro que ABARCA los entornos de su alcance; los GLOBALES abarcan la ENVOLVENTE de su capa (unión de scopes de los roles de alcance) — así Modelado/Integración no llegan a Producción (no se modela en PRD). Data-driven, sin hardcode.
- Generador (`generate_security_catalog.py`): `_role_layer` + `_role_scopes` + `_roles_for_diagram` → frontmatter `auto.roles` = [{id,category,scopes,layer,anchor}]. Anchor = sección del rol si tiene detalle, si no `roles-overview` (ancla nueva antes del Resumen) → los 12 nodos clickeables.
- `catalog_service.py`: `CatalogSecurityEntry.roles` + parseo.
- `main.py`: `_catalog_roles_svg(entry)`; el endpoint `/catalog/security/detail` lo usa cuando family=roles (en vez del linaje DAC). Nodos = MISMAS clases `.ln-node`/`.ln-node-text`/`.ln-node-subtext`/`.ln-node-a` que los otros SVG (misma tarjeta, tipografía y hover dorado) — la capa se distingue solo por la banda de fondo. Global → badge en dorado.
- `catalog_security_content.html`: figura de roles para family=roles (sin toggle). `tools.css`: clases `.rl-*` SOLO del marco (bandas/labels/entornos/guías), theme-aware con grises alfa.

HISTORIA (feedback de Joe, iterado): (1) primero cajas de colores con tipografía propia → se sentía "reinvención"; se re-skineó para ser hermano de los `.ln-*`. (2) roles globales no deben invadir PRD en Modelado/Integración → envolvente por capa. (3) todos los nodos clickeables (los sin detalle → resumen). PENDIENTE de dato (NO código): la matriz dice `SRC_Security_DAC` = "DEV, QA"; Joe espera que llegue a PRD. El SVG refleja fielmente la matriz — corregir la celda Scopes en el XLSX (→ "DEV, QA, PRD") y regenerar, o es un hallazgo real. No hardcodear.

### 2026-07-24 — Claude (roles SVG: el contenido dicta el SVG + parity de números/escala)
**Estado: implementado y verificado (estructura/tests); 139 verdes; Danone regenerado.**

Feedback de Joe convergido: el SVG de roles debe ser HERMANO de los otros y derivarse del CONTENIDO (secciones), no de un modelo conceptual aparte. Cambios:
- **El contenido dicta el SVG**: `_catalog_roles_svg(entry, sections)` arma los nodos DESDE las secciones renderizadas (un nodo por sección de rol; número = `section.number` real; ancla = `section.slug`). Nunca muestra un rol que no esté en el contenido. El frontmatter `roles` queda solo como lookup de capa/scope.
- **Todos los roles tienen sección** (`roles_with_detail` = todos): los sin matriz de permisos igual muestran descripción/alcance → los 12 aparecen en contenido y SVG.
- **Fix doble número**: el heading de cada matriz tenía `[NN]` baked además del eyebrow dorado de render_sections → se quitó el baked (`_append_role_permission_matrix`).
- **Fix escala/estilo**: el SVG usaba `width:100
### 2026-07-24 — Claude (roles SVG: el contenido dicta el SVG + parity de numeros/escala)
**Estado: implementado y verificado (estructura/tests); 139 verdes; Danone regenerado.**

Feedback de Joe convergido: el SVG de roles debe ser HERMANO de los otros y derivarse del CONTENIDO (secciones), no de un modelo conceptual aparte. Cambios:
- **El contenido dicta el SVG**: `_catalog_roles_svg(entry, sections)` arma los nodos DESDE las secciones renderizadas (un nodo por seccion de rol; numero = section.number real; ancla = section.slug). Nunca muestra un rol que no este en el contenido. El frontmatter `roles` queda solo como lookup de capa/scope.
- **Todos los roles tienen seccion** (`roles_with_detail` = todos): los sin matriz de permisos igual muestran descripcion/alcance -> los 12 aparecen en contenido y SVG.
- **Fix doble numero**: el heading de cada matriz tenia [NN] baked ademas del eyebrow dorado de render_sections -> se quito el baked (`_append_role_permission_matrix`).
- **Fix escala/estilo**: el SVG usaba width:100% (se estiraba -> fuentes/cajas gigantes). Ahora tiene width/height intrinsecos + clase `lineage-svg` (como los otros; scroll-x si no entra). Nodos = MISMAS clases `.ln-node`/`.ln-num`/`.ln-node-text`/`.ln-node-subtext`/hover `.ln-node-a`. Altura compacta (RH=34).
- CSS `.roles-svg { display:block }` (sin estirar); clases `.rl-*` solo del marco (bandas de capa + columnas de entorno), grises alfa theme-aware.

Verificado: 12 nodos = 12 secciones; numeros 04-15 = eyebrows reales; sin doble [NN]; svg intrinseco. PENDIENTE de dato (no codigo): SRC_Security_DAC Scopes="DEV, QA" en la matriz; Joe espera PRD -> corregir celda del XLSX y regenerar (o es hallazgo real). No hardcodear.

### 2026-07-24 — Claude (rúbrica de veredicto para Seguridad de Roles)
**Estado: implementado y verificado; 139 verdes; Danone regenerado (roles pasó de BUENA→PLENA).**

Antes el veredicto de roles era un DEFAULT fijo 'buena' (no computado) — por eso nunca llegaba a verde salvo override manual. Se agregó `_role_verdict(matrix)` (mismo espíritu que `_verdict` de DAC), data-driven, con 4 ejes:
- Separación de funciones: hay roles globales Y de alcance.
- Cobertura de capas: las 4 (Integración/Modelado/Consumo/Gobierno) tienen ≥1 rol.
- Control de acceso a datos: existe un rol DAC (por función, `_role_is_dac`).
- Sin modelado/integración en Producción: ningún rol de Integración/Modelado con scope PRD (riesgo).
Veredicto: `parcial` si hay modelado en PRD o falta separación global/alcance; `plena` si están las 4 capas + DAC (y sin reservas); `buena` en el resto. El bloque `manual:` del senior sigue pudiendo pisar `veredicto`/`apto`. La sección `## Cumplimiento` ahora lista los ejes con ✓/⚠ (transparencia, como DAC). Danone cumple los 4 → PLENA.

### 2026-07-24 — Claude (Novedades / What's New — Fase 1)
**Estado: implementado y verificado (parser/render/routing/tests); 139 verdes. Falta commit real con trailer para verlo vivo.**

Nueva iniciativa (no-catálogo): banner + página de "Novedades" para que se note qué cambió en el marco sin leerlo entero. SEÑAL opt-in: un trailer `Novedad:` en el mensaje del commit (los `fix`/refactors/acciones runtime NO aparecen → cero ruido, cero overload). Formato recomendado: `Novedad: <título> — <detalle>`.
- `webapp/services/whats_new_service.py`: `git log --grep "^Novedad:"` → entries [{hash,date,subject,novedad}] (cache TTL 30s); `build_markdown` arma contenido con estructura del copiloto (`## {dw-gold}\`hash\` · fecha · {dw-black}título` + `> detalle`); `is_unseen(request)` compara la cookie `dw-whatsnew-seen` con el hash más nuevo.
- `webapp/main.py`: `GET /whats-new` (página, render_sections, marca visto = avanza cookie SameSite=None) + `POST /whats-new/dismiss` (marca visto, devuelve vacío → HTMX quita el banner). El route de módulo pasa `whatsnew_unseen`.
- Templates: `whats_new.html` (shell + hero + secciones/empty-state), `partials/whats_new_banner.html` (banner con ✕ que hace hx-post dismiss). `module.html` incluye el banner si `whatsnew_unseen`. CSS `.whatsnew-banner` en tools.css (acento dorado, theme-aware).
- Estado por usuario = cookie (sobrevive deploys de DO; por-navegador). ✕ o visitar /whats-new → cookie = hash más nuevo → no reaparece hasta que haya uno más nuevo.

### 2026-07-25 — Claude/Joe (How-to "Cómo publicar un catálogo" + self-service regeneración + override senior en flujos)
**Estado: implementado y verificado (estructura/tests + DOM en vivo); 139 verdes. Piloto local, NO pusheado. Server local se corre con `.venv/Scripts/python.exe -m uvicorn webapp.main:app` :8082 (sin --reload; los .py necesitan restart, templates/YAML/CSS recargan solos).**

Sesión larga con Joe. Todo esto es del webapp/pack (no solo `tools/catalog`), pero se documenta acá porque gira alrededor del catálogo. Resumen para continuar:

**1) How-to como CONTENIDO del pack (no código) — patrón nuevo reutilizable.**
El instructivo "Cómo publicar un catálogo" NO vive en `main.py`: vive en `content_packs/<pack>/guides/<slug>.yaml` y lo carga `webapp/services/guides_service.py` (misma filosofía que módulos .md / novedades .yaml / academy .html). Escala a N how-tos agregando archivos. Schema del YAML (documentado en el propio archivo): `eyebrow`, `title`, `accent` (parte en dorado del título vía `<em>`), `lead` (token `{n}`=cant. pasos), `assets_dir`, y `steps[]` con: `title`, `short` (etiqueta corta de la botonera del landing), `body`, `substeps[]`, `link` (`{url,label}` o string; abre `_blank`), `action` (allowlist `{regenerate}`), `note` (`{text,label,template|url}` — separador+texto+botón), `shots[{img,caption}]`. El loader resuelve URLs de assets (con chequeo de existencia), `{n}`, `title_plain` (sin tags, para nav/alt), y valida slug (anti-traversal). Ruta `/catalog/como-publicar` es consumidora de una línea (`guides.load(...)`).

**2) Render estilo Danwor Academy (slides), montado como Academy.** Tras varias iteraciones con Joe (probamos: content-sections directas, iframe-en-slot con doble scroll — RECHAZADO — y finalmente el patrón Academy): la guía se cuenta como slides (`catalog_guide_slides.html`, reusa `dw-academy.css`) embebidas en un `academy-stage` (iframe) dentro del wrapper `catalog_guide.html` (shell del copiloto SIN panel derecho, `active_group="core"` para dejar los módulos visibles). Mismo mecanismo que `/academy/{stem}`. `?embed=1` → slides; sin embed → wrapper. Topbar del wrapper = alcance COMPLETO de botones (selector de marco si diagnóstico + toggle diagnóstico si owner + theme/logout/mobile) igual que módulos/academy. "← Volver" es texto plano `position:fixed` DENTRO del contenido (esquina sup-izq, `target=_parent` → módulo 04 pestaña Catálogo). Lightbox de capturas: el botón postea `dw-lightbox` al PADRE (app.js) que muestra la imagen sola a pantalla completa (no dialog confinado al iframe). Tema en vivo por postMessage `danwor-theme`. `<em>` como acento dorado ahora funciona en TODO el contenido (title|safe ya estaba; se agregó |safe a lead/body/substeps/note + CSS `.hero-sub em,.topic-lead em,.guide-note em,.substeps em{color:var(--gold);font-style:normal}`). Botonera del landing forzada a UNA fila (nowrap + flex:1 1 0 + labels `short`).

**3) Link de invocación** en `partials/catalog_tab.html`: SOLO en módulo 04 (rama de dominio), gate `module_number=="04"` (arquitectura/seguridad tienen procedimientos distintos, pendientes). Texto "**¿Falta un Dominio?** Cómo podés publicarlo →" (pregunta en 700, resto en 400; CSS `.tool-suggest__link a{font-weight:400}` + `a strong{font-weight:700}`). Navegación plena (no HTMX) al wrapper.

**4) Template de API/CDS** (`assets/guia-catalogo/template-api-cds.md`): vive como ASSET del pack (servido por `/pack-assets`, NO lo escanea el generador → no lo confunde con activo técnico). CALCA la estructura real generada: frontmatter `cds_view`/`tabla_base`/`extraccion` (FULL|DELTA) → `# API Corporativa de Extracción` → descripción itálica → `---` → `## Código` con bloque ```abap (annotations + `define view entity`). Con placeholders.

**5) Self-service de regeneración (Opción A, elegida por Joe).** Botón "↻ Regenerar catálogo ahora" en el paso 4 (via `action: regenerate`). Endpoint `POST /catalog/regenerate` (`main.py`): gate = estar logueado (NO owner); reusa `admin_tools.run_catalog_generation_locked(graph_token)` (lock NO bloqueante nuevo `_CATALOG_REGEN_LOCK` → "ya hay una regeneración en curso" si está tomado); auditado (`log_activity`). Clave de seguridad: la lectura de SharePoint usa el token DELEGADO del propio colaborador (Graph, viaja del cliente en `sessionStorage["dw-graph-token"]`) → la frontera es SharePoint (solo ve el CSN al que ya tiene acceso). Como las slides son un iframe (sessionStorage propio), el botón postea `dw-regenerate` al PADRE (app.js `dwRunRegenerate`) que lee el token, hace el POST y devuelve `dw-regenerate-result`. Mapeo de error: si el mensaje trae `401 + unauthorized` → "Tu token SharePoint expiró, necesitamos que reinicies tu sesión al copiloto (logout/login)…" + `ok=False`. NO se cablea el panel de gestión (sigue owner-only). PENDIENTES (Joe): confirmar que los logins de consultores traen scope de Graph/SharePoint en el token delegado; `catalog_local` durable en DO; token AUSENTE (no vencido) hoy cae al fallback local `catalog_inbound` en vez de dar 401 — evaluar mapear también a re-login.

**6) Override del senior en el veredicto de FLUJOS (paridad con seguridad).** Antes el flujo mostraba SIEMPRE el `mirada_danwor` auto; el bloque `manual:` (`compliance_veredicto_confirmado`, `notas_senior`) se escribía pero NO se leía. Ahora `catalog_service._parse_md` lo respeta: si `manual.compliance_veredicto_confirmado` (o alias `veredicto`) ∈ {plena,buena,parcial} → gana sobre el auto. El semáforo auto es un PUNTO DE PARTIDA que el juicio humano puede confirmar/ajustar. Se refleja al recargar (no hay cache: `load_catalog` lee los .md por request) y la regeneración PRESERVA el bloque manual (`_write_flow`→`_read_manual`; `render_flujo` mergea `**manual_override` último).
- **IMPORTANTE (corrección de un error mío, marcado por Joe): la clasificación es un ENUM CERRADO de 3 categorías, NO texto libre.** Primero puse `notas_senior` (párrafo) como el `apto` → rompía la convención transversal. Corregido: el `apto` mostrado sale del mapa canónico `catalog_service._APTO_ESTANDAR = {plena:"Referencia Danwor — reutilizable", buena:"Reutilizable con nota de especificidad", parcial:"Inspiración conceptual con reservas — no copiar tal cual"}`. `notas_senior` queda como nota INTERNA (no se muestra). DEUDA: esas frases están DUPLICADAS (acá + `generate_catalog.evaluate_compliance`) → candidato a fuente única en el refactor.

**7) Caso Despachos (Biogenesis) — juicio de clasificación.** Auto = `parcial` porque el motor es todo-o-nada: CUALQUIER hallazgo `alto` → parcial. En Despachos el único `alto` es `A004 → ABAPTABLES.A004` (tabla ABAP cruda, dimensión de condiciones periférica, NO el backbone). 10/11 orígenes ABAP desacoplan bien vía `ABAP_CDS.*`. El naming §28 es `nota` y NUNCA baja el veredicto (aclaración a Joe: solo la tabla cruda bajó la nota). Juicio honesto: `parcial` es defendible (import crudo es anti-patrón real; "no copiar tal cual" protege) pero CONTUNDENTE DE MÁS — una excepción localizada saca la misma nota que un flujo crudo de punta a punta. Joe confirmó override → Despachos quedó `buena` (`compliance_veredicto_confirmado: buena` en su .md; `notas_senior` con el porqué, interno). 
- **PENDIENTE — Opción (b) HECHA, falta (a):** enriquecer `evaluate_compliance` con lógica PROPORCIONAL: tier intermedio "buena · con reserva puntual" cuando el `alto` es minoritario/periférico (p. ej. ≤1 cruda sobre mayoría ideal y NO en el fact) reservando el `parcial` para lo sistémico, + exponer el RATIO ("10 de 11 orígenes desacoplan") en el subtítulo. Umbral a definir con Joe. Objetivo: que casos como Despachos ni necesiten override manual.

Convenciones/lecciones de la sesión (para no romperlas): (1) clasificaciones = enum cerrado, no free-text, y sus etiquetas deberían vivir en UN lugar. (2) how-tos = `guides/<slug>.yaml` + `guides_service` (no código). (3) `<em>` como acento dorado es transversal — usar con mesura (uno por título). (4) el manual del senior es override, el auto es punto de partida. (5) NO pushear piloto (catalog_local/, catalog_inbound/, feedback_log/, reports/ son runtime). (6) Claude no ingresa credenciales (no re-loguea el preview tras restart; se usa cookie de dev con SESSION_SECRET default).

Verificado con log sintético (parseo + markdown + render con hash en gold) y TestClient (routing OK). Como no hay commits `Novedad:` aún, el banner está oculto y /whats-new muestra empty-state (correcto). FASE 2 pendiente: automatizar la generación en deploy (hook/CI) — hoy es en vivo por request (git log cacheado), así que no hace falta botón. Respuesta a la duda de Joe: las acciones de Herramientas de gestión NO generan registros (son runtime, sin commit); solo los commits con trailer.

### 2026-07-26 — Claude/Joe (Bloque A: pulido de UX del panel derecho + consistencia)
**Estado: implementado y verificado (render + 139 tests); pusheado a prod. Piloto local NO cambia (catalog_local sigue runtime).** Lote de mejoras "de menos a más" sobre la zona del panel de herramientas (todo en `webapp/`, ningún cambio de contenido/pack):

- **Asistente (1.6)**: se quitó el eyebrow "Conversación metodológica" y el card empty "Arquitecto Danwor". Se reordenó: el input ("Tu consulta") va ARRIBA (donde en las otras tabs va el dropdown) y la conversación DEBAJO; se sacó el `border-top` de `.assistant-chat-form` (era la "línea gris huérfana" que quedaba con la conversación vacía). `module.html` + `tools.css` (`.assistant-conversation` margin/`:empty`, `.assistant-chat-form`).
- **Scroll consistente (1.3)**: el índice de módulos "salta" (anchor nativo en `#content`, behavior auto); los anchors internos del detalle de catálogo (nodos SVG, índice/linaje) usaban `scrollIntoView({behavior:"smooth"})` en `app.js` (handler `catalog-detail-open`) → cambiado a **`behavior:"auto"`** para igualar. NO se tocó `main.py:~295` (rail de feedback de Academy, otro contexto).
- **Suggest reutilizable (1.4 + Sección)**: `partials/tree_suggest.html` se generalizó a caja por `sg_kind` (tree|option|section) + `sg_id` único. Durante la navegación de un árbol muestra **"¿Falta una opción?"** (recupera el viejo "Mi opción no está acá"); en la lista, "¿Falta un árbol?". En Sección se sumó **"¿Falta una sección?"**. Endpoint `/trees/suggest` acepta `kind`/`sg_id` y `_save_tree_suggestion` guarda `kind`. Un solo endpoint sirve las 3 variantes; el `id` se devuelve para el swap HTMX.
- **Sección consistente**: bajo el dropdown se agregó el recuadro `.tool-empty` "N secciones disponibles" + descripción (igual estética que los empty-state de Árbol/Catálogo; Joe lo pidió calcado, no como línea suelta).
- **Subheaders minimalistas**: hero-meta pasó de "3 árboles asociados / 2 catálogos disponibles / 1 diagnóstico asociado" a "3 árboles / 2 catálogos / 1 diagnóstico" (se removió "asociados"/"disponibles"; "secciones" queda). Se actualizaron 2 asserts en `tests/webapp/test_routes.py` (líneas ~42 y ~331).
- **Herramientas de gestión en colapsables (1.7)**: `partials/admin_panel.html` — cada una de las 8 herramientas (Export runtime / Borrado selectivo / Catálogos técnicos / Case Library / Feedback / Propuestas de árbol / Contenido / Casos SAP SharePoint) pasó de `<section>` suelta a `<details class="admin-tool">` (summary = título, body = resto), cerradas por defecto, marcador +/− a la derecha. CERO cambio funcional: se preservaron todos los `hx-*`, ids (`res-*`) y forms. CSS `.admin-tool`/`.admin-tool__summary`/`.admin-tool__body`.

PENDIENTE ahora (epic B — sofisticación de la evaluación, ver también la entrada de Despachos arriba):
- **(a) rúbrica proporcional**: en `generate_catalog.evaluate_compliance`, tier intermedio "buena · reserva puntual" cuando el `alto` es minoritario/periférico (p. ej. ≤1 tabla cruda sobre mayoría `ideal` y NO en el fact) + exponer el ratio ("N de M orígenes desacoplan") en el subtítulo. Umbral a definir con Joe.
- **1.1 name-vs-reality**: nueva regla que cruza el NOMBRE (`ZCDS_…` ⇒ pretende API corporativa) contra el ORIGEN real (`ABAPTABLES.NSDM_V_*` ⇒ vista estándar, no `ABAP_CDS.*`) → exponer la discrepancia. Ej. real: `1TR_ZCDS_DSP_FACT_MCHB → ABAPTABLES.NSDM_V_MCHB`.
- **1.2 diagnóstico ⇄ automatización**: bucle bidireccional para criterio homogéneo (el auto toma ideas del manual/diagnóstico y lo desafía; el manual se enriquece con lo auto). El más ambicioso; se apoya sobre (a)+1.1.
- **1.8 (parkeado)**: acceso selectivo a cada colapsable de Herramientas de gestión vía env-var (tipo ALLOWED_USERS) — ahora habilitado estructuralmente por los `<details>` de 1.7. "Más adelante".
- Nota UX pendiente de Joe: la "línea gris" del `border-bottom` de `.tool-tabs` es el separador del tab-row (compartido por todas las tabs); no se removió.

### 2026-07-26 (b) — Claude/Joe (Asistente position-aware + catálogo en el routing + Volver sticky transversal)
**Estado: implementado y verificado (unit + DOM en vivo + 139 tests); pusheado a prod. Router determinista (cero tokens).** Robustez del asistente y on-ramp del catálogo al routing:

- **Asistente: orden clásico restaurado.** El reorden del 1.6 (input arriba) arreglaba la línea gris pero rompía el chat. Se volvió a conversación-arriba / input-abajo, manteniendo la línea gris removida (el fix real era sacar el `border-top` del `.assistant-chat-form`, no reordenar). `module.html` + `tools.css` (`.assistant-conversation:not(:empty){margin-bottom:16px}`).
- **Scroll-spy corregido (raíz del "el panel no sabe dónde estoy").** `app.js`: la sección activa es la MÁS PROFUNDA cuyo top ya cruzó una línea de referencia (~120px ≈ topbar 74 + scroll-margin 32), en vez de "la primera que intersecta una banda" (el sliver inferior de la sección anterior ganaba → al redirigir por `#anchor` el panel quedaba una sección atrás y el router creía que no estabas ahí). Reactivo por scroll (rAF) + recompute inicial que cubre el landing por anchor. Corrige un off-by-one en TODOS los módulos.
- **Router position-aware (assistant_routing).** (1) **Skip posicional**: si el destino recomendado es la sección donde YA estás (`active_section`, que viaja del form), se descarta y sigue al siguiente candidato (recurso/cross-module); aplica en las 3 ramas. (2) **Fallback "ya estás acá"**: si no queda nada mejor → "Veo que estás en X… abordémoslo desde otro ángulo" + botón Catálogo (`_catalog_action`) + "Explicame vos". Helpers `_is_current_section`, `_catalog_action`.
- **Catálogo en el routing (on-ramp epic B).** Para consultas de DISPONIBILIDAD (`_is_availability_query`: "tenemos/hay/existe/…") con match FUERTE a nivel dominio, el router lidera con el flujo REAL publicado y MATA la oferta de template: *"Sí — ya tenemos esto implementado: **{cliente} · {dominio}** ({icon} {veredicto}). Miralo en el catálogo."* + deep-link `/catalog/flow`. Reusa `catalog_service.best_domain_match(query, entries)` (exige token de dominio; empate → mejor veredicto; sinónimos stock↔inventario). Lee `catalog_local` runtime; si está vacío → None y cae al comportamiento normal (degrada solo, NUNCA rompe). Verificado: "tenemos extractor de inventarios?" → Biogenesis · Stock/Inventario (🟢 plena), no el template; how-to no se secuestra.
- **"← Volver" sticky transversal.** `.catalog-back` pasó a `position:sticky; top:0` con bg del panel por tema (blanco/#181819) → no desaparece al scrollear, misma regla que el `guide-back` del instructivo. Cubre flow/CDS/connection/security/whats_new.

Próximo del epic B: **CDS individual por nombre técnico** en el routing + **1.1 name-vs-reality** + **(a) rúbrica proporcional** + **1.2 loop diagnóstico⇄automatización**.
