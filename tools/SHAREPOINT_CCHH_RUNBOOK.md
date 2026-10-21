# Runbook — CCHH → SharePoint: grants Selected (LOCAL / DigitalOcean)

**Estado: preparado, sin ejecución real.** Herramienta: `tools/sharepoint_selected_grant.py` (detalle de compuertas en `tools/SHAREPOINT_SELECTED_GRANT.md`). Lo que hoy bloquea es **una** dependencia administrativa: el consentimiento delegado `Sites.ReadWrite.All` (ver §2).

## 1. Matriz de aplicaciones, carpetas y permisos

| | **LOCAL (CONTROL)** | **DigitalOcean (producción)** |
|---|---|---|
| Perfil de la herramienta | `control-local` | `production-do` |
| Aplicación receptora | `ad98b5cd-71d0-451f-8d70-e8581a4788fa` | `eeca6df8-2436-422f-a1b8-05ff9e8ff653` |
| Sitio / carpeta | sitio de CONTROL / `Hub` | `Comunicaciones` / `CCHH HUB/Busqueda y Seleccion/CVs` (destino fijado; **no** `CCHH HUB`) |
| Site ID | `a129a1d0-a4ee-4438-8de8-9e39e7007764` | `danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a` |
| Drive ID | `b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3` | `b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE` |
| Item ID | `01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ` | `01ULA425KSLK6LZAFFEVHKUSCO32CH64JG` (carpeta `CVs`; padre `Busqueda y Seleccion` = `01ULA425KHEKK4PGEOKFGKQKDWCUDFQEGV`) |
| Modos habilitados hoy | check, dry-run, apply, rollback | check, dry-run y **apply estricto** (§9.7); **rollback rechazado en código** |
| Rol a conceder | `write` (un grant) | `write`, **sin GO todavía** |
| Estado del grant | no creado (el PUT de prueba da 403) | no creado ni verificado |
| Permiso Selected de la app en Entra (`Files.SelectedOperations.Selected`) | el token del Hub ya lo trae según el diagnóstico; confirmar en Entra | **no verificado** |

Cliente administrativo (identidad de la herramienta, **no** es una app del Hub): Microsoft Graph Command Line Tools `14d82eec-204b-4c2f-b7e8-296a70dab67e`. Tenant `f287b953-5428-4a5e-ae17-bd9c4cfaedbe`. Los permisos personales del usuario sobre CONTROL **no** equivalen a permisos sobre Comunicaciones: cada destino se verifica con sus propias lecturas.

Variables del Hub a contrastar en DO (sólo lectura; no se cambian acá): `HC_SHAREPOINT_SITE_ID`, `HC_SHAREPOINT_DRIVE_ID`, `HC_CV_ROOT_ITEM_ID`, `HC_DOCUMENT_WRITE_SCOPE`. `HC_SHAREPOINT_DRIVE_ID` debe ser el drive de arriba y, al desplegar el destino definitivo, `HC_CV_ROOT_ITEM_ID` el item de `CVs` (`01ULA425KSLK6LZAFFEVHKUSCO32CH64JG`); `HC_CATALOG_ITEM_ID` y la lectura de catálogos no cambian (§9.4). La pantalla `/cchh/diagnostico/sharepoint` (owner) muestra el destino actual.

## 2. Consentimientos administrativos pendientes

| # | Qué | Quién | Para qué exactamente | Estado |
|---|---|---|---|---|
| 1 | `Sites.ReadWrite.All` **delegado** para Microsoft Graph Command Line Tools | Brian (admin del tenant) | Obtener un token con escritura de sitios para el único POST de permisos de CONTROL. Hoy sólo hay `Sites.Read.All`. | **Pendiente** (único bloqueo del paso CONTROL) |
| 2 | Contingencia: `Sites.FullControl.All` | Brian | Sólo si el POST da 403 con un usuario que **sí** es Propietario/Control total y `Sites.ReadWrite.All` ya consentido (la doc de Selected lo menciona). No se pide ahora. | No solicitar todavía |
| 3 | Permiso Selected de la app LOCAL en Entra | Admin de Entra | Verificar que exista y esté consentido (si falta, el grant no alcanza). Sólo verificar. | A verificar |
| 4 | Permiso Selected de la app DO (`eeca6df8…`) en Entra | Admin de Entra | Igual que el anterior, para producción. | A verificar (antes de habilitar DO) |
| 5 | Persona con Propietario/Control total de `CVs` | Owner del sitio Comunicaciones | Autorización efectiva para crear el grant productivo. | Sin verificar; GO independiente |

## 3. Diagnóstico (sólo lectura)

Variables (PowerShell; en bash son equivalentes con comillas simples):

```powershell
$TENANT='f287b953-5428-4a5e-ae17-bd9c4cfaedbe'; $ADMIN='14d82eec-204b-4c2f-b7e8-296a70dab67e'; $ME='joconnor@danwor.com'
$LOCAL_APP='ad98b5cd-71d0-451f-8d70-e8581a4788fa'; $CTRL_SITE='a129a1d0-a4ee-4438-8de8-9e39e7007764'
$CTRL_DRIVE='b!0KEpoe6kOESN6J455wB3ZLYmlHEbGsZPlL4_LU8lfDroKl3b1Ti0RYJfK4nkJLM3'; $CTRL_ITEM='01U7IMSJOGXNT3ZNP5NVAKDFLQWY3L6OMQ'
$DO_APP='eeca6df8-2436-422f-a1b8-05ff9e8ff653'; $DO_SITE='danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a'
$DO_DRIVE='b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE'; $DO_ITEM='01ULA425KSLK6LZAFFEVHKUSCO32CH64JG'   # CCHH HUB/Busqueda y Seleccion/CVs; padre: 01ULA425KHEKK4PGEOKFGKQKDWCUDFQEGV
```

**LOCAL** — `--check` y `--dry-run` (cambiar el modo; con `--request-write-scope` el `--dry-run` también ensaya el consentimiento de `Sites.ReadWrite.All` sin escribir nada y mostrará `consent_not_granted`/`AADSTS65001`/`AADSTS90094` mientras Brian no lo conceda):

```powershell
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $LOCAL_APP --site-id $CTRL_SITE --drive-id $CTRL_DRIVE --item-id $CTRL_ITEM --admin-client-id $ADMIN --expect-user $ME --request-write-scope --acknowledge-undetermined --acknowledge-inheritance-break
```

**DigitalOcean (producción)** — `--check` y `--dry-run` son de sólo lectura (el `--check` no pide scope de escritura); el apply productivo está en §9.7:

```powershell
python tools/sharepoint_selected_grant.py --check --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME
```

Lectura del resultado de producción: `target.name = CVs`, `target.parent_item_id = 01ULA425KHEKK4PGEOKFGKQKDWCUDFQEGV` y `web_id` verificado; `evidence` completa en las dos fuentes; `user.upn` = tu cuenta; `would_apply = false` mientras falten los reconocimientos del apply (esperado en `--check`; ver §9.7 para el `--dry-run` completo). `duplicate_state = UNDETERMINED` no es evidencia de ausencia ni de autorización. Un 403 en el destino productivo significa que **ese** usuario no ve Comunicaciones, sin importar CONTROL.

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
   - **Carpeta legible** (convención `Apellido, Nombre [id8]`): la carpeta del candidato se crea como `Hub/Apellido, Nombre [id8]` (el `[id8]` son los 8 primeros caracteres del `profile_id`, **informativo**); la identidad estable es el `driveItem.id`, guardado completo en `folder_item_id` de cada documento. Un segundo documento va a la misma carpeta por id (sin buscar ni crear); si cambia el nombre del candidato la carpeta **no** se renombra; las carpetas UUID históricas se siguen usando tal cual. Revalidar en CONTROL: carpeta legible nueva · segundo documento en la misma carpeta · cambio de nombre y nuevo documento · dos homónimos (dos carpetas) · carpeta UUID previa · reintento ambiguo sin duplicados.
   - **Exposición de datos personales:** el nombre del candidato queda en el nombre de carpeta, o sea en la navegación, las URLs/`web_url`, el historial del navegador, la búsqueda e índices del tenant y la auditoría de SharePoint (y en la metadata local/Spaces vía `web_url`). Los logs técnicos de la aplicación enmascaran esos nombres. Rectificar un nombre en SharePoint es una acción manual.
4. Repetir 2–3 con un segundo usuario con permiso de escritura propio sobre `Hub` (acceso efectivo = intersección app ∩ usuario).

## 6. Criterio de aceptación antes de habilitar DigitalOcean

Todos, con evidencia guardada en el handoff:

- [ ] CONTROL: grant creado (`result.json`), permiso `write` no heredado, prueba de escritura 201 y subida real de CV correcta (§5).
- [ ] Rollback ensayado en CONTROL o decisión explícita de no ensayarlo; se comprobó qué pasa con la herencia de `Hub`.
- [ ] El owner del sitio Comunicaciones acepta la **ruptura de herencia** de `CVs` (la carpeta productiva con los CVs pasaría a permisos propios; los cambios futuros de permisos de la biblioteca ya no se propagarían a ella; borrar el grant no garantiza restaurarla).
- [ ] `--check`/`--dry-run` de `production-do` limpios (§3) y el usuario que aplicará es Propietario/Control total de `CVs` (verificado en ese destino, no inferido de CONTROL).
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

## 8. Condiciones previas a cualquier grant productivo e impacto de romper la herencia de `CCHH HUB`

> **Actualización — destino fijado en `CVs`.** La decisión de alcance (condición 3) está tomada: `production-do` apunta a `CCHH HUB/Busqueda y Seleccion/CVs` (`01ULA425KSLK6LZAFFEVHKUSCO32CH64JG`), no a `CCHH HUB`. Lo de este apartado que habla de `CCHH HUB` describe el **peor caso** (grant sobre toda la carpeta) y se conserva como referencia; para `CVs` aplican las mismas condiciones con el radio acotado de §9.3.

`production-do` admite `--check`, `--dry-run` y `--apply` estricto (§9.7, **sólo `CVs`**); `--rollback` sigue rechazado en código. Ejecutar el grant productivo requiere un **GO explícito** del owner; este apartado lista lo que debe estar verificado **antes** de pedirlo.

### Condiciones (todas)
1. El código de esta rama está desplegado y estable en DigitalOcean, y `--check`/`--dry-run` de `production-do` (usuario `joconnor@danwor.com`) dan lo esperado: `target.name = CCHH HUB`, `webId` verificado, evidencia completa, `would_apply: false` con `apply_disabled_for_profile`.
2. Hay una persona con Propietario/Control total de `CCHH HUB` identificada y **aprobación expresa del owner del sitio Comunicaciones** de la ruptura de herencia (ver abajo).
3. **Decisión de alcance** (RESUELTA: destino `CVs`, ver §9; texto original conservado): hoy `HC_CV_ROOT_ITEM_ID` apunta a la propia carpeta `CCHH HUB`, o sea que las carpetas de candidatos cuelgan de la misma raíz que `Configuracion/Catalogos_CCHH.xlsx`. Un grant `write` sobre `CCHH HUB` alcanza a **todo el árbol** (CVs y el workbook de catálogos). Alternativa recomendada: una subcarpeta dedicada (p. ej. `CCHH HUB/CVs`) como raíz de CVs y grant sólo ahí (menor radio de impacto, no toca el workbook; los `driveItem.id` se conservan al mover). Requiere cambiar `HC_CV_ROOT_ITEM_ID` y mover las carpetas existentes: **no está incluido en esta rama**.
4. **Snapshot previo de permisos** de `CCHH HUB` (`GET /drives/{drive}/items/{item}/permissions` y la lista de usuarios/grupos con acceso desde la UI de SharePoint) y lista de quién debe conservar acceso; ventana de cambio acordada.
5. Verificación **antes y después** con al menos dos usuarios de CCHH que no sean owner: abrir un CV, refrescar el catálogo (lectura de `Catalogos_CCHH.xlsx`), subir un CV.
6. El permiso Selected de la app DO (`Files.SelectedOperations.Selected`) está consentido en Entra (sólo verificar; no se modifica).
7. La revalidación completa en CONTROL (sección 5) pasó con el mismo código que se despliega, incluida la convención de carpetas legibles.
8. Plan de rollback acordado (abajo) y responsable de ejecutarlo.

### Impacto de romper la herencia en `CCHH HUB` (evaluación; **no verificada contra el servicio productivo**)
- **Qué ocurre.** Según la documentación de Selected permissions de Microsoft, un grant sobre una carpeta rompe la herencia en ese recurso: `CCHH HUB` y todo lo que cuelga (CVs, `Configuracion`, el workbook de catálogos) pasan a tener permisos propios.
- **Riesgo 1 — pérdida de acceso inesperada.** Si la copia de los permisos existentes no incluye a todos los principales que hoy acceden por herencia, usuarios de CCHH podrían perder acceso a CVs o al catálogo (el refresh del catálogo daría 403 y los dropdowns quedarían sin actualizarse). Mitigación: snapshot previo y verificación con usuarios no-owner antes/después (condiciones 4 y 5); probar el mismo efecto en CONTROL comparando el listado de permisos antes/después del grant.
- **Riesgo 2 — deriva futura de permisos.** Los cambios que se hagan después a nivel de sitio/biblioteca (altas, bajas, grupos, offboarding) **dejan de propagarse** a `CCHH HUB`: las revisiones de acceso deben mirar esa carpeta explícitamente. Un usuario agregado directamente a la carpeta antes de la ruptura conserva acceso aunque se lo saque del grupo del sitio.
- **Riesgo 3 — alcance del grant.** El rol `write` de la app alcanza a todo el árbol, incluido el workbook de catálogos (fuente de verdad gobernada). El acceso efectivo en delegado es la intersección app ∩ usuario, pero un compromiso de la app expondría más de lo necesario: de ahí la recomendación de la condición 3.
- **Riesgo 4 — reversión incompleta.** Borrar el grant (`DELETE` del `permission_id`) **no garantiza** restaurar la herencia (la documentación no lo establece). Restaurarla es una acción manual del administrador del sitio en SharePoint («eliminar permisos únicos»), que además descarta cualquier otro permiso único de la carpeta: debe hacerse sabiendo el snapshot previo.
- **Riesgo 5 — gobernanza.** Aparecen permisos únicos en la carpeta (informes de acceso, revisiones periódicas, auditoría). No cambian retención, etiquetas de sensibilidad ni DLP.
- **Datos personales.** Las carpetas de candidatos incluyen el nombre en la ruta (ver sección 5): el grant no cambia eso, pero amplía quién (la app, en intersección con el usuario) puede escribir en ellas.

### Rollback del grant productivo (sólo si hiciera falta)
1. `--rollback` productivo (requeriría habilitarlo con GO) o `DELETE` manual del `permission_id` registrado en la auditoría.
2. El administrador del sitio restaura la herencia y reaplica/verifica los permisos del snapshot.
3. Reverificar con los dos usuarios no-owner de la condición 5.

## 9. Destino productivo `CCHH HUB/Busqueda y Seleccion/CVs` — fijado, sólo lectura

**Estado.** El perfil `production-do` apunta a la carpeta `CVs` (ya no a `CCHH HUB`). Admite `--check`, `--dry-run` y `--apply` **estricto** (§9.7); `--rollback` sigue rechazado en código. **El POST productivo NO se ejecutó**: no se concedió ningún permiso ni se tocó Entra ni DigitalOcean. El perfil `control-local` no cambió.

### 9.1 Identificadores verificados
Verificados por el owner con consultas GET independientes (resolución por sharing link y lectura por ruta dentro de la biblioteca productiva, cruzadas; 19 comprobaciones OK; carpeta vacía). Los ids **no** se tomaron del sharing link: salieron de las respuestas de Graph.

| Dato | Valor |
|---|---|
| Ruta | `CCHH HUB/Busqueda y Seleccion/CVs` (sitio Comunicaciones) |
| Site ID | `danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a` |
| Drive ID | `b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE` |
| **`CVs` Item ID** | `01ULA425KSLK6LZAFFEVHKUSCO32CH64JG` |
| Padre (`Busqueda y Seleccion`) Item ID | `01ULA425KHEKK4PGEOKFGKQKDWCUDFQEGV` |
| Destino anterior (ya **no** permitido) | `CCHH HUB` = `01ULA425ONX3YLUO3XTNDI46FMF43PFL4X` |

La herramienta no depende de la ruta: valida los cinco identificadores de la allowlist y **además** lee la metadata real del destino y exige: `id` exacto, `driveId`, `siteId`/`webId` del sitio, nombre `CVs`, que sea carpeta y que su padre (`parentReference.id`) sea `01ULA425KHEKK4PGEOKFGKQKDWCUDFQEGV`.

### 9.2 Comandos definitivos de diagnóstico (sólo lectura; los corre el owner)
Abren un login device code interactivo (https://microsoft.com/devicelogin) con `joconnor@danwor.com`; piden sólo `Sites.Read.All` + `User.Read`. Ningún modo emite POST/PUT/PATCH/DELETE ni escribe archivos.

```powershell
cd C:\Users\Joe\ArquitectoDanwor-cvsp
$TENANT='f287b953-5428-4a5e-ae17-bd9c4cfaedbe'; $ADMIN='14d82eec-204b-4c2f-b7e8-296a70dab67e'; $ME='joconnor@danwor.com'
$DO_APP='eeca6df8-2436-422f-a1b8-05ff9e8ff653'
$DO_SITE='danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a'
$DO_DRIVE='b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE'; $DO_ITEM='01ULA425KSLK6LZAFFEVHKUSCO32CH64JG'

python tools/sharepoint_selected_grant.py --check   --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME
```

Resultado esperado: `ok: true`; `allowlist = production-do`; `target.name = CVs` y `target.parent_item_id = 01ULA425KHEKK4PGEOKFGKQKDWCUDFQEGV`; `user.verified = true`, `user.upn = joconnor@danwor.com`, `matches_expected = true`; `evidence` con las dos fuentes (`driveItem.permissions`, `listItem.permissions`) y, en la primera, `inherited_count` / `direct_count`; `duplicate_state = UNDETERMINED`; `blockers` incluye `apply_disabled_for_profile` y `would_apply: false`. Guardar las dos salidas como evidencia previa al grant.

### 9.3 Controles previos al grant (revisión)
| Control | Qué cubre | Límite | Qué hacer |
|---|---|---|---|
| **Identidad exacta del destino** | Allowlist cerrada (tenant, app, site, drive, item) + metadata real: `id`, `driveId`, `siteId`, `webId`, nombre, carpeta y **padre** (agregado ahora para `production-do`). Una combinación mixta o el destino anterior (`CCHH HUB`) se rechazan **sin red**. | Si `CVs` se borrara y recreara, tendría otro `id` y la herramienta lo rechazaría (correcto: hay que revisar y actualizar la allowlist con un cambio de código). | Nada: fail-closed. |
| **Identidad del usuario** | `--expect-user` compara el UPN real (`GET /me`) con el esperado; en `--apply` sería obligatorio. | En check/dry-run es opcional: por eso los comandos lo incluyen siempre. | Usar siempre `--expect-user $ME`. |
| **Estado de permisos de `CVs`** | `--check` enumera `driveItem.permissions` y `listItem.permissions`: cantidad, coincidencias de la app DO y, nuevo, cuántos permisos se **heredan** (`inherited_count`, tienen `inheritedFrom`) y cuántos son **propios** (`direct_count`). | `driveItem.permissions` expone permisos efectivos **de compartir**, no es una ACL exhaustiva ni distingue por sí solo «hereda» de «ya tiene permisos únicos». | En una carpeta recién creada y vacía se espera `direct_count = 0` (salvo links de compartir). Un `direct_count > 0` se explica antes de seguir. Complementar con «Administrar acceso» de `CVs` en SharePoint y guardar una captura como snapshot. |
| **¿Ya existe un grant Selected?** | Sólo la **presencia** de un grant equivalente es autoritativa (`ALREADY_GRANTED`); `--apply` relee justo antes del POST y aborta ante cualquier coincidencia observable. | La **ausencia no se puede probar** (`UNDETERMINED`, por diseño) y un grant **heredado de un ancestro** (`CCHH HUB` o `Busqueda y Seleccion`) cubriría `CVs` sin aparecer en la enumeración. | El owner reconoce el estado (`--acknowledge-undetermined`) sabiendo esto. Indicio, no prueba: si el 403 original de producción fue contra `CCHH HUB`, entonces no había grant efectivo sobre `CCHH HUB` ni ancestros en ese momento. La verificación real es la prueba funcional posterior al grant (§9.5). |
| **Impacto sobre herencia** | El grant se hace sobre `CVs`: sólo `CVs` y su contenido pasan a tener permisos propios; `CCHH HUB`, `Configuracion` y el workbook quedan heredando de la biblioteca. Hoy `CVs` está **vacía**: no hay contenido cuyo acceso se pueda perder. | Quién ve `CVs` después depende de sus permisos propios; borrar el grant no garantiza restaurar la herencia (manual, §8 riesgo 4). | Snapshot previo; verificación antes/después con dos usuarios de CCHH que no sean owner (abrir y subir un CV) y una persona sin rol CCHH (no debe abrir `CVs`). Hacerlo **antes** de la primera subida real. |
| **Habilitación** | `--apply` habilitado sólo para `production-do → CVs` con compuertas estrictas (§9.7); `--rollback` rechazado antes de autenticar. | El rollback productivo no existe: revertir es manual. | Ejecutar el apply requiere GO explícito del owner. |

### 9.4 Configuración cuando se despliegue
- **`HC_CV_ROOT_ITEM_ID = 01ULA425KSLK6LZAFFEVHKUSCO32CH64JG`** en DigitalOcean (cambia de `CCHH HUB` a `CVs`). Se aplica **después** de verificar el grant y **antes** de la primera subida; cambiar la variable reinicia la app. No se modificó nada en DigitalOcean con este cambio.
- **Sin cambios:** `HC_SHAREPOINT_SITE_ID`, `HC_SHAREPOINT_DRIVE_ID`, `HC_DOCUMENT_WRITE_SCOPE` y **`HC_CATALOG_ITEM_ID`**. La lectura de `Catalogos_CCHH.xlsx` (`CCHH HUB/Configuracion/…`) sigue usando drive + `HC_CATALOG_ITEM_ID` con el token delegado de lectura; está fuera de `CVs` y la app no escribe ahí.
- Las carpetas de candidatos (`Apellido, Nombre [id8]`) se crearán bajo `CVs`. Como `CVs` está vacía no hay migración; el Hub sólo reutiliza una carpeta si su padre es el `HC_CV_ROOT_ITEM_ID` vigente.
- La prueba de escritura del diagnóstico sigue fijada a CONTROL + app LOCAL: ni `CCHH HUB` ni `CVs` de producción están permitidos (tests).

### 9.5 Secuencia hasta poder subir CVs en producción (cada paso con su GO)
1. Desplegar el código de la rama (sin permisos ni variables nuevas).
2. `--check` y `--dry-run` de §9.2; revisar la tabla de §9.3.
3. Aprobación del owner del sitio + snapshot de permisos de `CVs`.
4. (Hecho en código, sin ejecutar) `apply` habilitado para `production-do` (sólo `CVs`): §9.7.
5. `--apply` interactivo (un POST) con GO explícito, y comparación de permisos antes/después.
6. `HC_CV_ROOT_ITEM_ID = 01ULA425KSLK6LZAFFEVHKUSCO32CH64JG` en DO y prueba funcional con un candidato ficticio y un PDF inocuo (carpeta `Apellido, Nombre [id8]`, un archivo, `folder_item_id`, `web_url` que abre).

### 9.6 Qué sigue sin cambiar
`--rollback` de `production-do` sigue rechazado en código; `control-local` intacto; ningún permiso concedido ni configuración de producción modificada.

### 9.7 Apply productivo (`production-do → CVs`) — preparado, NO ejecutado
**Qué cambió en código.** `production-do` habilita `apply` con la política `strict_apply` (sólo ese perfil; `control-local` conserva su flujo). Con el apply se concede **un** grant `write` a la app DO (`eeca6df8-…`) sobre `CVs` (`01ULA425KSLK6LZAFFEVHKUSCO32CH64JG`); el POST va a `…/drives/{drive}/items/{CVs}/permissions` y a nada más.

**Compuertas, en orden.**
1. Allowlist cerrada: tenant, app, site, drive, item (+ padre real verificado). Cualquier otro destino —`CCHH HUB`, `Busqueda y Seleccion`, el workbook, CONTROL— se rechaza **antes de cualquier llamada**.
2. Antes de autenticar: `--confirm-apply "CONCEDER GRANT WRITE"`, `--acknowledge-undetermined`, `--acknowledge-inheritance-break`, **`--acknowledge-existing-unique-permissions`** (el operador declara que `CVs` ya tiene permisos únicos; Graph no lo expone), `--admin-client-id` = Graph Command Line Tools (`14d82eec-…`), `--expect-user` = `joconnor@danwor.com` (otro usuario u otra app administrativa se rechazan), terminal interactiva real y ausencia de un candado previo.
3. Token delegado con `Sites.ReadWrite.All` del tenant correcto y de la app administrativa; identidad verificada con `GET /me`.
4. Lectura del destino (ids, padre, nombre, carpeta), de los permisos (se registra el estado previo: cantidad, heredados/propios, id/roles/tipo de cada entrada, **sin nombres ni correos**) y del grant de la app (la ausencia sigue sin poder probarse: `UNDETERMINED`).
5. Confirmación interactiva: se muestra operación, usuario, carpeta, cuerpo, estado de permisos y riesgos; hay que escribir exactamente `CONCEDER GRANT WRITE`.
6. **Relectura inmediata antes del POST** de destino, identidad y permisos: si cambió el destino, el usuario, el conjunto de permisos, apareció un grant de la app o la lectura es incompleta → **se aborta sin escribir**.
7. Candado de un solo uso (`production-do.apply.lock` en el directorio de auditoría) + auditoría de **intención** (antes del POST) y de **resultado** (HTTP, request-id, `permission_id`).
8. **Un único POST, sin reintentos.** No se altera, recrea ni borra ningún permiso existente; no hay DELETE en este perfil.

**Evidencia previa recomendada (sólo lectura).** El `--dry-run` con todo lo anterior debe dar `ok: true`, `blockers: []`, `would_apply: true` y `permission_state_before`:

```powershell
python tools/sharepoint_selected_grant.py --dry-run --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME --request-write-scope --acknowledge-undetermined --acknowledge-inheritance-break --acknowledge-existing-unique-permissions
```

**Comando `--apply` (terminal interactiva real; sólo con GO explícito).**

```powershell
python tools/sharepoint_selected_grant.py --apply --tenant-id $TENANT --client-id $DO_APP --site-id $DO_SITE --drive-id $DO_DRIVE --item-id $DO_ITEM --admin-client-id $ADMIN --expect-user $ME --acknowledge-undetermined --acknowledge-inheritance-break --acknowledge-existing-unique-permissions --confirm-apply "CONCEDER GRANT WRITE"
```
(Las variables son las de §9.2.) La herramienta se niega sin TTY: no puede correr desde un agente. Guardar la salida y `reports/sharepoint_selected_grant/production-do-*-intent.json` / `-result.json` (traen `permission_id`, estado HTTP y request-id).

**Después de un 201.** Queda el candado en estado `created` (un segundo `--apply` se rechaza). Verificar: relectura de permisos (el `--check` debe mostrar la coincidencia de la app), «Administrar acceso» de `CVs` sin cambios salvo el grant, los dos usuarios no-owner y la persona sin rol CCHH (§9.3), y recién entonces fijar `HC_CV_ROOT_ITEM_ID` y probar un candidato ficticio (§9.5).

**Si falla.**
- **Respuesta definitiva 4xx** (401/403/404/409/429…): el grant **no** se creó; el candado se libera. Causas habituales de 401/403: el token no alcanza para administrar grants Selected (la doc habla de `Sites.FullControl.All` o Selected + Owner/FullControl) o el usuario no es Propietario/Control total de `CVs`. No se reintenta solo: un nuevo intento es un `--apply` completo, con su propio GO, después de entender la causa.
- **Resultado ambiguo** (timeout/corte, 5xx, sin respuesta; código de salida 4): **no reintentar**. El POST pudo haberse ejecutado. La herramienta relee y deja `evidence_after`; el **candado queda puesto** y bloquea cualquier `--apply` nuevo. Revisión manual obligatoria: (1) `--check` y «Administrar acceso» de `CVs` para ver si existe el grant de la app DO; (2) si existe, tratarlo como creado (anotar su `permission_id`); (3) si no existe, recién entonces el owner decide un nuevo intento. **Sólo después de esa revisión** se borra el candado a mano.
- **Respuesta 201 que no coincide** con lo pedido (`post_response_mismatch`): el candado queda; revisar el `permission_id` registrado antes de cualquier acción.
- **Rollback.** No hay rollback productivo en la herramienta. Revertir es manual: un administrador borra el permiso por su `permission_id` (Graph `DELETE …/permissions/{id}` o desde SharePoint) y restaura la herencia **sabiendo el snapshot previo** (la auditoría de intención guarda el estado previo, sin identidades; el snapshot completo de usuarios/grupos sale de «Administrar acceso»).

**Riesgos residuales.**
- La ausencia previa de un grant (o uno heredado de `CCHH HUB`/`Busqueda y Seleccion`) **no es demostrable** (`UNDETERMINED`): se mitiga con el reconocimiento explícito, la relectura y la verificación funcional posterior.
- «La carpeta ya tiene permisos únicos» es una **afirmación del operador** (Graph no lo expone); la evidencia automática es sólo indicativa y queda registrada con su carácter.
- No se prueba por adelantado que `Sites.ReadWrite.All` + Control total alcancen para administrar el grant: lo decide la respuesta real del POST.
- Un grant mal ubicado no se deshace desde la herramienta; el rollback es manual.
- El candado vive en el equipo del operador: otro equipo no lo ve (no ejecutar el apply desde dos máquinas).
