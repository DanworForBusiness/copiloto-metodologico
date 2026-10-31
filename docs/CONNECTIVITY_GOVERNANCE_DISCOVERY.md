# Discovery — Gobierno de conectividad e Informe de cliente transversal (read-only, 2026-10-30)

Caso disparador: Biogénesis Bagó — conexiones S/4HANA "equivalentes" dependen de DP Agents distintos (DEV/QA vía agente en RISE, PRD on-prem; la caída del on-prem cortó PRD). Pregunta: ¿puede el Hub detectar estas desviaciones por EVIDENCIA técnica, no por nombres? **Respuesta: sí, con lo que ya se extrae hoy.** Discovery sin cambios de código/UI/persistencia; sin escrituras; sin tocar conexiones/agentes.

---

## 1. Inventario de capacidades y código reutilizable

| Capacidad | Dónde (reutilizable) | Estado |
|---|---|---|
| Extracción de conexiones por CLI (`datasphere spaces connections list/get`) | guía `content_packs/datasphere_core/guides/como-publicar-origenes.yaml` | MANUAL (máquina del consultor, OAuth interactivo) → sube JSON a SharePoint |
| **Extracción autenticada OAuth SERVER-SIDE** | `tools/catalog/DSP_CLI_Export_Lineage.py::run_export` + `webapp/services/datasphere_publication_service.py` (`client_secret` por argumento, nunca persistido) | YA EXISTE para publicación de modelos; reutilizable para conexiones |
| Lectura de SharePoint (Graph `/shares`, token DELEGADO del usuario) | `generate_catalog.fetch_shared_file`, `webapp/services/datasphere_profiles.py` | EXISTE; sin cliente nuevo ni secretos en código |
| Perfiles OAuth por cliente (host/token/client_id en Excel privado de SharePoint) | `datasphere_profiles.load_profiles` | EXISTE (solo el dueño ve todos; el consultor usa los suyos) |
| **Parseo de conexión → metadata de AGENTE y TARGET** | `tools/catalog/generate_space_catalog.py` (`_connection_detail`, `_connection_info`), `tools/catalog/catalog_service.py` (`ConnectionRecord`: `agent`, `agent_status`, `dp_agent`, `location`, `data_provisioning`, `type_id`, `host`) | EXISTE y YA captura `agentName/agentGroupName/agentStatus/location` + `host/sysid/systemnumber/client` |
| **Hallazgo "Agente no conectado"** (`agentStatus != CONNECTED` → severidad media) | `generate_space_catalog.py:551-552` | EXISTE (control determinista básico ya operativo) |
| Tabla de conexiones en el Catálogo (Conexión·Tipo·Ubicación·Agente·Estado·Capacidades) | `generate_space_catalog.py:594-603`, `webapp/templates/partials/catalog_connections.html` | EXISTE (presentación por espacio/cliente) |
| Informe de cliente consolidado (`client-assessment-portfolio/v1`) | `webapp/services/client_assessment_portfolio.py`, `webapp/routers/catalog_assessment.py` (`/catalog/client/assessment[.json/.md]`) | EXISTE; una fila por entrada; arrastra Adopción (Juicio); sin score global |
| "Mirada Danwor": 3 lentes INDEPENDIENTES (Adopción/Gobierno/Portabilidad) | `webapp/services/asset_evaluation.py::asset_evaluation` → `{governance, portability, compatibility}`; template `catalog_assessment_report.html` | EXISTE; ejes separados, nunca sumados — **patrón a reutilizar para "conectividad"** |

**Conclusión 1:** la evidencia de agente/target YA se extrae y parsea; ya hay un control de agente-desconectado; ya existe un consolidado de cliente con lentes independientes sin score global. B4 (governed-decision/v1) aporta el plano de excepciones gobernadas.

---

## 2. Matriz de metadata técnicamente accesible

Fuente primaria: JSON de `datasphere spaces connections get` (hoy en `catalog_local/.csn_cache/<cliente>/Conexiones/conn_space_<SPACE>_<NAME>.json`).

| Dato | Campo (JSON/parser) | Fuente | Permiso | Clase |
|---|---|---|---|---|
| Espacio, nombre técnico/funcional, tipo (typeId), adapter | `space`, `name`, `business_name`, `typeId`, `adapter` | CLI connections get | OAuth del tenant | **OBTENIBLE** |
| **Agente (instancia)** | `agentName` | CLI connections get | OAuth del tenant | **OBTENIBLE** |
| **Grupo de agente** | `agentGroupName` | idem | idem | **OBTENIBLE** |
| **Estado del agente** | `agentStatus` (CONNECTED/DISCONNECTED) | idem | idem | **OBTENIBLE** (punto en el tiempo de la extracción) |
| Ubicación / modo DP | `location`, `dataProvisioningOption`, `dpAgentConnected` | idem | idem | **OBTENIBLE** |
| **Identidad del sistema destino** | `host`, `sysid`, `systemnumber`, `client`, `gwhost` (de `configuration.ConnectionProperties`) | idem | idem | **OBTENIBLE** |
| Capacidades declaradas, autenticación, database params | features/auth/db | idem | idem | **OBTENIBLE** |
| Replicación realtime | `realtimeReplicationStatus` | idem | idem | **OBTENIBLE** (punto en el tiempo) |
| **Equivalencia funcional DEV/QA↔PRD** (mismo destino, distinto agente) | derivado: comparar `(host,sysid,client)` y `(agentName,location)` | cómputo sobre lo obtenible | — | **INFERIDO** (determinista, pero decisión leak/ok necesita gobierno) |
| **Inventario completo de agentes** (todos los agentes, host del agente, versión, capacidades del agente) | NO está en `connections get` (es admin: System → DP Agents) | CLI connections NO lo expone | otra API/admin | **REQUIERE GOBIERNO HUMANO / otra fuente** |
| **Intención** (¿el agente distinto por entorno es deliberado?) | — | decisión arquitectónica | — | **REQUIERE GOBIERNO HUMANO** (B4 ratificación/excepción) |
| **Tenant / entorno (DEV/QA/PRD)** de cada export | hoy IMPLÍCITO en la carpeta/host; no hay dimensión explícita | convención SharePoint + host del login | — | **INFERIDO hoy; proponer explícito** |

**Conclusión 2:** la asociación conexión→agente y la identidad del destino son OBTENIBLES. El inventario de agentes como entidades (host/versión/capacidades del agente) NO viene por el camino actual → segunda fuente o gobierno humano. El tenant/entorno hoy es implícito → hay que hacerlo explícito en el contrato de evidencia.

---

## 3. Riesgos y restricciones de ejecución OAuth (desde DigitalOcean)

- **Ya existe** ejecución autenticada server-side (`run_export` con `client_secret` por argumento; el secreto vive solo durante la extracción, no se persiste/registra). Reutilizable para `connections list/get`.
- **Flujo de login**: el how-to usa `datasphere login` (authorization-code, INTERACTIVO con navegador). Para DO desatendido hace falta **OAuth client-credentials** por tenant (los perfiles de SharePoint ya guardan host/token-url/client-id; falta confirmar que cada cliente tenga un OAuth client habilitado para client-credentials con scope de administración de conexiones). **Riesgo: no todos los tenants tendrán client-credentials; algunos solo permiten authorization-code.**
- **Permisos**: leer agentStatus/agentName puede requerir rol de administración de conexiones/DP en el tenant; un OAuth de solo-modelado podría no verlo. **Verificar scope antes de automatizar.**
- **Secretos/gobierno**: automatizar desde DO implica custodiar client-secrets por tenant (hoy el consultor los ingresa; el dueño los tiene en SharePoint). Mantener el patrón "secreto efímero, nunca persistido en el repo/log".
- **Dependencia del binario CLI**: `cli_available` indica que el server necesita el CLI `datasphere` instalado; en DO hay que garantizar el binario/versión.
- **Punto-en-el-tiempo**: `agentStatus` es del instante de la extracción → la vigencia/evidencia debe fecharse (no afirmar "conectado ahora" con un export viejo).

**Recomendación:** mantener la EXTRACCIÓN como hoy (manual o server-side vía `run_export`) y centrar B-connectivity en el ANÁLISIS read-only de los JSON ya disponibles; la automatización OAuth total es un incremento posterior con verificación de scopes por tenant.

---

## 4. Propuesta de identidad y equivalencia entre tenants

**Identidad de conexión (contrato de evidencia, ADITIVO — no altera el esquema actual):**
`(tenant, environment, space, connection_name, type_id)` + **identidad del destino** `(host, sysid, systemnumber, client)` + **identidad del agente** `(agentName, agentGroupName, location)`.
- `tenant`/`environment` hoy implícitos → hacerlos explícitos (p. ej. derivados del host del login o declarados en el perfil del cliente).
- "Origen lógico" = la conexión por su rol funcional (p. ej. "S/4 producción"), que puede tener NOMBRES distintos por entorno.

**Equivalencia funcional (determinista, sin asumir por nombre):**
dos conexiones de entornos distintos son **funcionalmente equivalentes** si comparten la **identidad del destino** `(host|sysid|client)` normalizada — NO si comparten nombre. Sobre eso se compara el AGENTE.

**Niveles de confianza de la homologación:**
1. **ALTA** — mismo destino `(sysid,client)` verificado por campos del connection info + mismo tipo/adapter.
2. **MEDIA** — destino parcialmente identificable (p. ej. host sin sysid) o solo por nombre+tipo.
3. **BAJA / EVIDENCIA INSUFICIENTE** — el destino no es observable (p. ej. SQL genérico sin host en el export) → **no se afirma equivalencia**.

**Clasificación de resultados (los cuatro pedidos):**
- **Configuración diferente (legítima):** destino distinto por entorno (DEV→DEV, QA→QAS, PRD→PRD). *Esperado, no es incumplimiento.* (PoC: `S4ABAP`/`S4PREM`.)
- **Configuración no homologada (desviación):** **mismo destino, agente/ubicación distinta** sin excepción gobernada. (PoC: `BW_DS` — mismo `bbgasapbipro/BWP/400`, agente DEV=`On_Premise` vs QA=`BIO_DEVQA`.) *Señal para revisión/gobierno, no incumplimiento automático.*
- **Agente desconectado:** `agentStatus=DISCONNECTED`. (PoC: `PostgreSQL` en QA.) *Control determinista.*
- **Evidencia insuficiente:** destino no observable. (PoC: `SQL_SERVER_2019_onpremise`, host=None.)

**Homologación por entorno:** una **excepción gobernada** (reusar `governed-decision/v1`/B4) puede declarar "el agente distinto entre DEV/QA (RISE) y PRD (on-prem) es intencional" → una desviación con excepción vigente deja de ser señal de atención (pero sigue visible y trazable).

---

## 5. Modelo conceptual — Assessment de conectividad (independiente)

Motor NUEVO e INDEPENDIENTE (no mezcla con Judgment/Portabilidad/Gobierno; otra "lente" con su propia versión de reglas, p. ej. `connectivity-rules/v1`). Entrada = los JSON de conexión ya extraídos; salida = evidencia + señales, SIN score global.

Dimensiones candidatas (controles DETERMINISTAS vs señales de REVISIÓN):
| Dimensión | Control determinista | Señal de revisión |
|---|---|---|
| **Inventario y cobertura** | ¿hay export por espacio? ¿todas las conexiones del `list` tienen `get`? | faltantes/huérfanos |
| **Conformidad de configuración** | tipo/adapter/credential-mode esperados por rol | parámetros atípicos |
| **Dependencias operativas** | `agentStatus=CONNECTED`; `realtimeReplicationStatus` | agente único como SPOF entre entornos |
| **Diferencias entre entornos** | equivalencia de destino (ALTA/MEDIA) | mismo destino + agente distinto **sin** excepción |
| **Excepciones gobernadas** | existe ratificación/excepción vigente para la desviación | excepción vencida/ausente |
| **Integridad y vigencia de evidencia** | firma/fecha del export; recuperabilidad | export viejo (agentStatus no confiable) |

Veredicto: **por dimensión** (CONFORME / DESVIACIÓN / NO HOMOLOGADO / DESCONECTADO / EVIDENCIA INSUFICIENTE / EXCEPCIÓN GOBERNADA), nunca un único número. Distinguir SIEMPRE control (hecho) de señal (requiere criterio).

---

## 6. Modelo conceptual — Informe de cliente transversal

Reutilizar el patrón EXISTENTE, sin duplicar informes ni inventar un score global:
- El consolidado `client-assessment-portfolio/v1` hoy arrastra SOLO la Adopción (Juicio) por fila. **Extensión:** agregar, como **lente transversal adicional e independiente**, el estado de **conectividad del cliente** (resumen por entorno + desviaciones no homologadas + agentes desconectados + cobertura de evidencia), con la MISMA regla de "lentes que no se suman" de la "Mirada Danwor".
- Mantener por cada ítem: **identidad y procedencia** (tenant/space/connection/target/agent), **estado y vigencia** (fecha del export), **cobertura** (qué se pudo observar), **hallazgos y evidencias**, **navegación contextual** (desde Módulo 01 "Catálogo de conexiones" → ficha de conexión; desde Módulo 04 "Informe de cliente" → sección Conectividad).
- **Independencia entre motores/veredictos:** conectividad es su propio eje; NO altera el Juicio de modelado ni se promedia con él. El informe de cliente muestra N lentes lado a lado (Adopción, Gobierno, Portabilidad, **Conectividad**), cada una con su versión de reglas.
- **Evitar duplicación:** una sola construcción de snapshot por cliente (como hoy) que incorpore la lente de conectividad; sin segundo informe paralelo.

---

## 7. PoC mínima (local, sin secretos) — HECHA

Script read-only `_handoffs/... (scratchpad conn_poc.py)` sobre `catalog_local/.csn_cache/Biogenesis/Conexiones/` (DEV=DESARROLLO, QA=CALIDAD; **no hay PRD en el cache local**). Reutiliza `generate_space_catalog._connection_detail/_connection_info`. Resultado (evidencia real):

```
ENV NAME     TYPE         agentName   location agentStatus  host(target)                 sysid nr clnt
DEV BW_DS    SAPBW        On_Premise  agent    CONNECTED    bbgasapbipro                 BWP   01 400
QA  BW_DS    SAPBW        BIO_DEVQA   agent    CONNECTED    bbgasapbipro                 BWP   01 400   ← MISMO destino, AGENTE DISTINTO
DEV S4ABAP   ABAP         BIO_DEVQA   agent    CONNECTED    vhbibdevci...  DEV 00 100
QA  S4ABAP   ABAP         BIO_DEVQA   agent    CONNECTED    vhbibqasci...  QAS 00 200           ← destino distinto (esperado)
DEV S4PREM   SAPS4HANAOP  BIO_DEVQA   agent    CONNECTED    vhbibdevci...  DEV 00 120
QA  S4PREM   SAPS4HANAOP  BIO_DEVQA   agent    CONNECTED    vhbibqasci...  QAS 00 200           ← destino distinto (esperado)
QA  PostgreSQL GENERICJDBC Dinocloud  agent    DISCONNECTED ...                                  ← agente desconectado
```
Clasificación automática: `BW_DS` = **no homologado** (mismo destino, agente distinto); `S4ABAP/S4PREM` = **configuración diferente legítima** (destino por entorno); `PostgreSQL` = **desconectado**; `SQL_SERVER_2019` = **evidencia insuficiente** (host no observable). **La detección por evidencia, sin nombres, funciona.** Sin secretos, sin red, sin escrituras.

> Nota: el caso disparador exacto (S/4 PRD on-prem vs DEV/QA RISE, MISMO destino, agente distinto) requiere el export de PRD (no está en el cache local). El mecanismo queda demostrado con `BW_DS`.

---

## 8. Fases de implementación recomendadas

- **F0 (sin código, GO):** confirmar el contrato de evidencia aditivo (tenant/environment explícitos) y la fuente de PRD (export existente en SharePoint o nueva extracción). Definir reglas de homologación por destino.
- **F1 — Lectura/análisis read-only (motor `connectivity-rules/v1`):** librería pura que, sobre los JSON ya disponibles, produzca el inventario + clasificación (equivalencia por destino, no por nombre) + controles/señales. Golden sobre Biogénesis. Sin UI, sin persistencia.
- **F2 — Evidencia por tenant/space/connection/agente:** contrato de evidencia aditivo + identidad/vigencia; consumir excepciones gobernadas de `governed-decision/v1` (B4) para homologaciones intencionales.
- **F3 — Lente en el Informe de cliente:** sumar la lente "Conectividad" al consolidado existente (sin score global, independiente), con navegación contextual Mod 01 ↔ Mod 04.
- **F4 (posterior, con verificación de scopes) — Automatización OAuth desde DO:** extender `run_export` a `connections list/get` por tenant con client-credentials, solo si el scope lo permite.

### Decisiones pendientes del owner
1. ¿`environment` (DEV/QA/PRD) y `tenant` se declaran en el perfil del cliente o se derivan del host del login?
2. ¿Homologación se decide por **destino** `(sysid,client)` (recomendado) o también se admite por nombre+tipo con confianza MEDIA?
3. ¿La "desviación no homologada" es una **señal de revisión** (recomendado) o un control que puede marcar incumplimiento? (Owner pidió NO equiparar diferencia con incumplimiento.)
4. ¿Las homologaciones intencionales se registran como **excepción gobernada** reutilizando `governed-decision/v1` (B4) o un registro propio de conectividad?
5. ¿Inventario de agentes como entidades (host/versión/capacidades) entra en alcance? (hoy NO obtenible por `connections get` → necesita otra fuente/gobierno.)
6. ¿Automatizar extracción OAuth desde DO (F4) o mantener extracción manual/publicación y solo analizar? (riesgo de scopes por tenant.)

**Restricciones respetadas:** sin UI, sin cambios de persistencia, sin escrituras, sin tocar conexiones/agentes/replicaciones, sin push/PR/merge/deploy. STOP para revisión arquitectónica.
