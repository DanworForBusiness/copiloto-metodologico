//----------------------------------------------------------------------------------------------------//
//                               CDS View para uso SAP Datasphere (DWC)
//----------------------------------------------------------------------------------------------------//
// Autor:       Danwor IT Analytics Experts
// Fecha:       24-09-2024
// Descripcion: Esta vista CDS sirve como template para la integracion entre SAP y Datasphere
//              con sus respectivas anotaciones y/o consideraciones a utilizar como referencia.
//              Importante aclarar que ciertas anotaciones han sido intencionalmente tenidas en cuenta
//              o bien descartadas por los motivos que se detallan a continuacion:
//
//                - @AbapCatalog.preserveKey:
//                                  respeta la clave original de las tablas utilizadas, o las ignora.
//                                  donde la anotacion @ObjectModel.representativeKey puede asimismo
//                                  definir el campo por utilizar como clave de ser necesario. 
//
//                - @AccessControl.authorizationCheck:
//                                  se utiliza deliberadamente en #NOT_REQUIRED pues no habran objetos
//                                  de access control creados contra las CDS que se utilizan como fuente
//                                  directa entre ECC/S4HANA y DWC via usuario de servicio.
//
//                - @OData.publish: se utiliza deliberamente en false en forma constante para todas
//                                  las CDS views utilizadas en Datasphere ya que no se requiere servicios
//                                  adicionales por ser asignados mediante roles o utilizados por terceros
//                                  sino como fuente directa entre ECC y DWC via usuario de servicio.
//
//                - @VDM.*: las anotaciones del framework VDM (Virtual Data Model) no son
//                                  necesarias para su uso en Datasphere pues hay una integracion
//                                  directa.
//
//                - @Analytics.*:   se utilizan puntualmente dos:
//                                  1. dataCategory: para clasificar entre #TEXT, #DIMENSION, #FACT, etc.
//                                  2. dataExtraction.enabled: para su uso como extractor a DWC
//                                  3. changeDataCapture: para compatibilidad CDC y Replication Flows incremental
//----------------------------------------------------------------------------------------------------//
@AbapCatalog.sqlViewName: 'ZDWC_DIM_CDS'
@EndUserText.label: 'Master Data (Attr) CDS View Template para Datasphere (DWC)'
@AbapCatalog.preserveKey: true
@AccessControl.authorizationCheck: #NOT_REQUIRED
@OData.publish: false
@Analytics: { dataCategory: #FACT, dataExtraction: { enabled: true, delta: { changeDataCapture: { automatic: true }}}}

define view ZCDS_DWC_DIM_TEMPLATE as select from mara
{
  key mara.matnr as ProductID,        // Material Number (Key)
      mara.mtart as ProductType,      // Product Type
      mara.meins as BaseUnit,         // Base Unit of Measure
      mara.ersda as CreatedOn,        // Creation Date
      mara.ernam as CreatedBy,        // Created By
      mara.laeda as LastChangedDate   // Last Changed Date
}