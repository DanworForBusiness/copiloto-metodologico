# Fixtures — PoC F1 de gobierno de conectividad

Snapshots de conexión **sintéticos** usados por `tests/test_connectivity_governance_poc.py`.

**Son fixtures sintéticas derivadas de patrones observados, NO copias literales de la infraestructura de
ningún cliente.** Reproducen la *forma* real del export de conexiones de SAP Datasphere
(`conn_space_<SPACE>_<NAME>.json`) y los *patrones* a evaluar, pero todos los identificadores de
infraestructura son ficticios: hostnames bajo el TLD reservado `.invalid` (RFC 2606), SIDs genéricos
(`BWX`, `EAD`/`EAQ`, `EPD`/`EPQ`), mandantes genéricos (`100`/`200`/`120`) y nombres de agente neutros
(`AGENT_LOCAL`, `AGENT_SHARED`, `AGENT_CLOUD`). No contienen hostnames, SIDs, mandantes, nombres de
agente ni dominios reales, ni secretos de ningún tipo.

`DESARROLLO` = entorno DEV, `CALIDAD` = entorno QAS (el `environment` se asigna por gobierno en el test,
no se deriva del nombre del espacio).

Patrones preservados EXACTAMENTE (las reglas y los veredictos esperados del motor no cambian):

| Fixture | Patrón reproducido | Eje que ilustra |
|---|---|---|
| `DESARROLLO__BW_DS` / `CALIDAD__BW_DS` | mismo sistema físico (`bw-host.invalid`/`BWX`/`100`), agente distinto (`AGENT_LOCAL` vs `AGENT_SHARED`) | divergencia observada → DIVERGENTE |
| `DESARROLLO__S4ABAP` / `CALIDAD__S4ABAP` | destinos físicos distintos (`erp-a-dev.invalid`/`EAD` vs `erp-a-qas.invalid`/`EAQ`), mismo agente | sin asignación lógica → NO_EVALUABLE / equivalencia sólo con contrato |
| `DESARROLLO__S4PREM` / `CALIDAD__S4PREM` | ídem (`erp-p-dev.invalid` vs `erp-p-qas.invalid`) | ídem |
| `CALIDAD__PostgreSQL` | `agentStatus = DISCONNECTED`, sin host | estado operativo DESCONECTADO (independiente de conformidad) |
| `CALIDAD__SQL_SERVER_2019_onpremise` | conectado, sin host observable | evidencia INSUFICIENTE |

Los nombres de conexión (`BW_DS`, `S4ABAP`, `S4PREM`, `PostgreSQL`, `SQL_SERVER_2019_onpremise`) son
descriptores técnicos genéricos, retenidos por legibilidad y por el mapeo de familias del contrato.
