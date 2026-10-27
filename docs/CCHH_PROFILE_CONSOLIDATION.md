# CCHH · Consolidar perfiles duplicados

Dos perfiles del Banco de CVs que son la **misma persona** pasan a ser **una única identidad activa**, conservando ambos CVs, su procedencia y las referencias existentes. Operación administrativa, iniciada por una persona sobre exactamente dos perfiles; **no** hay detección ni fusión automática. Implementación: `webapp/services/profile_consolidation_service.py` (+ `profile_repository`, `profile_document_repository`, rutas en `webapp/routers/human_capital.py`, `templates/human_capital/profile_consolidation.html`).

## Contrato (decisiones del owner)

* **Permiso exclusivo `APP_OWNER`** (primera versión): el botón solo se dibuja para el dueño y **cada ruta lo exige en el servidor** (sesión CCHH + `access.is_owner` + mismo origen).
* **Bloqueo estricto**: si el perfil **duplicado** tiene candidaturas (búsquedas), entrevistas, onboarding —o no se puede verificar— no se consolida; se listan las relaciones y nada se migra por supuestos. Las relaciones del **principal** se conservan tal cual.
* **No se mueve ningún archivo ni carpeta de SharePoint**: los documentos conservan `site_id`/`drive_id`/`item_id`/`web_url`/`folder_item_id` (y cualquier campo de procedencia, p. ej. de la importación histórica). Solo cambia a qué candidato pertenecen. La carpeta física `Apellido, Nombre [id8]` del duplicado queda donde está.
* **Una consolidación parcial nunca aparece como exitosa ni deja a los dos perfiles operables**: mientras la operación dura, ambos perfiles están **bloqueados y ocultos** (no se listan, abren, editan, dan de baja ni reciben documentos, candidaturas o entrevistas). Si un paso falla la operación queda **PENDIENTE** y es **recuperable** (reanudar / revertir).

## Flujo

1. Seleccionar **exactamente dos** perfiles en el listado → «Consolidar perfiles» (POST con solo los ids).
2. **Plan** (solo lectura): principal/duplicado (con «invertir»), comparación de atributos, **conflictos con elección explícita por campo** (los datos que el principal no tiene se completan desde el duplicado y se muestran), documentos de ambos, relaciones, resumen de lo que se trasladará y bloqueos.
3. **Confirmación explícita** (casilla + digest del plan: si algo cambió desde el resumen → `STALE_PLAN`, hay que revisar y confirmar de nuevo).
4. Ejecución: intención durable → bloqueo de ambos → reasignación de documentos → baja lógica del duplicado (`consolidated_into`) → el principal recibe los campos resueltos (`consolidated_from`) y se **desbloquea** (único instante en que la consolidación «aparece») → resultado durable.

El `profile_id` del principal se conserva; no se crea ningún perfil nuevo; no se elimina información (el duplicado es una baja lógica).

## Recuperación y auditoría durable

* `human-capital/profile-consolidations/<operación>/intent.json` (write-once, **fail-closed** en Spaces como la exportación de CVs): actor, plan, resoluciones, **imagen previa completa de ambos perfiles y de cada documento**. `result.json` (COMPLETED / ABORTED) al cerrar y `events/` con reanudaciones, fallos y reversiones.
* **Reanudar** (`/cchh/consolidaciones` → solo el dueño): idempotente, mismo `operation_id`, ejecuta lo que falta (el estado se deduce de los datos). **Revertir**: restaura documentos y perfiles a su imagen previa registrada. Una consolidación COMPLETADA no se revierte desde la UI (la auditoría conserva todo lo necesario para hacerlo a mano).
* **Idempotencia**: repetir una consolidación ya completada → `ALREADY_CONSOLIDATED` (no-op). Dos confirmaciones simultáneas del mismo par → una completa y la otra devuelve `ALREADY_CONSOLIDATED`; dos consolidaciones que se solapan no se intercalan (lock de proceso + bloqueo por perfil).
* **Reasignación de documentos**: `profile_document_repository.reassign_for_consolidation` es una excepción **acotada** a la inmutabilidad: solo cambia `profile_id` y agrega la marca `consolidation`, exige la autorización de la intención auditada (`document_id → {from, to}`), verifica el origen, es idempotente y usa el mismo contrato de compensación que el resto (push a Spaces; si no se confirma, el documento vuelve a como estaba). No existe ninguna API genérica de edición de metadatos documentales (hay un test que lo afirma).

## Visibilidad de un perfil bloqueado

`human_capital_service.list_profiles`/`get_profile` lo ocultan; `profile_repository.update` lo rechaza (`ProfileLockedError`, que los routers ya traducen a «cambió, recargá»); `profile_document_repository` rechaza altas y bajas; altas de candidatura/entrevista/contacto lo tratan como no disponible; la importación histórica no lo acepta como destino de vínculo.

## Limitaciones y riesgos conocidos

* No hay transacción entre archivos: un corte entre pasos deja una operación pendiente (visible en el listado para el dueño), nunca una consolidación «a medias» presentada como completa.
* Réplica única (como el resto de CCHH): los locks son de proceso.
* Los ledgers de importación histórica que referencian al duplicado fallan de forma segura (`LINK_TARGET_MISSING`); no se reescriben.
* No se migran candidaturas/entrevistas/onboarding del duplicado: hay que resolverlas antes (esa decisión es del owner).
* La consolidación no revierte desde la UI una vez completada.
