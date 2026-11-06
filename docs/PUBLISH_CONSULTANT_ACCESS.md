# «Publicar modelo» para consultores — diagnóstico, propuesta y activación segura

Estado: **autorización por perfil** (dueño o consultor `full`), desacoplada de la publicación segura (P1) y sin interruptor de concesión. Rectifica la versión anterior, que condicionaba la capacidad a tres variables de entorno (ver §2).

## 1. Diagnóstico de autorización (UI y backend) — histórico, previo a la rectificación

**Una sola fuente de verdad, hoy atada al dueño:** `datasphere_publication_service.can_execute(request)` → `access.is_owner(request)` (desacoplada del Assessment ad-hoc en `d924606`, que sí se abrió a consultores). Todo lo demás se deriva de ella:

| Punto | Archivo | Qué hacía |
|---|---|---|
| Botón «+» y panel de publicación, y el aviso «Acceso restringido» | `templating._dsp_pub_tool` → `partials/catalog_tab.html` (`pub.can_execute`), `partials/datasphere_publication_tool.html` | oculta/muestra la UI |
| Deep link `?tab=catalogo&catalog_action=publish` y pestaña Catálogo vacía | `routers/modules.py` | idem, mismo predicado |
| `POST /modules/{stem}/datasphere-publication/run` | `routers/datasphere_publication.py` (`_authorize_read`/`_authorize`) | sesión (401) + mismo origen (403) + predicado (403) |
| Selector de perfiles de clientes (libro compartido con los secretos de TODOS los clientes) | `datasphere_profiles.selector_url(stem, can_execute)` y `profiles.resolve(profile_id, …)` dentro del `run` | **dependían implícitamente de que `can_execute` fuera solo-dueño**: el router de publicación no tiene check propio |
| `POST …/datasphere-profiles` y `POST …/datasphere-assessment/refresh-catalog` | `routers/datasphere_assessment.py` (`_authorize_owner` → `can_administer`) | ya son del dueño por su cuenta; no pasan por el predicado de publicación |

**Brechas que importan al abrirlo:** (1) ampliar `can_execute` sin más habría dejado a un consultor resolver perfiles compartidos (el check era implícito); (2) **no existe la noción de «cliente autorizado»**: `client` es texto libre (H4 de la auditoría) y escribe en `catalog_local/<cliente>`; (3) las garantías de integridad H1–H6 viven en el PR de P1 (`release/republication-p1-20261027`), **no** en `main`.

## 2. Quién puede publicar (vigente)

**La autorización es el perfil, no la configuración.** Un único predicado, `datasphere_publication_service.can_execute`, que delega en el del Assessment (`datasphere_assessment_service.can_execute`: dueño o `user_tier == "full"`):

| Perfil | Política |
|---|---|
| Dueño (`APP_OWNER`) | publica sobre cualquier cliente; puede usar los perfiles de clientes compartidos |
| Consultor `full` | publica (primera publicación y republicación por las mismas compuertas), con **sus propias** credenciales de Datasphere; la ejecución queda atribuida a su usuario |
| Solo lectura (`ACADEMY_USERS`) · fuera de la allowlist · anónimo | denegado siempre (UI y POST) |
| Perfiles de clientes compartidos (`profile_id`), `/datasphere-profiles`, `refresh-catalog`, herramientas administrativas | solo `APP_OWNER` (`access.is_owner` **no se amplió**) |

* **No hay interruptor para conceder la capacidad.** Se retiró `DANWOR_PUBLICATION_CONSULTANTS`: la capacidad ya está prevista por el perfil. El retiro de un usuario de la allowlist surte efecto en la siguiente request.
* **La integridad no interviene en la autorización.** `run()` publica siempre por la unidad transaccional, sea quien sea el que publique. Se retiró la sonda `publication_guarantees_active`.
* **`DANWOR_PUBLICATION_CONSULTANT_CLIENTS` es una restricción OPCIONAL.** Ausente o vacía = sin restricción adicional (no requiere configurar nada). Si está definida (lista separada por comas, sin distinguir mayúsculas ni espacios repetidos), un consultor solo publica sobre esos clientes (si no: `CLIENT_NOT_AUTHORIZED`, sin ejecutar nada); el dueño, sobre cualquiera. Un `*` suelto no es un cliente: se ignora (equivale a vacío).
* Se conservan: sesión, mismo origen, validación de host/token URL, preflight OAuth, secretos transitorios y saneamiento, `record_provenance` y la regla de retiro («quien lo subió o actualizó»).

UI y backend usan el **mismo** predicado: botón «+», panel, deep link `?catalog_action=publish` y `POST /modules/{stem}/datasphere-publication/run`. Además, el backend exige `client_authorized(request, client)` y `can_use_client_profiles` (solo dueño) cuando llega un `profile_id`.

**Por qué no hay distinción CREATE/UPDATE:** la identidad vigente no permite demostrarla (manifiestos v1/v2 heredados, variantes de mayúsculas del cliente, objetos del pool compartidos entre raíces del mismo cliente; el ledger por cliente solo existe desde P1). Primera publicación y republicación pasan por las mismas compuertas.

**Controles de propiedad sobre una republicación ajena (verificado):** hoy no existe ninguno que se aplique al usuario. La procedencia (`last_publisher`) se registra pero solo la lee la regla de retiro. Con la publicación segura hay una defensa de identidad del contenido (`POOL_IDENTITY_CONFLICT`: un objeto ya publicado desde otro Space/tenant con contenido distinto), no de propiedad por usuario. Un consultor `full` puede, por lo tanto, republicar el modelo de otro; el dueño también puede hacerlo hoy. No se agregó ningún bloqueo (queda como decisión del dueño; ver §3).

## 3. Integridad de la operación (sin camino alternativo)

Desde el hotfix P0 (2026-11-06) toda publicación es la unidad transaccional (journal, deshacer, candado entre procesos, confirmación durable, compuerta de lectores,
regeneración por cliente): el «camino de siempre» y su interruptor se retiraron. Riesgo residual que sigue vigente: **réplica única** (el candado y la compuerta no
protegen entre instancias) e **identidad del pool sin Space** (mitigada por `POOL_IDENTITY_CONFLICT`; ver `_handoffs/ADR_CATALOG_SPACE_IDENTITY.md`). La restricción
opcional de clientes sigue disponible para acotar el radio de daño.

## 4. Operación

* Para **acotar** a un consultor a ciertos clientes: definir `DANWOR_PUBLICATION_CONSULTANT_CLIENTS` (opcional; quitarla o vaciarla levanta la restricción en la siguiente request, sin redeploy de código).
* Para **retirar** a un consultor: sacarlo de la allowlist del Hub.
* Para **endurecer la integridad** de toda publicación (dueño y consultores): activar la publicación segura según su runbook; no cambia quién puede publicar.

## 5. Pruebas

`tests/webapp/test_datasphere_publication_consultant_access.py`, dueño y `full` publican sin configuración alguna (UI, deep link y POST) · el perfil de publicar coincide con el del Assessment (incluye que delega en él) · la autorización no consulta el código de la publicación segura (aunque no importe o su sonda falle) · credenciales propias atribuidas al consultor · primera publicación y republicación · restricción opcional de clientes (ausente/vacía/`*` no restringe; definida limita, sin distinguir mayúsculas ni espacios; el dueño no se limita) · solo lectura, fuera de la allowlist y anónimo denegados en UI y backend · retiro de la allowlist al siguiente request · POST manual no elude lo que la UI oculta · perfiles compartidos, `/datasphere-profiles` y `refresh-catalog` siguen solo-dueño · mismo origen · validación de host antes de cualquier extracción · el secreto no sale. La regla de retiro (propio sí, ajeno no, dueño cualquiera) sigue cubierta en `tests/webapp/test_catalog_retire.py`.
