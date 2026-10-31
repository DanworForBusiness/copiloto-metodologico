# F0 — Contrato de gobierno de conectividad (diseño arquitectónico, 2026-10-30)

> **ESTADO: APROBADO — GO arquitectónico conceptual del owner (2026-10-30).** Quedan ratificadas las **tres identidades independientes** (conexión / sistema físico / familia lógica), los **tres ejes ortogonales** de evaluación (conformidad arquitectónica / estado operativo / cobertura y confianza de evidencia) y la distinción entre **divergencia observada** y **no conformidad demostrada**. Versión del contrato: **`connectivity-governance/v1`**.
>
> **F1 autorizado como PoC PURA** sobre los snapshots existentes: sin conectarse a los tenants, sin modificar datos, **sin UI y sin persistencia de conectividad**. F1 debe demostrar que el motor puede (1) comparar configuraciones observadas, (2) reconocer equivalencia funcional cuando exista una asignación lógica gobernada, (3) detectar divergencias sin calificarlas automáticamente como incumplimientos, (4) evaluar conformidad sólo frente a una homologación explícita, y (5) representar por separado disponibilidad operativa y suficiencia de evidencia. Este documento es la versión versionada del contrato (antes en `_handoffs/`), conservada separada de los cambios UX.

Complemento de `CONNECTIVITY_GOVERNANCE_DISCOVERY.md` (discovery aprobado). **Este documento es el DISEÑO F0** (contrato), no la implementación: el motor F1 vive aparte (ver `connectivity_governance/` + sus tests). Versión del contrato: **`connectivity-governance/v1`**.

Principio rector (correcciones del owner incorporadas):
- **El Assessment OBSERVA configuraciones; un CONTRATO gobernado determina su conformidad.** Una diferencia observada NUNCA es, por sí sola, un incumplimiento.
- **La equivalencia funcional entre entornos se respalda por evidencia o por un contrato de homologación gobernado — jamás por nombres** (`RISE`, `DEV`, `On_Premise` no son prueba).

**Revisión 2026-10-30 (precisiones del owner):** los estados dejan de ser un enum único excluyente → se separan en **tres ejes ortogonales** (conformidad arquitectónica / estado operativo / cobertura y confianza de evidencia); las cinco etiquetas anteriores quedan como PRESENTACIÓN derivada (sección 3). `environment` se define como **atributo gobernado** (no identificador técnico); la identidad estable del tenant es su CUID/host (sección 1.1).

---

## 1. Contrato de identidad y equivalencia entre entornos

Tres identidades SEPARADAS (ninguna reemplaza a las otras):

### 1.1 Identidad de conexión (técnica, inequívoca)
`connection_id = (tenant, environment, space, connection_name, type_id)`
- Identifica UNA conexión concreta.
- **`tenant` = identidad técnica ESTABLE** del tenant Datasphere (p. ej. su CUID de aprovisionamiento / host real, contrastado por OAuth en la extracción). Es el identificador inequívoco; NO cambia por reclasificación.
- **`environment` (DEV/QAS/PRD) = ATRIBUTO GOBERNADO, no un identificador técnico único.** Es una etiqueta declarada en el registro (sección 2) y asignada a un tenant/espacio por el responsable; un tenant podría cumplir una función y su `environment` se decide por gobierno, nunca se deriva del nombre ni se usa como clave de identidad. La identidad inequívoca la da `tenant` (técnico); `environment` sólo clasifica.

### 1.2 Identidad del sistema físico (observable, evidencia)
`physical_system_id = (host, sysid, systemnumber, client[, gwhost, installation_number si observable])`
- Dice a qué INSTANCIA SAP apunta la conexión. Es EVIDENCIA, no la clave de equivalencia. Se extrae de `configuration.ConnectionProperties` (ya parseado por `generate_space_catalog._connection_info`).
- Puede ser NO observable (p. ej. JDBC genérico sin host) → entonces no aporta evidencia de destino.

### 1.3 Identidad lógica (familia de sistemas, gobernada)
`logical_system_id` = identificador de la FAMILIA SAP cuyos miembros son las instancias DEV/QAS/PRD de un mismo sistema funcional (p. ej. `S4_BIOGENESIS` con miembros DEV/QAS/PRD).
- **NO se infiere por nombre.** Se establece por: (a) **asignación explícita** en el registro de homologación (preferente), o (b) **evidencia suficientemente confiable** (p. ej. mismo `installation_number`/landscape declarado, o mapeo declarado por el responsable). La similitud de `sysid`/host NO basta para afirmar la familia (DEV/QAS/PRD tienen sysid y host distintos por diseño).

### 1.4 Regla de equivalencia funcional
Dos conexiones de entornos distintos son **funcionalmente equivalentes** si pertenecen a la MISMA `logical_system_id` (establecida por 1.3). La identidad física (1.2) sirve para:
- Detectar que dos conexiones apuntan al MISMO sistema físico (caso raro entre entornos; útil como señal), y
- Como evidencia de respaldo de una asignación lógica.
La equivalencia **nunca** se deriva sólo de `(host,sysid,client)` ni de nombres.

### 1.5 Perfil de conectividad esperado
Dentro de una `logical_system_id`, cada `environment` tiene un **perfil esperado** (homologado): agente o perfil de conectividad aprobado, tipo/adapter, modo credencial, etc. El perfil esperado vive en el registro (sección 2), no se infiere de la observación.

---

## 2. Esquema mínimo del registro de conectividad homologada

Registro GOBERNADO por cliente (reutiliza los patrones de `governed-decision/v1`/B4: append-only, autorización del responsable, identidad + evidencia + vigencia), en un **namespace/tipo PROPIO de conectividad** — NO mezclado con las ratificaciones del Judgment de modelos.

```
connectivity-homologation/v1
  client
  logical_system_id            # familia (ej. S4_BIOGENESIS)
  description                   # rol funcional (ej. "S/4HANA finanzas")
  members[]:                    # una entrada por entorno homologado
    - environment               # DEV | QAS | PRD | ...
      tenant                    # tenant Datasphere (host real contrastado por OAuth)
      related_connections[]     # connection_id(s) que materializan este miembro
      expected_agent_or_profile # agente/grupo/perfil de conectividad esperado
      expected_physical_system  # (host/sysid/client) esperado, opcional como evidencia
  homologation_owner            # responsable de la homologación (identidad autenticada)
  approved_at, version          # fecha + versión del contrato
  evidence_ref                  # digest/puntero de la evidencia de aprobación
  exceptions[]:                 # desviaciones autorizadas (ver sección 4)
    - scope                     # a qué miembro/conexión aplica
      rationale, approver, approved_at, review_by, evidence_ref
  status (derivado en lectura): ACTIVE | EXPIRED (por review_by)
```
- **Separación estricta**: una homologación de conectividad NO hereda ni produce ratificaciones del Judgment, y viceversa. Son planos independientes (igual que B4 es independiente de Judgment).
- **Vigencia**: `review_by` (como B4) → una homologación vencida deja de respaldar conformidad (pasa a requerir revisión).

---

## 3. Matriz de hallazgos, criterios de clasificación y evidencia necesaria

**Corrección del owner: los estados NO son un único enum excluyente.** Una conexión puede estar, a la vez, NO CONFORME (arquitectura) y DESCONECTADA (operación). Se modelan **TRES EJES ORTOGONALES e independientes**; cada uno se concluye por separado, con su propia evidencia. Las etiquetas de presentación (sección 3.4) se DERIVAN de la combinación — no la reemplazan.

### 3.1 Eje — Conformidad arquitectónica (contra el contrato)
| Valor | Definición | Evidencia necesaria | ¿Automático? |
|---|---|---|---|
| **CONFORME** | lo observado coincide con el perfil esperado del contrato ACTIVE para ese entorno/familia | contrato ACTIVE + observación que coincide | sí (comparación determinista) |
| **DIVERGENTE — pendiente de revisión** | hay divergencia observable (p. ej. agente distinto en la misma familia) **y no hay contrato** que fije el perfil esperado | observación de divergencia + ausencia de perfil esperado | detecta sí; conclusión = SEÑAL |
| **NO CONFORME** | lo observado CONTRADICE el perfil esperado de un contrato ACTIVE (sin excepción vigente) | contrato ACTIVE + observación que lo contradice | sí (comparación determinista) |
| **NO EVALUABLE** | no hay base para juzgar conformidad (sin asignación lógica / sin contrato Y sin divergencia observable) | — | sí (detecta la ausencia de base) |

*Regla dura: sin contrato, el techo es DIVERGENTE; jamás NO CONFORME.*

### 3.2 Eje — Estado operativo (observado, punto en el tiempo)
| Valor | Definición |
|---|---|
| **CONECTADO** | `agentStatus = CONNECTED` en el export |
| **DESCONECTADO** | `agentStatus = DISCONNECTED` |
| **DESCONOCIDO** | sin `agentStatus` observable / export sin el dato |
*El estado operativo NO prueba conformidad (conectado ≠ conforme) ni, por sí solo, una desviación arquitectónica (desconectado ≠ no conforme).*

### 3.3 Eje — Cobertura y confianza de evidencia (con procedencia y vigencia)
| Valor | Definición |
|---|---|
| **COMPLETA** | destino e identidad lógica observables; export fechado y vigente; firma/procedencia verificable |
| **PARCIAL** | parte observable (p. ej. agente sí, destino no) → algunos ejes concluyen, otros no |
| **INSUFICIENTE** | no permite concluir (destino no observable / sin asignación lógica / export vencido o sin procedencia) |
*Cada conclusión de los ejes 3.1/3.2 se acompaña SIEMPRE de su cobertura/confianza + procedencia (tenant/space/export + fecha).*

### 3.4 Etiquetas de presentación (DERIVADAS de los tres ejes — no un enum nuevo)
«Equivalente» (conformidad CONFORME) · «Diferente — requiere revisión» (DIVERGENTE) · «No homologada» (NO CONFORME) · «Agente desconectado» (eje operativo DESCONECTADO, independiente de la conformidad) · «Evidencia insuficiente» (cobertura INSUFICIENTE). Una fila puede mostrar más de una etiqueta (p. ej. «No homologada» + «Agente desconectado»).

### 3.5 Dimensiones del Assessment (control determinista vs señal), independientes, sin score global
1. **Conformidad arquitectónica** — eje 3.1.
2. **Estado operativo observado** — eje 3.2.
3. **Integridad de dependencias** — ¿todas las conexiones del `list` tienen `get`? ¿la familia tiene los miembros esperados por entorno?
4. **Comparabilidad entre entornos** — ¿hay `logical_system_id` suficiente? Si no ⇒ conformidad NO EVALUABLE + cobertura INSUFICIENTE para equivalencia.
5. **Cobertura y vigencia de evidencia** — eje 3.3 (procedencia/fecha/firma; homologación ACTIVE/EXPIRED).
6. **Excepciones gobernadas** — ¿la divergencia/no-conformidad tiene excepción vigente? (sección 4).

---

## 4. Tratamiento de excepciones y ausencia de evidencia

- **Excepción de conectividad** (tipo PROPIO, patrón B4): una divergencia respaldada por una `exceptions[]` ACTIVE en el registro deja de ser «DIFERENTE/NO HOMOLOGADA» y se presenta como **excepción gobernada** (visible y trazable, con `review_by`). No se hereda ninguna ratificación del Judgment.
- **Excepción vencida** (`review_by` pasado) ⇒ vuelve a su estado sin excepción (DIFERENTE o NO HOMOLOGADA según haya o no contrato).
- **Ausencia de evidencia** (EVIDENCIA INSUFICIENTE) ⇒ **nunca** se presenta como conformidad ni como incumplimiento; se reporta como no concluyente con el motivo (destino no observable / sin asignación lógica / export vencido). Fail-closed.
- **Ausencia de contrato** ⇒ el techo de conclusión es DIFERENTE (requiere revisión); jamás NO HOMOLOGADA.

---

## 5. Aplicación del contrato a los 4 casos de la PoC Biogénesis (DEV vs QA; sin contrato cargado todavía)

Cada caso se concluye en los TRES ejes por separado (sin contrato cargado todavía):

| Conexión | Observación | Conformidad (3.1) | Operativo (3.2) | Cobertura (3.3) | Con contrato |
|---|---|---|---|---|---|
| **BW_DS** | mismo sistema físico (`bbgasapbipro/BWP/400`), **agente distinto** (DEV `On_Premise` vs QA `BIO_DEVQA`) | **DIVERGENTE — pendiente de revisión** (divergencia de agente, sin perfil esperado) | CONECTADO (ambos) | COMPLETA (destino+agente observables) | con perfil esperado por entorno: **CONFORME** o **NO CONFORME**; o **excepción** si autorizado |
| **S4ABAP** | destino físico distinto (DEV→`vhbibdevci/DEV/100`, QA→`vhbibqasci/QAS/200`), mismo agente | **NO EVALUABLE** (sin `logical_system_id` no se afirma que DEV y QAS son la misma familia) | CONECTADO | PARCIAL/INSUFICIENTE para equivalencia (falta identidad lógica) | con `S4_BIOGENESIS` + perfiles: **CONFORME** (destino por entorno esperado) |
| **S4PREM** | ídem (DEV→DEV/120, QA→QAS/200), mismo agente | **NO EVALUABLE** | CONECTADO | PARCIAL/INSUFICIENTE para equivalencia | ídem con el contrato |
| **PostgreSQL (QA)** | `agentStatus = DISCONNECTED` | NO EVALUABLE (sin contrato/base) | **DESCONECTADO** | COMPLETA (estado observado) | el contrato no cambia el estado operativo |
| **SQL_SERVER_2019** | sin host/sysid observable | NO EVALUABLE | CONECTADO | **INSUFICIENTE** (destino no observable) | salvo que el contrato aporte identidad lógica/evidencia |

Observaciones clave: (1) **ninguna** observación por sí sola produce «NO CONFORME»; (2) la DESCONEXIÓN de PostgreSQL (eje operativo) es independiente de su conformidad (NO EVALUABLE) — no se mezclan; (3) `S4ABAP/S4PREM` NO se declaran «equivalentes por destino»: sin `logical_system_id` la conformidad de equivalencia es **NO EVALUABLE**; (4) el caso disparador real (PRD on-prem vs DEV/QA RISE) necesita el export de PRD + el contrato de familia.

---

## 6. Qué se resuelve automáticamente vs qué requiere el responsable de gobierno

**Automático (determinista desde la evidencia ya disponible):**
- Extracción/parseo de `connection_id`, `physical_system_id`, agente observado (`agentName/group/location/status`), capacidades.
- DETECCIÓN de divergencias (agente/destino distinto para conexiones relacionadas), agente desconectado, faltantes de export, vigencia de la evidencia.
- COMPARACIÓN contra el perfil esperado del contrato (cuando existe) → EQUIVALENTE / NO HOMOLOGADA.
- Marcado de EVIDENCIA INSUFICIENTE.

**Requiere decisión del responsable de gobierno:**
- Asignar `logical_system_id` (qué conexiones/entornos son la misma familia) — NO inferible con confianza por el motor.
- Declarar el **perfil de conectividad esperado** por entorno (agente/perfil homologado).
- Autorizar **excepciones** y su vigencia.
- Confirmar `tenant`/`environment`/`función` del tenant en el registro.

---

## 7. Reutilización de componentes (evitar duplicar motores/persistencia)

| Necesidad | Reutilizar | Nuevo (mínimo) |
|---|---|---|
| Extracción de conexiones | `tools/catalog/DSP_CLI_Export_Lineage.py::run_export` (OAuth server-side, ya existe) | — (F2: ampliar a `connections list/get` sólo LECTURA) |
| Parseo conexión→agente/target | `generate_space_catalog._connection_detail/_connection_info`, `catalog_service.ConnectionRecord` | — |
| Lectura SharePoint | `generate_catalog.fetch_shared_file`, `datasphere_profiles` | — |
| Registro gobernado + excepciones | patrones de `governed-decision/v1`/B4 (append-only, auth, identidad+evidencia+vigencia) | **tipo PROPIO** `connectivity-homologation/v1` (namespace separado; NO mezclar con Judgment) |
| Motor de evaluación | patrón de "lente" de `asset_evaluation` (ejes independientes, sin sumar) | módulo PURO `connectivity-rules/v1` (read-only; sin framework nuevo) |
| Informe transversal | `client_assessment_portfolio` (v1) + patrón "Mirada Danwor" (3 lentes independientes) | sumar **lente Conectividad** (sin score global, sin informe duplicado) |

Ambición aprobada del Informe de cliente: responder no sólo **qué modelos cumplen**, sino **qué dependencias están gobernadas, cuáles presentan riesgo y qué aún no fue evaluado** — con trazabilidad de procedencia/snapshot/vigencia/estado por módulo, mismo informe accesible desde Mod 01 y Mod 04, sin duplicar.

**Automatización (dirección objetivo, F2+):** reutilizar OAuth/extracción server-side; usar SÓLO endpoints de LECTURA (Connections REST API lee inventarios/definiciones sin privilegios excesivos); **evitar operaciones de validación/administración o monitoreo de agentes que requieran privilegios de información de sistema o tengan efecto operativo**. `agentStatus` operativo puede requerir privilegios elevados y es punto-en-el-tiempo → tratarlo como señal fechada, no como control de conformidad. Validar permisos/cobertura por tenant ANTES de automatizar.

---

## 8. Gates de aceptación para iniciar F1

F1 = PoC comparativa PURA sobre snapshots existentes (sin OAuth productivo, sin persistencia, sin UI). Para arrancar F1 se requiere:
1. **Contrato F0 aprobado** por el owner (identidad de 3 niveles con `environment` gobernado; regla de equivalencia por sistema lógico; **tres ejes ortogonales** conformidad/operativo/cobertura, con las etiquetas de presentación derivadas).
2. **Esquema del registro** `connectivity-homologation/v1` aprobado (campos mínimos de la sección 2).
3. **Asignaciones lógicas de la PoC** provistas por el responsable (al menos `S4_BIOGENESIS` con miembros DEV/QAS[/PRD] + perfil esperado), o acuerdo de correr F1 mostrando «EVIDENCIA INSUFICIENTE» donde falte.
4. **Snapshots disponibles**: DEV/QA ya están en `catalog_local/.csn_cache/Biogenesis/Conexiones/`; **definir si se incorpora el export de PRD** (hoy ausente) o se documenta como pendiente.
5. **Spec de clasificación congelada** (esta sección 3/4) + goldens esperados para los 4+ casos Biogénesis.
6. **Alcance F1 estrictamente read-only**: librería pura `connectivity-rules/v1` + tests; NO OAuth, NO escrituras, NO UI, NO persistencia; sin tocar servicios productivos.
7. **Criterio de independencia**: la lente de conectividad no altera ni se promedia con Judgment/Portabilidad/Gobierno.

Al aprobarse F0, **conservar este documento versionado en el repositorio** (contrato `connectivity-governance/v1`) para que las implementaciones partan de un contrato y no de interpretaciones por sesión. — **Hecho:** este archivo es la copia versionada (commit propio de «Documentación contractual F0», separado de UX y de la PoC F1).

**ESTADO: F0 APROBADO (2026-10-30).** F1 habilitado como PoC pura sobre snapshots (read-only, sin UI, sin persistencia). Sin push/PR/merge/deploy.
