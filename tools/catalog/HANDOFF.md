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

## ✅ Actualización 2026-08-02: se levantó la restricción NO-PUSH
La restricción de abajo ("PILOTO / NO-PUSH") queda **desestimada** — Joe confirmó
que el catálogo (y desde el 2026-08-02, también Portabilidad BDC) ya está pusheado
a producción (commit `9c45091`, ver bitácora). El resto del texto de esta sección
se conserva tal cual por valor histórico (para entender qué se excluía y por qué),
pero **ya NO aplica como regla vigente**: no asumas NO-PUSH para catálogo/portabilidad
sin confirmar con Joe. Sigue firme, aparte, la convención de NUNCA commitear
`reports/`, `catalog_local/`, `catalog_inbound/` (datos runtime efímeros) ni keys.

<details>
<summary>Texto original de la restricción (histórico, ya no vigente)</summary>

Piloto en localhost; NO se pushea a producción hasta nueva orden. Al commitear,
EXCLUIR explícitamente (nunca `git add -A`):
- `tools/catalog/` (generador, servicio, este HANDOFF)
- Cambios de catálogo en `webapp/main.py`, `webapp/services/{assistant_service,admin_tools,content_service}.py`
- `webapp/templates/partials/catalog_*.html`, `webapp/templates/catalog_flow.html`, `webapp/templates/catalog_cds.html`, tab Catálogo en `module.html`, botón en `partials/admin_panel.html`
- Datos runtime `catalog_local/APIs/`, `catalog_inbound/APIs/` (código CDS, git-excluidos)
- `webapp/static/tools.css` (`.catalog-*`/`.ln-*`/`.lineage-*`), `webapp/static/app.js` (handlers del catálogo)
- Datos runtime git-excluidos/efímeros: `catalog_local/`, `catalog_inbound/`
- Ninguna key (OpenAI/Graph) se pushea.

</details>

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
- **"← Volver" sticky transversal.** `.catalog-back` es `position:sticky; top:-48px` (el `-48px` contrarresta el `padding-top:48px` del scroller `.module-content`, si no el sticky pega en el borde del padding y el contenido asoma en esa franja por encima del Volver → bleed). Con eso pega en el tope REAL y el contenido scrollea POR DEBAJO. Bg del panel por tema (blanco/#181819), texto plano (no chip). Verificado con elementFromPoint (la franja superior la ocupa el Volver, no el contenido). Cubre flow/CDS/connection/security/whats_new (todos en `<main class="module-content">`).

Próximo del epic B: **CDS individual por nombre técnico** en el routing + **1.1 name-vs-reality** + **(a) rúbrica proporcional** + **1.2 loop diagnóstico⇄automatización**.

### 2026-07-26 (c) — Claude/Joe (Detalle de conexión type-aware: JDBC genérico + feedback por sección)
**Estado: implementado y verificado (139 tests + render en vivo del partial); pilot/local, NO pusheado.** El detalle de conexión asumía formato SAP para todos los tipos; PostgreSQL (`typeId=GENERICJDBC`) imprimía campos vacíos (Connection type / Application Server / System Number / Mandante / ID / Idioma) y grupos que no aplican con "-". Fix en 3 archivos (dos capas del pipeline JSON → `.md` → parse → HTML, sin alambre):

- **Generador (`generate_space_catalog.py`)**: `_connection_info` ahora es type-aware — para JDBC genéricos, además de `ConnectionProperties.ConnectionInfo` (vacío en JDBC) toma `ConnectionProperties.configurations` (`dbtype`, `driverClass`, `url`) y el `kind` del objeto (`repository.remote`). El whitelist del render de "Connection Info" incorpora `dbtype`, `driverClass`, `url`, `kind`. Efecto: el `.md` de PostgreSQL ahora emite esos parámetros (antes no tenía sección Connection Info). Regenerado vía `python -m tools.catalog.generate_space_catalog --all catalog_local/.csn_cache --out catalog_local/arquitectura` (el `generator_sig` cambia al editar el código → `force_all`).
- **Webapp (`main.py`)**: `_catalog_is_generic_jdbc(conn)` (por `type_id==GENERICJDBC` o presencia de driverClass/url) + rama JDBC en `_catalog_connection_rows`, análoga a la de MSSQL. Grupos para JDBC: **Conectividad** = `JDBC driver class` (driverClass) + `JDBC URL` (url); **Cloud Connector** = solo `Configurado` (se descartan Location ID / Virtual Destination / Virtual Host / Virtual Port para no imprimir "-"); **Autenticación** = `Modo credencial` (credentialMode, ya en props); **Remote Tables** = `Data Provisioning` (adapterName) + `Data Access` (kind) + agente/estado; **Replication Flows** se OMITE si la conexión no declara la capability (`_catalog_has_feature(..., "Replication Flows")` False cuando FEATURES REPLICATION FLOW=No). El bloque SAP por defecto queda intacto (verificado: ABAP sigue con host/mandante/sysid/idioma completos).
- **Feedback por sección (`partials/catalog_connection_content.html`)**: cada `<section>` del detalle incluye ahora `partials/catalog_section_feedback.html` (dominio=type_id, slug=s.slug) — faltaba en el catálogo de tipos de conexión (solo estaba el del SVG/lineage). Reusa los endpoints `/catalog/section-feedback*` existentes.

Verificado en vivo: PostgreSQL renderiza Conectividad(JDBC)/Cloud Connector(solo Configurado=No)/Autenticación(technicaluser)/Remote Tables(CamelJdbcAdapter · repository.remote), SIN grupo Replication Flows, con botonera 👍👎 en cada sección. ABAP sin regresión.

### 2026-07-26 (d) — Claude/Joe (Router: guardrail de objeto + conexiones como evidencia)
**Estado: implementado y verificado (139 tests + reproducción del caso + deep-link full-page en vivo); pilot/local, NO pusheado. Router determinista (cero tokens).** Caso reportado por Joe: en módulo 01, "cómo configurar PostgreSQL?" devolvía el recurso curado **A04 · SAP Help SAC: Configure Workspaces** (SAC+workspace no tiene relación con PostgreSQL) y el catálogo NO intervenía. Dos bugs independientes:

- **Bug 1 — falso positivo por VERBO, no por OBJETO.** `_score_asset_candidate` puntuaba el SAC en 21 casi entero por `configure ↔ configurar` (verbo genérico); "postgresql" no está en NINGÚN recurso del corpus y aun así ganaba. **Fix A (guardrail de objeto)** en `core/routing_scorers.py`: nuevos `_query_object_tokens(question)` (tokens del objeto = significativos menos verbos genéricos `configur/instal/conect/...` y dominio `datasphere/referencia/modelo/...`) y `_asset_covers_query_object(question, asset)` (si la consulta nombra un objeto específico, el asset ganador debe compartirlo por token específico O eje de plataforma SAC/BW/PowerBI; si no, se descarta). Se aplica en `_find_best_asset` y `_find_cross_module_asset` (el SAC venía de A04, cross-module). Resultado: "configurar Snowflake" (no lo tenemos) ya no devuelve el SAC → cae a sección/IA. Casos buenos intactos (DP Agent, OpenID, Cloud Connector tienen su objeto en keywords).
- **Bug 2 — el catálogo no intervenía (dos causas).** (a) El on-ramp del catálogo solo disparaba para DISPONIBILIDAD ("¿tenemos X?"); "cómo configurar X" es how-to. (b) `_find_matching_catalog` solo lee FLUJOS (`load_catalog`); las CONEXIONES (`load_architecture_connections`, donde vive `Biogenesis · PostgreSQL · Generic JDBC`) no estaban cableadas. **Fix B** en `webapp/services/assistant_routing.py`: `_find_matching_connection(question, pack)` (match fuerte: token del objeto ∩ identidad de la conexión = nombre técnico/type_label/type_id/business_name) + `_catalog_module()` helper. En `route()`, tras el lead de flujos, un lead de CONEXIÓN para `_is_asset_seeking_query OR _is_availability_query`: *"Referencia: **{cliente}** ya tiene una conexión **{nombre}** ({type_label}) resuelta en Datasphere…"* + deep-link `/catalog/connections/detail`. Decisión de Joe: la conexión LIDERA como evidencia (no acompaña).
- **Deep-link full-page.** `/catalog/connections/detail` solo devolvía el partial (bare). Ahora, igual que `/catalog/flow`: HX-Request → fragmento; navegación directa → página completa con shell+nav (nuevo `templates/catalog_connection.html`, espejo de `catalog_flow.html`). Verificado: título "Biogenesis · Generic JDBC · Catálogo Danwor", hero/diagrama/nav OK.

Verificado (router determinista): "configurar PostgreSQL" → conexión Biogenesis PostgreSQL; "configurar SQL Server" → conexión MSSQL; "configurar Snowflake" → sección (guardrail mató el SAC); "configurar DP Agent"/"OpenID" → su how-to correcto (sin romper); "tenemos extractor de inventarios?" → sigue liderando el FLUJO Stock/Inventario; "qué es PostgreSQL"/"diferencia PostgreSQL vs MSSQL" → None/IA (no secuestran). 139 tests verdes.

### 2026-07-26 (e) — Claude/Joe (Epic B (a): rúbrica de cumplimiento PROPORCIONAL)
**Estado: implementado y verificado (139 tests + regeneración real de catalog_local + medición antes/después); pilot/local, NO pusheado.** El motor era todo-o-nada: cualquier hallazgo `alto` → `parcial`. Una excepción localizada (1 tabla cruda periférica) sacaba la misma nota que un flujo crudo de punta a punta (caso Despachos, que necesitaba override manual).

- **Decisión de arquitectura (Joe delegó "lo más apropiado en mirada de arquitectura").** Rúbrica PROPORCIONAL + CENTRALIDAD, no umbral absoluto (el ≤1 absoluto es frágil: 3 crudas sobre 40 daría parcial y 1 sobre 2 daría buena — al revés de lo que importa). En `generate_catalog.evaluate_compliance`:
  - La materialidad se mide sobre la **FAMILIA ABAP** del flujo (`ideal` ABAP_CDS + `acceptable_view` NSDM_V_* + `poor_raw` ABAPTABLES), NO sobre todos los orígenes: los extractores SAPI/BW son otro eje y diluían la métrica. (Alineado además con la mirada del senior: "10/11 orígenes ABAP desacoplan".)
  - `poor_raw` → **`alto`** (sistémico → `parcial`) si alimenta el hecho/cubo (`_is_cube`) o es material (> 1/3 del sourcing ABAP); → **`medio`** (reserva puntual, no baja a parcial) si es minoritaria y periférica.
  - Nuevo tier de veredicto: tras `if altos`, si hay un `abap_direct` en `medio` → **`buena`** con apto "Reutilizable con reserva puntual — origen crudo minoritario y periférico". El RATIO ("10 de 11 orígenes ABAP desacoplan vía CDS/vista estándar; 1 cruda") vive en la EVIDENCIA del hallazgo (dinámico), no en la etiqueta (respeta el enum cerrado de 3 categorías). `dual_source` sigue siendo `alto` siempre (paralelismo = smell de gobierno sistémico).
- **Medición antes/después (regeneración real):** Despachos 🟠 parcial → 🟡 **buena** SOLO (sin override); Stock/Inventario 🟢 plena (sin cambio); Danone General 🟡 buena BW Bridge (sin cambio); **Loma General 🟠 parcial CORRECTAMENTE** (muchas crudas + múltiples `dual_source` ERP↔S4 en paralelo = sistémico, la rúbrica NO lo bajó). La rúbrica distingue la reserva localizada del problema sistémico.
- **Override de Despachos ahora REDUNDANTE.** `manual.compliance_veredicto_confirmado: buena` ya no hace falta (el AUTO llega solo a buena). PENDIENTE decisión de Joe: quitarlo para que surja el apto rico "reserva puntual" (con override presente, `catalog_service` muestra el canónico `_APTO_ESTANDAR[buena]` = "Reutilizable con nota de especificidad"; el hallazgo con el ratio se muestra igual en ambos casos). `notas_senior` puede quedar como contexto interno.
- DEUDA aún abierta: las frases de apto están duplicadas (`evaluate_compliance` ↔ `catalog_service._APTO_ESTANDAR`) — fuente única pendiente.

Próximo del epic B (secuencia): **1.1 name-vs-reality** (cruzar nombre `ZCDS_…` que pretende API corporativa contra el origen real `ABAPTABLES.NSDM_V_*`) → **1.2 loop diagnóstico⇄automatización** → **CDS individual por nombre técnico en el routing**.

### 2026-07-26 (f) — Claude/Joe (Epic B 1.1: nombre-vs-realidad + tests de la rúbrica)
**Estado: implementado y verificado (146 tests: +7 nuevos; regeneración real); pilot/local, NO pusheado.**

- **1.1 name-vs-reality** (`generate_catalog.evaluate_compliance`, nuevo Eje 4): un artefacto cuyo NOMBRE lleva `ZCDS` se presenta como CDS view corporativa (extracción desacoplada vía API corporativa); si su ORIGEN real NO es `ABAP_CDS.*` (p. ej. `ABAPTABLES.NSDM_V_*` vista estándar, o tabla cruda), el nombre no refleja el patrón real. Finding `name_vs_reality` severidad **nota** (transparencia, NO baja veredicto — la calidad del desacople ya la puntúa el Eje 1). Verificado sobre datos reales: cae SOLO en `1TR_ZCDS_DSP_FACT_MCHB→ABAPTABLES.NSDM_V_MCHB` (Stock/Inventario, que sigue 🟢 plena); los otros 14 `ZCDS_*` mapean a `ABAP_CDS.ZCDS_*` y no se marcan. Valor: expone que un "FACT ZCDS" es en realidad un wrapper de vista NSDM, matiz que el veredicto de titular esconde.
- **Tests** (`tests/test_compliance_rubric.py`, 7): todo-ideal→plena; cruda periférica minoritaria→buena·reserva (hallazgo `medio` + ratio "10 de 11" en evidencia); cruda en el hecho→parcial (`alto`); cruda material >1/3→parcial; dual_source→parcial; name_vs_reality marca ZCDS→NSDM_V y NO marca ZCDS→ABAP_CDS. Blindan (a) y 1.1.
- Umbral confirmado por Joe (delegó "mirada de arquitectura"): material = > 1/3 del sourcing ABAP. Override de Despachos: se DEJÓ (confirmación explícita del senior, inofensiva).

Próximo del epic B: **1.2 loop diagnóstico⇄automatización** (se apoya sobre (a)+1.1, ya disponibles) → **CDS individual por nombre técnico en el routing**.

### 2026-07-26 (g) — Claude/Joe (Epic B 1.2: bucle diagnóstico ⇄ automatización — reporte de reconciliación)
**Estado: implementado y verificado (146 tests + corrida CLI y camino webapp + app bootea con la ruta nueva); pilot/local, NO pusheado. Determinístico, sin IA (decisión de Joe).** Cierra el bucle entre las dos miradas del mismo dominio (eje EXTRACCIÓN):
- **Automatización** = `evaluate_compliance` (audita el resultado de los flujos publicados).
- **Diagnóstico** = árbol `03_extraction_options` (criterio prescriptivo: hojas con `decision`/`next_steps`/`notes`) + `reports/lessons.yaml` (lecciones de campo cosechadas) + el override del senior en cada flujo.

Forma (elegida por Joe): **reporte en Analítica** (encaja con `reports/`, NO toca el render de flujos). Nuevo script raíz `analyze_reconciliation.py` (espeja `analyze_cases.py`: `main()` + `write_report`), escribe `reports/reconciliation.yaml` + `reports/reconciliation_summary.txt`. Wiring: `admin_tools.run_reconciliation_analysis()` → ruta `POST /admin/reconciliation/analyze` → botón "⚖️ Reconciliación criterio" en `admin_panel.html` (colapsable, modo diagnóstico).

Cierra el bucle en ambas direcciones (determinístico):
- **auto → DESAFÍA al diagnóstico:** `senior_divergences` (flujos donde veredicto-auto ≠ override-senior) + `catalog_audit` (sourcing ABAP agregado: desacople rate, crudas, name-vs-reality por flujo) contrastado contra el criterio prescriptivo del árbol.
- **diagnóstico → ENRIQUECE al auto:** `field_lessons` (lecciones del eje extracción, dedup, con outcome) como criterio homogéneo a incorporar. `tree_criteria` expone las hojas recomendadas.
- **reconciliation:** observaciones determinísticas que cruzan ambas (ej. "el árbol prescribe CDS-as-API; la auditoría muestra 22/52 ABAP desacoplan (42%) — brecha a revisar", "sin divergencias auto↔senior: el criterio convergió").

Corrida real sobre catalog_local: 5 flujos, **0 divergencias auto↔senior** (¡el criterio convergió! — porque (a) hizo que el auto de Despachos coincida solo con el juicio del senior, sin necesitar override), 30 excepciones crudas auditadas (28 en Loma Stock — sistémico, ya `parcial`; 1 en Despachos — A004, la reserva puntual), 4 lecciones de campo. Nota: `classify_extraction` está replicada en el script (comentario marca la paridad con `generate_catalog`) — candidata a fuente única junto con `_APTO_ESTANDAR` en el refactor de deuda.

Con esto quedan 3/4 del epic B. Falta solo: **CDS individual por nombre técnico en el routing** (el asistente debería resolver una consulta por el nombre de una CDS puntual, ej. "ZCDS_DSP_FACT_MCHB", ofreciendo su código/detalle del catálogo de APIs).

### 2026-07-26 (h) — Claude/Joe (Epic B 4/4: CDS individual por nombre técnico en el routing) — EPIC B COMPLETO
**Estado: implementado y verificado (148 tests: +2 nuevos herméticos); pilot/local, NO pusheado. Router determinista.**
- `assistant_routing._find_matching_cds(question)`: si la consulta nombra una CDS view PUBLICADA (match por substring sobre los nombres reales de `catalog_local/APIs/`, exige marcador `ZCDS`, tolera el prefijo de transporte `1TR_`, ante varios gana el más largo), devuelve su nombre. En `route()` se cablea PRIMERO (señal más precisa: nombre técnico exacto): lidera con *"**{cds}** es una CDS view del catálogo de APIs corporativas… Acá está su definición y código."* + deep-link a `/catalog/cds?name=…` (ruta full-page ya existente).
- Verificado: "que hace ZCDS_DSP_FACT_LIPS?" / "1TR_ZCDS_DSP_DIM_ADRC" → lidera con la CDS; "ZCDS_DSP_FACT_MCHB" → **None (no la inventa)** porque no tiene código corporativo (es el caso 1.1: wrapper de NSDM_V_MCHB, no hay `APIs/ZCDS_DSP_FACT_MCHB.md`) — honestidad; "qué es una CDS view" → sigue a sección/IA (no secuestra). No rompe los leads de conexión (PostgreSQL) ni flujo (disponibilidad).
- Tests herméticos (`tests/webapp/test_assistant_routing.py`, +2): usan `DANWOR_CATALOG_DIR` con un APIs/ temporal (no dependen de catalog_local runtime → no fragilizan CI).

**EPIC B COMPLETO (4/4):** (a) rúbrica proporcional ✓ · 1.1 name-vs-reality ✓ · 1.2 loop diagnóstico⇄automatización (reporte reconciliación) ✓ · CDS individual en routing ✓. 148 tests verdes.
Deuda transversal pendiente (no bloqueante): fuente única de (1) frases de apto (`evaluate_compliance` ↔ `catalog_service._APTO_ESTANDAR`) y (2) `classify_extraction` (generate_catalog ↔ analyze_reconciliation). Sin pushear: feedback single-source, fix JDBC PostgreSQL, routing guardrail+conexiones, y todo el epic B.

### 2026-07-26 (i) — Claude/Joe (Router: lead de INVENTARIO de conectividad)
**Estado: implementado y verificado (149 tests: +1); pilot/local, NO pusheado. Router determinista.** Joe reportó: "¿qué conexiones tenemos resueltas en proyectos?" (módulo 01) devolvía una sección débil ("Configuración de Espacios") en vez del catálogo de conexiones — que ES la pestaña Catálogo del propio módulo 01. Gap: `_find_matching_connection` solo matchea un OBJETO puntual (PostgreSQL); no había handler para el inventario GENÉRICO.
- `assistant_routing._is_connectivity_inventory_query`: término de conectividad (conexion/conexiones/conectividad/orígenes de datos/fuentes de datos) + señal de inventario (availability tenemos/hay/… o cuales/listado/inventario/resuelt/implementad). Gate: `not _is_asset_seeking_query` (un how-to "cómo conectar X" NO es inventario). `_connections_inventory(pack)` agrega tipos + clientes reales de `load_architecture_connections`. `_connectivity_catalog_action` → pestaña Catálogo del módulo 01.
- En `route()` va DESPUÉS del lead de conexión puntual (así "configurar PostgreSQL" sigue liderando con la conexión específica) y ANTES de assets/secciones. Responde con el inventario REAL: *"En el catálogo de Arquitectura & Conectividad tenemos 5 tipos de conexión resueltos en 1 cliente (Biogenesis): ABAP, Generic JDBC, MSSQL, SAP S/4HANA OP, SAPBW…"* + deep-link.
- Verificado: las 3 variantes de inventario → catálogo real; "configurar PostgreSQL" → conexión puntual (sin cambio); "conectar Power BI por ODBC" → how-to (sin secuestro). Test hermético del clasificador (`tests/webapp/test_assistant_routing.py`).

### 2026-07-26 (j) — Claude/Joe (Fix: deep-link del asistente al detalle de catálogo carga en el slot)
**Estado: implementado y verificado (149 tests + render del partial); pilot/local, NO pusheado.** Bug reportado por Joe: en módulo 04, "¿qué modelo de datos tenemos para despachos?" recomienda bien Despachos con botón directo, abre el contenido, pero **ni "← Volver" ni "Abrir CDS View" andan** — mientras que navegando el flujo manualmente sí.
- **Causa raíz (un solo origen para ambos síntomas):** el botón del asistente era un `<a href>` plano → navegación COMPLETA a la página standalone `catalog_flow.html`, que NO tiene `#catalog-detail-slot` ni el modo `catalog-detail-open`. "Abrir CDS View" hace `hx-target="#catalog-detail-slot"` (main.py:3155) → target inexistente → no hace nada. "Volver" (`dwCatalogBack`) chequea `catalog-detail-open` → ausente → cae a `window.location.reload()`. El card MANUAL, en cambio, carga por HTMX en el slot (`htmx:afterSwap` agrega `catalog-detail-open`) y ambos botones funcionan.
- **Fix:** `AssistantRouteAction` ganó un flag `embed`. Los leads de **flujo** y **conexión-detalle** lo setean en `True`. En `assistant_route_actions.html`, una acción `embed` renderiza `hx-get={url} hx-target="#catalog-detail-slot" hx-swap="innerHTML"` (href queda de fallback para abrir en pestaña) → replica EXACTO el camino del card manual → Volver + CDS funcionan. Las acciones no-embed (módulo/externas) siguen igual.
- **Alcance:** flujo (bug reportado) + conexión-detalle (misma máquina `dwCatalogBack`). CDS-por-nombre del asistente NO se tocó: su "← Volver al flujo" necesita cliente+dominio que ese lead no aporta (caveat pendiente: darle un Volver genérico o pasar contexto). Las páginas standalone (`/catalog/flow` por URL directa) siguen con Volver=reload/CDS-muerto — limitación pre-existente, fuera de alcance.

### 2026-07-26 (k) — Claude/Joe (Router: lead de INVENTARIO de CDS + fix Volver del CDS sin flujo)
**Estado: implementado y verificado (151 tests: +2); pilot/local, NO pusheado.** Joe: en módulo 03, "¿qué CDS tenemos ya armadas?" devolvía el **Template CDS** (un how-to) en vez del inventario real de las 11 CDS views del catálogo de APIs.
- `assistant_routing`: `_list_cds_names()` (refactor compartido con `_find_matching_cds`), `_is_cds_inventory_query` (términos cds/vista cds/extractor/api corporativa + señal de inventario; gate `not _is_asset_seeking_query` → "cómo creo una CDS" sigue yendo al template), `_cds_inventory()` (agrupa FACT/DIM/otras). En `route()`, tras el lead CDS-por-nombre: responde con el inventario REAL ("11 CDS views ya armadas: 4 FACT (…); 6 DIM (…); TSPAT") + un botón `embed` por CDS que abre su código en el slot (`/catalog/cds`). No hay página índice de APIs, por eso el valor va en el mensaje + botones por CDS.
- **Fix Volver del CDS sin contexto de flujo**: `catalog_cds_content.html` — si hay cliente+dominio → "← Volver al flujo" (como antes); si NO (CDS abierta por nombre/inventario desde el asistente) → "← Volver" genérico (`dwCatalogBack`, cierra el detalle y restaura el módulo). Resuelve el caveat de la entrada (j). El lead CDS-por-nombre pasó a `embed=True`.
- Verificado: "qué CDS tenemos armadas" / "cuales extractores tenemos" → inventario + 11 botones embed; "cómo creo una CDS" → sección + template (sin secuestro); "mostrame ZCDS_DSP_FACT_LIPS" → embed. Tests herméticos (clasificador + inventario con APIs/ temporal).
Follow-up (chip task_fe94f4f6) sigue vigente para generalizar inventario a **flujos** y **seguridad**.

### 2026-07-26 (l) — Claude/Joe (Router: inventario de dominios/modelos + burbujas con bullets + guard comparativo)
**Estado: implementado y verificado (152 tests: +1); pilot/local, NO pusheado.** Joe: "¿qué modelos de datos / dominios tenemos desarrollados?" caía a secciones de módulo 02 (Seguridad) en vez del catálogo de flujos.
- `assistant_routing`: `_is_domain_inventory_query` (términos modelo de datos/modelos analiticos/dominio(s)/flujo(s)/caso(s) de uso + señal de inventario), `_domains_inventory(pack)` (dedup por cliente+dominio, orden por veredicto plena→buena→parcial), `_domains_catalog_action` (pestaña Catálogo del módulo 04). Lead tras el de conectividad: inventario REAL de los 5 flujos con veredicto + un botón `embed` por dominio que abre el flujo en el slot. El puntual "modelo de datos para despachos" sigue liderando con Despachos (el lead de disponibilidad corre antes).
- **Guard comparativo/conceptual** en los 3 detectores de inventario (`not _is_conceptual_or_comparative_query`): arregla un falso positivo real — "Power BI puede consumir modelos analíticos? existe diferencia ODBC/OData?" tenía "modelos analiticos"+"existe" y era secuestrada como inventario; ahora vuelve a resolverse como comparativa (sección + recurso relacionado).
- **Burbujas con bullets** (pedido de Joe: no texto corrido): los 3 inventarios (conectividad/CDS/dominios) pasaron de un párrafo run-on a lead + lista markdown (`- **item**`), que `render_llm_html` (markdown+sane_lists) renderiza como `<ul><li>`. Aprendizajes: el em-dash `—` en un bullet lo parte en dos `<li>` (lo reemplacé por paréntesis); un párrafo CTA después de la lista se absorbe en el último `<li>` (el `\n\n` se colapsa) → el CTA se movió al lead y la lista queda al final.
- Tests: `_is_domain_inventory_query` (clasificador) + el de Power BI vuelve a verde.
Follow-up (chip task_fe94f4f6): ya cubre CDS y dominios; queda **seguridad** para cerrar el patrón. Y opcional: página índice navegable del catálogo de APIs.

### 2026-07-26 (m) — Claude/Joe (Feedback por sección en el catálogo de Seguridad)
**Estado: implementado y verificado (render + 152 tests); pilot/local, NO pusheado.** Joe: en el catálogo de seguridad (roles) faltaban las botoneras minimalistas por sección (solo estaba la del SVG/diagrama). Mismo fix que en conexiones (entrada del detalle JDBC): en `partials/catalog_security_content.html`, cada `<section>` del detalle incluye ahora `partials/catalog_section_feedback.html` (cliente=entry.cliente, dominio=entry.criterio, slug=s.slug). Reusa los endpoints `/catalog/section-feedback*` y el mismo `entry`/`pack` que ya usaba el feedback del diagrama. Cubre roles y DAC (mismo template).

### 2026-07-26 (n) — Claude/Joe (Criterio espejado con SOLIDEZ: fuente única + invariantes + reconciliación automática; y estilos reutilizados)
**Estado: implementado y verificado (155 tests: +3 invariantes); pilot/local, NO pusheado.** Respuesta al pedido de Joe: pasar de un reporte manual ("botón Reconciliación") a que el criterio se mantenga espejado transversalmente **solo y con solidez**. Decisión: lecciones de campo como **notas automáticas** (nunca cambian el veredicto solas — se preserva la autoridad del senior).

- **Fuente ÚNICA del criterio** (mata el drift en la raíz, no lo reconcilia después): nuevo `tools/catalog/compliance_criteria.py` (sin imports: `VERDICTS`, `APTO_CANONICAL`, `classify_extraction`). Lo importan los tres consumidores → `generate_catalog` (ponderación), `catalog_service` (exhibición), `analyze_reconciliation` (reconciliación). Antes había 2 copias de `classify_extraction` (gen + recon) y 2 de las frases `apto` (evaluate_compliance + catalog_service._APTO_ESTANDAR). Ahora `_APTO_ESTANDAR is APTO_CANONICAL` (mismo objeto) y `classify_extraction` es el mismo objeto en los 3. El generador construye plena/parcial/BW desde `APTO_CANONICAL` + sufijo (la parte canónica no puede diverger).
- **Invariantes como tests** (`tests/test_compliance_rubric.py`, +3): fuente única del clasificador (identidad de objeto), fuente única del apto (identidad + derivación), y la reconciliación detecta divergencia auto↔senior. Si alguien reintroduce criterio por copia → rojo.
- **Reconciliación AUTOMÁTICA en la regeneración**: `analyze_reconciliation.run(pack, cat_dir) → (report, summary)` (refactor reutilizable; `main()` lo usa). `admin_tools._reconciliation_line` corre `run()` tras generar el catálogo y agrega una línea al output: "OK: criterio espejado — 0 divergencias" o "ATENCIÓN: N divergencia(s) auto↔senior a resolver → …". Ya no es un botón aparte (el botón sigue disponible on-demand). Nunca bloquea la regeneración.
- **Feed de lecciones limpio**: dedup normalizando acentos (la misma lección con/sin tildes era doble) + descarte de ruido (< 8 chars, ej. "xx"). De 4 lecciones con basura a 2 reales.

**Estilos reutilizados (no espejados)**: Joe notó que el contenido del catálogo se veía como "una copia" del de módulos. Causa: `tools.css` redeclaraba `.catalog-detail h2` (font-size clamp más chico, letter-spacing/line-height propios) en vez de heredar el canónico `.content-heading h2` (app.css). Confirmado que el catálogo usa la MISMA estructura `.content-heading` que los módulos (render_sections). Fix: eliminada la redeclaración → el encabezado del catálogo REUSA el del módulo (un solo lugar). El resto de estilos de contenido ya eran compartidos (scopeados a `.methodology`); la tabla de conexión (`table-layout:fixed`) es override funcional legítimo que se queda. PENDIENTE: verificación VISUAL de Joe en tema claro/oscuro (Claude no puede loguearse al preview).

Follow-up abierto (chip): inventario de **seguridad** (cerrar el patrón). Opcional futuro: lecciones que *propongan* ajustes de rúbrica (no solo notas).

### 2026-07-28 — Claude/Joe (How-tos self-service por módulo + ruta genérica + campo `code`)
**Estado: implementado y verificado (155 tests); pushear ahora.** Se completó el instructivo "Cómo publicar" para los 4 módulos con catálogo (01/02/03/04):
- **Ruta GENÉRICA** `/catalog/guide/{slug}` (antes `/catalog/como-publicar` hardcodeaba el slug). Una guía nueva = solo un `guides/<slug>.yaml` + un link, cero código. `/catalog/como-publicar` queda como alias (redirect). Campo `back_module` en el YAML → el "← Volver" vuelve al módulo correcto (dominios→04, orígenes→01, seguridad→02).
- **3 how-tos nuevos**: `como-publicar-dominios.yaml` (era `como-publicar.yaml`), `como-publicar-origenes.yaml`, `como-publicar-seguridad.yaml`. Imágenes reorganizadas en subcarpetas `assets/guia-catalogo/{Dominios,Conexiones,Seguridad}/` (antes todo suelto). `assets_dir` apunta a la subcarpeta.
- **Links de invocación** en la pestaña Catálogo: "¿Falta un Origen?" (módulo 01), "¿Falta un Objeto?" (02), "¿Falta un Dominio?" (03 y 04).
- **Campo `code`** (nuevo, `guides_service`): bloque de terminal/CLI con YAML literal `code: |` (preserva saltos), render `<pre class="topic-code">` monospace oscuro, `white-space:pre-wrap`. Código INLINE en prosa/sub-pasos: backticks `` `…` `` → `_inline()` los convierte a `<code>` ESCAPANDO el contenido (así `< > \ |` y `<campo_id>` se muestran literales). Para `"` en strings YAML: comillas simples o bloque (no dobles, que escapan `\`).
- **"← Volver" del how-to**: pasó de barra `fixed` bespoke a REUSAR `.catalog-back` (sticky, mismos valores), con gutter izquierdo chico (no centrado en el container).
- **Guard comparativo** en los leads de conexión/flujo del router: una comparativa ("Power BI consume modelos? diferencia ODBC/OData") ya no la secuestra el lead de conexión al matchear "OData" (apareció al regenerar catalog_local con una conexión ODATA real).
Pilot local: `catalog_local/` y `reports/` runtime, NO se commitean. Módulo 05 (Reporting) pendiente de instructivo.

### 2026-07-28 (b) — Claude/Joe (Asistente: filtros de catálogo por faceta/veredicto)
**Estado: implementado y verificado (157 tests: +2); pusheado.** 4 casos que Joe reportó (módulo 01) donde el asistente no cruzaba el catálogo:
- **Filtro de conexiones por FACETA** (`_find_connection_facet`): adapter (ABAP/JDBC/OData/MSSQL/Camel), cloud connector, features (Model Import/Remote Tables/Data Flows/Replication Flows). "¿qué conexiones usan adaptador ABAP?" → lista las 4; sin match ("model import") → afirma "ninguna de las N" + "Explicame vos".
- **Filtro de flujos por VEREDICTO** (`_find_flow_verdict`): "referencias/reutilizables/plena"→🟢, "con reservas/parcial"→🟠, etc. "¿qué referencias reutilizables tenemos?" → los flujos plena.
- **"conector(es)"** sumado a `_CONNECTIVITY_TERMS` (inventario de conectividad).
- **No-match** = afirmar el "no" con evidencia + botón "Explicame vos" (explain_module).
- **Guard `_is_howto`** (NUEVO): reemplaza a `_is_asset_seeking_query` SOLO en connectivity-inventory + facet + verdict, porque el heurístico amplio daba falso positivo por SUBSTRING en sustantivos de inventario ("conect·ores"→conect, "model import"→import, "referencia·s"→referencia) y bloqueaba las consultas. `_is_howto` matchea "como" + verbos por PALABRA COMPLETA (`re.findall`), no roots. CDS-inventory y domain-inventory MANTIENEN `_is_asset_seeking_query` (protege "template CDS" → asset). Regresión OK: template CDS→asset, comparativas→sección, configurar PostgreSQL→conexión, inventarios previos intactos.


### 2026-07-30 ? Codex/Joe (Base inicial: Cat?logo de Data Products)
**Estado: implementado como base y validado con smoke test; piloto/local, NO pusheado.** Se agreg? el primer soporte para un cat?logo separado de Data Products, sin mezclarlo con Dominios.
- **Convenci?n elegida:** carpeta por producto bajo `Data Products/<PRODUCT_ID>/...` (ej. `Data Products/DP_SND_SalesDocumentItemCore/productCsn.json`, `serviceDefinitionObject.json`, `serviceEntities.json`). Se prefiere esta estructura sobre archivos planos `PRODUCT_ID_archivo.json` porque el Data Product es el contenedor l?gico y va a escalar mejor para contrato, sample, metadata, documentaci?n y adjuntos.
- **Generador nuevo:** `tools/catalog/generate_data_products_catalog.py`. Lee subcarpetas bajo `Data Products`, arma un `.md` por producto en `catalog_local/data_products/<PRODUCT_ID>.md`, extrae t?tulo, entidades, campos y archivos detectados, y deja frontmatter `auto.tipo=data_product`.
- **Integraci?n de regeneraci?n:** `webapp/services/admin_tools.py` ejecuta el generador de Data Products despu?s de seguridad. Si falla, no bloquea el resto de la regeneraci?n. `tools/catalog/generate_catalog.py` ahora excluye `Data Products` / `data_products` para que no aparezcan como dominio.
- **Servicio/UI:** `tools/catalog/catalog_service.py` carga `CatalogDataProductEntry`. La pesta?a Cat?logo de m?dulos 03/04 ahora tiene preselector `Cat?logo de datos` con `Dominios` y `Data Products`; los Data Products se muestran como tiles y abren `/catalog/data-product` en el slot central. Templates nuevos: `webapp/templates/partials/catalog_data_product_content.html` y `webapp/templates/catalog_data_product.html`.
- **Validaci?n:** `py_compile` OK para los m?dulos tocados. Smoke test local OK: genera `Cat?logo de Data Products regenerado (1 producto)` y el loader devuelve `DP_SND_SalesDocumentItemCore / Sales Document Item Core / 1 entidad / 2 campos`.
- **Pendiente para iterar con Joe:** regenerar desde SharePoint real y ajustar el render del detalle seg?n el contenido real del primer Data Product. La implementaci?n asume que la carpeta `Data Products` cuelga del mismo root que ya descarga el proceso de CSN/SharePoint.

### 2026-07-30 - Codex/Joe (Gobierno de dominios vs. Data Products)
**Estado: implementado y validado localmente; piloto/local, NO pusheado.** Se corrigió la contaminación del selector de dominios por salidas históricas y por carpetas que no son dominios metodológicos.
- **Dominio como fuente de verdad:** `tools/catalog/generate_catalog.py` excluye carpetas técnicas/transversales (`APIs`, `CDS`, `Espacios`, `Conexiones`, `Seguridad`, `Data Products`, `data_products`) y también ignora carpetas raíz `DP_*` para que un Data Product nunca compita como dominio.
- **Limpieza gobernada:** el generador ahora poda salidas obsoletas en `catalog_local` después de regenerar. Solo elimina subcarpetas de dominio generadas por este catálogo, identificables por `flujo_*.md`; no toca `APIs`, `arquitectura`, `seguridad`, `data_products`, `.csn_cache` ni código CDS.
- **Data Products flexible:** `tools/catalog/generate_data_products_catalog.py` soporta tanto `Data Products/<PRODUCT_ID>/*.json` como `<PRODUCT_ID empezando con DP_>/*.json` en la raíz descargada. Esto cubre el caso en que el link compartido de SharePoint descarga directamente el contenedor de productos.
- **Validación:** `py_compile` OK para `generate_catalog.py`, `generate_data_products_catalog.py` y `admin_tools.py`. Corrida local de `generate_catalog.generate_all(...)` removió 5 salidas obsoletas y dejó `catalog_local` con dominios vigentes solamente (`Biogenesis/Stock`, `Danone/Inventario`, `Loma_Negra/Stock` en el inbound local de prueba).
- **Pendiente:** si la regeneración real sigue informando `Catálogo de Data Products: sin productos para generar`, la carpeta `Data Products` no está llegando dentro del root SharePoint configurado para CSN. En ese caso conviene agregar una segunda fuente explícita (env/config) para Data Products, no forzar heurísticas dentro del generador de dominios.

### 2026-07-30 - Codex/Joe (Data Products en SharePoint con prefijos)
**Estado: implementado y validado localmente; piloto/local, NO pusheado.** SharePoint descarga carpetas con prefijos visuales como `01. APIs` y `02. Data Products`, e incluso puede incluir caracteres decorativos delante. Se robusteció la normalización para que el catálogo no dependa del nombre exacto.
- `generate_catalog.py`: normaliza nombres de carpeta antes de excluir `API/APIs/CDS`, `Data Products`, `Seguridad`, `Espacios`, `Conexiones`. `_sync_apis` también usa esta normalización, por lo que soporta `01. API`, `01. APIs`, `APIs` o `CDS`.
- `generate_data_products_catalog.py`: detecta `Data Products` aunque venga como `02. Data Products` u otro prefijo equivalente.
- Data Product: se extrae `businessName` desde los JSON (`Sales Document Item Core`) y se persiste como `auto.business_name`; el tile lo usa como encabezado principal, alineado con el patrón visual de Dominios, dejando `product_id` como metadata técnica.
- Validación local contra `catalog_local/.csn_cache`: dominios quedan `Despachos` y `Stock / Inventario`; Data Products genera `DP_SND_SalesDocumentItemCore.md` con `business_name: Sales Document Item Core`.

### 2026-07-30 - Codex/Joe (Data Products: tile inicial y revisi?n transversal pendiente)
**Estado: implementado y validado localmente; piloto/local, NO pusheado.** Se agreg? la primera experiencia de tiles para `Cat?logo de datos > Data Products`, tomando como base `DP_SND_SalesDocumentItemCore`.
- **Generaci?n:** `tools/catalog/generate_data_products_catalog.py` extrae `businessName`, `dataCategories`, `industries`, `deliveryMode`, entidades y campos desde los JSON del Data Product (`productCsn.json`, `serviceDefinitionObject.json`, `serviceEntities.json`). El markdown generado queda en `catalog_local/data_products/<PRODUCT_ID>.md`.
- **Servicio:** `tools/catalog/catalog_service.py` expone `CatalogDataProductEntry` con `business_name`, `data_categories`, `industries`, `delivery_mode` y `artifact_names`. `artifact_names` filtra entidades con `kind = entity` y normaliza nombres t?cnicos tipo `...:v1.2TD_MD_CUSTOMER` a `2TD_MD_CUSTOMER`.
- **UI tile:** `webapp/templates/partials/catalog_tab.html` muestra el `business_name` como t?tulo principal, conserva badge/veredicto, agrega colapsables `VER DETALLES` (Categor?a, Industria, Delivery Mode) y `VER ARTEFACTOS` (entidades publicadas). Los summaries no deben incluir `+` literal porque el componente ya lo renderiza.
- **Bug corregido:** `/catalog/data-product` en `webapp/main.py` importaba tarde/incorrectamente `render_sections`; se agreg? el import local igual que en dominios/seguridad para resolver `NameError: render_sections is not defined`.
- **Validaci?n:** `py_compile` OK para `webapp/main.py`, `tools/catalog/catalog_service.py`, `tools/catalog/generate_data_products_catalog.py`. Smoke local OK: `Sales Document Item Core`, detalles `Company Data / Energy and Natural Resources / DeltaShare`, artefactos `3TF_SND_SalesDocumentItemCore` y `2TD_MD_CUSTOMER`. Se verific? `replacement_chars=0` y se corrigi? mojibake en `Categor?a`.
- **Pedido expl?cito de Joe para Claude:** hacer una revisi?n transversal de cat?logos (`Dominios`, `Fuentes de Datos`, `Seguridad`, `Data Products`) para detectar duplicaci?n de l?gica, estilos locales divergentes o spaghetti entre cambios de ambos lados. Priorizar reutilizaci?n de patrones existentes: cards, badges, colapsables, render de markdown, feedback de cierre, tablas de cumplimiento y SVG/linaje.
- **Pr?ximo paso:** construir la p?gina central de Data Product al clickear el tile, siguiendo el patr?n del cat?logo de dominios: header + subt?tulo/veredicto + SVG de flujo/linaje + cajas clickeables que naveguen a secciones con detalle t?cnico. El cat?logo de dominios es la referencia m?s cercana, aunque los tags de Data Products sean distintos.

### 2026-07-30 - Codex/Joe (Data Products: p?gina central con linaje)
**Estado: implementado y validado; piloto/local, NO pusheado.** Se complet? la primera p?gina central del cat?logo de Data Products, siguiendo el patr?n visual del cat?logo de dominios.
- `tools/catalog/generate_data_products_catalog.py`: `GENERATOR_VERSION = "data-products-v3"`. El markdown generado ahora conserva `Resumen`, agrega `Entidades publicadas`, crea secciones individuales por entidad publicada (`kind = entity`) y mantiene `Cumplimiento`. Los nombres t?cnicos del servicio se normalizan para mostrar artefactos como `3TF_SND_SalesDocumentItemCore` y `2TD_MD_CUSTOMER`. Si los campos del contrato vienen bajo una descripci?n funcional y no matchean el nombre t?cnico de la entidad, se asignan conservadoramente a la primera entidad publicada sin inventar relaciones.
- `webapp/main.py`: nuevo helper `_catalog_data_product_lineage_svg(product)` que reusa las clases compartidas `.catalog-lineage`, `.lineage-svg`, `.ln-node`, `.ln-edge`, `.ln-node-a`. El SVG muestra `DATA PRODUCT ? ENTIDADES PUBLICADAS`; las cajas de entidad son clickeables y apuntan a las secciones generadas por el mismo criterio de slug.
- `webapp/templates/partials/catalog_data_product_content.html`: incorpora el bloque de linaje entre el hero y el contenido, con feedback de diagrama, igual que dominios/conexiones.
- Validaci?n: regeneraci?n local OK desde `catalog_local/.csn_cache`; smoke OK (`Sales Document Item Core`, artefactos `3TF_SND_SalesDocumentItemCore`, `2TD_MD_CUSTOMER`, slugs del SVG existentes); `replacement_chars=0`; suite completa OK: `158 passed, 185 warnings`.
- Pendiente/para Claude: revisi?n transversal de cat?logos sigue vigente. En particular, evaluar si conviene extraer helpers compartidos para normalizaci?n de artefactos, badges/veredictos, generaci?n de linajes simples y render de cumplimiento, porque Data Products, Dominios, Fuentes de Datos y Seguridad ya comparten patrones visuales y empiezan a duplicar peque?as piezas.


### 2026-07-31 - Codex/Joe (Data Products: linaje completo HANA/HDLF + layout vertical)
**Estado: implementado y validado con smoke/py_compile; piloto/local, NO pusheado.** Se avanzó sobre el catálogo de Data Products para que el linaje deje de mostrar solo el contrato publicado y pueda reconstruir dependencias técnicas desde los JSON de Data Products + exports CSN de linaje.
- **Generador Data Products:** `tools/catalog/generate_data_products_catalog.py` quedó como generador dedicado/no versionado aún en git. Lee `Data Products/<PRODUCT_ID>/...`, genera `catalog_local/data_products/<PRODUCT_ID>.md` y ahora arma `auto.lineage_nodes` reutilizando el parser de dominios (`generate_catalog.load_folder`) por archivo JSON individual para evitar colisiones entre entidades HANA y Object Store con el mismo nombre lógico.
- **Runtime HANA vs. HDLF/Object Store:** el generador marca cada nodo con `runtime: hana` u `object_store`. Si una definición contiene `@DataWarehouse.persistence.hdlf.tableFormat` se considera Object Store; si no, HANA. Las entidades publicadas en HDLF pueden venir con workaround de archivo/nombre `_PUB_` (ej. `3TF_PUB_SND_...`); se normaliza quitando `_PUB_` para conectar con su contraparte HANA (`3TF_SND_...`).
- **Delta capable:** si aparece un artefacto con sufijo final `_Delta`, no se muestra como nodo duplicado. Se colapsa sobre el artefacto base y se marca `delta_capable: true`; el SVG lo renderiza como subtexto `delta capable`.
- **Salida visible limpia:** los IDs internos `hana::...` / `object_store::...` se preservan solo en frontmatter para el grafo, pero no se filtran en las tablas visibles (`Technical Name`, `Deriva de`, `Asociaciones` usan nombres técnicos limpios). También se quitó del texto visible la frase vieja `El detalle se deriva de los JSON técnicos disponibles para el contrato.`
- **SVG Data Products:** `webapp/main.py` ajustó `_catalog_data_product_lineage_svg` y `_catalog_lineage_svg`. El modo simple queda invertido: entidades publicadas a la izquierda y Data Product como destino final. El modo `incluir linaje completo` usa layout vertical para ahorrar ancho: banda HANA arriba, banda `HDLF Object Store` abajo. Las entidades publicadas y el Data Product viven en la misma banda verde HDLF/Object Store; no se usa un tercer color para el producto. Las conexiones intra-HANA usan clase `ln-edge--hana` con línea punteada para distinguirlas visualmente de la publicación hacia HDLF.
- **CSS:** `webapp/static/tools.css` suma estilos compartidos para runtime boxes (`ln-runtime-box--hana`, `ln-runtime-box--object_store`) y subtexto (`ln-node-subtext`), más `ln-edge--hana` punteada. Scopeado al SVG de linaje para no tocar markdown core.
- **Validación:** `py_compile` OK para `webapp/main.py`, `tools/catalog/catalog_service.py` y `tools/catalog/generate_data_products_catalog.py`. Regeneración local OK: `DP_SND_SalesDocumentItemCore`. Smoke del SVG OK: contiene HANA, HDLF Object Store, `delta capable`, `Data Product` y `ln-edge--hana`. No se corrió suite completa en esta pasada.
- **Pendiente deliberado:** Joe pidió evaluar si las flechas/conectores podrían ser clickeables para iluminar conexiones específicas. No se implementó todavía para evitar meter estado/JS frágil antes de validar el layout visual. Si se retoma, hacerlo como mejora puntual sobre SVG paths, sin romper anclas actuales de nodos.

**Actualización 2026-07-31 (post validación visual Joe):** se ajustó el SVG de Data Products para que la banda HDLF/Object Store arranque desde la izquierda con margen interno, la banda superior muestre `HANA Space`, el hover de nodos con el mismo nombre técnico ilumine ambas cajas HANA/HDLF mediante CSS `:has(...)`, y el Data Product solo reciba conexiones desde entidades publicadas `object_store::...` (nunca directo desde HANA). `py_compile webapp/main.py` OK; smoke confirmó upstreams raíz `['object_store::2TD_MD_CUSTOMER', 'object_store::3TF_SND_SalesDocumentItemCore']`.

**Actualización 2026-07-31 (cierre visual Data Products):** tras prueba visual de Joe, el layout vertical quedó afinado: banda HANA arriba con label `HANA Space`, banda HDLF/Object Store abajo arrancando desde la izquierda con margen interno, y el Data Product solo recibe aristas desde entidades publicadas en `object_store::...` (no desde HANA). Las aristas HANA→HDLF quedan punteadas; HANA↔HANA y HDLF↔Data Product quedan plenas. Se agregó hover por nombre técnico compartido para iluminar pares HANA/HDLF equivalentes (ej. `2TD_MD_CUSTOMER`). Validado con `py_compile webapp/main.py` y smoke de upstreams raíz.
**Siguiente pendiente inmediato:** limpiar mojibake visible en la página de Data Products (`?` en tooltips/separadores, `t?cnico`), y luego agregar el botón `support` solicitado para acceso directo a instrucciones/artefacto, idealmente reutilizando el patrón existente de Dominios.

**Actualizaci?n 2026-07-31 (cierre mojibake + support Data Products):** se limpi? mojibake en el generador y en la vista de Data Products usando escapes/entidades seguras para no depender de la consola PowerShell (`?`, `t?cnico`, `Descripci?n`, `Relaci?n`, `S?` donde aplica texto real). Las claves de tablas vuelven a renderizar como `??` tambi?n en el linaje extendido, en vez de `S?`. Se agreg? en el hero del detalle de Data Product un bot?n de soporte con clases compartidas `support-links heading-supports` / `support-link`, apuntando a la gu?a de publicaci?n (`/catalog/guide/como-publicar-dominios?pack=...`) en nueva pesta?a; queda pendiente, si Joe lo pide, separar una gu?a espec?fica `como-publicar-data-products` para no reutilizar la de dominios. Validado con regeneraci?n local de `DP_SND_SalesDocumentItemCore`, `py_compile webapp/main.py tools/catalog/generate_data_products_catalog.py`, y checks sin `t?cnico`, `Descripci?`, `Relaci?`, `| S? |` ni separadores ` ? ` en el markdown generado.


### Actualización 2026-07-31 (conteo Data Products publicado)
- Codex corrigió el contador visual de Data Products en tile y header central: ahora usa `artifact_names` deduplicado en lugar de `entities` crudo.
- Motivo: `auto.entidades` puede contener varias entidades técnicas del CSN/servicio, pero el usuario ve y navega únicamente los artefactos publicados (`3TF_SND_SalesDocumentItemCore`, `2TD_MD_CUSTOMER`).
- Validación: `py_compile webapp/main.py tools/catalog/catalog_service.py`; carga de `catalog_service.load_data_products()` confirma `raw_entities=6` y `published_artifacts=2`.
- Estado: no requiere regenerar catálogo; basta refrescar/reiniciar servidor si la plantilla estaba cacheada.

### Actualización 2026-07-31 (support correcto en Data Product Resumen)
- Codex corrigió el botón de soporte de Data Products: se removió del hero/header principal y ahora se genera dentro de `01 Resumen` mediante `{{support}}` en el markdown generado.
- El soporte se deriva del tag `additionalDocuments` del `productCsn.json`; para este piloto se resuelve por convención contra `additionalDocuments.pptx` y apunta al SharePoint informado: `https://danwor.sharepoint.com/:p:/s/PrcticaAnalytics/IQAuQ_sKiXWKSoy_pXz3Fe5yAUZKhdR-NjSKMCRcu_M5F5Q?e=5yWev3`.
- Nota técnica: `render_sections()` descarta contenido antes del primer `##`; por eso el `{{support}}` debe quedar inmediatamente debajo de `## Resumen`, no antes del heading.
- Validación: `py_compile tools/catalog/generate_data_products_catalog.py webapp/main.py`; regeneración local; `render_sections()` confirma `support-link=True` y URL correcta en la sección `Resumen`.

### 2026-08-02 — Claude/Joe (Data Products: fixes de linaje + vínculo CDS/RF + alineación de metadata con Dominios)
**Estado: implementado y verificado; pusheado a prod.**
- **3 fixes**: tipo de artefacto (DWC_LOCAL_TABLE→"Tabla local"; remota federada→"Tabla remota"; se saca "Origen remoto"; se captura `Entity.repository_type`); cajitas del SVG navegan (slug del ancla = `slugify` del heading real, no `_safe_slug`); conectores cross-band HANA→HDLF en **vertical** (salen por abajo, entran por arriba).
- **Vínculo CDS de extracción** (análogo del `origen_remoto` de Dominios, pero para tablas locales con delta capture): se parsea el **Replication Flow** (`RF_*.json`, `replicationTasks[].sourceObject.name`→`targetObject.name`, recorta `_Delta`), mapea tabla local→CDS (`origen_cds`), y ofrece "Abrir CDS View" reutilizando `/catalog/cds`. Se sincroniza `02. Data Products/01. APIs/` al pool `catalog_local/APIs/` (`_sync_data_product_apis`).
- **Metadata alineada a Dominios sobre fuente única** `generate_catalog.artifact_metadata_rows` (orden/labels/columnas compartidos): headers `Descripción(negro)·ID(dorado)` (`_dp_heading`), sub-headers `#### Campos (N)`, Capa (§28 en HANA / "HDLF · Object Store"), "Origen · vía Replication Flow", "Deriva de · vía Transformation Flow" (HDLF), cumplimiento con el mismo semáforo (`_catalog_dp_render_compliance`). Se quitó el BOM UTF-8 de 2 partials (gap falso) + guard `tests/test_template_hygiene.py`.

### 2026-08-02 — Claude/Joe (Portabilidad BDC: dimensión cross-runtime configurable)
**Estado: implementado y validado (2 incidentes reales + 9 escenarios); pusheado.** Ver doc completo `tools/catalog/PORTABILITY_BDC.md`.
- **Qué es**: estima riesgo de portar dominio/DP a HDLF/BDC/Databricks. **SEPARADA** del cumplimiento de extracción (no lo toca; `config.peso_global=0` → dimensión paralela, cero recálculo de históricos).
- **Matriz configurable** `content_packs/<pack>/portability_bdc.yaml` (crece sin código; seeds de respaldo en `tools/catalog/portability_bdc.py`). Reglas: **PB-001** DecimalFloat monetario→normalizar DECIMAL(38,6); **PB-002** escala inferida alta (escala≥10, ej. 37,19)→cast en el origen; **PB-003** numérico-como-String (nota, ruidosa — match por TOKEN + guarda anti-UOM); **PB-004** derivado sin cast; **PB-005** PK/FK tipo distinto (bloqueante); **PB-010/011** reconocen normalizaciones DECIMAL(38,6)/(15,6) (no penalizan). Estados validado/recomendado.
- **Evaluador** `evaluate_portability(columns, asset_type, matrix)`: modos **dominio** (preventivo) y **data_product** (retrospectivo: safety_check penaliza / adaptación-hint no penaliza). 5 tipos de hallazgo (riesgo/adaptacion/buena_practica/hint/no_evaluable), score 0-100 con **cap por regla** (muchos warnings no hunden el score) + reglas bloqueantes + `no_evaluable` si falta metadata. Semáforo del hallazgo por severidad (riesgo alto→red, medio→orange, nota→amber; adaptación/hint→blue; buena→green).
- **Extractor extendido** (aditivo, `generate_catalog._extract_elements` + query parse): `native` (`@DataWarehouse.native.dataType`), `derived`+`cast` (de las columnas del query), `assoc_col` (columna PK del `on` de la asociación).
- **Wiring**: `generate_catalog.render_flujo` (dominio, sobre TODAS las entidades) y `generate_data_products_catalog._render_product` (DP, sobre el **contrato publicado** = nodos object_store) persisten `auto.portabilidad_bdc`. `catalog_service` lee `entry.portabilidad_bdc` + `manual.portabilidad_ack`. UI: sección **"## Portabilidad BDC"** en dominios (`_catalog_flow_view`) y DP (route), reutilizando `.catalog-status--*`. Telemetría `tools/catalog/portability_telemetry.py` → `reports/portability_bdc.yaml` + línea en la regeneración (`admin_tools._portability_line`). Acknowledgements de senior → `manual.portabilidad_ack` (preservado en regen).
- **Pendiente/decisiones**: PB-003 es ruidosa (calibrar keywords/guardas en el YAML); verificar los PB-005 reales de Loma antes de marcar la regla `validado`. Tests: `tests/test_portability_bdc.py` (12), `tests/test_catalog_artifact_type.py`.

### 2026-08-02 — Claude/Joe (Data Products: resolución de CONTRATO BASE compartido cross-DP, sin duplicar)
**Estado: implementado y validado sobre `SalesDocumentItemCore` + `OpenSalesOrderFulfilment` reales; pusheado.**
- **Problema**: varios DPs comparten ingesta+capa2 (contrato base); no se quiere duplicar archivos en cada carpeta.
- **Diseño (robusto al placement humano)**: la **resolución es por NOMBRE** — `_dp_artifact_index(folders)` (todas las carpetas de DP + `_shared`, con hash) + `_resolve_lineage` arma el linaje de cada DP sobre la **UNIÓN** de archivos y la reachability desde sus entidades publicadas selecciona su subgrafo (los artefactos propios de OTROS DPs quedan afuera solos). **"Compartido" DERIVA del uso** (artefacto usado por ≥2 DPs), NO de la carpeta. `_shared` es **opcional** (higiene); si el contrato base vive dentro de un DP igual resuelve.
- **Gobierno (nudges, no bloquea)** `_shared_governance` → `reports/data_products_shared.yaml` + línea: **base atrapada** (compartido que vive en un solo DP → riesgo si se borra), **drift** (mismo nombre, contenido distinto), **duplicados** redundantes. `generate_all` es **2 pasadas**: (1) resolver todo + computar uso; (2) renderizar con `shared_with` (con qué DPs se comparte, simétrico).
- **UI**: subtexto "compartido" en la caja del SVG (+ ancho de caja factoriza el subtexto), fila "Contrato base · Compartido con `DP_X`" en la metadata, y leyenda **"vía Transformation Flow"** (tooltip + inline sutil) en las punteadas cross-band.
- **Validación real**: 4 artefactos base compartidos (`1TR`, `2TF`, `2TD`, `I_CUSTOMER`); 3 atrapados en `SalesDocumentItemCore` (nudge a `_shared`); linaje del 2º DP completo resolviendo del 1º sin duplicar. Tests: `tests/test_data_product_shared_lineage.py` (4). Total suite **183 verdes**.
- **Housekeeping futuro (charlado con Joe)**: mover el contrato base a una carpeta `_shared` cuando convenga — sigue funcionando y es más rápido (deja de escanear todas las carpetas).

### 2026-08-02 — Claude (Academy: botón "pantalla completa / standalone") — fuera de la familia Catálogos
**Estado: implementado y validado (Python + browser logueado); no toca nada de `tools/catalog/`.**
- **Pedido**: además del embebido de siempre (`/academy/{stem}` dentro del shell del copiloto), poder abrir el MISMO contenido de Danwor Academy en pestaña nueva, a pantalla completa, con su header propio, respetando el tema claro/oscuro.
- **Hallazgo clave**: la ruta standalone YA existía sin saberlo — es la misma `/pack-assets/{pack}/academy/{asset}` que arma el iframe, pero **sin** `embed=1`. `embed=1` es lo que hace que el HTML de storytelling **oculte** su propio `.topbar` (CSS inyectada `html[data-dw-embed] .topbar{display:none}` en `pack_asset`, `webapp/main.py`); sin ese flag, el asset muestra su topbar nativo tal cual. Cero lógica nueva de render — solo cambia la query string.
- **Theme sin flash**: `pack_asset` YA soportaba override de tema por query param genérico (cualquier query key pisa el prop, incluido `theme`, con fallback a la cookie `dw-content-theme`) — tampoco hubo que tocar esa parte. Solo había que **generar el link** con `?theme=<valor vivo>`.
- **Cambios**:
  - `webapp/main.py` (`academy_page`): nueva variable `standalone_href = /pack-assets/{pack}/academy/{asset}?branding=danwor` (sin `embed=1`, sin `academy_feedback`), pasada al template.
  - `webapp/templates/academy.html`: botón `<a class="academy-standalone-btn" :href="'{{ standalone_href }}&theme=' + contentTheme" target="_blank" rel="noopener">` dentro de `.academy-stage`, antes del iframe. El `theme` viaja atado en vivo al `contentTheme` de Alpine (`copilotLayout`), no hardcodeado — si el usuario togglea tema y clickea, abre en el tema correcto.
  - `webapp/static/layout.css`: `.academy-standalone-btn` (34×34, `position:absolute; top:14px; right:14px`, mismos tokens que `.theme-toggle` del header — `var(--card)/--line/--gold`, sin paleta nueva). Icono SVG "external-link" (cuadrado con la esquina superior-derecha abierta + flecha), mismo stroke que los demás icon-buttons del header.
  - Ubicación deliberada: **solo** en `academy.html` (no en `module.html`), así que módulos inmersivos (mismo patrón `.academy-stage`/iframe) NO heredan el botón — cumple "no en módulos ni catálogo".
- **Tests** (`tests/webapp/test_routes.py`): botón presente en `/academy/{stem}` con href sin `embed=1`; ausente en páginas de módulo; `/pack-assets/.../academy/*.html` respeta `?theme=` con fallback a cookie (casos: sin nada→light, query pisa cookie, cookie sin query). Suite completa: **186 verdes**.
- **Verificado en browser** (sesión forjada localmente vía `auth_service.create_session`, sin usar Azure AD real): botón visible top-right sobre el iframe de Academy, href correcto (`branding=danwor&theme=light`, sin `embed=1`); al navegar directo a esa URL standalone, el topbar propio del HTML se muestra (`display:block`) y `data-dw-theme="light"` — cero flash.

### 2026-08-02 — Claude/Joe (Academy standalone: ronda 2 — feedback visual + 3 bugs reales)
**Estado: implementado y validado (Python + browser); 188 verdes.** Sigue fuera de `tools/catalog/`.

Feedback visual de Joe sobre el botón "pantalla completa" (contrastaba demasiado, tile blanco + badge gris oscuro):
- `webapp/static/layout.css` `.academy-standalone-btn`: pasó de "chrome del header" (fondo `var(--card)` + borde + ícono dorado) a **camuflado con el tile**: sin borde, fondo `#fff`/`#181819` por tema (`.content-theme-light/dark`), ícono gris apagado (`#9a9aa4`/`#5c5c66`) que solo se pone dorado en `:hover`. Offset horizontal pasó de `right:14px` a `right:clamp(1rem,3vw,2.5rem)` — mismo clamp() que usa el `.rail` de puntos del propio asset (`dw-academy.css`), para quedar aproximadamente en la misma columna vertical (no es 100% pixel-perfect: el `vw` del botón se calcula sobre el viewport COMPLETO del copiloto y el del rail sobre el viewport del iframe, que son más angostos — geometrías de documentos distintas, sin JS de sincronización no hay manera exacta, pero quedan a pocos px).

**Bug 1 — logo del topbar se perdía en tema claro** (screenshot de Joe: header blanco con logo/tag casi invisibles vs. header oscuro legible): 3 de los ~12 HTML de Academy (`el-faro`, `dos-canales`, `el-guardrail`, `las-4-compuertas`) ya hardcodeaban `.topbar{background:#0c0c0d}`; el resto heredaba `background:var(--bg)` (blanco en tema claro) de `dw-academy.css` o de su propio `<style>` local (`paradigmas.html`, `ia-agentica.html` redefinían `.topbar` completo con la misma `var(--bg)`). Fix: `background:#0c0c0d` fijo en `dw-academy.css` (afecta a `cowork-vs-code`, `cuanta-confianza`, `entrena-tu-chef`, `desafio`, `sac`, `databricks`, `datasphere`) + mismo cambio puntual en `paradigmas.html` e `ia-agentica.html` (que pisaban el CSS compartido con su propio bloque). Los 4 que ya estaban bien no se tocaron.

**Bug 2 — standalone en tema oscuro no pintaba oscuro pese a `data-dw-theme="dark"` correcto**: `pack_asset` (`webapp/main.py`) inyecta `html[data-dw-theme="dark"],html[data-dw-theme="dark"] body{background:transparent!important}` para que en el embed normal se vea el canvas oscuro del copiloto DETRÁS del iframe. Esa regla NO estaba scopeada a `[data-dw-embed]` → en standalone (pestaña propia, sin nada detrás) el body quedaba transparente y el navegador mostraba blanco por debajo, aunque las variables de color (`--bg`, `--ink`, etc.) sí resolvían bien a la paleta oscura. Fix: la regla ahora es `html[data-dw-embed][data-dw-theme="dark"]...`. Test de regresión: `test_academy_asset_dark_theme_keeps_opaque_background_when_standalone`.

**Bug 3 — el botón "maximizar" abría siempre el asset de ATERRIZAJE, no el que se estaba viendo**: si dentro del iframe embebido navegás a otro asset (p. ej. paradigmas→datasphere, vía los finale-cards con `data-carry-branding`), el iframe cambia de documento pero `standalone_href` era un string fijo calculado UNA vez en el SSR de `academy_page` para el stem de aterrizaje. El copiloto YA tenía el mecanismo para saber qué asset se está viendo (`postMessage({type:'danwor-academy-nav', asset})` que el HTML embebido manda en cada load, usado hoy para resaltar el nav izquierdo) — se reusó: `app.js` (`copilotLayout`) ahora guarda `academyAsset` en ese mismo listener, y el botón construye su URL en el cliente: `'/pack-assets/{{ pack }}/academy/' + (academyAsset || '{{ asset }}') + '?branding=danwor&theme=' + contentTheme`. Se eliminó `standalone_href` de `main.py` (ya no hace falta precalcular la URL completa, alcanza con pasar `asset` crudo).

**Bug/feature 4 — sin forma de volver tras navegar dentro del iframe embebido**: en pantalla completa hay "← Volver" nativo (topbar del propio HTML), pero embebido no había nada — el botón Atrás del navegador no sirve porque el historial que se mueve es el del IFRAME, no el del copiloto. Pedido de Joe: emular el "← Volver" que ya existe en el detalle de Catálogo (dentro de módulo → Catálogo → detalle). Se agregó `.academy-back-btn` (mismo icon-button que el de maximizar, espejado a la izquierda, ícono flecha) + método `academyGoBack()` en `app.js` que hace `iframe.contentWindow.history.back()`. Visibilidad: `x-show="academyAsset && academyAsset !== '{{ asset }}'"` — **ojo con el intento fallido**: primero se gateó contando mensajes recibidos (`academyNavCount`, mostrar si >1, asumiendo que el mensaje del load inicial ya cuenta 1); resultó que el `x-init="init()"` del shell SUMADO al auto-init nativo de Alpine.js (cuando el objeto de datos tiene un método llamado `init()`, Alpine lo llama solo, además de lo que dispare `x-init`) ejecuta `init()` **dos veces**, duplicando cualquier listener registrado adentro — el contador arrancaba en 2, no en 1, y el botón aparecía ya en el landing. Joe lo detectó ("no debiera aplicar en el landing... en academy (paradigmas) no debiera estar"). Se cambió a comparar `academyAsset` contra el asset de aterrizaje en vez de contar mensajes — inmune al doble-init. **Nota para quien toque `copilotLayout` de nuevo**: el doble-init es real y preexistente; cualquier lógica que cuente invocaciones de `init()` o eventos registrados adentro (no solo declarativa/idempotente) va a duplicarse.
- **Pendiente/aclarado, no bloqueante**: si el "← Volver" navega hacia atrás y el navegador restaura el documento previo desde **bfcache**, el script inline de esa página (que manda el propio `postMessage`) no se re-ejecuta (bfcache no re-corre scripts) → `academyAsset` puede quedar desactualizado un tick hasta la próxima navegación real. No se intentó resolver (afecta también al resaltado del nav izquierdo, que ya vivía con esta limitación antes de esta sesión).
- **Pregunta de Joe sobre producción (DigitalOcean detrás de `danwor.com.ar/copiloto` vía `<iframe>`)**: el link del botón usa ruta RELATIVA (`/pack-assets/...`) y `target="_blank"`. Un href relativo se resuelve contra el **documento que contiene el `<a>`**, no contra lo que muestra la barra de direcciones del navegador de arriba. Ese `<a>` vive en el documento servido por el copiloto (origen real `*.ondigitalocean.app`), así que al abrir la pestaña nueva el navegador arma la URL absoluta contra ESE origen — nunca contra `danwor.com.ar`. Ya es el comportamiento de TODOS los links del shell (logo, logout, nav) que son relativos y ya funcionan hoy en prod detrás del mismo wrapper. No hay `<base>` tag en ningún template (se verificó) que pudiera alterar esto. Debería andar igual en DO sin cambios; queda pendiente que Joe lo confirme en el deploy real (no se puede probar el wrapper de `danwor.com.ar` desde este entorno).
- **Tests nuevos**: `test_academy_asset_dark_theme_keeps_opaque_background_when_standalone`, `test_academy_page_has_embedded_back_button_hidden_on_landing_asset`; ajustados los de la ronda 1 (`academyAsset`/`asset` en vez de `standalone_href`). Suite: **188 verdes**.

### 2026-08-02 — Claude/Joe (Academy: cierre unificado con el de módulo — deuda técnica real, no mirror)
**Estado: implementado y validado; 189 verdes.**
- **Problema señalado por Joe**: comparando el cierre de módulo ("¿Qué te pareció este contenido?" + pulgares) con el de Academy, no se parecían — tipografía, tamaño de texto y padding de las cards distintos. Doble mantenimiento: tocás uno, el otro queda desalineado.
- **Se descartó el mirror manual** (copiar valores a mano) porque YA EXISTE una fuente única real para esta botonera: `webapp/static/feedback.css`, con el comentario explícito "ÚNICA FUENTE… la usan fin de módulo, catálogo y how-to". El "how-to" (`catalog_guide_slides.html`, contenido dentro de un iframe `dw-academy`) ya prueba que el patrón funciona embebido: linkea `/static/feedback.css` + un shim `:root{--card:var(--bg-card);--soft:var(--ink-soft)}` porque `dw-academy.css` nombra esas variables distinto. Academy tenía su PROPIO bloque `<style>` (`.dw-af-*`, ~30 reglas) duplicando visualmente esas clases con valores levemente distintos (de ahí la diferencia).
- **Fix real (no mirror)**: `webapp/main.py`
  - `_academy_feedback_html`: cambió de clases propias (`dw-academy-feedback`/`dw-af-*`) a las MISMAS clases que `partials/module_feedback.html` (`module-feedback`, `module-feedback__inner`, `__eyebrow`, `__prompt`, `__reactions`, `__react`, `__emoji`, `__label`, `__note`, `__error`, `__actions`, `__ghost`, `__primary`) en los 3 estados (landing/form/thanks). Solo difiere el copy ("recorrido" vs "módulo", ya intencional) y el transporte (`fetch()`+`outerHTML` en vez de `hx-post`, porque estos HTML no cargan htmx).
  - `_academy_feedback_assets`: el `<style>` de ~30 líneas se reemplazó por `<link rel="stylesheet" href="/static/feedback.css">` + el mismo shim de variables que `catalog_guide_slides.html` + un `<style>` de 4 líneas SOLO para el reveal-on-scroll (`#academy-feedback{opacity:0;...} .is-visible{opacity:1;...}`), que es lo único genuinamente propio de Academy (storytelling de una sola scrolleada; el módulo no lo necesita porque ya vive en un documento que se scrollea normal). El JS de reveal/rail-stop/submit no cambió.
  - Resultado: si mañana se toca `feedback.css`, los TRES cierres (módulo, catálogo/how-to, Academy) cambian juntos — ya no hay copia paralela que mantener.
- **Verificado en browser** (sesión forjada): scrolleado hasta el cierre en `/academy/ACAD01_Paradigmas` embebido vs. scrolleado hasta `#module-feedback` en un módulo — mismo tamaño de eyebrow/prompt, mismo padding y forma de las cards, mismos emojis. Visualmente indistinguibles.
- **Test nuevo**: `test_academy_feedback_reuses_module_feedback_classes_and_stylesheet` (linkea feedback.css, usa clases `module-feedback__*`, cero `dw-af-*`/`dw-academy-feedback`, shim presente). Suite: **189 verdes**.
- **Follow-up mismo día**: Joe notó que se perdió la línea gris separadora arriba del cierre. Causa: `feedback.css` pone `.module-feedback{border-top:0}` A PROPÓSITO — en módulo esa línea la aporta el `border-bottom` de la ÚLTIMA `.content-section` (que no tiene ningún `:last-of-type{border-bottom:none}` que la saque). En Academy, en cambio, `.stop:last-of-type`/`.topic:last-of-type` (dw-academy.css) SÍ tienen `border-bottom:none`, así que no queda ninguna línea antes del cierre — el CSS viejo (`.dw-academy-feedback`) lo compensaba con su propio `border-top:1px solid var(--line)`, que se perdió al migrar a la clase compartida. Fix: se repuso `#academy-feedback{border-top:1px solid var(--line)}` en el `<style>` propio de Academy (junto al reveal), sin tocar `feedback.css` (no se quiere reintroducir el border-top ahí porque en módulo/catálogo lo dejaría duplicado). Test actualizado. Suite: **189 verdes** (sin nuevos tests, se amplió el existente).

### 📌 Backlog — housekeeping de CSS muerto/duplicado (NO HACER TODAVÍA, sin implementar)
Disparado por el hallazgo de arriba (Academy tenía ~30 líneas de CSS propio duplicando `feedback.css` en vez de reusarlo — herencia técnica real, no una sola vez). Joe preguntó si es riesgoso escanear todo el proyecto y borrar hojas de estilo/selectores sin uso; quedó en standby, explícitamente pospuesto — **no arrancar sin que Joe lo pida**.
- **Plan acordado si se retoma**: (1) escaneo de SOLO LECTURA primero — cruzar cada selector de cada `.css` (`app.css`, `tools.css`, `layout.css`, `feedback.css`, `brand-hero.css`, `dw-academy.css`, `feedback.css`) contra todo el HTML/Jinja/JS/Python/markdown del repo, armar lista de candidatos a huérfanos; (2) recién ahí sacar de a uno, con verificación visual (browser) antes de cada borrado — nunca un borrado masivo de una sola pasada.
- **Por qué no es automatizable 100%** (para no repetir el análisis si se retoma): un grep estático no ve clases armadas dinámicamente — `classList.add(...)` en JS, bindings `:class`/`x-show` de Alpine, f-strings de Python (`main.py` arma HTML como texto, ver `_academy_feedback_html` antes de esta sesión), ni clases que solo aparecen DENTRO de contenido markdown que se renderiza recién en runtime (`content_service.render_sections`). Cualquiera de esos genera falsos positivos (selector "sin uso" que en realidad sí se usa) sin que ningún test lo detecte — no hay cobertura de "esto se ve bien visualmente".

### 2026-08-02 — Claude/Joe (Auth: login roto embebido en incógnito/mobile — diagnosticado y mitigado con aviso)
**Estado: diagnosticado con evidencia real (2 trazas) + mitigación pusheada a producción (2 commits aislados). NO toca el resto del working tree (ver nota abajo).**
- **Síntoma reportado por Joe**: en `danwor.com.ar/copiloto/dt/` (wrapper que iframea `https://copiloto-metodologico-rc6x9.ondigitalocean.app/academy/Funnel-como-faro?pack=datasphere_core`, análogo a cómo `danwor.com.ar/copiloto/` iframea la raíz), el login por popup (`auth.js`, ver mecanismo abajo) se rompía DE FORMAS DISTINTAS en cada intento: 1º intento → el popup se cerraba bien pero el iframe volvía a mostrar la pantalla de login (parecía que "no pasó nada"); 2º intento → el popup se autonavegaba mostrando el copiloto completo él solo (URL real de DO, sin el wrapper), huérfano. Joe aportó el dato clave: **solo pasa en incógnito o mobile — en navegador normal de escritorio funciona bien**.
- **Mecanismo de login** (para quien no lo conozca): Microsoft no permite loguearse DENTRO de un iframe, así que `auth.js` abre un POPUP top-level que hace todo el OAuth2+PKCE, y al terminar le avisa al `window.opener` (el iframe) por `postMessage` para que navegue autenticado. La cookie de sesión es `SameSite=None;Secure` (ya así antes de esta sesión) justamente para poder viajar embebida.
- **Diagnóstico con evidencia real** (no solo teoría): se pusheó un commit AISLADO y temporal con trazas (`webapp/static/auth.js`, buscar `DW-AUTH-DEBUG`) que guardan breadcrumbs en `localStorage` (compartido entre popup e iframe, a diferencia de `sessionStorage`) — `dwAuthDebugDump()` en consola arma la tabla completa. Joe reprodujo en incógnito con dos DevTools abiertas (wrapper + popup) y compartió las tablas. Lectura:
  - Intento 1: `notifyOpener` con `willSucceed:true` (el `opener` SÍ estaba vivo, el popup se cerró bien) — el hand-off popup→iframe funcionó. El problema fue DESPUÉS: el iframe navegó a la Academy con la sesión recién creada, pero esa cookie —de PRIMERA parte para el popup (`ondigitalocean.app` top-level)— es de TERCERO para el iframe (embebido bajo `danwor.com.ar`), y ahí el navegador la bloquea. El server vio "sin sesión" y redirigió de nuevo a `/?...&next=...#acceso` — de ahí la sensación de "no pasó nada".
  - Intento 2: el `opener` ya no estaba disponible (probablemente por el mismo tipo de aislamiento, agravado tras la navegación repetida del iframe) → cae al fallback existente (`window.location.replace(homeUrl())`) → el popup se autonavega y queda huérfano mostrando la app.
  - **Conclusión**: es un problema ESTRUCTURAL de iframear un origen distinto (`ondigitalocean.app` bajo `danwor.com.ar`) combinado con el bloqueo de cookies/storage de terceros — ya agresivo en incógnito/Safari/Chrome-mobile hoy, y que Chrome va a terminar aplicando para todos los navegadores. NO es un bug chico de lógica parcheable con una línea.
- **Caminos posibles evaluados con Joe** (rankeados): (1) reverse proxy — servir el copiloto bajo el MISMO origen que `danwor.com.ar` → elimina el problema de raíz para siempre, pero es un cambio de infraestructura, no solo código; (2) `Storage Access API` (`requestStorageAccess()`) → mitigación parcial, soporte irregular en Chrome; (3) dejarlo diagnosticado y no tocar nada más por ahora. **Joe eligió una 4ª opción, más chica**: no arreglar el problema de fondo todavía, pero SÍ detectarlo y avisar en vez de dejar que el usuario pegue contra un flujo que sabemos que no va a funcionar.
- **Mitigación implementada** (`webapp/static/auth.js` + `webapp/templates/landing.html`): nueva función `warnIfStorageRestricted()`, llamada en la rama "app normal" de `init()` (antes de esperar el click de login). Chequea `window.top !== window.self` (¿está embebido?) y, si sí, `document.hasStorageAccess()` (API real para esto — Safari/Firefox/Chrome recientes; dice si ESTE frame ya tiene acceso a sus cookies sin partición). Si no soporta la API o si SÍ tiene acceso → no hace nada (fail-open, no bloquea a navegadores que andarían bien). Si NO tiene acceso → oculta el botón de login y muestra `#storage-restricted-warning` (mismo estilo `.login-error` ya existente, sin paleta nueva): *"Estás intentando acceder al copiloto desde mobile o incógnito. De momento no soportamos esta navegabilidad embebida — probá desde un navegador de escritorio, fuera de una ventana privada/incógnito."*
- **Por qué esta API y no sniffing de user-agent o detectar "incógnito"**: los navegadores deliberadamente NO exponen una forma limpia de saber "¿estoy en incógnito?" (para no habilitar discriminación/fingerprinting); `hasStorageAccess()` en cambio mide DIRECTAMENTE la capacidad real que nos importa (¿esta cookie va a servir?), sin adivinar el modo de navegación.
- **Verificado en browser** (localhost): acceso directo (no embebido) → botón visible, sin warning (sin regresión). Embebido en un iframe SAME-ORIGIN (no debería restringir) → `hasStorageAccess()` resuelve `true`, botón sigue visible. No se pudo simular localmente el caso realmente roto (third-party storage bloqueado) porque requiere un iframe cross-origin real + navegador en modo incógnito/mobile — ese caso lo prueba Joe en producción.
- **⚠️ Nota operativa de esta sesión — cuidado al commitear**: el working tree tenía MUCHO trabajo previo de Codex ya en el índice de git (portabilidad BDC, catálogo) marcado NO-PUSH en este mismo HANDOFF, mezclado con el trabajo de Academy de esta sesión (sin acordar push). Para pushear solo lo de auth se usó `git reset` (desstagear todo, sin tocar contenido — reversible) + `git add` de los 2-3 archivos puntuales + commit + push, dejando el resto del working tree EXACTAMENTE como estaba (sucio, sin commitear). Se hicieron 2 commits aislados a `main` (que audeploya a DigitalOcean): `debug(auth): trazas temporales...` (7652385) y `feat(auth): advertir cuando...` (ef31987). **Las trazas de diagnóstico (`DW-AUTH-DEBUG`) siguen en `auth.js` — sacarlas cuando se dé por cerrado este frente** (Joe no pidió sacarlas todavía).
- **Pendiente**: Joe tiene que probar el aviso en incógnito/mobile real (producción) y confirmar que se ve donde corresponde. Si más adelante se decide ir por el reverse proxy o Storage Access API, retomar desde acá — el diagnóstico ya está firme, no hay que re-derivarlo.

### 2026-08-02 — Claude/Joe (Auth: cierre del frente — sacar debug + bug real: contenido consumible sin sesión)
**Estado: implementado, testeado, pusheado (commit aislado `0e889ed`).**
- Joe dio por suficiente el diagnóstico de incógnito/mobile (el aviso "anduvo") y pidió: (1) sacar todo el debug temporal (`DW-AUTH-DEBUG`) de `auth.js`, y (2) arreglar un bug real que encontró: **abrir Academy en pantalla completa (botón maximizar), copiar esa URL, volver al copiloto, desloguearse — y la URL copiada seguía sirviendo el contenido sin sesión.**
- **Causa**: `pack_asset` (`/pack-assets/{pack}/{asset_path}`, sirve el HTML/CSS/imágenes real de Academy y de módulos) y `pack_resource` (`/pack-resources/...`, templates/recursos del asistente) NUNCA chequeaban sesión — a diferencia de `/academy/{stem}`, `/modules/{stem}` y el resto de las rutas protegidas, que sí lo hacen. Como el botón "pantalla completa" apunta directo a `pack_asset`, quedaba expuesto: cualquiera con el link (copiado, en el historial, compartido) consumía el contenido sin login, sin límite de tiempo.
- **Fix**: mismo patrón que el resto del código — `if not auth_service.session_user(request): return _login_redirect(request, selected)` al inicio de ambas funciones (antes de resolver/leer el archivo). `pack_resource` no tomaba `Request` como parámetro; se le agregó. `_login_redirect` preserva el destino en `next=`, así que tras loguearse el usuario vuelve al mismo contenido que quería ver.
- **Se evaluó y descartó** tocar `dancing_logo` (`/brand/dancing/...`, el logo animado del topbar): es puramente decorativo, sin valor metodológico/de negocio — no aplica el mismo criterio de "contenido del copiloto".
- **Debug retirado**: todo el bloque `DW-AUTH-DEBUG` de `auth.js` (función `dbg()`, `localStorage`, `window.dwAuthDebugDump/Clear`, y las ~8 líneas `dbg(...)` intercaladas) — cero rastro. La feature real que se agregó en el medio (`warnIfStorageRestricted`, aviso de storage bloqueado) se mantuvo intacta, solo se le sacó su línea de `dbg(...)`.
- **Maniobra de commit** (para no arrastrar el resto del working tree, que sigue con trabajo de catálogo NO-PUSH + Academy sin acordar push): se generó una versión de `webapp/main.py` y `tests/webapp/test_routes.py` = **HEAD + únicamente el fix de auth** (tomando `git show HEAD:<archivo>` a un scratch, reaplicando ahí SOLO las líneas de `pack_asset`/`pack_resource`/el test nuevo), se corrió la suite completa contra esa combinación aislada (51 tests de `test_routes.py`, todos verdes), se commiteó y pusheó esa versión, y RECIÉN DESPUÉS se restauraron los archivos de trabajo completos (con todo lo demás de la sesión) desde el backup en scratch. Verificado con `git diff` que el commit pusheado tenía SOLO las líneas de auth + el test, nada de lo demás.
- **Test nuevo**: `test_pack_asset_and_pack_resource_require_session` — anónimo → 307 a `#acceso` con `next=` en ambas rutas; con sesión, sigue sirviendo el contenido real (sin regresión).
- **Verificado en browser** (localhost): sin cookie de sesión, `GET /pack-assets/datasphere_core/academy/paradigmas.html?...` ya no devuelve el storytelling — redirige a la landing con `#acceso`.
- Suite completa: **190 verdes**.

### 2026-08-02 — Claude/Joe (push grande: se levanta el NO-PUSH del catálogo — ya estaba en prod)
**Corrección importante sobre este mismo HANDOFF**: la restricción "PILOTO / NO-PUSH" del encabezado (y las notas de sesiones previas asumiendo que el catálogo era solo local) estaban **desactualizadas** — Joe confirmó que el catálogo piloto YA estaba pusheado a producción de antes. Por las dudas, así es el flujo real: se trabaja en localhost → se pushea a `main` → eso dispara el auto-deploy de DigitalOcean. No hay un branch de "deployment" intermedio.

Dado eso, se pusheó en un solo commit (`9c45091`) TODO lo que quedaba pendiente de la sesión, salvo `reports/*` (artefactos runtime generados, nunca se commitean — convención aparte de la discusión de piloto/no-piloto):
- **Portabilidad BDC completa** (`tools/catalog/portability_bdc.py`, `portability_telemetry.py`, `PORTABILITY_BDC.md`, `content_packs/datasphere_core/portability_bdc.yaml`, wiring en `generate_catalog.py`/`generate_data_products_catalog.py`/`catalog_service.py`, tests) — ya documentada en la entrada del 2026-08-02 más arriba ("Portabilidad BDC: dimensión cross-runtime configurable").
- **Todo el polish de Academy "pantalla completa"** de esta sesión: header siempre oscuro, botón minimalista alineado al rail, sincronización cross-asset del botón maximizar, botón "← Volver", fix de fondo dark standalone, unificación del cierre con `feedback.css`.
- Antes de commitear se corrió la suite completa (190 verdes) y se grepeó el diff completo por patrones de credenciales/secrets (nada encontrado, solo menciones en prosa de este mismo HANDOFF).
- **De acá en más, este HANDOFF ya NO debería asumir "NO-PUSH" para el catálogo/portabilidad** salvo que Joe lo vuelva a pedir explícitamente — si se lee una nota vieja que lo dice, verificar con Joe antes de tomarla como vigente.

### 2026-08-02 — Claude/Joe (Assessment BDC-native + gap-check contra artículo SAP Community) — PENDIENTE, sesión cortada por crédito
**Estado: assessment entregado (artifact, solo lectura, Joe lo está revisando); gap-check del artículo entregado en chat; NADA implementado todavía. Retomar en sesión nueva.**

- **Assessment grande** (Datasphere-first → BDC-native + backport selectivo): entregado como artifact HTML, NO guardado en el repo (vivía en el scratchpad de esa sesión). Hallazgo central: ya existe `content_packs/bdc_next/` (`extends: datasphere_core`, `content_mode: replace`) con 00+01 ("Estrategia y Readiness BDC") + apéndice A01 — es un esqueleto real, seleccionable hoy detrás del flag de diagnóstico, NO una idea. Confirmé con cita textual 3 de 5 hipótesis del brief de Joe (contradicción capas-Danwor vs Bronze/Silver/Gold entre módulo 01 líneas 168-179 y módulo 04 líneas 137-148; equivalencia contrato≈Data Product en módulo 03 líneas 196-211; naming de Transformation Flows sin capturar transición de capa en módulo 04 líneas 257-269) y 2 parciales. Recomendación central: NO crear una plataforma de conocimiento nueva (nada de carpeta `knowledge/` ni ramas Git) — el sistema de packs YA es el vehículo de convergencia; falta solo un registro de clasificación (`tools/catalog/BDC_CONVERGENCE.md` propuesto, hermano de `PORTABILITY_BDC.md`). Matriz completa de 13 aprendizajes (K-01…K-13) clasificados CANONICAL/READINESS/BDC_ONLY/etc. **Joe está revisando el artifact en detalle — retomar leyendo su feedback antes de tocar nada.**
- **Gap-check puntual** contra <https://community.sap.com/t5/technology-blog-posts-by-sap/sap-s-4hana-custom-abap-cds-view-to-datasphere-replication-flow-setup/ba-p/14437051> (blog SAP: Replication Flow + CDC para CDS custom, RMS/APE, pipeline de tablas internas). Leído completo vía browser (el fetch directo da 403). Comparado contra módulo 03, módulo 04 y **apéndice A05** (Troubleshooting y Performance — el que realmente se solapa, no el 04: A05 ya redirige extracción/CDS al 03 vía su propio `out_of_scope_redirect`, así que nada de esto va a 04).
  - **Pendiente de implementar (2 cambios acotados, NO más)**:
    1. **Nuevo guardrail en módulo 03**: las columnas `Change_Type`/`Change_Date` de una tabla local Datasphere con CDC son **internas**, prohibido usarlas para lógica downstream fuera de Premium Outbound Integration (Notas SAP 3725855 y 3634750). Es una decisión de DISEÑO, no solo troubleshooting — hoy no está documentado en ningún lado del repo.
    2. **Enriquecer (no reemplazar) la sección "HANA Triggers y CDC Buffer" de A05** (`content_packs/datasphere_core/content/apendices/A05_Troubleshooting_y_Performance.md`): hoy describe el buffer `/1DH/SL*` de forma más simple (triggers → buffer, consumido por ODQMON). El artículo describe un pipeline de 3 etapas: Master Logging Table (`/1DH/ML<n>`) → Subscriber Logging Table (`/1DH/SL<n>`) → Resilient Data Buffer Table (`/1DH/RDB<n>`), con jobs `/1DH/OBSERVE_LOGTAB` (~1h) y `/1DH/PUSH_CDS_DELTA` (~6h) y transacciones DHADM/DHCDCMON/DHRDBMON.
       - **⚠️ Ojo antes de fusionar**: A05 usa terminología ODQMON (framework ODP/ODQ clásico); el artículo usa DHCDCMON/DHRDBMON (RMS/APE para Replication Flow + CDC sobre CDS custom). NO asumir que son el mismo mecanismo con nombre distinto — puede ser una generación más nueva o un camino paralelo específico de Replication Flow. Verificar contra SAP Help ANTES de reescribir A05; si no se puede confirmar, marcar la sección nueva como `PENDING_EVIDENCE` y presentarla como complementaria, no como reemplazo del contenido ODQMON existente (que sigue siendo válido para su propio contexto).
  - **Descartado explícitamente** (para no sumar por sumar): el bloque completo de anotaciones `@ObjectModel` del ejemplo del artículo (`compositionRoot`, `supportedCapabilities`, `modelingPattern: #ANALYTICAL_DIMENSION`) — es modelado analítico/BW-style que no aplica al Patrón 3 (CDC con join) que ya tenemos en el módulo 03; diluiría el ejemplo en vez de mejorarlo. La recomendación de "preferir CDS/data products estándar sobre tablas ABAP directas" del artículo YA está cubierta casi verbatim en el guardrail del 03 y en "Future-Proof con ECC" de A05 — no requiere cambio, como mucho una referencia externa.
  - **Delta operativo menor, opcional**: el artículo menciona que la frecuencia de un Replication Flow es configurable (default 1h, puede bajarse a minutos o "0/0" para near-real-time) — no crítico, podría ir como nota al pie en A05 o en la sección de RF del 03 si se hace el resto.
- **Próximo paso al retomar**: (1) confirmar con Joe feedback sobre el assessment grande; (2) si da luz verde al gap-check, implementar SOLO los 2 puntos de arriba (guardrail 03 + enriquecimiento A05, con la verificación DHCDCMON/ODQMON pendiente antes de escribir prosa definitiva); correr suite completa después (afecta `section_meta`/parsing de headings, mismo cuidado que cualquier edición de contenido).

### 2026-08-02 — Claude/Joe (control de cambios para producción + gap-check implementado)
**Estado: implementado, testeado (222 verdes), NO pusheado — decisión de Joe pendiente sobre si esto entra al mismo push de siempre o se maneja aparte.**

- **Pregunta de Joe**: dado que `datasphere_core` es productivo y lo consume gente real, ¿cómo gestionar control de cambios de contenido sin frenar mejoras chicas ni arriesgar confianza en el copiloto? Aclaré primero que el assessment NO proponía fusionar los dos marcos en uno dinámico — `bdc_next` sigue separado para el lifecycle profundo; lo que sí toca producción es el backport acotado (H1/H2/H4, guardrails). Propuesta de 3 niveles de riesgo: (1) mejoras chicas de siempre, sin proceso nuevo; (2) backport acotado → commits atómicos por tema + corpus de regresión de routing (nuevo) + reusar `nav_hidden` a nivel sección para preview en diagnóstico (idea, no implementada); (3) lifecycle BDC nuevo → ya protegido por `bdc_next` siendo invisible fuera de diagnóstico.
- **Hallazgo importante al construir el punto 2**: `tests/README.md` describía una "red de seguridad del router" (`ui/tools_panel.py`, `core/routing.py`, `fake_streamlit.py`) que **ya no existe** — quedó huérfana de la migración Streamlit→FastAPI, junto con `tests/golden/routing_decisions.json` (golden viejo, de la cascada Streamlit). El corpus real (`tests/prompts_corpus.yaml`, 32 casos con preguntas reales por módulo 01-05) SÍ sobrevivió intacto pero sin ningún test que lo consuma.
- **Revivido, no reinventado**: `tests/test_routing_corpus_regression.py` reconecta ese mismo corpus contra el router ACTUAL (`webapp/services/assistant_routing.py::AssistantRoutingService.route`), pineando la decisión completa (mensaje + acciones) en un golden nuevo `tests/golden/routing_decisions_v2.json` (32 casos). Mismo patrón que la suite vieja (`UPDATE_GOLDEN=1` para regenerar tras un cambio intencional). Confirmé que hoy varios casos `oos_*` (redirect por keyword) en realidad resuelven vía el matcher de secciones cross-módulo (`_find_cross_module_section`, más preciso) ANTES de llegar al `out_of_scope_redirect` — coincide con lo que ya documentaba el README viejo sobre la cascada Streamlit, sigue siendo cierto en FastAPI.
- **`tests/README.md` actualizado**: retira la descripción de la infraestructura muerta, documenta la suite revivida y por qué `golden/routing_decisions.json` (viejo) queda huérfano sin tocar (no se borra por las dudas, pero no se reusa).
- **Gap-check del artículo SAP implementado** (los 2 puntos que habían quedado pendientes en la entrada anterior), con la regresión de routing como red de seguridad real, no solo declarada:
  1. Guardrail nuevo en módulo 03 (`03_Integracion_y_Estrategia_de_Datos.md`, frontmatter `guardrails` + callout nuevo "Columnas internas de CDC" después de "CDC Avanzado"): `Change_Type`/`Change_Date` de una tabla local con CDC son internas, prohibido construir lógica downstream sobre ellas fuera de Premium Outbound Integration (Notas SAP 3725855, 3634750).
  2. Enriquecido A05 (`apendices/A05_Troubleshooting_y_Performance.md`, sección "HANA Triggers y CDC Buffer"): nuevo callout "Replication Flow con CDC (RMS/APE)" con el pipeline Master Logging Table (`/1DH/ML<n>`) → Subscriber Logging Table (`/1DH/SL<n>`) → Resilient Data Buffer Table (`/1DH/RDB<n>`), jobs `/1DH/OBSERVE_LOGTAB`/`/1DH/PUSH_CDS_DELTA` y transacciones DHADM/DHCDCMON/DHRDBMON — **presentado explícitamente como complementario** al contenido ODQMON existente, sin asumir equivalencia (la verificación contra SAP Help sigue pendiente, tal como se dejó anotado).
- **Verificación**: `tests/test_routing_corpus_regression.py` sigue en 32/32 verdes DESPUÉS de las dos ediciones (ningún routing se movió) — exactamente lo que la suite nueva está para probar. Suite completa: **222 verdes**. Verificado en browser que ambos callouts nuevos renderizan bien (module 03 y A05).
- **Re-auditoría a pedido de Joe** ("¿revisaste todo o dejaste contenido rico afuera?"): repasé la tabla de comparación de nuevo con ojo crítico y encontré un ítem real que había marcado "opcional" y nunca implementé — la frecuencia de reintento del Replication Flow es configurable (default 1h, bajable a minutos, "0/0" = near-real-time). Se agregó como 3er callout en el módulo 03 ("Frecuencia de delta"). Re-confirmado: 32/32 routing + 222 verdes. Quedan 2 exclusiones deliberadas, con motivo: (a) el valor `Change_Type=I` ("Pure Insert") de la tabla del artículo — el propio ejemplo del autor muestra que un INSERT real durante delta da `Change_Type=A`, no `I`, y el resumen final del artículo solo habla de A/D para delta — se omitió por ambigüedad de la propia fuente, no por descuido; (b) los comentarios del thread (job `/1DH/CDC_RLFW_SWITCH_RUNNING`, señal de alerta buffer-vs-subscriber, recomendación de `CNV_NOTE_ANALYZER_DI`) — son respuestas informales del autor en los comments, no el cuerpo curado del artículo; el `CNV_NOTE_ANALYZER_DI` ya está cubierto (como `CNV_NA_DI`) en el guardrail existente del módulo 03, así que no aporta contenido nuevo, y lo demás es menor/anecdótico.
- **Corrección de criterio editorial (Joe, tras ver screenshot)**: había metido los 3 callouts nuevos (CDC Avanzado -ya existía-, Columnas internas de CDC, Frecuencia de delta) apilados uno atrás de otro al final de "Template de Extracción vía API Corporativa" — mecánico, no storytelling. Joe lo marcó bien: (1) los callouts en este repo son de uso minimalista, a lo sumo UNO como cierre de un tramo, no una fila de fichas; (2) el tono/color no estaba bien elegido — "Columnas internas de CDC" en `tone="warn"` (rojo) competía visualmente con un guardrail real cuando es informativo/preventivo, no una alerta de algo roto. **Fix**: "Columnas internas de CDC" y "Frecuencia de delta" se fusionaron en UN párrafo de prosa normal (sin callout), insertado entre la tabla de anotaciones y el Tip existente; el único callout que queda en ese tramo es el original "CDC Avanzado" (punch), intacto, como cierre. A05 no tenía este problema (un solo callout nuevo ahí, con prosa/código de por medio antes del próximo) — no se tocó. Re-verificado: 32/32 routing + 222 verdes, render confirmado en browser (la lista de `.callout__title` del módulo 03 volvió a los 9 títulos originales, sin duplicados apilados).
- **Lección para futuras ediciones de contenido**: antes de envolver algo nuevo en `{{callout:...}}`, preguntarse si realmente necesita salirse del flujo de lectura (cierre de un tramo, dato que rompe el patrón) o si es mejor prosa integrada — y si ya hay un callout cerca, sumar contenido a la prosa en vez de agregar una ficha más.
- **Resuelta H1 del assessment BDC** (contradicción capas-Danwor vs Bronze/Silver/Gold, módulo 01 vs 04): la tabla "Coexistencia de Paradigmas" del módulo 01 (`01_Arquitectura_y_Conectividad.md`) tenía una fila `Capas: Ingesta.../ Capas: Bronze, Silver, Gold` que daba a entender que el runtime elegido determinaba el esquema de capas. Se sacó esa fila de la tabla. Ajuste de forma (a pedido de Joe, tras el primer intento en prosa suelta): la tabla cierra con sus dos badges originales (`paradigm-tags`: "Lo que construimos hoy" / "BDC-Ready") intactos, y el agregado va DESPUÉS como callout `tone="punch"` ("Las capas no cambian con el runtime") — un solo cierre visual, no prosa perdida ni una ficha más apilada. Remite al módulo 04 · Evolución de Arquitectura por Capas (que ya tiene la formulación correcta) en vez de duplicarla — una sola fuente de verdad. Verificado en browser: badges intactos, el callout aparece justo debajo como sibling. Re-confirmado: 32/32 routing + 222 verdes.
- **Refuerzo visual en el HTML del módulo 04** (a pedido de Joe, cierre del hilo de H1): en `content_packs/datasphere_core/assets/danwor-arquitectura-capas.html` (asset interactivo embebido en "Aplicación de Arquitectura por Capas"), la sección [24] narra el principio en 3 filas (Ingesta/Armonización/**Publicación**) pero la [25] lo aplica en 4 tarjetas clickeables (Ingesta/Armonización/**Propagación/Consumo**) — el desglose de Publicación en 2 capas solo vivía en texto (la guía operativa del módulo), no en el diagrama. Se agregó un bracket puramente visual: label "Publicación" (mismo tono gold que el resto de acentos del asset) alineado exactamente sobre las tarjetas Propagación+Consumo, vía 2 elementos nuevos posicionados por CSS Grid (`grid-column`/`grid-row` explícitos) — **sin envolver las `.capa-card`**, que siguen siendo hermanas directas de `.capas-row` exactamente como antes. Esto importa porque `toggleCapa()` y el `querySelectorAll('.capa-card')` del script no cambiaron en absoluto (verificado antes de tocar nada). Responsive: el bracket asume el grid de 4 columnas, así que se oculta explícitamente en el breakpoint de 2 columnas (`max-width:1100px`) y no aplica en el de 1 columna (`700px`) — sin eso, el `grid-column:3/5` quedaría fuera de rango en un grid más angosto. Verificado con JS en browser: label alineado pixel-perfect sobre Propagación+Consumo (mismos `left`/`right` que las tarjetas), click/drawer intactos, y en 1000px el bracket desaparece con las 4 tarjetas bien acomodadas en 2×2 sin huecos.
- **Fix conceptual en el HTML de HDLF** (`danwor-arquitectura-capas.html`, banda HDLF): la relación entre Delta Tables y Parquet Files estaba invertida — el código tenía `Replication Flow → Parquet Files` (HANA empuja a HDLF) y `Delta Tables → Transformation Flow` (HDLF vuelve a HANA). Joe la corrigió al modelo estándar de Delta Lake: Delta Table SE CONSTRUYE sobre Parquet (Parquet + transaction log), no son dos archivos paralelos.
  - Cambios: **Transformation Flow** ahora escribe/materializa **Parquet Files** (antes era Replication Flow); **Replication Flow** es la que puede devolver la **Delta Table** a HANA (antes era Transformation Flow) — se swapearon los íconos de dirección (↓/↑) y el orden de los 2 chips en `.flow-connector`. Delta Tables y Parquet Files pasaron de ser cajas lado a lado (`hdlf-files-row` con 2 hijas sueltas) a un `.hdlf-stack` (columna, Delta arriba/Parquet abajo, unidas por un chevron "▲") con **el mismo ancho** (a pedido explícito de Joe, tras un primer intento con Delta angosta que no funcionó bien porque el texto no entraba en el % pedido).
  - **Ruteo de flechas sin cruces**: en vez de que ambas entren por arriba (lo que hubiera hecho que la flecha de Transformation cruce por encima de la caja de Delta, que está arriba en el stack), Transformation Flow entra por el **lado derecho de Parquet, centrada en su alto** (`pqRightX`/`pqMidY` calculados en runtime desde el `getBoundingClientRect()` real, no un % fijo — robusto a cualquier ancho de caja) y Replication Flow sigue entrando por arriba de Delta (no tiene nada en el medio, no hay riesgo de cruce). Tooltips y la nota `.hdlf-note` actualizados con el texto correcto (Parquet = base física de Transformation Flow; Delta = se construye sobre Parquet, replicable o compartible). Se agregó `margin-top:10px` en `.hdlf-note` a pedido de Joe (quedaba pegada a Parquet Files).
  - **Nota pendiente aparte** (no tocar ahora, lo dijo Joe explícitamente): hay in consistencia de nombres en el repo entre "Delta Sharing" y el renombre reciente de SAP "Open Sharing" — mismo concepto, dos nombres distintos usados en distintos lugares. Queda para una revisión de diccionario futura, no mezclar con este fix.
  - Verificado con JS en browser (no hay tests automatizados para HTML puro): las 2 cajas miden exactamente el mismo ancho/posición (267-417px), la curva de Transformation Flow termina en el punto exacto calculado (lado derecho de Parquet, centro vertical), sin superponerse a Delta. Sin regresión: 222 tests verdes (esto no toca Python/routing).
- **No pusheado todavía** — queda a criterio de Joe si esto va en un commit propio o junto con la próxima tanda.
