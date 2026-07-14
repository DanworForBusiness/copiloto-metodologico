---
module: "A06"
title: "SAP Analytics Cloud — Gobierno de Contenido"
nav_label: "A06 · SAP Analytics Cloud"
tags: ["module:A06", "domain:sac", "domain:governance", "domain:lifecycle"]
key_topics:
  - id: vistas
    label: System vs Workspaces
    heading: "Vista System vs Vista Workspaces"
  - id: creacion
    label: Creación y Promoción
    heading: "Creación y Promoción de Contenido"
  - id: comparticion
    label: Compartición
    heading: "Compartición y Herencia de Permisos"
  - id: transporte
    label: Transporte e Identidad
    heading: "Transporte e Identidad Técnica de Objetos"
  - id: troubleshooting
    label: Troubleshooting Transporte
    heading: "Troubleshooting de Transporte"
  - id: arbol
    label: Árbol de Visibilidad
    heading: "Árbol de Decisión: Visibilidad en Workspace"
  - id: migracion
    label: Migración a Workspace
    heading: "Migración: Team Folder hacia Workspace"
  - id: checklist
    label: Checklist DEV/QA/PRD
    heading: "Checklist DEV / QA / PRD"
  - id: antipatrones
    label: Antipatrones
    heading: "Antipatrones"
scope: |
  Apéndice técnico-operativo de gobierno y ciclo de vida de CONTENIDO de SAP
  Analytics Cloud (SAC): organización del repositorio de archivos (vista System,
  workspaces, team folders, carpeta Public, carpetas privadas de usuario),
  vista System vs vista Workspaces, compartición y herencia de permisos,
  transporte de contenido e identidad técnica de objetos (Object ID / Folder ID),
  Deleted Files, alineación de workspaces entre tenants, migración de team folders
  hacia workspaces y checklist DEV/QA/PRD. Es la profundidad operativa del contenido
  de organización SAC que el módulo 05 introduce a nivel decisión. No cubre elección
  de herramienta de reporting ni modos de conexión (módulo 05), seguridad DAC de
  Datasphere (módulo 02), ni la topología de espacios/transportes Datasphere y la
  dirección del landscape DEV→QA→PRD (módulo 01), que SAC hereda como supuesto.
guardrails:
  - "El límite de gobierno de contenido SAC es el workspace, análogo al espacio en Datasphere: no tratar team folders ni la carpeta Public como su reemplazo."
  - "El transporte de SAC se resuelve por identidad técnica del objeto (Object ID), no por nombre ni ruta: no asumir que mover en origen mueve en destino, ni que nombres iguales son el mismo objeto."
  - "La visibilidad de contenido depende del acceso y la compartición, no de la profundidad de carpetas ni de la pertenencia al workspace: no aplanar jerarquías para resolver permisos."
  - "Diferenciar siempre comportamiento documentado por SAP, buena práctica Danwor e inferencia; no presentar una hipótesis histórica de implementación como hecho documentado."
quick_action_guidance:
  "Guía operativa del módulo":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - APÉNDICE A06 (SAC):
      - La guía debe centrarse en gobierno y ciclo de vida de CONTENIDO SAC: repositorio de archivos, workspaces vs team folders vs Public, vista System vs Workspaces, compartición/herencia, transporte por Object ID, migración a workspace.
      - No es sobre elección de herramienta de reporting ni conexión (eso es módulo 05), ni sobre DAC de Datasphere (módulo 02).
      - La decisión dominante es: usar el workspace como ubicación autoritativa de contenido gobernado; team folders y Public solo para compartición amplia o simple deliberada.
      - Siempre diferenciar comportamiento SAP documentado, práctica Danwor e inferencia.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA A06 - GUÍA OPERATIVA:
      Formulá la decisión dominante como: gobernar el contenido SAC ubicándolo en el workspace correcto, con compartición explícita, herencia validada e identidad técnica preservada a lo largo del transporte DEV→QA→PRD.

      Incluí obligatoriamente:
      - Diferencia entre vista System (admin) y vista Workspaces (colaboración filtrada por acceso).
      - Cuándo workspace vs team folder vs Public.
      - Contenido privado por defecto en el workspace y su promoción a la jerarquía gobernada.
      - Compartición de carpetas raíz a los teams y herencia hacia hijos existentes.
      - Transporte por Object ID: actualización en la ubicación existente del destino; mover (no copiar) para corregir linaje.
      - Deleted Files y colisiones de Folder ID como fuentes de incidentes.
  "Checklist de implementación":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - APÉNDICE A06 (SAC):
      - El checklist valida readiness de gobierno de contenido SAC y de un transporte entre tenants.
      - Cubre estructura de workspace alineada entre tenants, membresía, compartición, herencia, identidad de objetos/carpetas, Deleted Files, dependencias, propiedad y permisos post-transporte.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA A06 - CHECKLIST:
      Construí un checklist operativo de gobierno y transporte de contenido SAC (DEV/QA/PRD).
      Validá: estructura de workspace idéntica entre tenants, membresía de teams, administradores del workspace, compartición de carpetas raíz, herencia aplicada a hijos existentes, Object/Folder ID alineados origen-destino, dependencias (modelos, datasets, data actions, connections), Deleted Files sin conflictos, propiedad, y revalidación de permisos y compartición después del transporte.
  "Errores frecuentes":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - APÉNDICE A06 (SAC):
      - Enfocá en antipatrones de gobierno de contenido SAC y de transporte.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA A06 - ERRORES FRECUENTES:
      Priorizá: tratar System/Common/<Team> como si fuera el workspace; llamar "Public" a todo lo que cuelga de System/Common; crear contenido productivo en carpetas privadas de usuario; aplanar jerarquías para resolver visibilidad; copiar un reporte cuando se busca conservar su linaje de transporte; asumir nombres iguales = mismo Object/Folder ID; asumir que mover en origen mueve en destino; asumir que pertenecer al workspace da acceso a todo; borrar en destino sin revisar dependencias ni Deleted Files.
      Para cada error, explicá causa raíz, impacto y corrección Danwor.
  "Ejemplos prácticos":
    context: |
      CONTEXTO EDITORIAL DANWOR PARA ESTA ACCIÓN - APÉNDICE A06 (SAC):
      - Ejemplos de organización y ciclo de vida de contenido SAC.
    prompt: |
      INSTRUCCIÓN ESPECÍFICA PARA A06 - EJEMPLOS PRÁCTICOS:
      Construí ejemplos concretos: crear la jerarquía gobernada de un workspace de Finanzas desde la vista System y compartirla; promover una story privada a la jerarquía gobernada; corregir un linaje de transporte moviendo el objeto con Object ID coincidente; migrar un team folder legacy a un workspace. Para cada uno indicá pasos, decisión Danwor y riesgo si se hace mal.
out_of_scope_redirect:
  - keywords: ["herramienta reporting", "SAC vs Power BI", "modo conexión", "DirectQuery",
               "import mode", "live data connection", "schema user", "ODBC", "OData", "performance reporting"]
    module: "05"
    label: "Reporting y Consumo"
    message: "La elección de herramienta y los modos de conexión corresponden al módulo 05 · Reporting y Consumo. Este apéndice cubre el gobierno del CONTENIDO SAC (workspaces, carpetas, transporte)."
  - keywords: ["DAC", "data access control", "roles datasphere", "seguridad datos datasphere"]
    module: "02"
    label: "Seguridad y Gobernanza"
    message: "La seguridad de datos (DAC) de Datasphere corresponde al módulo 02 · Seguridad y Gobernanza."
  - keywords: ["espacio datasphere", "topología espacios", "DEV QA PRD datasphere", "transporte datasphere", "cross-sharing", "tenancy"]
    module: "01"
    label: "Arquitectura y Conectividad"
    message: "La topología de espacios, la dirección del landscape y el transporte de Datasphere corresponden al módulo 01 · Arquitectura y Conectividad. SAC hereda esa misma dirección DEV→QA→PRD."
---
# A06 SAP Analytics Cloud — Gobierno de Contenido

> Este apéndice profundiza la sección **Organización de Contenido en SAC** del módulo 05. Ahí se define la arquitectura y la decisión canónica (el workspace como límite de gobierno, análogo al espacio en Datasphere); acá se cubre la operación: vistas, compartición, transporte por identidad técnica, migración y troubleshooting. La dirección del landscape **DEV → QA → PRD** se hereda del módulo 01 (Espacios & Transportes); no se redefine.
>
> **Convención de fuentes:** *SAP* = comportamiento documentado por SAP (Help / KBA, referenciado en el apéndice A07 · Biblioteca de Referencias). *Danwor* = buena práctica del framework. *Inferencia* = hipótesis que requiere evidencia (logs, historial de transporte, auditoría) antes de tratarse como hecho.

## {dw-black}Vista System vs {dw-gold}Vista Workspaces
{{section_meta: keywords="vista system sac, files system, files workspaces, system vs workspaces, vista administrativa sac, vista workspace usuario, repositorio archivos sac, file repository sac, administrador workspace, ver contenido no compartido, membresia workspace vs acceso, por que no veo la carpeta, workspace aparece pero no veo reportes"}}

SAC organiza el contenido en un **repositorio de archivos** con dos vistas sobre el mismo repositorio. La distinción es la base de todo el gobierno de contenido y del troubleshooting de visibilidad.

| Vista | `Files → System → <Workspace>` | `Files → Workspaces → <Workspace>` |
|---|---|---|
| **Naturaleza** | Interfaz **administrativa y de gobierno** del repositorio | Vista de **colaboración filtrada por acceso** |
| **Qué muestra** | El contenido del workspace de forma integral, sujeto a los permisos del administrador — puede exponer contenido que no aparece en la vista de usuario | Solo lo que fue compartido con el usuario o con alguno de sus equipos |
| **Para quién** | Administradores del workspace / responsables de contenido | Cualquier miembro del workspace |
| **Permite** | Inspeccionar todo el WS, organizar carpetas, administrar la raíz gobernada, mover contenido in/out, encontrar contenido no compartido, ver `System/<Workspace>/Users/<User_ID>` cuando hay autorización | Consumir y colaborar sobre el contenido al que se tiene acceso |

**No** describir la vista System como "otro acceso directo" o una ruta alternativa: es la representación administrativa del repositorio, con visibilidad que la vista de usuario no tiene *(SAP)*.

{{callout: title="Membresía ≠ acceso al objeto" tone="warn" icon="🔸"}}
Un usuario puede ver el workspace listado en `Files → Workspaces` y **no** ver una carpeta o un reporte determinado si ese contenido no fue compartido con él ni con alguno de sus equipos. La **membresía del workspace** y el **acceso a un archivo/carpeta** son conceptos relacionados pero distintos. Que un administrador vea un objeto en la vista System **no** es evidencia de que los miembros comunes lo vean *(SAP)*.
{{/callout}}

## {dw-black}Creación y {dw-gold}Promoción de Contenido
{{section_meta: keywords="crear contenido sac, contenido privado workspace, users user_id, carpeta privada usuario sac, contenido privado por defecto, promover contenido sac, publicar story, promocion privado a gobernado, kba 3576331, carpetas gobernadas workspace, crear carpetas system, borrador story"}}

### Contenido compartido y gobernado *(Danwor)*

Los administradores del workspace o los responsables designados de contenido crean las **carpetas administradas de nivel superior desde la vista System** y luego las comparten con los equipos correspondientes:

```text
System/<Workspace>/Reports
System/<Workspace>/Models
System/<Workspace>/Data Actions
```

### Contenido privado de desarrollo *(SAP — KBA 3576331)*

El contenido creado directamente por una persona desde la vista nativa `Files → Workspaces` es **privado por defecto** y se guarda en la carpeta dedicada de ese usuario dentro del workspace:

```text
System/<Workspace>/Users/<User_ID>
```

Es aceptable para desarrollo personal, borradores, trabajo en curso y experimentación privada. **No** es la ubicación autoritativa de contenido compartido o productivo del dominio.

### Promoción: de privado a gobernado *(Danwor)*

Proceso controlado para mover o publicar contenido aprobado desde una carpeta privada hacia la jerarquía gobernada del workspace:

1. **Propiedad** — verificar quién es el owner del objeto y sus dependencias.
2. **Dependencias** — relevar modelos, datasets, data actions y connections asociados.
3. **Ubicación** — mover a la carpeta funcional gobernada (`System/<Workspace>/<Functional_Folder>`).
4. **Compartición** — configurar la compartición explícita con los equipos.
5. **Herencia** — asegurar que el objeto tome la configuración prevista de la carpeta padre.
6. **Nombres y metadatos** — validar contra la convención (ver módulo 04).
7. **Preparación de transporte** — verificar identidad técnica y estructura destino.
8. **Baja de obsoletos** — eliminar o archivar copias previas que ya no son autoritativas.

## {dw-black}Compartición y {dw-gold}Herencia de Permisos
{{section_meta: keywords="compartir carpeta sac, share files folders sac, view edit full control, niveles de acceso sac, herencia permisos sac, copy from parent, apply to subfolders, share existing files subfolders, permisos hijos existentes, dependencias comparticion, compartir dentro workspace, permisos sac inmediatos"}}

{{support: Share Files or Folders="https://help.sap.com/docs/SAP_ANALYTICS_CLOUD/00f68c2e08b941f081002fd3691d86a7/35b0bdae69254b04b075e09702d8cb56.html"}}

### Patrón recomendado *(Danwor)*

1. Asignar usuarios de negocio y creadores de contenido a **equipos (teams)**.
2. Asignar esos equipos al **workspace**.
3. Crear las carpetas gobernadas de nivel superior desde la **vista System**.
4. **Compartir** cada carpeta de nivel superior con los equipos del workspace.
5. Usar **niveles de acceso predefinidos** salvo motivo documentado para permisos personalizados:

| Nivel | Para quién |
|---|---|
| **View** | Consumidores de contenido |
| **Edit** | Desarrolladores / responsables de mantenimiento |
| **Full Control** | Solo owners designados del contenido y administradores |

6. Al remediar una jerarquía existente, **aplicar la configuración de compartición a los archivos y subcarpetas existentes**.
7. Asegurar que los objetos nuevos **hereden** la configuración desde la carpeta padre.
8. Revisar por separado los **objetos dependientes** (modelos, datasets, data actions, connections).

{{callout: title="El equipo como centralizador de miembros — su carpeta de equipo queda vacía" tone="punch" icon="🤝"}}
Al crear un **equipo (team)** en SAC se genera automáticamente su carpeta de equipo en `System/Common/<Team_Name>` *(SAP)*. En el patrón Danwor el equipo se usa **solo como centralizador de los miembros que lo conforman** y se asigna **directamente a nivel workspace**; al compartir las carpetas gobernadas del workspace con ese equipo, **todos sus miembros entran en alcance** de una sola vez. Por eso, en este caso de uso, la carpeta `System/Common/<Team_Name>` **queda vacía**: no se le pone contenido ni se comparten objetos directamente en ella. Cargarla mezcla la **doble función** del objeto equipo —grupo de membresía (que asignamos al workspace) vs. carpeta de contenido en `Common`—, fragmenta el gobierno fuera del límite del workspace y es precisamente lo que origina los conflictos de linaje/transporte de la sección *Troubleshooting de Transporte* (Object IDs que terminan viviendo bajo `System/Common/<Team>`). *(Danwor)*
{{/callout}}

### Comportamientos de SAC a tener presentes *(SAP — "Share Files or Folders")*

- Compartir una carpeta **no** actualiza automáticamente los objetos hijos existentes, salvo que se seleccione explícitamente la opción de **compartir los archivos y subcarpetas existentes**.
- **`Copy from parent`** puede reemplazar la configuración de compartición de un objeto hijo con la de su padre.
- **`Apply to subfolders`** propaga la configuración de una carpeta hacia sus descendientes.
- Los cambios de permisos son **inmediatos** y pueden conceder o quitar acceso de forma amplia.
- Las **dependencias** pueden necesitar compartición independiente.
- Dentro de un workspace, el contenido solo puede compartirse con **otros miembros del workspace**.

{{callout: title="Herencia ≈ DAC tipo Jerarquía" tone="note" icon="🔗"}}
El modelo de compartición por carpeta con herencia hacia descendientes es análogo, en espíritu, al DAC de tipo **Jerarquía** de Datasphere (módulo 02): el acceso configurado en el nodo padre propaga a los hijos. La diferencia operativa clave en SAC: la herencia sobre hijos **existentes** no es automática — hay que aplicarla deliberadamente.
{{/callout}}

## {dw-black}Transporte e {dw-gold}Identidad Técnica de Objetos
{{section_meta: keywords="transporte contenido sac, content network sac, object id sac, identidad tecnica objeto, folder id sac, id carpeta sac, actualizar objeto en ubicacion existente, mover no copiar, corregir linaje transporte, kba 3524959, kba 3311985, transporte por id no por nombre, mismo nombre distinto id, colision carpetas transporte"}}

El comportamiento de los transportes de SAC (vía **Content Network** / export-import entre tenants) está determinado por la **identidad técnica de los objetos** (Object ID), no por sus nombres visibles ni por sus rutas de carpetas *(SAP — KBA 3524959)*.

Ejemplo. Tenant de origen:

```text
System/Finanzas/Reports/Costeo Directo/Report A
ID técnico del objeto: REPORT-123
```

Tenant de destino:

```text
System/Common/FINANZAS/Costeo Directo/Report A
ID técnico del objeto: REPORT-123
```

Cuando `Report A` se transporta nuevamente:

1. SAC identifica el objeto existente en el destino por su **Object ID**.
2. SAC **actualiza ese objeto existente**.
3. SAC **no** mueve automáticamente el objeto del destino hacia la ubicación actual del origen.
4. El reporte **permanece en su ubicación existente del destino**, salvo que se lo mueva manualmente o se lo elimine antes de una nueva importación.

Esto es esperado y **por diseño** *(SAP)*.

```text
Object ID en el tenant de ORIGEN
              │
              ▼
      Paquete de transporte (Content Network)
              │
              ▼
El tenant DESTINO busca el mismo Object ID
              │
     ┌────────┴─────────┐
  Se encuentra       No se encuentra
     │                    │
     ▼                    ▼
Actualiza el          Crea/importa el
objeto en SU          objeto según el
ubicación actual      paquete y la
del destino           estructura destino
```

{{callout: title="Corregir linaje: mover, no copiar" tone="punch" icon="⭐"}}
Para redirigir un linaje de transporte, **mover** el objeto del destino que tiene el Object ID coincidente hacia la ubicación deseada — nunca crear una copia y asumir que el transporte empezará a actualizar esa copia. Una copia nueva tiene un **Object ID distinto** y quedará fuera del linaje. *(Danwor, sobre comportamiento SAP)*
{{/callout}}

### Identidad de carpetas y colisiones de nombres *(SAP — KBA 3311985)*

Las **carpetas también tienen Object/Folder ID**. Dos carpetas con el mismo nombre visible pueden tener IDs distintos y representar objetos sin relación entre sí. Carpetas con el mismo nombre pero distinto ID pueden provocar comportamientos incorrectos o destructivos durante los transportes vía Content Network.

Control operativo *(Danwor)*:

1. Abrir la carpeta en SAC.
2. Inspeccionar su URL o los metadatos disponibles.
3. Registrar el **Folder ID**.
4. Comparar las carpetas correspondientes entre origen y destino.
5. No asumir que nombres iguales implican identidades técnicas iguales.

## {dw-black}Troubleshooting de {dw-gold}Transporte
{{section_meta: keywords="troubleshooting transporte sac, deleted files sac, objeto en deleted files, importacion falla sac, kba 3493600, reimportar sac, alineacion workspaces tenants, misma estructura carpetas exportar, caso qa prd finanzas, linaje transporte incidente, retransportar workspace"}}

### Deleted Files *(SAP — KBA 3493600)*

Un objeto ubicado en **Deleted Files** puede seguir interfiriendo con una importación o hacer que SAC informe que el objeto todavía existe. Antes de un procedimiento destructivo de reimportación *(Danwor)*:

1. Respaldar o documentar el objeto existente.
2. Revisar dependencias, schedules, bookmarks, enlaces, propiedad y compartición.
3. Eliminar el objeto del destino solo cuando sea necesario.
4. Revisar `Files → Deleted Files`.
5. Eliminar permanentemente el objeto en conflicto cuando la remediación lo requiera.
6. Reimportar el paquete.
7. Revalidar la configuración de seguridad y operación.

### Alineación de workspaces entre tenants *(SAP + Danwor)*

SAP documenta que el contenido de un workspace solo puede exportarse/copiarse hacia otro tenant cuando allí existe la **misma estructura de carpetas** *(SAP — "Exporting Content")*. Este requisito de igualdad de estructura y el comportamiento de ubicación de objetos existentes (sección anterior) son **reglas distintas**: la estructura igual habilita el transporte; el Object ID decide qué objeto se actualiza y dónde queda.

Validar como mínimo: nombre del workspace, disponibilidad, jerarquía de carpetas requerida, modelo de membresía de teams, administradores del workspace, compartición de carpetas, dependencias de objetos, identidades técnicas origen/destino, permisos de transporte, propiedad y conflictos con Deleted Files.

### Caso real — linaje QA/PRD (anonimizado)

Estado inicial:

```text
QA :  System/Common/FINANZAS/Costeo Directo
PRD:  System/Finanzas/Reports/Costeo Directo
```

Ambas ubicaciones contenían reportes del mismo linaje de transporte. QA **también** tenía creada la jerarquía de carpetas del workspace, pero eso **no** convertía a los objetos de esa ubicación en los destinos del transporte.

- **Causa raíz** *(SAP)*: los Object ID de los reportes existentes en QA correspondían a objetos ubicados dentro del team folder `System/Common/FINANZAS`. La existencia de un workspace o de una jerarquía con nombres similares no cambia el destino del transporte; SAC encontraba los IDs bajo `System/Common/FINANZAS` y actualizaba esos objetos en su ubicación actual.
- **Resolución** *(Danwor)*: los objetos de QA con IDs coincidentes se **movieron** —no se copiaron— desde el team folder hacia `System/Finanzas/Reports/Costeo Directo`, y luego se retransportaron. El nuevo transporte actualizó los reportes directamente en la ubicación del workspace de QA porque los objetos técnicos coincidentes ya estaban allí.
- **Lección** *(Danwor)*: al corregir un linaje, identificar el objeto del destino con el Object ID coincidente, moverlo (no copiar), verificar con un transporte controlado, eliminar/archivar duplicados con IDs distintos y revalidar permisos.

> Cualquier explicación histórica del tipo "los reportes se crearon en team folders y luego se movieron solo en PRD" es una **inferencia**: tratarla como hipótesis salvo que los activity logs, el historial de transportes o los registros de auditoría lo confirmen.

## {dw-black}Árbol de Decisión: {dw-gold}Visibilidad en Workspace
{{section_meta: keywords="no veo carpetas workspace, workspace aparece pero no veo reportes, arbol decision visibilidad sac, troubleshooting visibilidad workspace, usuario no ve reporte sac, diagnostico acceso sac, por que no aparece la story"}}

Síntoma: *un workspace aparece en `Files → Workspaces`, pero las carpetas o reportes esperados no se visualizan.*

```text
1. ¿El objeto se encuentra en Files → System → <Workspace>?
     NO → verificar ruta esperada, tenant, resultado del transporte y Deleted Files.
     SÍ → seguir.
2. ¿Está bajo System/<Workspace>/Users/<User_ID>?
     SÍ → es contenido privado del usuario (salvo que se haya compartido).
          Decidir: ¿sigue privado o se promueve a la jerarquía gobernada?
     NO → seguir.
3. ¿Está bajo una carpeta gobernada del workspace?
     SÍ → inspeccionar la compartición de la carpeta y del objeto.
4. ¿El usuario pertenece a un equipo asignado al workspace?
     NO → corregir la membresía del workspace.
     SÍ → seguir.
5. ¿La carpeta fue compartida con el usuario o con alguno de sus equipos miembros?
     NO → compartir la carpeta raíz gobernada correspondiente.
     SÍ → seguir.
6. ¿La compartición se aplicó a los archivos y subcarpetas existentes?
     NO → aplicar/restablecer deliberadamente la herencia.
     SÍ → seguir.
7. ¿Las dependencias del reporte están compartidas?
     → verificar modelos, datasets, data actions, connections y dependencias cross-workspace.
8. ¿El rol del usuario incluye los permisos requeridos sobre objetos/archivos SAC?
     → validar por separado los permisos del rol y la compartición de carpetas.
9. ¿El usuario prueba por la vista Workspaces y el admin por la vista System?
     → ambas vistas pueden mostrar resultados distintos de forma legítima.
10. ¿Se aplanó la jerarquía intentando resolver la visibilidad?
     → restaurar la jerarquía lógica y corregir la compartición (no la profundidad).
```

{{callout: title="La profundidad no determina visibilidad" tone="warn" icon="🔸"}}
Mover `System/Finanzas/Reports/Costeo Directo/Report A` a `System/Finanzas/Reports/Report A` **no** hace que el reporte aparezca automáticamente en `Files → Workspaces → Finanzas`. La visibilidad la determinan el **acceso y la compartición**, no la cantidad de niveles de carpeta. No aplanar una jerarquía lógica como solución a un problema de seguridad o visibilidad. *(Danwor, sobre comportamiento SAP)*
{{/callout}}

## {dw-black}Migración: Team Folder hacia {dw-gold}Workspace
{{section_meta: keywords="migrar team folder a workspace, migracion contenido sac, runbook migracion sac, system common team a workspace, mover team folder workspace, migrar carpeta equipo workspace, legacy team folder sac"}}

Runbook para migrar contenido gobernado desde `System/Common/<Team_Name>` hacia `System/<Workspace_Name>/<Functional_Folder>` *(Danwor)*.

| Fase | Acciones |
|---|---|
| **Descubrimiento** | Inventariar archivos, carpetas, dependencias, propietarios, compartición, schedules, bookmarks, publicaciones, enlaces y paquetes de transporte. Registrar Object/Folder IDs. Identificar duplicados por nombre **y** por ID. Determinar qué objetos son el linaje activo de transporte. |
| **Preparación** | Crear/verificar el workspace destino. Crear la jerarquía de carpetas acordada desde la vista System. Asignar administradores y teams miembros. Configurar la compartición de las carpetas raíz. Validar la **misma estructura** en todos los tenants relevantes. |
| **Migración** | **Mover** los objetos existentes con IDs coincidentes. Evitar copiar (salvo que se busque un objeto nuevo e independiente). Preservar la identidad del objeto autoritativo. Revalidar dependencias y permisos. |
| **Verificación** | Abrir los reportes desde la vista nativa Workspaces con cuentas de consumidor y de desarrollador. Confirmar gestión por la vista System. Ejecutar un transporte controlado y confirmar que los objetos existentes se actualizan **en su ubicación**. Verificar enlaces directos, bookmarks, schedules, entradas de Catalog, propiedad y compartición. |
| **Limpieza** | Eliminar/archivar duplicados con IDs distintos. Revisar Deleted Files. Actualizar inventarios y registros de propiedad. |
| **Rollback** | Priorizar **mover** el objeto con ID coincidente (conserva referencias y linaje) por sobre eliminar. La eliminación no es la primera opción de remediación. |

## {dw-black}Checklist {dw-gold}DEV / QA / PRD
{{section_meta: keywords="checklist sac transporte, checklist dev qa prd sac, validacion transporte sac, readiness transporte contenido sac, puntos control workspace sac, verificar transporte workspace"}}

| Item | Pregunta de control |
|---|---|
| **Estructura alineada** | ¿La jerarquía de carpetas del workspace es idéntica en DEV, QA y PRD? |
| **Workspace y membresía** | ¿Existen el workspace, sus administradores y los teams miembros en cada tenant? |
| **Object ID origen/destino** | ¿El objeto del destino con el Object ID coincidente está en la ubicación correcta antes de retransportar? |
| **Folder ID** | ¿Se comparó el Folder ID de las carpetas homónimas entre tenants (sin asumir que nombre = identidad)? |
| **Compartición** | ¿Las carpetas raíz gobernadas están compartidas con los teams correspondientes? |
| **Herencia** | ¿La compartición se aplicó a los archivos y subcarpetas existentes? |
| **Dependencias** | ¿Modelos, datasets, data actions y connections están compartidos y disponibles en destino? |
| **Deleted Files** | ¿Se revisó `Files → Deleted Files` por objetos en conflicto antes de reimportar? |
| **Propiedad** | ¿La propiedad del contenido quedó verificada tras el movimiento/transporte? |
| **Permisos post-transporte** | ¿Se revalidaron compartición y asignaciones de team después del transporte (sin asumirlos idénticos al origen)? |

> **Precaución de seguridad en transportes** *(pendiente de validación — KBA 3460999 y KBA 3561284, no accesibles sin login SAP)*: existe documentación SAP sobre seguridad a nivel de objeto/equipo en transportes y sobre que la configuración de acceso transportada para equipos **puede agregarse** a la existente en el equipo destino en lugar de sobrescribirla. Hasta verificar el contenido oficial, tratarlo como señal para **revalidar** compartición y asignaciones de teams después de cada transporte, y no asumirlas idénticas al origen.

## {dw-black}Antipatrones
{{section_meta: keywords="antipatrones sac, malas practicas contenido sac, errores gobierno sac, que evitar workspaces sac, team folder no es workspace, no aplanar carpetas, no copiar linaje, public sac uso"}}

- Tratar `System/Common/<Team>` como equivalente a un workspace.
- Llamar "Public" a todo el contenido bajo `System/Common` (solo `System/Common/Public` es la carpeta Public del tenant).
- Crear contenido productivo dentro de carpetas privadas de usuario del workspace.
- Aplanar estructuras de carpetas para resolver problemas de compartición/visibilidad.
- Poner contenido o compartir objetos directamente en la carpeta de equipo (`System/Common/<Team>`) cuando el equipo se usa como centralizador de miembros del workspace: esa carpeta debe quedar vacía.
- Copiar un reporte cuando el objetivo es conservar su linaje de transporte.
- Asumir que archivos o carpetas con el mismo nombre tienen el mismo Object/Folder ID.
- Asumir que mover en el tenant de origen se replica en el destino.
- Asumir que pertenecer a un workspace concede acceso automático a todo su contenido.
- Asumir que las vistas System y Workspaces deben mostrar resultados idénticos.
- Eliminar objetos del destino sin revisar antes dependencias y Deleted Files.
- Crear copias no administradas en Public o team folders solo para mejorar el descubrimiento (usar el Catalog como capa de descubrimiento; los requisitos de acceso del objeto subyacente siguen aplicando).
- Presentar una inferencia histórica de implementación como un hecho documentado por SAP.

## {dw-black}Referencias {dw-gold}Oficiales
{{section_meta: keywords="referencias sac, fuentes oficiales sac, sap help sac, kba sac, documentacion gobierno sac, links sac gobierno contenido"}}

Las fuentes oficiales SAP (SAP Help y KBAs) de gobierno de contenido SAC están catalogadas en el apéndice **A07 · Biblioteca de Referencias**, sección *Links SAP* y *KBAs SAP*. Núcleo aplicable a este apéndice: *Manage Files and Folders*, *Configure Workspaces*, *Share Files or Folders*, *Exporting Content*, *Change Content Owners*, *Find and Access Content in the Catalog*, *Managing Content*; y KBAs **3576331** (contenido privado en workspaces), **3524959** (ubicación por Object ID en el destino), **3311985** (colisión de carpetas con distinto ID), **3493600** (objetos en Deleted Files) y, opcionales, **3460999** / **3561284** (seguridad de equipos en transportes).
