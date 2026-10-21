# Runbook — CCHH → SharePoint: grants Selected (LOCAL / DigitalOcean)

**Estado: preparado, sin ejecución real.** Herramienta: `tools/sharepoint_selected_grant.py` (detalle de compuertas en `tools/SHAREPOINT_SELECTED_GRANT.md`). Lo que hoy bloquea es **una** dependencia administrativa: el consentimiento delegado `Sites.ReadWrite.All` (ver §2).

## 1. Matriz de aplicaciones, carpetas y permisos

| | **LOCAL (CONTROL)** | **DigitalOcean (producción)** |
|---|---|---|
| Perfil de la herramienta | `control-local` | `production-do` |
| Aplicación receptora | `ad98b5cd-71d0-451f-8d70-e8581a4788fa` | `eeca6df8-2436-422f-a1b8-05ff9e8ff653` |
| Sitio / carpeta | sitio de CONTROL / `Hub` | `Comunicaciones` / `CCHH HUB` |
| Site ID | `a129a1d0-a4ee-4438-8de8-9e39e7007764` | `danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a` |
| Drive ID | `b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3` | `b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE` |
| Item ID | `01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ` | `01ULA425ONX3YLUO3XTNDI46FMF43PFL4X` |
| Modos habilitados hoy | check, dry-run, apply, rollback | **sólo check y dry-run** (apply/rollback/`--request-write-scope` rechazados en código) |
| Rol a conceder | `write` (un grant) | `write`, **sin GO todavía** |
| Estado del grant | no creado (el PUT de prueba da 403) | no creado ni verificado |
| Permiso Selected de la app en Entra (`Files.SelectedOperations.Selected`) | el token del Hub ya lo trae según el diagnóstico; confirmar en Entra | **no verificado** |

Cliente administrativo (identidad de la herramienta, **no** es una app del Hub): Microsoft Graph Command Line Tools `14d82eec-204b-4c2f-b7e8-296a70dab67e`. Tenant `f287b953-5428-4a5e-ae17-bd9c4cfaedbe`. Los permisos personales del usuario sobre CONTROL **no** equivalen a permisos sobre Comunicaciones: cada destino se verifica con sus propias lecturas.

Variables del Hub a contrastar en DO (sólo lectura; no se cambian acá): `HC_SHAREPOINT_SITE_ID`, `HC_SHAREPOINT_DRIVE_ID`, `HC_CV_ROOT_ITEM_ID`, `HC_DOCUMENT_WRITE_SCOPE`. `HC_SHAREPOINT_DRIVE_ID` debe ser el drive de arriba y `HC_CV_ROOT_ITEM_ID` el item `CCHH HUB` o un **descendiente** suyo (el grant se hereda hacia abajo). La pantalla `/cchh/diagnostico/sharepoint` (owner) muestra el destino actual.

## 2. Consentimientos administrativos pendientes

| # | Qué | Quién | Para qué exactamente | Estado |
|---|---|---|---|---|
| 1 | `Sites.ReadWrite.All` **delegado** para Microsoft Graph Command Line Tools | Brian (admin del tenant) | Obtener un token con escritura de sitios para el único POST de permisos de CONTROL. Hoy sólo hay `Sites.Read.All`. | **Pendiente** (único bloqueo del paso CONTROL) |
| 2 | Contingencia: `Sites.FullControl.All` | Brian | Sólo si el POST da 403 con un usuario que **sí** es Propietario/Control total y `Sites.ReadWrite.All` ya consentido (la doc de Selected lo menciona). No se pide ahora. | No solicitar todavía |
| 3 | Permiso Selected de la app LOCAL en Entra | Admin de Entra | Verificar que exista y esté consentido (si falta, el grant no alcanza). Sólo verificar. | A verificar |
| 4 | Permiso Selected de la app DO (`eeca6df8…`) en Entra | Admin de Entra | Igual que el anterior, para producción. | A verificar (antes de habilitar DO) |
| 5 | Persona con Propietario/Control total de `CCHH HUB` | Owner del sitio Comunicaciones | Autorización efectiva para crear el grant productivo. | Sin verificar; GO independiente |

## 3. Diagnóstico (sólo lectura)

Variables (PowerShell; en bash son equivalentes con comillas simples):

```powershell
$TENANT='f287b953-5428-4a5e-ae17-bd9c4cfaedbe'; $ADMIN='14d82eec-204b-4c2f-b7e8-296a70dab67e'; $ME='jose@danwor.com'
$LOCAL_APP='ad98b5cd-71d0-451f-8d70-e8581a4788fa'; $CTRL_SITE='a129a1d0-a4ee-4438-8de8-9e39e7007764'
$CTRL_DRIVE='b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3'; $CTRL_ITEM='01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ'
$DO_APP='eeca6df8-2436-422f-a1b8-05ff9e8ff653'; $DO_SITE='danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a'
$DO_DRIVE='b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE'; $DO_ITEM='01ULA425ONX3YLUO3XTNDI46FMF43PFL4X'
```

**LOCAL** — `--check` y `--dry-run` (cambiar el modo; con `--request-write-scope` el `--dry-run` también ensaya el consentimiento de `Sites.ReadWrite.All` sin escribir nada y mostrará `consent_not_granted`/`AADSTS65001`/`AADSTS90094` mientras Brian no lo conceda):

```powershell
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $LOCAL_APP --site-id $CTRL_SITE --drive-id $CTRL_DRIVE --item-id $CTRL_ITEM --admin-client-id $ADMIN --expect-user $ME --request-write-scope --acknowledge-undetermined --acknowledge-inheritance-break
```

**DigitalOcean (producción, sólo lectura)** — `--check` y `--dry-run`; no admiten `--request-write-scope`:

```powershell
python tools/sharepoint_selected_grant.py --check --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME
```

Lectura del resultado de producción: `target.name = CCHH HUB` y `web_id` verificado; `evidence` completa en las dos fuentes; `user.upn` = tu cuenta; `would_apply` siempre `false` con el bloqueo `apply_disabled_for_profile` (esperado). `duplicate_state = UNDETERMINED` no es evidencia de ausencia ni de autorización. Un 403 en el destino productivo significa que **ese** usuario no ve Comunicaciones, sin importar CONTROL.

Pantalla del Hub (owner): `/cchh/diagnostico/sharepoint` → «Sonda de lectura» (`app_grant_state`: PRESENT / ABSENT sólo si el llamador es owner del item / UNKNOWN). En DO es sólo lectura; la «Prueba de escritura» funciona únicamente en localhost.

## 4. Aplicación autorizada sobre CONTROL (requiere GO específico y el consentimiento #1)

Precondiciones: §2 #1 concedido; el `--dry-run` de LOCAL con `--request-write-scope` da `ok: true`, `blockers: []`, `would_apply: true`, `target.name = Hub`; tu cuenta es Propietario/Control total de `Hub`. **Terminal interactiva real** (el panel Terminal o PowerShell; la herramienta se niega sin TTY, así que no puede correr desde un agente):

```powershell
python tools/sharepoint_selected_grant.py --apply --tenant-id $TENANT --client-id $LOCAL_APP --site-id $CTRL_SITE --drive-id $CTRL_DRIVE --item-id $CTRL_ITEM --admin-client-id $ADMIN --expect-user $ME --acknowledge-undetermined --acknowledge-inheritance-break --confirm-apply "CONCEDER GRANT WRITE"
```

La herramienta muestra operación, cuerpo, usuario, carpeta y riesgos; tipear `CONCEDER GRANT WRITE`. Relee permisos justo antes del POST y aborta ante cualquier coincidencia. Un solo POST. Guardar la salida y `reports/sharepoint_selected_grant/control-local-*-result.json` (trae `permission_id`).

## 5. Verificación funcional del uploader (después del grant en CONTROL)

1. `GET /drives/{drive}/items/{item}/permissions/{permission_id}` (o `--check`): rol `write`, `inheritedFrom` nulo. Ver en SharePoint que `Hub` quedó con permisos propios.
2. **Prueba de escritura (un único TXT)**, en el Hub local (localhost, sólo owner), `/cchh/diagnostico/sharepoint` → «Prueba de escritura» con el sharing link de `Hub` y la frase `ESCRIBIR ARCHIVO DE PRUEBA`. Usa el **mismo token delegado MSAL del navegador que el uploader** (`dwHumanCapitalDocumentWriteToken`: scopes base + `HC_DOCUMENT_WRITE_SCOPE`, `AZURE_CLIENT_ID` de la app LOCAL), nunca el token de la herramienta administrativa. Un solo PUT, nombre `cchh-sharepoint-upload-test-<UTC>-<UUID>.txt`, `conflictBehavior=fail`, sin reintentos. Antes daba **403**; esperado **201**. Guardas (sin PUT si fallan): token de la app LOCAL (`token_app_not_allowed`), carpeta resuelta = drive/item de `Hub` (`control_not_allowlisted`), distinta de CURRENT. Resultado esperado: `ok: true`, `stage: "put"`, `attempts: 1`, `verification.all_ok: true` (nombre, tamaño, drive y carpeta padre coinciden) y `put.item.web_url`.
   - **Verificar después:** abrir `put.item.web_url`, o listar `Hub` en SharePoint, o (lectura) `GET /drives/{DRIVE}/items/{item.id}?$select=id,name,size,parentReference,file` / `GET /drives/{DRIVE}/items/{ITEM}/children?$select=id,name,size&$top=200`.
   - **Limpieza controlada:** a mano en SharePoint, borrando **sólo** el archivo cuyo nombre sea exactamente `filename` del resultado (único por UTC+UUID); va a la papelera del sitio, recuperable. No hay DELETE automatizado a propósito.
   - **Si falla:** `403` otra vez → revisar el permiso Selected de la app LOCAL en Entra y que el usuario tenga escritura propia en `Hub` (app ∩ usuario); `put_unverified` → el archivo existe pero la respuesta no coincide: limpiar a mano y reportar.
3. Subida real de un CV desde la UI local apuntando a CONTROL (variables `HC_*` locales hacia `Hub`, no producción): el archivo aparece en la carpeta del candidato (descendiente de `Hub`) y el perfil queda con su documento.
4. Repetir 2–3 con un segundo usuario con permiso de escritura propio sobre `Hub` (acceso efectivo = intersección app ∩ usuario).

## 6. Criterio de aceptación antes de habilitar DigitalOcean

Todos, con evidencia guardada en el handoff:

- [ ] CONTROL: grant creado (`result.json`), permiso `write` no heredado, prueba de escritura 201 y subida real de CV correcta (§5).
- [ ] Rollback ensayado en CONTROL o decisión explícita de no ensayarlo; se comprobó qué pasa con la herencia de `Hub`.
- [ ] El owner del sitio Comunicaciones acepta la **ruptura de herencia** de `CCHH HUB` (la carpeta productiva con los CVs pasaría a permisos propios; los cambios futuros de permisos de la biblioteca ya no se propagarían a ella; borrar el grant no garantiza restaurarla).
- [ ] `--check`/`--dry-run` de `production-do` limpios (§3) y el usuario que aplicará es Propietario/Control total de `CCHH HUB` (verificado en ese destino, no inferido de CONTROL).
- [ ] Permiso Selected de la app DO verificado en Entra (§2 #4) y variables `HC_*` de DO consistentes con el perfil (§1).
- [ ] **GO independiente** para habilitar producción: cambio de código revisado (`modes` de `production-do`) con tests, nunca una bandera.

## 7. Riesgos, rollback y resultados desconocidos

| Riesgo | Tratamiento |
|---|---|
| Ruptura de herencia en la carpeta (CONTROL: bajo; producción: alto) | Aceptación explícita del owner del sitio; ver §6. |
| `Sites.ReadWrite.All` no suficiente | La respuesta real del POST decide; el reporte lista ambas hipótesis (scope / Propietario). Escalar según §2 #2, no antes. |
| Grant creado pero el PUT sigue en 403 | Sospechar del permiso Selected de la app en Entra (§2 #3/#4) y del usuario (intersección). |
| Duplicados | La ausencia no se puede probar; mitigado con reconocimiento + relectura + un solo POST. Un duplicado se borra por su propio id. |

**Rollback** (un DELETE, nunca automático; terminal interactiva):

```powershell
python tools/sharepoint_selected_grant.py --rollback --tenant-id $TENANT --client-id $LOCAL_APP --site-id $CTRL_SITE --drive-id $CTRL_DRIVE --item-id $CTRL_ITEM --admin-client-id $ADMIN --expect-user $ME --permission-id <permission_id> --audit-file reports/sharepoint_selected_grant/<archivo>-result.json --confirm-rollback "REVOCAR GRANT WRITE"
```

Sólo borra un `permission_id` registrado por un apply previo y que siga siendo un grant `write` no heredado de la app LOCAL en ese ítem. No hay rollback productivo habilitado.

**Resultado desconocido (código de salida 4: timeout/corte tras el POST o el DELETE).** No reintentar. La herramienta ya relee y deja `evidence_after`. (1) Esperar unos minutos y correr `--check`; (2) si aparece un grant de la app, usar su `permission_id` (figura en `evidence_after`) con `--rollback` sólo si hay que revertir; (3) si no aparece, tratarlo como no creado y decidir un nuevo intento con GO; (4) conservar el `result.json` en el handoff.
