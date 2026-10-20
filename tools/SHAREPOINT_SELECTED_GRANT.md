# Grant Selected `write` sobre una carpeta de SharePoint — `tools/sharepoint_selected_grant.py`

**Estado del checkpoint: PREPARADO, SIN EJECUCIÓN REAL.** `--apply` está deshabilitado en código (`APPLY_ENABLED = False`) y la aplicación de producción no es destino ejecutable.
Ningún test ni invocación de este checkpoint habló con Graph ni creó un grant.

## Qué hace

Concede el rol `write` a **una** aplicación Entra sobre **una** carpeta concreta (nunca el sitio completo), con el endpoint oficial de Graph v1.0:

```
POST /drives/{drive-id}/items/{item-id}/permissions
{"grantedToV2": {"application": {"id": "<client id>"}}, "roles": ["write"]}
```

Es una herramienta administrativa **independiente** del Hub: sólo stdlib de Python, no importa `webapp`/`config`/`core`, no toca el uploader de CCHH, no usa el token del Hub y no agrega infraestructura.

## Modos

| Modo | Efecto |
|---|---|
| `--check` | Sólo lectura. Valida allowlist + token, lee la metadata del destino (debe ser **carpeta**) y enumera lo que Graph deja ver. Nunca emite un POST. |
| `--dry-run` | `--check` + preview **exacto** del request y veredicto de las compuertas (`would_apply`, `blockers`). Nunca emite un POST. |
| `--apply` | Única operación de escritura: **un** POST, sin reintentos. **Deshabilitado**: sale con código 3 antes de autenticar o llamar a Graph. |

Códigos de salida: `0` ok · `1` error de Graph/red · `2` validación, allowlist o autenticación · `3` apply deshabilitado/no confirmado/bloqueado por compuertas.

## Invocación

Todos los identificadores son obligatorios y se validan contra la allowlist (no hay valores por defecto):

```bash
python tools/sharepoint_selected_grant.py --dry-run \
  --tenant-id f287b953-5428-4a5e-ae17-bd9c4cfaedbe \
  --client-id ad98b5cd-71d0-451f-8d70-e8581a4788fa \
  --site-id a129a1d0-a4ee-4438-8de8-9e39e7007764 \
  --drive-id "b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3" \
  --item-id 01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ \
  --admin-client-id <app registration pública del admin>
```

Dependencias: Python ≥ 3.10 y nada más (sin `msal`, `requests` ni SDK de Graph).

## Autenticación (identidad administrativa independiente)

Nunca hay contraseñas, secretos ni tokens en argumentos CLI, código ni logs (`--token` no existe). Dos vías:

1. **Variable de entorno `DANWOR_GRAPH_ADMIN_TOKEN`**: token delegado que el admin obtuvo con sus propias herramientas. La herramienta lo pre-chequea de forma conservadora (tenant, cliente ≠ apps del Hub, audiencia Graph, delegado, no vencido, scope) y **rechaza** un token ilegible: leer claims de un token de Graph no es un contrato soportado (lo dice la doc de Entra), por eso es sólo una compuerta adicional; quien valida el token es Graph.
2. **Device code flow (RFC 8628) con `--admin-client-id`**: cliente **público** (sin secreto), autoridad = el tenant de la allowlist, sin `offline_access` (no hay refresh token). Los scopes concedidos se leen de la **respuesta del endpoint de token**, no de decodificar el token. El código de login se imprime por stderr; el token queda sólo en memoria.

El token del Hub (apps `ad98b5cd…`/`eeca6df8…`) se rechaza en ambas vías: un grant no puede concederse con la identidad de la app que lo recibe.

La app registration del admin **no es** la del Hub y **no se crea acá**. Debe ser un cliente público con "Allow public client flows" y con el scope delegado consentido (ver siguiente sección). Quién la aporta y cómo se consiente es parte del procedimiento futuro.

## Permisos requeridos — dos niveles distintos

| Nivel | Qué es | Requisito |
|---|---|---|
| **(a) Permiso Graph del cliente administrativo** | Scope delegado del token del admin | `Sites.Read.All` alcanza para `--check`/`--dry-run`. Para `--apply`: `Sites.ReadWrite.All` (o `Sites.Manage.All` / `Sites.FullControl.All`). Con `Sites.ReadWrite.All` un **único** token cubre todo: leer metadata, enumerar permisos y crear el grant. |
| **(b) Autorización efectiva sobre el recurso** | Lo que el **usuario** admin puede hacer sobre esa carpeta | En delegado, Graph exige además que el usuario pueda administrar el acceso (propietario o Control total sobre la carpeta). Un token con el scope correcto de un usuario sin ese rol devuelve 403 en el POST. |

Qué se verifica y qué no: (a) se verifica antes de operar (scope del token). (b) **no se puede verificar de antemano** con una API documentada; se manifiesta como 403 `accessDenied` en el POST. Un 403 con (a) correcto debe leerse como falta de (b).

Nota: no se usa `Sites.FullControl.All` salvo que sea lo único disponible. Y ni el token del Hub (`Files.SelectedOperations.Selected`) ni un rol `write` permiten administrar permisos.

## Allowlist

Una fila en `ALLOWED_GRANTS` fija tenant, Client ID destinatario, site (GUID del site collection), drive e item. Los cinco valores del CLI deben coincidir **exactamente**. Además, antes de preparar nada se lee el destino real y se exige: `id` del ítem, `parentReference.driveId` y `sharepointIds.siteId` iguales a la allowlist, y que sea **carpeta** (`folder` presente, `file` ausente).

Hoy sólo existe `control-local` (app LOCAL → carpeta `Hub` de CONTROL). La app de producción `eeca6df8-…` está **reservada**: se rechaza explícitamente aunque los demás identificadores sean correctos. Agregar el destino definitivo (carpeta `CCHH HUB` del sitio Comunicaciones + app de producción) requiere un cambio de código revisado y un GO explícito del owner.

## Evidencia de duplicados (y su límite)

Sólo la **presencia** de un grant equivalente es evidencia autoritativa. Estados:

| Estado | Significado | `--apply` |
|---|---|---|
| `ALREADY_GRANTED` | La app aparece con rol `write`/`owner`/`fullcontrol` | No hace nada |
| `ROLE_INSUFFICIENT_PRESENT` | La app aparece sólo con `read` | Bloqueado (cambiar el rol es otra operación, no soportada) |
| `UNDETERMINED` | No se vio ningún grant, **pero la ausencia no se puede probar** | **Bloqueado** |
| `ABSENT_CONFIRMED` | Ausencia autoritativa | **No es alcanzable hoy** |

Por qué la ausencia no se puede probar (`absence_is_authoritative` devuelve siempre `False`):

- `GET /drives/{d}/items/{i}/permissions` lista los permisos **de compartir efectivos**; un propietario ve todos, un no-propietario sólo los que le aplican. No es una fuente de grants Selected y **no se usa como prueba de ausencia**.
- `GET /sites/{s}/lists/{l}/items/{id}/permissions` devuelve permisos de aplicación, pero la documentación no dice que sea una enumeración exhaustiva de grants Selected, ni que liste los creados vía `driveItem`, y no hay un control positivo validado.
- Los grants heredados de ancestros (herencia jerárquica) no se enumeran.

Consecuencia: **no hay garantía de idempotencia** y no se construye una falsa. Una enumeración con error (401/403/404) o incompleta nunca se interpreta como ausencia. Para habilitar `--apply` en el futuro hace falta una decisión explícita del owner sobre qué evidencia aceptar (por ejemplo, un control positivo documentado) — hoy no existe esa vía.

## Qué se registra

Sólo HTTP status, `error.code`, mensaje (acotado a 300 caracteres), `request-id`, `client-request-id`, y los resultados de enumeración (fuente, completitud, cantidad, roles de la app destinataria). Nunca tokens, headers de autorización ni secretos.

## Riesgos

- **Ruptura de herencia.** Microsoft documenta que asignar permisos de aplicación a una carpeta **rompe la herencia en ese recurso** (queda con permisos únicos). En `Hub` (CONTROL) el riesgo es bajo. En una carpeta de CVs significa que los cambios futuros de permisos en la biblioteca dejan de propagarse a esa carpeta: evaluarlo con el admin del sitio antes de aplicarlo en producción. Cuenta además para el límite de scopes de seguridad únicos (un grant es insignificante).
- **Alcance heredado hacia abajo.** El rol se aplica a la carpeta y a sus descendientes (herencia jerárquica): las subcarpetas por candidato quedan cubiertas.
- **Acceso efectivo = intersección.** En delegado, el acceso real es la intersección entre el grant de la app y los permisos del usuario que sube: cada persona que suba CVs necesita además su propio permiso de escritura sobre esa carpeta.
- **Un grant por Client ID.** El grant de la app LOCAL no dice nada sobre la app de producción ni sobre CURRENT.
- **Consentimiento previo.** El grant no reemplaza al consentimiento del scope en Entra (ya resuelto para LOCAL) ni al scope en el token: Selected necesita los tres pasos.

## Revisar y revertir un grant (manual, con GO; la herramienta no borra)

Revisión (lectura): ejecutar `--check` y mirar `evidence` (rol visto para la app) o, como administrador, `GET /drives/{drive-id}/items/{item-id}/permissions` y `GET /sites/{site-id}/lists/{list-id}/items/{item-id}/permissions`.

Reversión: `DELETE /drives/{drive-id}/items/{item-id}/permissions/{perm-id}` (doc "Remove access to an item": sólo permisos **no heredados** — `inheritedFrom` nulo —; 204 si sale bien; delegado `Files.ReadWrite`/`Files.ReadWrite.All`/`Sites.ReadWrite.All`). El `perm-id` sale de la respuesta del POST (`permission_id` en el reporte) o del listado.
La documentación **no establece** que borrar el grant restablezca la herencia de la carpeta: restablecerla es una acción aparte en SharePoint. Verificarlo con el admin del sitio.

## Evidencia documental oficial

- Crear permiso en un driveItem (sólo permisos de aplicación en SharePoint/OneDrive; body `grantedToV2`; 201): <https://learn.microsoft.com/en-us/graph/api/driveitem-post-permissions?view=graph-rest-1.0>
- Selected permissions (tres pasos, roles `read`/`write`/`owner`/`fullcontrol`, herencia, intersección en delegado): <https://learn.microsoft.com/en-us/graph/permissions-selected-overview>
- Listar permisos de un driveItem ("effective sharing permissions"; propietario vs no-propietario): <https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0>
- Listar permisos de un listItem: <https://learn.microsoft.com/en-us/graph/api/listitem-list-permissions?view=graph-rest-1.0>
- Crear permiso en un listItem (ruta de carpeta por listItem): <https://learn.microsoft.com/en-us/graph/api/listitem-post-permissions?view=graph-rest-1.0>
- Eliminar un permiso: <https://learn.microsoft.com/en-us/graph/api/permission-delete?view=graph-rest-1.0>
- Device authorization grant: <https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code>

Inconsistencia documental a tener presente: la página de driveItem lista `Files.ReadWrite` como permiso mínimo para el POST, la de listItem exige `Sites.ReadWrite.All` y el overview de Selected exige `Sites.FullControl.All` o `Sites.Selected`/`Lists.SelectedOperations.Selected` + Owner/FullControl. Esta herramienta pide `Sites.ReadWrite.All` por ser el único que cubre lectura, enumeración y POST con un solo token; **no se probó contra el servicio**.

## Procedimiento futuro (requiere GO explícito del owner en cada paso)

1. El owner decide quién actúa como admin y aporta una app registration pública (o un token) con `Sites.ReadWrite.All` consentido. Eso es consentimiento administrativo: no se solicita en este checkpoint.
2. Confirmar que el usuario admin es propietario/Control total de la carpeta `Hub`.
3. `--check` y luego `--dry-run` con la identidad admin; revisar `target`, `evidence`, `preview` y `blockers`.
4. El owner decide la política sobre `UNDETERMINED` (hoy bloquea) y habilita `--apply` en un commit aparte; recién entonces `--apply --confirm-apply "CONCEDER GRANT WRITE"`.
5. Verificar con `--check` (el reporte posterior trae `evidence_after`) y repetir la prueba de escritura controlada del Hub.
6. Producción: otra fila de allowlist y otro GO; nunca reutilizar el resultado de CONTROL.
