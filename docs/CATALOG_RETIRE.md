# «Retirar del catálogo» — ícono de papelera en los tiles

## Qué se hizo y por qué es lógico y no físico

La tarjeta (`partials/catalog_flows.html`, usada por Modelos, Líneas de negocio y Clientes) gana una **acción icónica** al borde derecho: papelera SVG con el mismo primitivo (`.table-action-btn--danger` + tooltip `.chart-tooltip`) que Capital Humano, sin texto visible. Al accionarla: diálogo de confirmación compartido (`dwConfirm`) → `POST /catalog/flow/retire` → la tarjeta se reemplaza en su lugar por un aviso de resultado.

**El borrado físico hoy no es seguro**, por eso el retiro es **lógico** (tombstone `catalog_local/.retired/<entry_id>.json`; `catalog_governance.visible_entries` lo aplica en el Catálogo y el asistente; el cargador `catalog_service`, congelado por una prueba de aislamiento del scoring legacy, no se toca):
* el flujo se **regenera** desde el pool: borrar el `.md` se deshace en la siguiente publicación o «Generar catálogo»;
* los objetos del pool pueden ser **compartidos** entre raíces del mismo cliente (el borrado por manifiesto es justo lo que las auditorías H1/H6 señalan como riesgoso);
* los **borrados no se propagan a Spaces** (H2): un restore resucita lo borrado;
* el assessment gobernado y el manifiesto quedan huérfanos si se borra solo el flujo.
Con el retiro lógico no se toca ningún archivo, manifiesto, pool, assessment ni objeto remoto (verificado byte a byte en las pruebas); es **reversible** (quitar el tombstone) y **volver a publicar el modelo levanta el retiro** (la publicación es la intención contraria).

## Casilla opcional «Eliminar también sus archivos»

El diálogo de confirmación (compartido, `dwConfirm`) admite una **casilla opcional**, siempre **desmarcada** al abrir (mismo patrón que «Eliminar también los documentos de SharePoint» en la baja de un candidato de CCHH). La nota aclaratoria y la casilla usan clases propias del diálogo en `tools.css` (`.dw-confirm__check`/`__hint`; `.field-hint` vive solo en la hoja de CCHH).

* **Sin marcar**: retiro lógico de siempre (nada se borra).
* **Marcada**: además del retiro, borra la **carpeta propia del tile** (`<cliente>/<carpeta>/`: `flujo_*.md` + `vistas/`, incluida la curaduría `manual:`) en disco y en Spaces. **Irreversible**; se recupera regenerando el catálogo o republicando el modelo.
* **Misma autorización** que el retiro (dueño, o último publicador); el endpoint recibe `delete_files` y no amplía permisos.
* **Salvaguardas** (`catalog_governance.purge_files`): solo borra una carpeta que contiene ESE único flujo, a exactamente dos niveles bajo la raíz del catálogo, sin enlaces simbólicos; el tombstone se escribe **antes** y se **conserva** (evita que una regeneración resucite el tile) con constancia `files_deleted`; los objetos de Spaces se borran **primero** y, si alguno falla, el disco queda intacto (el tile queda retirado con sus archivos y se informa).
* **No se tocan**: pool (`.csn_cache`), manifiestos, assessment gobernado, objetos compartidos ni la procedencia.
* Feedback del tile: «Retirado y sus archivos fueron eliminados» / «Retirado, pero no se pudieron eliminar sus archivos (se conservan)».

## Procedencia: qué existía y qué se agregó

| Dato | Antes | Ahora |
|---|---|---|
| Autor original | no existe (ni en el flujo, ni en el manifiesto v2, ni en el assessment) | `first_publisher` por tile, **desde ahora** |
| Último actualizador | no existe; solo `activity_log` (opcional `LOG_ACTIVITY`, se escribe al terminar, se enlaza por (cliente, space, raíz) y no por tile) | `last_publisher` por tile, desde ahora |
| Actor de la operación | `operation_id` solo en memoria/log | `operation_id` + instante en el sidecar |

Sidecar `catalog_local/.publication_actors/<entry_id>.json`, escrito por `datasphere_publication_service.run` tras una publicación exitosa (no rompe la publicación si falla). **Los tiles publicados antes de este cambio no tienen procedencia y solo los puede retirar el dueño**: nunca se infiere un autor.

## Política (decisión del owner) y base de la regla

APP_OWNER retira cualquier tile · un usuario con perfil completo solo el tile cuyo **último publicador** es él · cualquier otro caso: sin ícono y `403` en el endpoint (también para un tile inexistente: no revela nada; sin sesión `401`; mismo origen obligatorio). UI y endpoint usan el **mismo predicado** (`catalog_governance.can_retire`). La base elegida es el **último actualizador** (el actor más reciente, el dato que el sistema ya registra de forma fiable); el sidecar guarda además el autor original por si se prefiere «autor original o cualquier actualizador».

Hoy los consultores no publican (la capacidad para consultores está apagada), así que en la práctica solo el dueño aparecerá como publicador hasta que se active esa capacidad.

## Riesgos residuales

* Un tile sin procedencia (todo lo existente) solo lo retira el dueño.
* El retiro está atado a `entry_id` (derivado de la ruta del flujo): si el dominio/consumo se renombra, el tile reaparecería con otro id.
* Sin la casilla, el flujo, el pool, el assessment y los objetos de Spaces permanecen (residuo intencional y reversible); con ella solo se elimina la carpeta propia del tile (el pool y el assessment siguen); una limpieza física futura requiere propagar borrados a Spaces y definir propiedad de objetos compartidos.
* El retiro no oculta el tile en caminos que lean `catalog_service` directamente (herramientas offline, CLI) ni en la regeneración de assessments de P1 (que se verá cuando P1 se integre).
* Dos consultores publicando el mismo tile: gana el último; el anterior pierde el derecho a retirarlo.
