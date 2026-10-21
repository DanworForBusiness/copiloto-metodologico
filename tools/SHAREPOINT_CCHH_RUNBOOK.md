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
$TENANT='f287b953-5428-4a5e-ae17-bd9c4cfaedbe'; $ADMIN='14d82eec-204b-4c2f-b7e8-296a70dab67e'; $ME='joconnor@danwor.com'
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
   - **Carpeta legible** (convención `Apellido, Nombre [id8]`): la carpeta del candidato se crea como `Hub/Apellido, Nombre [id8]` (el `[id8]` son los 8 primeros caracteres del `profile_id`, **informativo**); la identidad estable es el `driveItem.id`, guardado completo en `folder_item_id` de cada documento. Un segundo documento va a la misma carpeta por id (sin buscar ni crear); si cambia el nombre del candidato la carpeta **no** se renombra; las carpetas UUID históricas se siguen usando tal cual. Revalidar en CONTROL: carpeta legible nueva · segundo documento en la misma carpeta · cambio de nombre y nuevo documento · dos homónimos (dos carpetas) · carpeta UUID previa · reintento ambiguo sin duplicados.
   - **Exposición de datos personales:** el nombre del candidato queda en el nombre de carpeta, o sea en la navegación, las URLs/`web_url`, el historial del navegador, la búsqueda e índices del tenant y la auditoría de SharePoint (y en la metadata local/Spaces vía `web_url`). Los logs técnicos de la aplicación enmascaran esos nombres. Rectificar un nombre en SharePoint es una acción manual.
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

## 8. Condiciones previas a cualquier grant productivo e impacto de romper la herencia de `CCHH HUB`

`production-do` queda en `--check` y `--dry-run`: `--apply` y `--rollback` siguen rechazados en código. Habilitar el grant productivo requiere un **GO independiente** y un cambio de código revisado (`modes` de `production-do`); este apartado lista lo que debe estar verificado **antes** de pedirlo.

### Condiciones (todas)
1. El código de esta rama está desplegado y estable en DigitalOcean, y `--check`/`--dry-run` de `production-do` (usuario `joconnor@danwor.com`) dan lo esperado: `target.name = CCHH HUB`, `webId` verificado, evidencia completa, `would_apply: false` con `apply_disabled_for_profile`.
2. Hay una persona con Propietario/Control total de `CCHH HUB` identificada y **aprobación expresa del owner del sitio Comunicaciones** de la ruptura de herencia (ver abajo).
3. **Decisión de alcance** (pendiente del owner): hoy `HC_CV_ROOT_ITEM_ID` apunta a la propia carpeta `CCHH HUB`, o sea que las carpetas de candidatos cuelgan de la misma raíz que `Configuracion/Catalogos_CCHH.xlsx`. Un grant `write` sobre `CCHH HUB` alcanza a **todo el árbol** (CVs y el workbook de catálogos). Alternativa recomendada: una subcarpeta dedicada (p. ej. `CCHH HUB/CVs`) como raíz de CVs y grant sólo ahí (menor radio de impacto, no toca el workbook; los `driveItem.id` se conservan al mover). Requiere cambiar `HC_CV_ROOT_ITEM_ID` y mover las carpetas existentes: **no está incluido en esta rama**.
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

## 9. Estrategia `CCHH HUB/CVs` (carpeta productiva dedicada) — preparación, sin ejecutar

**Objetivo.** Que el grant productivo y las escrituras de la app caigan sólo en una subcarpeta `CVs` y no en toda la carpeta `CCHH HUB` (que también aloja `Configuracion/Catalogos_CCHH.xlsx`). Es la alternativa recomendada de la condición 3 del §8. **Estado: no se verificó contra SharePoint productivo** (no hubo autenticación administrativa en esta sesión); todo lo de abajo es análisis de código y diseño, más los comandos para confirmarlo.

### 9.1 Identificadores (qué está verificado y qué no)
| Dato | Valor | Verificación |
|---|---|---|
| `site_id` | `danwor.sharepoint.com,65c64790-f933-46b9-823f-8e4ad57e69b1,b9b7da7d-8f67-46a9-be27-5f176394718a` | Perfil `production-do` + §1; formato y coherencia validados por el código/tests de la herramienta (170). **No** releído en vivo. |
| `drive_id` | `b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE` | Ídem. |
| `item_id` de `CCHH HUB` | `01ULA425ONX3YLUO3XTNDI46FMF43PFL4X` | Ídem. |
| `item_id` de `CCHH HUB/CVs` | **desconocido** | Requiere el GET de §9.3. Si `CVs` no existe, hay que crearla (§9.2) y volver a leerlo. |

### 9.2 Si `CVs` no existe
No la crea el Hub ni esta herramienta. La crea una persona con permiso de edición sobre `CCHH HUB`, desde SharePoint (**carpeta vacía**, nombre exacto `CVs`), y **se hace antes de cualquier grant y antes de que se suba el primer CV** (con la carpeta vacía el costo de cualquier cambio de permisos es mínimo). Crear la carpeta es una escritura productiva: requiere el GO que corresponda.

### 9.3 Diagnóstico de sólo lectura (lo corre el owner; ningún comando escribe)
Se necesita un token Graph de lectura (`Sites.Read.All`) del propio owner: en el Hub productivo ya logueado, consola del navegador, `await window.dwGraphToken()`. No se pega en chats ni se guarda; usarlo desde una variable de sesión.

```powershell
$TOKEN = Read-Host 'Graph token (lectura)' -AsSecureString | ConvertFrom-SecureString -AsPlainText   # PowerShell 7+
$H = @{ Authorization = "Bearer $TOKEN" }
$DO_DRIVE = 'b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE'; $DO_ITEM = '01ULA425ONX3YLUO3XTNDI46FMF43PFL4X'

# 1) ¿Existe CCHH HUB/CVs? (200 = existe, 404 = no existe -> §9.2)
Invoke-RestMethod -Headers $H -Uri "https://graph.microsoft.com/v1.0/drives/$DO_DRIVE/root:/CCHH%20HUB/CVs?`$select=id,name,folder,parentReference,webUrl"

# 2) Contenido directo de CCHH HUB (sólo nombre/id/tipo; si hay carpetas de candidatos ya subidas aparecen acá)
(Invoke-RestMethod -Headers $H -Uri "https://graph.microsoft.com/v1.0/drives/$DO_DRIVE/items/$DO_ITEM/children?`$select=id,name,folder&`$top=200").value | Select-Object name, id, @{n='isFolder';e={[bool]$_.folder}}
```
Alternativa con la herramienta existente (sólo lectura): `HC_SHAREPOINT_DRIVE_ID` seteado a `$DO_DRIVE` y `python tools/human_capital/resolve_catalog_workbook_ids.py --token <token> --file-path "CCHH HUB/CVs"`. **Ojo:** imprime la etiqueta `HC_CATALOG_ITEM_ID`, pero el valor es el id de `CVs`: no debe pegarse como `HC_CATALOG_ITEM_ID`.

Qué hay que registrar: `id` y `parentReference.id` de `CVs` (el padre debe ser `01ULA425ONX3YLUO3XTNDI46FMF43PFL4X`), `folder` presente, y si el punto 2 muestra carpetas de candidatos (UUID o `Apellido, Nombre [id8]`) ya existentes bajo `CCHH HUB`.

### 9.4 Alcance de herencia y permisos (análisis; confirmar con 9.3 y el `--check`)
- **Qué cambia respecto de §8.** El grant se hace sobre `CVs`, no sobre `CCHH HUB`: sólo `CVs` y su contenido pasan a tener permisos propios. `CCHH HUB`, `Configuracion` y `Catalogos_CCHH.xlsx` siguen heredando de la biblioteca como hoy. Desaparecen los riesgos 1 y 3 del §8 para el workbook; el riesgo 4 (revertir la herencia es manual) queda acotado a `CVs`.
- **Catálogos intactos.** El workbook se identifica por `HC_SHAREPOINT_DRIVE_ID` + `HC_CATALOG_ITEM_ID` (ids estables, no ruta), se **lee** con el token delegado de lectura y la app no escribe ahí: ni el grant ni el cambio de `HC_CV_ROOT_ITEM_ID` lo tocan. `HC_CATALOG_ITEM_ID` no cambia.
- **Quién ve `CVs` después del grant.** Pasa a depender de los permisos propios de la carpeta (la doc de Selected no garantiza que se copien los de `CCHH HUB`). Con la carpeta vacía y recién creada, se compara antes/después la lista de principales y se verifica con dos usuarios de CCHH no-owner (abrir un CV, subir un CV) y una persona sin rol CCHH (no debe abrir `CVs`).
- **Acceso efectivo** = app ∩ usuario delegado: el rol `write` de la app sólo alcanza el árbol de `CVs`.

### 9.5 Cómo se actualizaría la configuración (cuando exista el `item_id` real y haya GO)
1. **Variable del Hub en DigitalOcean:** `HC_CV_ROOT_ITEM_ID=<item_id de CVs>`. El resto (`HC_SHAREPOINT_SITE_ID`, `HC_SHAREPOINT_DRIVE_ID`, `HC_CATALOG_ITEM_ID`, `HC_DOCUMENT_WRITE_SCOPE`) no cambia. Se aplica **después** de verificar el grant y **antes** de la primera subida; cambiar la variable reinicia la app.
2. **Allowlist `production-do`** (`tools/sharepoint_selected_grant.py`, cambio de código revisado, aparte): reemplazar el destino por `CVs` (`item_id`, `folder_name: "CVs"`) en lugar de sumar un perfil: así la herramienta no puede apuntar nunca a `CCHH HUB`. Se mantienen `modes = READ_ONLY_MODES` (`--apply`/`--rollback` siguen deshabilitados hasta un GO independiente), el aviso de herencia ajustado a `CVs`, y los tests se actualizan (ids nuevos + un test que impide que el item de `CCHH HUB` figure como destino con apply habilitado).
3. `--check` y `--dry-run` con el nuevo destino: `target.name = CVs`, `would_apply: false`.
4. Habilitar `apply` para ese único destino = otro cambio de código con GO; recién ahí el grant.
5. **Carpetas ya existentes bajo `CCHH HUB`** (el punto 2 de §9.3 las muestra; en producción debería no haber ninguna porque la subida siempre dio 403): el Hub sólo reutiliza una carpeta si su padre es el `HC_CV_ROOT_ITEM_ID` vigente. Si existieran, hay que **moverlas** a `CVs` desde SharePoint (el `driveItem.id` se conserva y siguen válidas); si no se mueven, la próxima subida del candidato crea una carpeta nueva en `CVs` y la anterior queda huérfana (sus `web_url` siguen abriendo). El Hub no mueve ni borra nada.

### 9.6 Qué sigue sin cambiar
`--apply` y `--rollback` de `production-do` siguen rechazados en código; no se concede ningún permiso ni se modifica Entra ni variables de producción con este apartado.
