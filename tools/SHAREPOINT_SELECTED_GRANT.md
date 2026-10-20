# Grant Selected `write` sobre una carpeta de SharePoint — `tools/sharepoint_selected_grant.py`

**Estado: PREPARADO PARA UNA EJECUCIÓN CONTROLADA, SIN EJECUCIÓN REAL.** Ningún test ni invocación de este checkpoint habló con Graph ni creó/borró un grant. La ejecución real requiere un GO específico del owner y la hace una persona en una terminal interactiva (la herramienta se niega a correr sin TTY).

## Qué hace

Concede el rol `write` a **una** aplicación Entra (la app LOCAL) sobre **una** carpeta concreta (`Hub` de CONTROL; nunca el sitio completo ni `CCHH HUB`), con el endpoint oficial de Graph v1.0:

```
POST /drives/{drive-id}/items/{item-id}/permissions
{"grantedToV2": {"application": {"id": "<client id>"}}, "roles": ["write"]}
```

Es independiente del Hub: sólo stdlib, no importa `webapp`/`config`/`core`, no usa el token del Hub y no agrega infraestructura.

## Modos

| Modo | Efecto |
|---|---|
| `--check` | Sólo lectura. Valida allowlist + token, lee la metadata del destino (debe ser **carpeta**), enumera lo que Graph deja ver y consulta la identidad (`GET /me`). Nunca emite POST/DELETE ni escribe archivos. |
| `--dry-run` | `--check` + preview **exacto** del request y veredicto de las compuertas (`would_apply`, `blockers`, `pending_at_apply`). Con las mismas banderas de reconocimiento que el apply muestra qué haría. Nunca emite POST/DELETE ni escribe archivos. |
| `--apply` | **Un** POST, sin reintentos, detrás de todas las compuertas de abajo. Registra auditoría (archivos nuevos). |
| `--rollback` | **Un** DELETE del `permission_id` registrado por un `--apply` previo, verificado y confirmado. **Nunca** se ejecuta solo ni como parte de `--apply`. |

Códigos de salida: `0` ok · `1` error de Graph/red · `2` validación, allowlist, autenticación o identidad · `3` no confirmado / bloqueado por compuertas · `4` el POST/DELETE **pudo** haberse ejecutado (resultado desconocido; no se reintenta).

## Compuertas de `--apply` (en este orden; cualquier falla = sin POST)

1. **Allowlist estricta**: tenant, app destinataria, site, drive e item deben coincidir exactamente con la fila `control-local`. La app de producción `eeca6df8-…` está reservada y se rechaza. Cualquier otro destino (p. ej. `CCHH HUB`) se rechaza sin tocar la red.
2. **Banderas explícitas del owner** (antes de autenticar): `--confirm-apply "CONCEDER GRANT WRITE"`, `--acknowledge-undetermined`, `--acknowledge-inheritance-break`, `--admin-client-id`, `--expect-user`.
3. **TTY interactivo**: sin consola interactiva se detiene (un agente o un pipe no pueden aplicar).
4. **Token**: tenant correcto, app administrativa = `--admin-client-id` (nunca una app del Hub), audiencia Graph, delegado, no vencido y scope de escritura de sitios (`Sites.ReadWrite.All`). Los errores OAuth conservan el código `AADSTS`, los `error_codes` y una pista (`consent_not_granted`, `consent_rejected_by_user`, `scope_not_requestable_by_this_client`).
5. **Identidad**: `GET /me` debe devolver el UPN indicado en `--expect-user`.
6. **Destino por IDs**: `id`, `parentReference.driveId` y `sharepointIds.siteId` coinciden con la allowlist, es **carpeta**, y su nombre es `Hub` (el nombre se suma a los IDs; no los reemplaza).
7. **Enumeración** (driveItem + listItem): cualquier grant ya presente (cualquier rol) bloquea; una fuente incompleta bloquea; `UNDETERMINED` exige el reconocimiento explícito.
8. **Confirmación interactiva**: se muestra operación, cuerpo, usuario, carpeta, estado de duplicados y riesgo de herencia; hay que tipear exactamente `CONCEDER GRANT WRITE`.
9. **Relectura inmediata antes del POST**: si aparece **cualquier** coincidencia observable o la relectura es incompleta, aborta sin POST.
10. **Registro de intención** (`…-intent.json`, archivo nuevo, modo `x`): si no se puede escribir, aborta **antes** del POST.
11. **Un único POST**. Si hay timeout/corte después de enviarlo: resultado **desconocido** (código 4), **sin reintento**; sólo se relee y se informa para verificar a mano.
12. **Registro del resultado** (`…-result.json`) con `permission_id` y respuesta, y relectura posterior (`evidence_after`).

## Auditoría

`--audit-dir` (por defecto `reports/sharepoint_selected_grant/`, ignorado por git). Sólo `--apply` y `--rollback` escriben; nunca contienen tokens ni headers. El `…-result.json` guarda `post.permission_id` y es el insumo del rollback. Si el resultado fue desconocido, el `permission_id` puede salir de `evidence_after` (cada coincidencia lleva su `permission_id`).

## Comandos exactos

Variables (PowerShell o bash, mismos valores):

```bash
TENANT=f287b953-5428-4a5e-ae17-bd9c4cfaedbe
APP=ad98b5cd-71d0-451f-8d70-e8581a4788fa
SITE=a129a1d0-a4ee-4438-8de8-9e39e7007764
DRIVE='b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3'
ITEM=01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ
ADMIN=14d82eec-204b-4c2f-b7e8-296a70dab67e   # Microsoft Graph Command Line Tools (cliente público)
ME=jose@danwor.com
```

1) Ensayo de consentimiento y preview (no escribe nada; pide `Sites.ReadWrite.All` para ver si el tenant lo permite):

```bash
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $APP --site-id $SITE --drive-id "$DRIVE" --item-id $ITEM --admin-client-id $ADMIN --expect-user $ME --request-write-scope --acknowledge-undetermined --acknowledge-inheritance-break
```

Esperado: `ok: true`, `user.upn` = el tuyo, `target.name = Hub`, `blockers: []`, `would_apply: true`, `preview` con el POST exacto. Si falla con `consent_not_granted`/`AADSTS65001`/`AADSTS90094`, **no avanzar**: ese código es el único motivo concreto para escalar al admin del tenant.

2) Aplicación real (requiere GO específico; terminal interactiva; un solo POST):

```bash
python tools/sharepoint_selected_grant.py --apply --tenant-id $TENANT --client-id $APP --site-id $SITE --drive-id "$DRIVE" --item-id $ITEM --admin-client-id $ADMIN --expect-user $ME --acknowledge-undetermined --acknowledge-inheritance-break --confirm-apply "CONCEDER GRANT WRITE"
```

Luego tipear `CONCEDER GRANT WRITE` cuando lo pida. Guardar el JSON de salida y el `…-result.json`.

3) Validación posterior: `GET /drives/{drive}/items/{item}/permissions/{permission_id}` debe mostrar rol `write` y `inheritedFrom` nulo; después repetir la prueba de escritura controlada del Hub (`be4d1a2`): hoy da 403, el éxito esperado es 201.

4) Rollback (sólo si hace falta; terminal interactiva; un solo DELETE):

```bash
python tools/sharepoint_selected_grant.py --rollback --tenant-id $TENANT --client-id $APP --site-id $SITE --drive-id "$DRIVE" --item-id $ITEM --admin-client-id $ADMIN --expect-user $ME --permission-id <permission_id> --audit-file reports/sharepoint_selected_grant/<archivo>-result.json --confirm-rollback "REVOCAR GRANT WRITE"
```

Antes de borrar verifica que el permiso vivo sea un grant `write` **no heredado** de la app LOCAL en ese ítem; tipear `REVOCAR GRANT WRITE`.

## Autenticación (identidad administrativa independiente)

Nunca hay contraseñas, secretos ni tokens en argumentos CLI, código ni logs. Dos vías:

1. **`DANWOR_GRAPH_ADMIN_TOKEN`**: token delegado del admin; se pre-chequea de forma conservadora (tenant, app = `--admin-client-id`, ≠ apps del Hub, audiencia Graph, delegado, vigente) y se rechaza si no es legible.
2. **Device code (RFC 8628) con `--admin-client-id`**: cliente **público**, autoridad = tenant de la allowlist, sin refresh token. Pide `Sites.Read.All` (check/dry-run), `Sites.ReadWrite.All` (apply/rollback o `--request-write-scope`) y `User.Read`.

## Permisos requeridos — dos niveles distintos

| Nivel | Qué es | Requisito |
|---|---|---|
| **(a) Permiso Graph del cliente administrativo** | Scope delegado del token | `Sites.ReadWrite.All` para apply/rollback. **Es condición necesaria según esta herramienta, pero no está probado que sea suficiente**: la doc de la página del POST lista `Files.ReadWrite` como mínimo, la de listItem exige `Sites.ReadWrite.All` y el overview de Selected exige `Sites.FullControl.All` o `Sites.Selected`/`Lists.SelectedOperations.Selected` + Owner/FullControl. |
| **(b) Autorización efectiva** | Lo que el **usuario** puede hacer sobre la carpeta | Propietario / Control total de `Hub`. No se puede verificar de antemano con una API documentada; un 403 en el POST con (a) correcto indica falta de (b). |

Un 401/403 en el POST se reporta con ambas hipótesis (`diagnosis`). Subir de scope (p. ej. `Sites.FullControl.All`, que requiere consentimiento de administrador) no está implementado ni autorizado: sería otro cambio de código con GO.

## Evidencia de duplicados (y su límite)

Sólo la **presencia** de un grant equivalente es evidencia autoritativa.

| Estado | Significado | `--apply` |
|---|---|---|
| `ALREADY_GRANTED` | La app aparece con rol `write`/`owner`/`fullcontrol` | Bloquea (nada que hacer) |
| `ROLE_INSUFFICIENT_PRESENT` | La app aparece sólo con `read` | Bloquea (cambiar el rol es otra operación) |
| `UNDETERMINED` | No se vio ningún grant, **pero la ausencia no se puede probar** | Avanza **sólo** con `--acknowledge-undetermined` |
| `ABSENT_CONFIRMED` | Ausencia autoritativa | No es alcanzable hoy |

Por qué la ausencia no se prueba (`absence_is_authoritative` devuelve siempre `False`): `driveItem/permissions` lista permisos de **compartir** (un propietario ve todos, un no-propietario sólo los que le aplican); `listItem/permissions` no está documentado como enumeración exhaustiva de grants Selected; los heredados de ancestros no se enumeran. Por eso la idempotencia no sale de una consulta sino del conjunto: reconocimiento explícito + relectura inmediata + aborto ante cualquier coincidencia + un único POST sin reintentos. Si aun así quedara un duplicado, cada permiso tiene su propio id y se borra individualmente.

## Riesgos residuales

- **Ruptura de herencia.** Microsoft documenta que asignar permisos de aplicación a una carpeta rompe la herencia en ese recurso (queda con permisos únicos; los cambios futuros de la biblioteca ya no se propagan a `Hub`). **Borrar el grant no garantiza restaurar la herencia**: la documentación de "Remove access to an item" no lo dice, y restablecerla es una acción aparte en SharePoint (verificar con el admin del sitio). Aceptable en `Hub` de CONTROL; evaluarlo antes de repetirlo en una carpeta productiva.
- **Suficiencia del scope no probada** (ver arriba): el POST real es la prueba.
- **Resultado desconocido tras timeout**: puede haber un grant creado sin respuesta. La herramienta relee y registra, pero la verificación final es manual (SharePoint / `--check`).
- **Alcance heredado hacia abajo y acceso efectivo = intersección** con el usuario que sube; un grant por Client ID (el de LOCAL no dice nada de producción ni de CURRENT).
- **Consentimiento previo** del permiso Selected en la app LOCAL (Entra): el grant no lo reemplaza. Si el PUT de prueba sigue en 403 con el grant visible, ese es el siguiente sospechoso.
- Auditoría local (`reports/…`): no se versiona; copiar `result.json` al handoff si se necesita conservarlo.

## Evidencia documental oficial

- Crear permiso en un driveItem: <https://learn.microsoft.com/en-us/graph/api/driveitem-post-permissions?view=graph-rest-1.0>
- Selected permissions (roles, herencia, intersección en delegado): <https://learn.microsoft.com/en-us/graph/permissions-selected-overview>
- Listar permisos de un driveItem ("effective sharing permissions"): <https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0>
- Listar / crear permisos en un listItem: <https://learn.microsoft.com/en-us/graph/api/listitem-list-permissions?view=graph-rest-1.0> · <https://learn.microsoft.com/en-us/graph/api/listitem-post-permissions?view=graph-rest-1.0>
- Eliminar un permiso (sólo no heredados, `inheritedFrom` nulo; 204): <https://learn.microsoft.com/en-us/graph/api/permission-delete?view=graph-rest-1.0>
- Device authorization grant: <https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code>
