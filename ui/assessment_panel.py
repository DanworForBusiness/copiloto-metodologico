"""
ui/assessment_panel.py — SAP BDC Certification Exam simulator.

Traducción fiel del App.tsx original:
- Timer: q_start en session_state, elapsed calculado en cada render
- Checklist: visible después de 30s + typedOnce, actualizado en cada keystroke (rerender de textarea)
- Sin loops, sin time.sleep, sin st.rerun() dentro de widgets
- Auto-score igual que el tsx
"""
from __future__ import annotations
import random, time, math
import streamlit as st

try:
    from core.assessment_engine import evaluate_with_llm, load_exam
    _LLM_OK = True
except ImportError:
    _LLM_OK = False

try:
    from core.state import AppState
    _STATE_OK = True
except ImportError:
    _STATE_OK = False

# ── preguntas inline (igual que baseQuestions del tsx) ──────────────────────
_QUESTIONS = [
    {"id":"bdc-components","category":"BDC Architecture",
     "prompt":{"en":"What are the key software solutions that make up SAP Business Data Cloud, and what role does each one play?","es":"¿Cuáles son las soluciones clave que componen SAP Business Data Cloud y qué rol cumple cada una?"},
     "reference":{"en":"SAP Business Data Cloud integrates: (1) SAP Datasphere for governed data integration, modelling/semantics, and data product consumption; (2) SAP Databricks for scalable data engineering, ML/AI and advanced analytics; (3) SAP Analytics Cloud (SAC) for analytics visualisation and planning; all connected by Foundation Services providing shared identity/security, governance, catalog/metadata and lifecycle capabilities across the platform.","es":"SAP Business Data Cloud integra: (1) SAP Datasphere para integración gobernada, modelado/semántica y consumo de data products; (2) SAP Databricks para data engineering escalable, ML/AI y analítica avanzada; (3) SAP Analytics Cloud (SAC) para visualización y planificación; todo unificado por Foundation Services (identidad/seguridad, gobierno, catálogo/metadata y ciclo de vida) en la plataforma."},
     "checklist":[{"id":"mentions-datasphere","label":{"en":"Mentions Datasphere role","es":"Menciona rol de Datasphere"},"keywords":["datasphere"],"matchMode":"any"},{"id":"mentions-databricks","label":{"en":"Mentions Databricks role","es":"Menciona rol de Databricks"},"keywords":["databricks"],"matchMode":"any"},{"id":"mentions-sac","label":{"en":"Mentions SAC role","es":"Menciona rol de SAC"},"keywords":["analytics cloud","sac"],"matchMode":"any"},{"id":"mentions-foundation","label":{"en":"Mentions Foundation Services","es":"Menciona Foundation Services"},"keywords":["foundation"],"matchMode":"any"}]},
    {"id":"databricks-features","category":"SAP Databricks",
     "prompt":{"en":"What would you say are some of the key features of SAP Databricks (in the context of BDC)?","es":"¿Cuáles son algunas características clave de SAP Databricks (en el contexto de BDC)?"},
     "reference":{"en":"Key features include scalable lakehouse processing, notebook-based development (SQL/Python), ML/AI tooling, governed catalog/lineage and access controls via Unity Catalog, and secure zero-copy sharing via Delta Sharing when integrated in BDC.","es":"Incluye procesamiento escalable tipo lakehouse, desarrollo con notebooks (SQL/Python), tooling de ML/AI, gobierno/catálogo/lineage y controles de acceso vía Unity Catalog, y compartición segura zero-copy vía Delta Sharing en BDC."},
     "checklist":[{"id":"uc","label":{"en":"Unity Catalog governance","es":"Gobierno con Unity Catalog"},"keywords":["unity catalog"],"matchMode":"any"},{"id":"delta","label":{"en":"Delta Sharing / zero-copy","es":"Delta Sharing / zero-copy"},"keywords":["delta","zero-copy","zero copy"],"matchMode":"any"},{"id":"notebooks","label":{"en":"Notebooks (Python/SQL)","es":"Notebooks (Python/SQL)"},"keywords":["notebook","python","sql"],"matchMode":"min","minHits":2}]},
    {"id":"datasphere-role","category":"SAP Datasphere",
     "prompt":{"en":"How does SAP Datasphere fit into SAP Business Data Cloud?","es":"¿Cómo encaja SAP Datasphere dentro de SAP Business Data Cloud?"},
     "reference":{"en":"Datasphere is the governed data integration and semantic modelling layer in BDC. It connects to SAP and non-SAP sources, models and harmonises data, applies governance (spaces, authorisations, lineage), and exposes governed models/data products to consumption (SAC) and innovation (Databricks) via sharing mechanisms.","es":"Datasphere es la capa de integración gobernada y modelado semántico en BDC. Conecta fuentes SAP y no SAP, modela/armoniza, aplica gobierno (spaces, autorizaciones, lineage) y expone modelos/data products a consumo (SAC) e innovación (Databricks) vía mecanismos de sharing."},
     "checklist":[{"id":"integration","label":{"en":"Integration/connectivity","es":"Integración/conectividad"},"keywords":["integrat","connect"],"matchMode":"any"},{"id":"semantic","label":{"en":"Semantic/modeling","es":"Semántica/modelado"},"keywords":["semantic","model"],"matchMode":"any"},{"id":"govern","label":{"en":"Governance","es":"Gobierno"},"keywords":["govern","authoriz","space","lineage"],"matchMode":"any"}]},
    {"id":"sac-role","category":"SAP Analytics Cloud",
     "prompt":{"en":"How does SAP Analytics Cloud fit into SAP Business Data Cloud?","es":"¿Cómo encaja SAP Analytics Cloud dentro de SAP Business Data Cloud?"},
     "reference":{"en":"SAC is the consumption layer for analytics and planning: dashboards/stories, interactive analysis, planning models, and collaboration. In BDC it consumes governed models from Datasphere (often via live connections) and can incorporate predictive/AI insights depending on configuration.","es":"SAC es la capa de consumo para analítica y planificación: dashboards/stories, análisis interactivo, modelos de planificación y colaboración. En BDC consume modelos gobernados de Datasphere (a menudo vía live connections) y puede incorporar insights predictivos/AI según configuración."},
     "checklist":[{"id":"analytics","label":{"en":"Analytics visualisation","es":"Visualización analítica"},"keywords":["dashboard","story","visual"],"matchMode":"any"},{"id":"planning","label":{"en":"Planning","es":"Planificación"},"keywords":["plan"],"matchMode":"any"},{"id":"datasphere-consume","label":{"en":"Consumes Datasphere models","es":"Consume modelos de Datasphere"},"keywords":["datasphere","live"],"matchMode":"any"}]},
    {"id":"intelligent-app-arch","category":"Intelligent Applications",
     "prompt":{"en":"What is an intelligent application and what is its technical architecture?","es":"¿Qué es una aplicación inteligente y cuál es su arquitectura técnica?"},
     "reference":{"en":"An intelligent application is a packaged, domain-focused solution that ships prebuilt assets across layers: (1) Data Products (sources/curated datasets), (2) Data Models/semantics (harmonised models in Datasphere), and (3) Consumption content (dashboards/stories in SAC). It is activated via BDC Cockpit and provisions the required assets and governance.","es":"Una aplicación inteligente es una solución empaquetada orientada a dominio que entrega activos preconstruidos: (1) Data Products (datasets curados), (2) Data Models/semántica (modelos armonizados en Datasphere) y (3) Contenido de consumo (dashboards/stories en SAC). Se activa vía BDC Cockpit y provisiona activos y gobierno."},
     "checklist":[{"id":"layer1","label":{"en":"Mentions Data Products","es":"Menciona Data Products"},"keywords":["data product"],"matchMode":"any"},{"id":"layer2","label":{"en":"Mentions Data Models/semantics","es":"Menciona Data Models/semántica"},"keywords":["model","semantic"],"matchMode":"any"},{"id":"layer3","label":{"en":"Mentions SAC content","es":"Menciona contenido SAC"},"keywords":["sac","story","dashboard"],"matchMode":"any"}]},
    {"id":"intelligent-app-install","category":"Intelligent Applications",
     "prompt":{"en":"What are the steps to install an intelligent application?","es":"¿Cuáles son los pasos para instalar una aplicación inteligente?"},
     "reference":{"en":"In BDC Cockpit: (1) find the intelligent application in the catalog, (2) activate/install it, (3) cockpit provisions required data products, models and SAC content, (4) validate access/governance, then consume/extend in Datasphere/SAC and share to Databricks if needed.","es":"En BDC Cockpit: (1) buscar la app inteligente en el catálogo, (2) activarla/instalarla, (3) el cockpit provisiona data products, modelos y contenido SAC, (4) validar accesos/gobierno, luego consumir/extender en Datasphere/SAC y compartir a Databricks si aplica."},
     "checklist":[{"id":"cockpit","label":{"en":"Uses BDC Cockpit","es":"Usa BDC Cockpit"},"keywords":["cockpit"],"matchMode":"any"},{"id":"activate","label":{"en":"Activate/install","es":"Activar/instalar"},"keywords":["activate","install"],"matchMode":"any"},{"id":"provision","label":{"en":"Provision assets","es":"Provisionar activos"},"keywords":["provision"],"matchMode":"any"}]},
    {"id":"one-domain-model","category":"Domain Semantics",
     "prompt":{"en":"What is one-domain-model and how does SAP Business Data Cloud support it?","es":"¿Qué es one-domain-model y cómo lo soporta SAP Business Data Cloud?"},
     "reference":{"en":"A one-domain model is a harmonised, reusable semantic model for a business domain (e.g., Finance, Sales) that can be consumed consistently across use cases. BDC supports it through curated data products + semantic modelling in Datasphere and consumption in SAC, with governance and sharing to innovation tools like Databricks.","es":"Un one-domain model es un modelo semántico armonizado y reutilizable para un dominio (p. ej., Finanzas, Ventas) consumible de forma consistente. BDC lo soporta vía data products curados + modelado semántico en Datasphere y consumo en SAC, con gobierno y sharing hacia Databricks."},
     "checklist":[{"id":"harmonized","label":{"en":"Harmonised semantic model","es":"Modelo semántico armonizado"},"keywords":["semantic","harmon"],"matchMode":"any"},{"id":"domain","label":{"en":"Domain-focused reuse","es":"Reuso por dominio"},"keywords":["domain","reuse"],"matchMode":"any"},{"id":"datasphere-sac","label":{"en":"Datasphere + SAC consumption","es":"Consumo con Datasphere + SAC"},"keywords":["datasphere","sac"],"matchMode":"all"}]},
    {"id":"bw-why-integrate","category":"BW Integration",
     "prompt":{"en":"For a company running SAP BW for mission-critical analytics, why consider integrating BW with SAP Business Data Cloud?","es":"Para una empresa con SAP BW para analítica crítica, ¿por qué integrar BW con SAP Business Data Cloud?"},
     "reference":{"en":"To protect and extend BW investments by exposing BW content as data products into BDC, enabling governed modelling in Datasphere, combining with non-SAP/third-party data, and accelerating innovation with Databricks (ML/AI) and SAC (analytics/planning) - often following a lift/shift/innovate approach.","es":"Para proteger y ampliar la inversión en BW exponiendo contenido como data products en BDC, habilitando modelado gobernado en Datasphere, combinando con datos no SAP/terceros y acelerando innovación con Databricks (ML/AI) y SAC (analítica/planificación), típicamente con enfoque lift/shift/innovate."},
     "checklist":[{"id":"protect","label":{"en":"Protect BW investments","es":"Proteger inversión BW"},"keywords":["invest"],"matchMode":"any"},{"id":"data-products","label":{"en":"Expose as data products","es":"Exponer como data products"},"keywords":["data product"],"matchMode":"any"},{"id":"innovate","label":{"en":"Innovation (Databricks/SAC)","es":"Innovación (Databricks/SAC)"},"keywords":["databricks","sac"],"matchMode":"min","minHits":1}]},
    {"id":"bw-prereq-hana","category":"BW Integration",
     "prompt":{"en":"MidCorp runs SAP BW 7.5 on a non-HANA database. Are we ready to integrate with BDC? If not, what first?","es":"MidCorp corre SAP BW 7.5 sobre una base no-HANA. ¿Está listo para integrar con BDC? Si no, ¿qué debe hacer primero?"},
     "reference":{"en":"No. BW must run on SAP HANA (BW/4HANA or BW 7.5 on HANA) to integrate via BW data products. First migrate/convert BW to HANA, then enable data product generation/subscriptions.","es":"No. BW debe correr sobre SAP HANA (BW/4HANA o BW 7.5 on HANA) para integrar vía data products. Primero migrar/convertir a HANA y luego habilitar generación/suscripción de data products."},
     "checklist":[{"id":"not-ready","label":{"en":"States not ready","es":"Indica que no está listo"},"keywords":["no","not"],"matchMode":"any"},{"id":"hana","label":{"en":"Requires HANA","es":"Requiere HANA"},"keywords":["hana"],"matchMode":"any"},{"id":"migrate","label":{"en":"Mentions migration/conversion","es":"Menciona migración/conversión"},"keywords":["migrat","convert"],"matchMode":"any"}]},
    {"id":"bw-infoproviders","category":"BW Integration",
     "prompt":{"en":"Which SAP BW InfoProviders can be used to generate data products?","es":"¿Qué InfoProviders de SAP BW se pueden usar para generar data products?"},
     "reference":{"en":"Commonly: ADSOs and CompositeProviders (and depending on release/config, other BW objects that can be exposed). A safe answer: ADSOs + CompositeProviders are the typical sources for BW data products.","es":"Comúnmente: ADSOs y CompositeProviders (y según release/config, otros objetos exponibles). Respuesta segura: ADSOs + CompositeProviders como fuentes típicas."},
     "checklist":[{"id":"adso","label":{"en":"Mentions ADSO","es":"Menciona ADSO"},"keywords":["adso"],"matchMode":"any"},{"id":"composite","label":{"en":"Mentions CompositeProvider","es":"Menciona CompositeProvider"},"keywords":["composite"],"matchMode":"any"}]},
    {"id":"bw-keep-up-to-date","category":"BW Integration",
     "prompt":{"en":"How do we ensure BW-generated data products are kept up-to-date with the latest data?","es":"¿Cómo aseguramos que los data products generados desde BW estén actualizados con los últimos datos?"},
     "reference":{"en":"BW remains the system of record for the underlying data. Keep BW loads current (e.g., process chains). Data products reflect the latest BW data once the BW targets are refreshed; also ensure data product subscriptions/provisioning are active and scheduled/monitored as needed.","es":"BW es el sistema de registro de los datos subyacentes. Mantener cargas BW al día (p. ej., process chains). Los data products reflejan los datos una vez refrescados los targets en BW; asegurar suscripciones/provisioning activos y monitoreo/agenda según corresponda."},
     "checklist":[{"id":"system-of-record","label":{"en":"BW is system of record","es":"BW es sistema de registro"},"keywords":["system of record","record"],"matchMode":"any"},{"id":"process-chains","label":{"en":"Process chains / BW loads","es":"Process chains / cargas BW"},"keywords":["process chain","load"],"matchMode":"any"},{"id":"monitor","label":{"en":"Monitoring/scheduling","es":"Monitoreo/agenda"},"keywords":["monitor","schedule"],"matchMode":"any"}]},
    {"id":"delta-sharing","category":"Protocols",
     "prompt":{"en":"What is Delta Sharing and how is it used in SAP Business Data Cloud?","es":"¿Qué es Delta Sharing y cómo se usa en SAP Business Data Cloud?"},
     "reference":{"en":"Delta Sharing is an open protocol for secure, governed, zero-copy data sharing. In BDC it enables data products to be shared between Datasphere and SAP Databricks without duplicating physical data, while enforcing access controls and governance.","es":"Delta Sharing es un protocolo abierto para compartir datos de forma segura, gobernada y zero-copy. En BDC permite compartir data products entre Datasphere y SAP Databricks sin duplicar datos físicos, respetando controles de acceso y gobierno."},
     "checklist":[{"id":"zero-copy","label":{"en":"Zero-copy","es":"Zero-copy"},"keywords":["zero-copy","zero copy"],"matchMode":"any"},{"id":"secure-governed","label":{"en":"Secure + governed protocol","es":"Protocolo seguro y gobernado"},"keywords":["secure","govern"],"matchMode":"min","minHits":1},{"id":"datasphere-databricks","label":{"en":"Used between Datasphere and Databricks","es":"Se usa entre Datasphere y Databricks"},"keywords":["datasphere","databricks"],"matchMode":"all"}]},
    {"id":"install-data-product","category":"Data Products",
     "prompt":{"en":"Having identified a data product in the BDC catalogue, what are the steps to install it so you can use it in a SAP Datasphere data model?","es":"Habiendo identificado un data product en el catálogo BDC, ¿cuáles son los pasos para instalarlo y usarlo en un modelo de SAP Datasphere?"},
     "reference":{"en":"In BDC Cockpit: locate the data product, activate/provision it, then it becomes available in Datasphere (in the relevant space) to consume/model/integrate. Validate authorisations and then build models on top.","es":"En BDC Cockpit: ubicar el data product, activarlo/provisionarlo y luego queda disponible en Datasphere (en el space correspondiente) para consumir/modelar/integrar. Validar autorizaciones y construir modelos."},
     "checklist":[{"id":"cockpit","label":{"en":"BDC Cockpit","es":"BDC Cockpit"},"keywords":["cockpit"],"matchMode":"any"},{"id":"activate-provision","label":{"en":"Activate/provision","es":"Activar/provisionar"},"keywords":["activate","provision"],"matchMode":"min","minHits":1},{"id":"available-in-ds","label":{"en":"Available in Datasphere","es":"Disponible en Datasphere"},"keywords":["datasphere"],"matchMode":"any"}]},
    {"id":"share-to-databricks","category":"Data Products",
     "prompt":{"en":"Describe the steps to share a data product with SAP Databricks so you can begin developing a machine learning model.","es":"Describí los pasos para compartir un data product con SAP Databricks para empezar a desarrollar un modelo de ML."},
     "reference":{"en":"Activate/provision the data product in BDC Cockpit, share it to Databricks via Delta Sharing so it appears in Unity Catalog, then use Databricks notebooks to engineer features and train models.","es":"Activar/provisionar el data product en BDC Cockpit, compartirlo hacia Databricks vía Delta Sharing para que aparezca en Unity Catalog, y luego usar notebooks para feature engineering y entrenamiento."},
     "checklist":[{"id":"cockpit","label":{"en":"Activate in Cockpit","es":"Activar en Cockpit"},"keywords":["cockpit","activate"],"matchMode":"min","minHits":1},{"id":"delta","label":{"en":"Delta Sharing","es":"Delta Sharing"},"keywords":["delta"],"matchMode":"any"},{"id":"unity","label":{"en":"Unity Catalog","es":"Unity Catalog"},"keywords":["unity catalog"],"matchMode":"any"}]},
    {"id":"share-results-back","category":"Databricks -> BDC",
     "prompt":{"en":"What are the steps to share the results from your ML model in SAP Databricks back to SAP Business Data Cloud?","es":"¿Cuáles son los pasos para compartir resultados de un modelo ML en Databricks de vuelta a SAP Business Data Cloud?"},
     "reference":{"en":"Persist outputs as Delta tables in Databricks, register/govern them in Unity Catalog, then share them back via Delta Sharing so they can be consumed in Datasphere (and optionally surfaced in SAC).","es":"Persistir outputs como tablas Delta en Databricks, registrarlas/gobernarlas en Unity Catalog y compartirlas de vuelta vía Delta Sharing para consumirlas en Datasphere (y opcionalmente exponer en SAC)."},
     "checklist":[{"id":"delta-tables","label":{"en":"Persist as Delta tables","es":"Persistir como tablas Delta"},"keywords":["delta table","delta tables"],"matchMode":"any"},{"id":"unity","label":{"en":"Register in Unity Catalog","es":"Registrar en Unity Catalog"},"keywords":["unity catalog"],"matchMode":"any"},{"id":"share-back","label":{"en":"Share back to Datasphere/BDC","es":"Compartir a Datasphere/BDC"},"keywords":["share","datasphere"],"matchMode":"min","minHits":1}]},
    {"id":"bdc-cockpit","category":"BDC Operations",
     "prompt":{"en":"What is the SAP Business Data Cloud Cockpit and who would use it?","es":"¿Qué es el SAP Business Data Cloud Cockpit y quién lo usa?"},
     "reference":{"en":"BDC Cockpit is the central control plane to discover, activate, provision and manage data products and intelligent applications and their sharing/tenants. It is typically used by platform admins, data architects, and implementation consultants (not business consumers).","es":"BDC Cockpit es el plano de control central para descubrir, activar, provisionar y administrar data products y aplicaciones inteligentes y su sharing/tenants. Lo usan admins, arquitectos de datos y consultores (no usuarios de negocio consumidores)."},
     "checklist":[{"id":"control-plane","label":{"en":"Control plane","es":"Plano de control"},"keywords":["control plane"],"matchMode":"any"},{"id":"discover-activate","label":{"en":"Discover/activate/provision","es":"Descubrir/activar/provisionar"},"keywords":["discover","activate","provision"],"matchMode":"min","minHits":2},{"id":"persona","label":{"en":"Admins/architects (not business users)","es":"Admins/arquitectos (no usuarios negocio)"},"keywords":["admin","architect","not business"],"matchMode":"any"}]},
    {"id":"foundation-services","category":"Foundation Services",
     "prompt":{"en":"What is the role of Foundation Services in SAP Business Data Cloud?","es":"¿Cuál es el rol de Foundation Services en SAP Business Data Cloud?"},
     "reference":{"en":"Foundation Services provide shared platform capabilities across BDC solutions, including identity and access management, security, governance, catalog/metadata, and lifecycle/tenant management to enable consistent integration and controls.","es":"Foundation Services provee capacidades compartidas en BDC: identidad y accesos, seguridad, gobierno, catálogo/metadata y gestión de ciclo de vida/tenant para habilitar integración y controles consistentes."},
     "checklist":[{"id":"iam","label":{"en":"Identity/access","es":"Identidad/accesos"},"keywords":["identity","access"],"matchMode":"any"},{"id":"security","label":{"en":"Security","es":"Seguridad"},"keywords":["security"],"matchMode":"any"},{"id":"governance","label":{"en":"Governance/metadata/lifecycle","es":"Gobierno/metadata/ciclo de vida"},"keywords":["govern","metadata","lifecycle"],"matchMode":"min","minHits":1}]},
    {"id":"ord-protocol","category":"Protocols",
     "prompt":{"en":"What is the ORD protocol and what part does it play in SAP Business Data Cloud?","es":"¿Qué es el protocolo ORD y qué rol cumple en SAP Business Data Cloud?"},
     "reference":{"en":"ORD (Open Resource Discovery) standardises how resources like APIs and data products are described and discovered (metadata/catalog). It supports interoperability and governance by enabling consistent discovery; it does not move data.","es":"ORD (Open Resource Discovery) estandariza cómo se describen y descubren recursos como APIs y data products (metadata/catálogo). Soporta interoperabilidad y gobierno; no transporta datos."},
     "checklist":[{"id":"name","label":{"en":"Open Resource Discovery","es":"Open Resource Discovery"},"keywords":["open resource discovery","ord"],"matchMode":"any"},{"id":"metadata","label":{"en":"Metadata/discovery","es":"Metadata/descubrimiento"},"keywords":["metadata","discover"],"matchMode":"any"},{"id":"not-data-move","label":{"en":"Not for moving data","es":"No mueve datos"},"keywords":["not","move data","doesn't"],"matchMode":"any"}]},
    {"id":"storage-tech","category":"Storage",
     "prompt":{"en":"What is the physical storage technology used in SAP Business Data Cloud?","es":"¿Cuál es la tecnología de almacenamiento físico usada en SAP Business Data Cloud?"},
     "reference":{"en":"BDC leverages SAP HANA for in-memory persistence and a scalable data lake storage option for larger volumes/cost-efficient storage, depending on workload and architecture.","es":"BDC utiliza SAP HANA para persistencia in-memory y una opción de data lake escalable para mayores volúmenes/menor costo, según workload y arquitectura."},
     "checklist":[{"id":"hana","label":{"en":"Mentions HANA","es":"Menciona HANA"},"keywords":["hana"],"matchMode":"any"},{"id":"datalake","label":{"en":"Mentions data lake","es":"Menciona data lake"},"keywords":["data lake","lake"],"matchMode":"any"},{"id":"workload","label":{"en":"Explains why/when","es":"Explica por qué/cuándo"},"keywords":["volume","cost","scal"],"matchMode":"any"}]},
    {"id":"third-party-integration","category":"Integration",
     "prompt":{"en":"How can a company integrate third-party data with its SAP data using SAP Business Data Cloud?","es":"¿Cómo puede una empresa integrar datos de terceros con sus datos SAP usando SAP Business Data Cloud?"},
     "reference":{"en":"Use Datasphere connectivity/integration to connect to non-SAP sources (databases, files, cloud services), ingest or virtualise data, harmonise it with SAP data models, govern access, and then consume in SAC or share to Databricks for innovation.","es":"Usar conectividad/integración de Datasphere para conectar fuentes no SAP (DBs, archivos, servicios cloud), ingerir o virtualizar, armonizar con modelos SAP, gobernar accesos y consumir en SAC o compartir a Databricks para innovación."},
     "checklist":[{"id":"connect","label":{"en":"Connections/connectors","es":"Conexiones/conectores"},"keywords":["connect","connector"],"matchMode":"any"},{"id":"harmonise","label":{"en":"Harmonise with SAP","es":"Armonizar con SAP"},"keywords":["harmon","integrat"],"matchMode":"any"},{"id":"consume-share","label":{"en":"Consume in SAC or share to Databricks","es":"Consumir en SAC o compartir a Databricks"},"keywords":["sac","databricks"],"matchMode":"min","minHits":1}]},
]

CHECKLIST_DELAY = 30
PROCEDURE_CUES = ["step","steps","first","then","next","finally","after","before","1.","2.","3.","enable","enables","used","use","share","shared","activate","provision","install","configure","publish","consume","between","without","enforcing"]
BUCKET_WEIGHTS = {"BDC":0.4,"BW":0.18,"DBX":0.16,"DSP":0.16,"SAC":0.1}


# ── helpers ─────────────────────────────────────────────────────────────────
def _k(k): return f"_assess_{k}"

def _shuffle(qs, seed):
    a = list(qs); s = seed
    def rnd():
        nonlocal s; s = (s*9301+49297)%233280; return s/233280
    for i in range(len(a)-1, 0, -1):
        j = int(rnd()*(i+1)); a[i],a[j]=a[j],a[i]
    return a

def _match(text, item):
    hits = sum(1 for k in item["keywords"] if k in text)
    m = item.get("matchMode","any")
    if m == "all":   return hits == len(item["keywords"])
    if m == "min":   return hits >= item.get("minHits",2)
    return hits >= 1

def _auto_checklist(text, checklist):
    t = text.lower()
    return {it["id"]: _match(t, it) for it in checklist}

def _coverage(text, checklist):
    auto = _auto_checklist(text, checklist)
    matched = sum(1 for v in auto.values() if v)
    total = max(len(checklist), 1)
    return matched, total, matched/total

def _to_score(cov):
    if cov >= 0.95: return 5
    if cov >= 0.8:  return 4
    if cov >= 0.6:  return 3
    if cov >= 0.4:  return 2
    if cov >= 0.2:  return 1
    return 0

def _suggested(text, checklist):
    matched, total, cov = _coverage(text, checklist)
    concept = arch = _to_score(cov)
    t = text.lower()
    cue_hits = sum(1 for c in PROCEDURE_CUES if c in t)
    has_proc = cue_hits >= 2 or ("in bdc" in t and cue_hits >= 1)
    proc = _to_score(cov)
    if has_proc and proc < 3: proc = 3
    if cov >= 0.95 and has_proc: proc = 5
    return concept, arch, proc, matched, total, cov

def _bucket(category):
    c = (category or "").lower()
    if "bw" in c: return "BW"
    if "databricks" in c: return "DBX"
    if "datasphere" in c: return "DSP"
    if "analytics cloud" in c or "sac" in c: return "SAC"
    return "BDC"

def _score_color(value: float, max_val: float = 5.0) -> str:
    """Return a color based on value/max_val ratio. Works for 0-5 scores or 0-100 pct (pass max_val=100)."""
    ratio = value / max_val if max_val else 0
    if ratio >= 0.8:  return "#22c55e"   # green
    if ratio >= 0.4:  return "#C9960A"   # amber
    return "#111"                         # dark

def _ensure_init():
    if _k("order") not in st.session_state:
        seed = random.randint(0,99999)
        st.session_state[_k("seed")]     = seed
        st.session_state[_k("order")]    = _shuffle(_QUESTIONS, seed)
        st.session_state[_k("idx")]      = 0
        st.session_state[_k("phase")]    = "intro"
        st.session_state[_k("finished")] = False
        st.session_state[_k("lang")]     = "en"
        st.session_state[_k("sec_per_q")]= 90
        st.session_state[_k("answers")] = {}
        st.session_state[_k("scores")]  = {}
        st.session_state[_k("show_ref")]= {}
        st.session_state[_k("timed_out")]= {}
        st.session_state[_k("manual_score")]= {}
        st.session_state[_k("q_start")] = None
        st.session_state[_k("typed_once")]= {}
        st.session_state[_k("llm_fb")]  = {}

def _reset_run():
    seed = random.randint(0,99999)
    st.session_state[_k("seed")]      = seed
    st.session_state[_k("order")]     = _shuffle(_QUESTIONS, seed)
    st.session_state[_k("idx")]       = 0
    st.session_state[_k("phase")]     = "intro"
    st.session_state[_k("finished")]  = False
    st.session_state[_k("answers")]   = {}
    st.session_state[_k("scores")]    = {}
    st.session_state[_k("show_ref")]  = {}
    st.session_state[_k("timed_out")] = {}
    st.session_state[_k("manual_score")]= {}
    st.session_state[_k("q_start")]   = None
    st.session_state[_k("typed_once")]= {}
    st.session_state[_k("llm_fb")]    = {}

def _go_to_q(idx):
    st.session_state[_k("idx")]     = idx
    st.session_state[_k("q_start")] = time.time()


# ── Danwor design system ─────────────────────────────────────────────────────
_DS = """
<style>
  .dw-assess-wrap * { font-family: "Source Sans", sans-serif !important; }
  .dw-assess-wrap p,
  .dw-assess-wrap li,
  .dw-assess-wrap div,
  .dw-assess-wrap span,
  .dw-assess-wrap label { font-size: 14px !important; }
  .dw-card {
    background: #fff;
    border: 1px solid #e5e5e5;
    border-radius: 12px;
    padding: 20px 22px;
    margin-bottom: 14px;
  }
  .dw-card-header {
    font-size: 11px !important;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #999;
    margin-bottom: 10px;
  }
  .dw-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    border: 1px solid #e5e5e5;
    font-size: 11px !important;
    background: #fafafa;
    color: #555;
    margin-right: 6px;
  }
  .dw-score-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid #f0f0f0;
  }
  .dw-progress-track {
    background: #f0f0f0;
    border-radius: 999px;
    height: 6px;
    margin-top: 4px;
  }
  .dw-progress-fill {
    background: #C9960A;
    border-radius: 999px;
    height: 6px;
    transition: width .3s;
  }
  .dw-ref-box {
    background: #fafafa;
    border: 1px solid #e5e5e5;
    border-radius: 10px;
    padding: 14px;
    line-height: 1.6;
    margin-top: 10px;
  }
  .dw-check-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    border-bottom: 1px solid #f5f5f5;
  }
  textarea {
    border-radius: 8px !important;
    background: #fff !important;
    font-size: 14px !important;
  }
  .dw-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-weight: 400;
    padding: 0.25rem 0.75rem;
    border-radius: 0.5rem;
    min-height: 2.5rem;
    margin: 0px;
    line-height: 1.6;
    font-size: 14px;
    font-family: "Source Sans", sans-serif;
    color: inherit;
    width: 100%;
    user-select: none;
    background-color: rgb(255, 255, 255);
    border: 1px solid rgba(49, 51, 63, 0.2);
    cursor: pointer;
    text-decoration: none;
    box-sizing: border-box;
  }
  .dw-btn:hover { border-color: rgba(49, 51, 63, 0.5); background-color: #fafafa; }
  .dw-btn-primary {
    background-color: #111;
    color: #fff;
    border-color: #111;
    font-weight: 600;
  }
  .dw-btn-primary:hover { background-color: #333; border-color: #333; }
  .dw-assess-wrap div[data-testid="stButton"] button {
    border-radius: 0.5rem !important;
    font-size: 14px !important;
    background-color: rgb(255, 255, 255) !important;
    border: 1px solid rgba(49, 51, 63, 0.2) !important;
    color: inherit !important;
    font-weight: 400 !important;
  }
  .dw-assess-wrap div[data-testid="stButton"] button:hover {
    border-color: rgba(49, 51, 63, 0.5) !important;
    background-color: #fafafa !important;
  }
  .dw-assess-wrap div[data-testid="stButton"] button[kind="primary"] {
    background-color: #111 !important;
    color: #fff !important;
    border-color: #111 !important;
    font-weight: 600 !important;
  }
  .dw-assess-wrap div[data-testid="stButton"] button[kind="primary"]:hover {
    background-color: #333 !important;
    border-color: #333 !important;
  }
</style>
"""

# ── UI strings ───────────────────────────────────────────────────────────────
_UI = {
    "en": dict(
        language="Language", mode="Mode", exam="Exam", practice="Practice",
        timer="Timer", secPerQ="Sec/Q", newRun="New random run", reset="Reset",
        item="Item", category="Category", timeLeft="Time left",
        yourAnswer="Your answer (write what you'd say)",
        hintChecklist="Hint checklist", scoring="Scoring (0–5 each)",
        concept="Concept", architecture="Architecture", procedure="Procedure",
        finalAvg="Final (avg)", showRef="Show reference", hideRef="Hide reference",
        prev="Prev", next="Next", finishQuiz="Finish Quiz", backToQuiz="Back to quiz",
        finishTitle="Quiz completed", finishStats="Summary",
        finishSummary="Answered {a} out of {t}. Timed out on {x} question(s).",
        timedOut="Timed out", noAnswer="(No answer)",
        timeUp="Time is up. Inputs are locked. Review the reference answer and proceed.",
        start="Start", chooseLanguage="Choose your language",
        examModeHint="In exam mode: inputs lock at 0 and reference is shown automatically.",
        checklistDelayedHint="Checklist will appear after {s}s once you start typing.",
        coverageLabel="Coverage", suggestedScoreLabel="Suggested score",
        autoNote="Auto-detected by keywords. Scoring auto-fills unless manually overridden.",
        finishWeightedNote="Weighted scoring (Pass mark: 60%). Borderline range: 55–59%.",
        referenceLabel="Reference", finishAnswered="Answered: {a} / {t}",
        finishTimedOut="Timed out: {x}",
        introConversationEnds="Conversation will end among the 30 minutes mark.",
        introDescriptionTitle="Description:",
        introDescriptionBody="You will meet Riley Bennet, Principal Consultant from MidCorp, who will lead a team through SAP Business Data Cloud implementation. Demonstrate that you have the data knowledge of SAP Business Data Cloud required to join the implementation team. Always provide thorough answers with depth to ensure a good score.",
        introScoringTitle="Scoring Guide:",
        introPassRule="60% to pass, where technical knowledge expected is break-down:",
        introWeights=["SAP BDC: 40%","SAP BW Integration: 18%","Databricks: 16%","Datasphere: 16%","SAP SAC: 10%"],
        introTimerLabel="Timer:", introBeginExam="Begin Exam", introBack="Back",
        introTitle="Exam introduction",
        llmFeedback="AI Feedback", getLlm="Get AI feedback",
    ),
    "es": dict(
        language="Idioma", mode="Modo", exam="Examen", practice="Práctica",
        timer="Temporizador", secPerQ="(s)", newRun="Nueva corrida aleatoria", reset="Reiniciar",
        item="Ítem", category="Categoría", timeLeft="Tiempo",
        yourAnswer="Tu respuesta (lo que dirías)",
        hintChecklist="Checklist de pistas", scoring="Scoring (0–5 c/u)",
        concept="Concepto", architecture="Arquitectura", procedure="Procedimiento",
        finalAvg="Final (prom)", showRef="Ver referencia", hideRef="Ocultar referencia",
        prev="Anterior", next="Siguiente", finishQuiz="Finalizar quiz", backToQuiz="Volver al quiz",
        finishTitle="Quiz finalizado", finishStats="Resumen",
        finishSummary="Respondiste {a} de {t}. Se terminó el tiempo en {x} pregunta(s).",
        timedOut="Sin tiempo", noAnswer="(Sin respuesta)",
        timeUp="Se acabó el tiempo. Los inputs están bloqueados. Revisá la respuesta de referencia.",
        start="Empezar", chooseLanguage="Elegí el idioma",
        examModeHint="En modo examen: los inputs se bloquean al llegar a 0.",
        checklistDelayedHint="El checklist aparece en {s}s una vez que empieces a escribir.",
        coverageLabel="Cobertura", suggestedScoreLabel="Score sugerido",
        autoNote="Auto-detectado por keywords. El scoring se auto-completa salvo override manual.",
        finishWeightedNote="Scoring ponderado (mínimo 60%). Rango borderline: 55–59%.",
        referenceLabel="Referencia", finishAnswered="Respondidas: {a} / {t}",
        finishTimedOut="Sin tiempo: {x}",
        introConversationEnds="La conversación finalizará cuando se cumplan los 30 minutos.",
        introDescriptionTitle="Descripción:",
        introDescriptionBody="Vas a conocer a Riley Bennet, Principal Consultant de MidCorp, quien liderará un equipo en una implementación de SAP Business Data Cloud. Demostrá que tenés el conocimiento necesario para sumarte al equipo.",
        introScoringTitle="Guía de scoring:",
        introPassRule="Se aprueba con 60%, con el siguiente peso por conocimiento técnico:",
        introWeights=["SAP BDC: 40%","Integración SAP BW: 18%","Databricks: 16%","Datasphere: 16%","SAP SAC: 10%"],
        introTimerLabel="Temporizador:", introBeginExam="Comenzar examen", introBack="Volver",
        introTitle="Introducción del examen",
        llmFeedback="Feedback IA", getLlm="Obtener feedback IA",
    ),
}

# ── screens ──────────────────────────────────────────────────────────────────
def _render_intro():
    ss = st.session_state
    lang = ss.get(_k("lang"), "en")
    t    = _UI[lang]
    st.markdown(_DS, unsafe_allow_html=True)

    # Header
    st.markdown(
        "<div style='margin-bottom:20px;margin-top:4px'>"
        "<div style='font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;"
        "color:#999;margin-bottom:6px'>Danwor IT Analytics · Certification</div>"
        "<div style='font-size:24px;font-weight:800;letter-spacing:-.5px;margin:0;line-height:1.2'>"
        "SAP Business Data Cloud</div>"
        "<div style='font-size:13px;color:#888;margin-top:4px'>Exam introduction</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    col_info, col_score = st.columns([3, 2])

    with col_info:
        st.markdown(
            f"<div class='dw-card'>"
            f"<div class='dw-card-header'>{t['introDescriptionTitle']}</div>"
            f"<div style='font-size:13px;line-height:1.65;color:#333'>{t['introDescriptionBody']}</div>"
            f"<div style='margin-top:14px;padding-top:12px;border-top:1px solid #f0f0f0;"
            f"font-size:12px;color:#888'>{t['introConversationEnds']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        with st.expander("⚙️ Settings", expanded=False):
            sec = st.slider("Seconds per question", 30, 300,
                            ss.get(_k("sec_per_q"), 90), 15,
                            key="assess_sec_slider")
            ss[_k("sec_per_q")] = sec
        if st.button(t["introBeginExam"], key="intro_begin_exam", type="primary"):
            ss[_k("phase")]      = "quiz"
            ss[_k("idx")]        = 0
            ss[_k("finished")]   = False
            ss[_k("q_start")]    = time.time()
            ss[_k("typed_once")] = {}
            st.rerun()

    with col_score:
        weights_html = "".join(
            f"<div style='display:flex;justify-content:space-between;padding:6px 0;"
            f"border-bottom:1px solid #f5f5f5;font-size:13px'>"
            f"<span>{w.split(':')[0]}</span>"
            f"<span style='font-weight:700;color:#111'>{w.split(':')[1].strip()}</span></div>"
            for w in t["introWeights"]
        )
        st.markdown(
            f"<div class='dw-card'>"
            f"<div class='dw-card-header'>{t['introScoringTitle']}</div>"
            f"<div style='font-size:12px;color:#666;margin-bottom:10px'>{t['introPassRule']}</div>"
            f"{weights_html}"
            f"<div style='margin-top:14px;padding:10px;background:#f5f5f5;border-radius:8px;"
            f"font-size:12px;color:#555;text-align:center'>"
            f"Pass mark: <strong>60%</strong> &nbsp;·&nbsp; Borderline: 55–59%</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_quiz():
    ss   = st.session_state
    lang = ss.get(_k("lang"), "en")
    t    = _UI[lang]
    order     = ss[_k("order")]
    idx       = ss[_k("idx")]
    q         = order[idx]
    qid       = q["id"]
    answers   = ss[_k("answers")]
    scores    = ss[_k("scores")]
    show_ref  = ss[_k("show_ref")]
    llm_fb    = ss[_k("llm_fb")]
    answer    = answers.get(qid, "")

    st.markdown(_DS, unsafe_allow_html=True)

    # ── header (mismo estilo que intro) ──
    answered_n = sum(1 for qq in order if answers.get(qq["id"],"").strip())
    pct = int(answered_n / len(order) * 100)
    st.markdown(
        f"<div style='margin-bottom:12px'>"
        f"<div style='font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#999;margin-bottom:4px'>SAP BDC Certification</div>"
        f"<div style='font-size:24px;font-weight:800;letter-spacing:-.5px;margin:0;line-height:1.2;color:#111'>SAP Business Data Cloud</div>"
        f"</div>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px'>"
        f"<span class='dw-pill'>{t['category']}: {q['category']}</span>"
        f"<div style='font-size:12px;color:#888'>{t['item']} {idx+1} / {len(order)}</div></div>"
        f"<div class='dw-progress-track'><div class='dw-progress-fill' style='width:{pct}%'></div></div>"
        f"<div style='font-size:11px;color:#999;margin-top:3px;margin-bottom:14px'>{answered_n}/{len(order)} answered</div>",
        unsafe_allow_html=True,
    )

    # ── question card ──
    st.markdown(
        f"<div class='dw-card'>"
        f"<div class='dw-card-header'>Question {idx+1}</div>"
        f"<div style='font-size:16px;font-weight:700;line-height:1.25;color:#111'>{q['prompt'][lang]}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── two columns: left=answer, right=checklist+scoring stacked ──
    ans_col, right_col = st.columns([3, 2])

    with ans_col:
        def _on_answer_change():
            val = ss.get(f"ans_{qid}", "")
            answers[qid] = val
            if val.strip():
                c, a_, p, *_ = _suggested(val, q["checklist"])
                scores[qid] = {"concept": c, "architecture": a_, "procedure": p}
            else:
                scores[qid] = {"concept": 0, "architecture": 0, "procedure": 0}

        st.text_area(
            t["yourAnswer"],
            value=answer,
            height=340,
            key=f"ans_{qid}",
            label_visibility="collapsed",
            placeholder=t["yourAnswer"],
            on_change=_on_answer_change,
        )
        # Also sync on every render in case value was set without triggering callback
        current_val = ss.get(f"ans_{qid}", answer)
        if current_val != answer:
            answers[qid] = current_val
            if current_val.strip():
                c, a_, p, *_ = _suggested(current_val, q["checklist"])
                scores[qid] = {"concept": c, "architecture": a_, "procedure": p}

    with right_col:
        # Checklist card — read fresh from widget state so it updates on focus-out
        answer = ss.get(f"ans_{qid}", answers.get(qid, ""))
        if not answer.strip():
            checklist_html = (
                f"<div style='color:#bbb;font-size:13px;padding:20px 0;text-align:center'>"
                f"{'Hints will become visible only after you\'ve completed your answer and focused out the text area.' if lang=='en' else 'Las pistas se muestran al completar la respuesta y salir del campo de texto.'}"
                f"</div>"
            )
            coverage_html = ""
        else:
            auto = _auto_checklist(answer, q["checklist"])
            matched, total, cov = _coverage(answer, q["checklist"])
            checklist_html = "".join(
                "<div class='dw-check-item'>"
                + ("<span style='font-size:15px'>✅</span>" if auto.get(it["id"]) else "<span style='font-size:15px'>⬜</span>")
                + ("<span style='font-size:14px;color:#111'>" if auto.get(it["id"]) else "<span style='font-size:14px;color:#888'>")
                + it["label"][lang] + "</span></div>"
                for it in q["checklist"]
            )
            coverage_html = (
                f"<div style='margin-top:8px;padding:8px 10px;background:#f5f5f5;border-radius:8px;"
                f"font-size:12px;color:#666;display:flex;justify-content:space-between'>"
                f"<span>{t['coverageLabel']}: {matched}/{total}</span>"
                f"<span>{t['suggestedScoreLabel']}: {round(cov*5)}/5</span></div>"
            )
        st.markdown(
            f"<div class='dw-card' style='padding:16px;background:#fff'>"
            f"<div class='dw-card-header'>{t['hintChecklist']}</div>"
            f"{checklist_html}{coverage_html}</div>",
            unsafe_allow_html=True,
        )

        # Scoring card — read-only, auto-calculated
        cur = scores.get(qid, {"concept":0,"architecture":0,"procedure":0})
        avg_score = (cur["concept"]+cur["architecture"]+cur["procedure"])/3
        score_rows = "".join(
            f"<div class='dw-score-row'>"
            f"<span style='font-size:14px;font-weight:700;color:#111'>{t[dim]}</span>"
            f"<div style='display:flex;align-items:center;gap:6px'>"
            f"<div style='width:60px;background:#f0f0f0;border-radius:99px;height:5px'>"
            f"<div style='width:{int(cur.get(dim,0)/5*100)}%;background:{_score_color(cur.get(dim,0))};border-radius:99px;height:5px'></div></div>"
            f"<span style='font-size:14px;font-weight:700;min-width:24px;text-align:right'>{cur.get(dim,0)}/5</span>"
            f"</div></div>"
            for dim in ["concept","architecture","procedure"]
        )
        st.markdown(
            f"<div class='dw-card' style='margin-top:10px;padding:16px;background:#fff'>"
            f"<div class='dw-card-header'>{t['scoring']} &nbsp;·&nbsp; "
            f"<span style='color:#111;font-weight:700'>{t['finalAvg']}: {avg_score:.1f}/5</span></div>"
            f"{score_rows}</div>",
            unsafe_allow_html=True,
        )

    # ── LLM feedback ──
    if answer.strip() and _LLM_OK:
        if not llm_fb.get(qid):
            if st.button(t["getLlm"], key=f"llm_{qid}"):
                with st.spinner("Evaluating..."):
                    fb = evaluate_with_llm(q, answer, lang=lang)
                llm_fb[qid] = fb or "No response."
                st.rerun()
        if llm_fb.get(qid):
            st.markdown(
                f"<div class='dw-ref-box' style='border-color:#d1fae5;background:#f0fdf4'>"
                f"<div style='font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;"
                f"color:#666;margin-bottom:8px'>{t['llmFeedback']}</div>"
                f"<div style='font-size:14px;line-height:1.65'>{llm_fb[qid]}</div></div>",
                unsafe_allow_html=True,
            )

    # ── reference + navigation — single row: [Prev] [Show reference] [Next] ──
    if show_ref.get(qid):
        st.markdown(
            f"<div class='dw-ref-box'>"
            f"<div style='font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;"
            f"color:#999;margin-bottom:8px'>{t['referenceLabel']}</div>"
            f"<div style='font-size:14px;line-height:1.65'>{q['reference'][lang]}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)
    is_last = idx >= len(order)-1
    nav_l, nav_mid, nav_r = st.columns([1, 2, 1])
    with nav_l:
        if st.button(f"◀ {t['prev']}", disabled=(idx==0), use_container_width=True):
            _go_to_q(idx-1); st.rerun()
    with nav_mid:
        if st.button(t["hideRef"] if show_ref.get(qid) else t["showRef"],
                     key=f"ref_{qid}", use_container_width=True):
            show_ref[qid] = not show_ref.get(qid, False)
            st.rerun()
    with nav_r:
        if st.button(t["finishQuiz"] if is_last else f"{t['next']} ▶",
                     use_container_width=True, type="primary"):
            if is_last:
                ss[_k("finished")] = True
            else:
                _go_to_q(idx+1)
            st.rerun()


def _render_finished():
    ss   = st.session_state
    lang = ss.get(_k("lang"), "en")
    t    = _UI[lang]
    order     = ss[_k("order")]
    answers   = ss[_k("answers")]
    scores    = ss[_k("scores")]

    answered_n = sum(1 for qq in order if answers.get(qq["id"],"").strip())
    total_n    = len(order)

    buckets = {b:[] for b in BUCKET_WEIGHTS}
    for qq in order:
        s = scores.get(qq["id"], {"concept":0,"architecture":0,"procedure":0})
        avg01 = ((s["concept"]+s["architecture"]+s["procedure"])/3)/5
        buckets[_bucket(qq["category"])].append(avg01)
    def avg_b(arr): return sum(arr)/len(arr) if arr else 0
    overall = sum(avg_b(buckets[b])*w for b,w in BUCKET_WEIGHTS.items())
    pct = round(overall*100)
    status = "pass" if pct>=60 else ("borderline" if pct>=55 else "fail")
    status_color = {"pass":"#22c55e","borderline":"#d97706","fail":"#e53935"}[status]
    status_bg    = {"pass":"#f0fdf4","borderline":"#fffbeb","fail":"#fff5f5"}[status]
    status_label = {"pass":"PASS","borderline":"BORDERLINE","fail":"NOT PASSED"}[status]

    st.markdown(_DS, unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;"
        "color:#999;margin-bottom:4px'>SAP BDC Certification · Results</div>",
        unsafe_allow_html=True,
    )
    hdr_l, hdr_r = st.columns([3,1])
    with hdr_l:
        st.markdown(f"## {t['finishTitle']}")
    with hdr_r:
        c1, c2 = st.columns(2)
        if c1.button(t["backToQuiz"], use_container_width=True):
            ss[_k("finished")] = False; ss[_k("phase")] = "quiz"
            _go_to_q(0); st.rerun()
        if c2.button(t["newRun"], use_container_width=True):
            _reset_run(); st.rerun()

    score_col, breakdown_col = st.columns([1, 2])
    with score_col:
        st.markdown(
            f"<div class='dw-card' style='text-align:center;background:{status_bg}'>"
            f"<div style='font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#999;margin-bottom:8px'>Final Score</div>"
            f"<div style='font-size:56px;font-weight:900;color:{status_color};line-height:1'>{pct}%</div>"
            f"<div style='font-size:14px;font-weight:700;color:{status_color};margin-top:6px'>{status_label}</div>"
            f"<div style='margin-top:12px;font-size:12px;color:#888'>Pass mark: 60%</div>"
            f"<div style='margin-top:16px;padding-top:14px;border-top:1px solid #eee;font-size:13px'>"
            f"<strong>{answered_n}/{total_n}</strong> answered</div></div>",
            unsafe_allow_html=True,
        )
    with breakdown_col:
        bucket_rows = "".join(
            f"<div style='display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f0f0f0'>"
            f"<span style='font-size:13px;color:#444'>{b}</span>"
            f"<div style='display:flex;align-items:center;gap:10px'>"
            f"<div style='width:80px;background:#f0f0f0;border-radius:99px;height:6px'>"
            f"<div style='width:{int(avg_b(buckets[b])*100)}%;background:{_score_color(avg_b(buckets[b]), 1.0)};border-radius:99px;height:6px'></div></div>"
            f"<span style='font-size:12px;font-weight:600;min-width:32px;text-align:right;color:{_score_color(avg_b(buckets[b]), 1.0)}'>{int(avg_b(buckets[b])*100)}%</span>"
            f"<span style='font-size:11px;color:#bbb;min-width:30px'>×{int(w*100)}%</span>"
            f"</div></div>"
            for b, w in BUCKET_WEIGHTS.items()
        )
        st.markdown(
            f"<div class='dw-card'><div class='dw-card-header'>Score by Category</div>"
            f"{bucket_rows}"
            f"<div style='margin-top:10px;font-size:12px;color:#999'>{t['finishWeightedNote']}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        "<div style='font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;"
        "color:#999;margin:16px 0 8px'>Question Review</div>",
        unsafe_allow_html=True,
    )
    for i, qq in enumerate(order):
        qid   = qq["id"]
        a     = answers.get(qid,"").strip()
        s     = scores.get(qid,{"concept":0,"architecture":0,"procedure":0})
        avg_s = (s["concept"]+s["architecture"]+s["procedure"])/3
        c_avg    = _score_color(avg_s)
        c_con    = _score_color(s["concept"])
        c_arc    = _score_color(s["architecture"])
        c_prc    = _score_color(s["procedure"])
        a_color  = "#333" if a else "#bbb"
        st.markdown(
            f"<div class='dw-card'>"
            f"<div style='display:flex;justify-content:space-between;margin-bottom:8px'>"
            f"<div><span class='dw-pill'>{t['item']} {i+1}/{total_n}</span>"
            f"<span class='dw-pill'>{qq['category']}</span></div>"
            f"<span style='font-size:13px;font-weight:700;color:{c_avg}'>{avg_s:.1f}/5</span></div>"
            f"<div style='font-size:14px;font-weight:600;margin-bottom:10px'>{qq['prompt'][lang]}</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px'>"
            f"<div style='background:#fafafa;border:1px solid #eee;border-radius:8px;padding:10px;font-size:13px;line-height:1.55'>"
            f"<div style='font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#bbb;margin-bottom:6px'>{t['yourAnswer']}</div>"
            f"<div style='white-space:pre-wrap;color:{a_color}'>{a or t['noAnswer']}</div></div>"
            f"<div style='background:#fafafa;border:1px solid #eee;border-radius:8px;padding:10px;font-size:13px;line-height:1.55'>"
            f"<div style='font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#bbb;margin-bottom:6px'>{t['referenceLabel']}</div>"
            f"<div style='white-space:pre-wrap;color:#333'>{qq['reference'][lang]}</div></div>"
            f"</div>"
            f"<div style='margin-top:8px;font-size:12px;color:#888'>"
            f"{t['concept']}: <span style='color:{c_con};font-weight:700'>{s['concept']}</span>"
            f" &nbsp;·&nbsp; {t['architecture']}: <span style='color:{c_arc};font-weight:700'>{s['architecture']}</span>"
            f" &nbsp;·&nbsp; {t['procedure']}: <span style='color:{c_prc};font-weight:700'>{s['procedure']}</span></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

def _render_lang_select():
    ss = st.session_state
    st.markdown(_DS, unsafe_allow_html=True)
    st.markdown(
        "<div class='dw-card' style='max-width:480px;margin:40px auto;text-align:center'>"
        "<div style='font-size:22px !important;font-weight:800;letter-spacing:-.5px;margin-bottom:4px'>SAP BDC</div>"
        "<div style='font-size:13px;color:#888;margin-bottom:24px'>Certification Exam · Danwor IT Analytics</div>"
        "<div style='font-size:13px;color:#555;margin-bottom:16px'>Choose your language / Elegí el idioma</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, c1, c2, _ = st.columns([1, 1.2, 1.2, 1])
    with c1:
        if st.button("🇬🇧 English", use_container_width=True):
            ss[_k("lang")]  = "en"
            ss[_k("phase")] = "intro"
            st.rerun()
    with c2:
        if st.button("🇦🇷 Español", use_container_width=True):
            ss[_k("lang")]  = "es"
            ss[_k("phase")] = "intro"
            st.rerun()


# ── entry point ──────────────────────────────────────────────────────────────
def render_assessment_panel():
    _ensure_init()
    ss    = st.session_state
    phase = ss.get(_k("phase"), "lang_select")

    st.markdown("<div class='dw-assess-wrap'>", unsafe_allow_html=True)

    if ss.get(_k("finished")):
        _render_finished()
    elif phase == "lang_select":
        _render_lang_select()
    elif phase == "intro":
        _render_intro()
    elif phase == "quiz":
        _render_quiz()

    st.markdown("</div>", unsafe_allow_html=True)