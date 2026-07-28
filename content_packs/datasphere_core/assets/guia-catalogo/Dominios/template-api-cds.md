---
cds_view: ZCDS_DSP_DIM_XXXX      # nombre técnico de la vista CDS
tabla_base: XXXX                 # tabla base de la extracción (p. ej. MCH1)
extraccion: FULL                 # FULL | DELTA
---
# API Corporativa de Extracción
_API corporativa de extracción FULL hacia SAP Datasphere utilizando la tabla base XXXX._

---
## Código
```abap
@AbapCatalog.viewEnhancementCategory: [#NONE]
@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '<etiqueta funcional>'
@Metadata.ignorePropagatedAnnotations: true
@ObjectModel.usageType:{
    serviceQuality: #X,
    sizeCategory: #S,
    dataClass: #MIXED
}
@Analytics: {dataExtraction: { enabled: true } }

define view entity ZCDS_DSP_DIM_XXXX as select from xxxx
{
key <campo_clave>,
    <campo>
}
```
