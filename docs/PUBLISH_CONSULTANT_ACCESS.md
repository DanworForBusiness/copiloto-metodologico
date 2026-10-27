# «Publicar modelo» para consultores — diagnóstico, propuesta y activación segura

Estado: **preparación local de la autorización, apagada por defecto**. Con la configuración actual de producción nada cambia (solo el dueño publica). Este documento es la base de la revisión arquitectónica; no se habilita ninguna escritura insegura.

## 1. Diagnóstico de autorización (UI y backend)

**Una sola fuente de verdad, hoy atada al dueño:** `datasphere_publication_service.can_execute(request)` → `access.is_owner(request)` (desacoplada del Assessment ad-hoc en `d924606`, que sí se abrió a consultores). Todo lo demás se deriva de ella:

| Punto | Archivo | Qué hacía |
|---|---|---|
| Botón «+» y panel de publicación, y el aviso «Acceso restringido» | `templating._dsp_pub_tool` → `partials/catalog_tab.html` (`pub.can_execute`), `partials/datasphere_publication_tool.html` | oculta/muestra la UI |
| Deep link `?tab=catalogo&catalog_action=publish` y pestaña Catálogo vacía | `routers/modules.py` | idem, mismo predicado |
| `POST /modules/{stem}/datasphere-publication/run` | `routers/datasphere_publication.py` (`_authorize_read`/`_authorize`) | sesión (401) + mismo origen (403) + predicado (403) |
| Selector de perfiles de clientes (libro compartido con los secretos de TODOS los clientes) | `datasphere_profiles.selector_url(stem, can_execute)` y `profiles.resolve(profile_id, …)` dentro del `run` | **dependían implícitamente de que `can_execute` fuera solo-dueño**: el router de publicación no tiene check propio |
| `POST …/datasphere-profiles` y `POST …/datasphere-assessment/refresh-catalog` | `routers/datasphere_assessment.py` (`_authorize_owner` → `can_administer`) | ya son del dueño por su cuenta; no pasan por el predicado de publicación |

**Brechas que importan al abrirlo:** (1) ampliar `can_execute` sin más habría dejado a un consultor resolver perfiles compartidos (el check era implícito); (2) **no existe la noción de «cliente autorizado»**: `client` es texto libre (H4 de la auditoría) y escribe en `catalog_local/<cliente>`; (3) las garantías de integridad H1–H6 viven en el PR de P1 (`release/republication-p1-20261027`), **no** en `main`.

## 2. Capacidad Publicar para consultores (implementada, apagada)

| Perfil | Política |
|---|---|
| Dueño (`APP_OWNER`) | conserva todo, sin depender de la capacidad de consultores |
| Consultor full | puede publicar **solo si** la capacidad está *disponible* (abajo) y **solo sobre clientes autorizados explícitamente** |
| Solo lectura (`ACADEMY_USERS`) · fuera de la allowlist · anónimo | denegado siempre (UI y POST) |
| Perfiles de clientes compartidos, regenerar el Catálogo, herramientas administrativas | solo `APP_OWNER` (`access.is_owner` **no se amplió**) |

**La capacidad está disponible únicamente si coinciden tres condiciones** (`consultants_enabled`, falla cerrado):
1. `DANWOR_PUBLICATION_CONSULTANTS` encendida — decisión explícita del dueño;
2. garantías de integridad ACTIVAS: `publication_txn.safe_mode_enabled()` (`DANWOR_PUBLICATION_SAFE`, fase 0 / P1). Si el código de la publicación segura no está en el despliegue, o la sonda falla, **no se concede**;
3. `DANWOR_PUBLICATION_CONSULTANT_CLIENTS` con al menos un cliente (lista separada por comas, sin comodines, sin distinguir mayúsculas ni espacios repetidos).

UI y backend usan el **mismo** predicado (`can_execute`) y además el backend exige `client_authorized(request, client)` (si no, `CLIENT_NOT_AUTHORIZED` sin ejecutar nada) y `can_use_client_profiles` (solo dueño) cuando llega un `profile_id`. El consultor usa **sus propias credenciales** de Datasphere (la autorización real la sigue dando el tenant) y la ejecución queda atribuida a su usuario. Se conservan mismo origen, validación de hosts/token URL, preflight OAuth, secretos transitorios y saneamiento.

**Por qué no hay distinción CREATE/UPDATE:** la identidad vigente no permite demostrarla. La ausencia de un manifiesto no prueba una primera publicación (manifiestos v1/v2 heredados, variantes de mayúsculas del cliente, objetos del pool compartidos entre raíces del mismo cliente, y el ledger por cliente solo existe desde P1); lo único demostrable —«este cliente fue autorizado»— es lo que se exige. Primera publicación y republicación pasan por las mismas compuertas y se cierran juntas.

## 3. Dependencias y bloqueos reales respecto de la fase 0

* **H1–H3, H5, H6** (extracción incompleta, borrados/persistencia confirmada, candado, manifiestos íntegros) y los endurecimientos B1/B2 están en el PR de P1, **pendiente de merge** y con `DANWOR_PUBLICATION_SAFE` apagado. Hasta entonces `publication_guarantees_active()` es `False` en `main` ⇒ **ningún consultor puede publicar**, por construcción.
* **B2 (persistencia obligatoria)**: con la publicación segura y la etapa de assessment, producción exige `DANWOR_PERSISTENCE=remote`, `DANWOR_PUBLICATION_MIRROR_UP=1` y `governed-assessments` en `SPACES_PERSIST_PATHS`; sin eso toda publicación (también la del dueño) aborta con un código explícito.
* **H4 (identidad del pool sin Space)** queda mitigado, no resuelto: P1 agrega `POOL_IDENTITY_CONFLICT` y clave canónica de cliente, pero el aislamiento físico por tenant/space (Annex A) sigue pendiente. Es la razón de la **lista explícita de clientes** (acota el radio de daño) y de no admitir comodines.
* Réplica única (candado y compuerta de lectores no protegen entre instancias).
* **Historial de autoría:** no se tocó; sigue siendo el diseño separado (`log_activity("datasphere_publication_run")` ya registra usuario/cliente/space/modelo/estado; sus límites están en la auditoría).

## 4. Activación segura y gradual

| Etapa | Qué se enciende | Quién publica | Verificación |
|---|---|---|---|
| 0 | desplegar este código con todo apagado | solo el dueño (igual que hoy) | los consultores no ven «+»; POST manual → 403 (probado) |
| 1 | merge de P1; `DANWOR_PUBLICATION_SAFE=1` + requisitos B2, en entorno aislado primero | solo el dueño, ya con garantías | runbook de la publicación segura; republicación idéntica = `NO_CHANGE` |
| 2 | `DANWOR_PUBLICATION_CONSULTANTS=1` + **un** cliente piloto en `…CONSULTANT_CLIENTS` | consultores full, solo ese cliente | consultor: «+» visible, otro cliente → `CLIENT_NOT_AUTHORIZED`; dueño verifica el resultado |
| 3 | ampliar la lista cliente por cliente | idem | revisión por cliente (H4 sigue acotado por la lista) |
| 4 | evaluar CREATE/UPDATE y aislamiento por Space solo cuando la identidad lo permita | — | decisión arquitectónica aparte |

**Retroceso inmediato:** apagar `DANWOR_PUBLICATION_CONSULTANTS` (o vaciar la lista, o apagar la publicación segura) quita la capacidad en la **siguiente request**, sin redeploy de código: el predicado se evalúa por solicitud.

## 5. Pruebas

Implementadas (`tests/webapp/test_datasphere_publication_consultant_access.py`, 26): todo apagado = como antes · cada condición por separado es necesaria (UI y POST) · sin código de publicación segura / sonda que falla = cerrado · consultor full ve «+», panel y clientes, publica con **sus** credenciales atribuidas a su usuario · primera publicación y republicación bajo las mismas compuertas (y se cierran juntas) · cliente no autorizado / comodín / vacío · coincidencia sin distinguir mayúsculas · dueño sobre cualquier cliente · solo lectura, fuera de allowlist y anónimo denegados · retiro de la allowlist al siguiente request · **POST manual no elude lo que la UI oculta** (también en otro módulo) · perfiles compartidos, `/datasphere-profiles` y `refresh-catalog` siguen solo-dueño · mismo origen · validación de host antes de cualquier extracción · el secreto no sale. Mutaciones verificadas (quitar cualquiera de las compuertas rompe pruebas). Las 53 pruebas previas de publicación/Assessment pasan sin cambios.

Propuestas para la revisión (requieren P1 integrado): E2E consultor con el exportador real y bucket simulado (publicación inicial, republicación idéntica `NO_CHANGE`, fallo de Spaces ⇒ `ROLLED_BACK`, B2 abortando por configuración) y un contrato que falle si aparece un nuevo consumidor de `access.is_owner` en el camino de publicación.

## 6. Capturas comparativas

Mismo código (plantillas, predicado y CSS reales), módulo 04 → Catálogo, perfil por perfil (`scratchpad/capturas_publicar/`): **1** consultor con la capacidad apagada (hoy: sin «+», como la captura del consultor) · **2** dueño (con «+», como la captura del dueño) · **3** consultor con la capacidad disponible (con «+», idéntico al del dueño). Solo lectura y consultor sin garantías: sin «+» (verificado en DOM, `build_pub_demo.py`).
