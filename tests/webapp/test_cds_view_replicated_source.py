"""«CDS View» para tablas locales REPLICADAS desde un contrato CDS vía Replication Flow (además de las remotas FEDERADAS). La entidad CDS de origen se obtiene de la evidencia
estructurada del RF (``catalog_ingestion_orchestration._cds_source_from_rfs``) y se resuelve con el MISMO ``catalog_cds_docs.resolve`` que la vía federada. Contrato funcional ÚNICO en
localhost y DigitalOcean: el consumo lee el pool/APIs materializados (local o restaurados desde Spaces), nunca el backend de persistencia. No se infiere la CDS por el nombre de la tabla,
no se siguen sombras delta, y ante ambigüedad / origen no-CDS / sin Markdown no se muestra vínculo."""
from __future__ import annotations

import core.remote_store as rs
from webapp import main
from webapp.services import catalog_cds_docs
from webapp.services import catalog_ingestion_orchestration as O


def _rf(container, target, source):
    return {"RF": {"contents": {"sourceSystem": [{"container": container}],
                                "replicationTasks": [{"targetObject": {"name": target}, "sourceObject": {"name": source}}]}}}


# ── Fuente CDS del RF (función pura, sin I/O): los casos del contrato ──
def test_single_governed_cds_producer_resolves_the_source():
    assert O._cds_source_from_rfs(_rf("/CDS_EXTRACTION", "T", "ZCDS_X"), "T") == "ZCDS_X"
    assert O._cds_source_from_rfs(_rf("/CDS", "T", "ZCDS_X"), "T") == "ZCDS_X"


def test_non_cds_container_is_not_linked():
    assert O._cds_source_from_rfs(_rf("/OTHER", "T", "RAW_X"), "T") is None          # RF cuyo origen no es un contrato CDS
    assert O._cds_source_from_rfs(_rf(None, "T", "RAW_X"), "T") is None


def test_ambiguous_multiple_producers_is_not_linked():
    rfs = {"RF1": {"contents": {"sourceSystem": [{"container": "/CDS_EXTRACTION"}], "replicationTasks": [{"targetObject": {"name": "T"}, "sourceObject": {"name": "A"}}]}},
           "RF2": {"contents": {"sourceSystem": [{"container": "/CDS_EXTRACTION"}], "replicationTasks": [{"targetObject": {"name": "T"}, "sourceObject": {"name": "B"}}]}}}
    assert O._cds_source_from_rfs(rfs, "T") is None                                   # varios productores → no se afirma un vínculo potencialmente incorrecto


def test_no_producer_is_not_linked():
    assert O._cds_source_from_rfs({}, "T") is None
    assert O._cds_source_from_rfs(_rf("/CDS_EXTRACTION", "OTRA", "ZCDS_X"), "T") is None


def test_delta_shadow_target_is_not_followed_by_name():
    # el RF apunta a la sombra delta (``T_Delta``), no a la tabla base ``T``: no se infiere por nombre → conservador, sin vínculo
    assert O._cds_source_from_rfs(_rf("/CDS_EXTRACTION", "T_Delta", "ZCDS_X"), "T") is None


def test_source_is_not_inferred_from_the_local_table_name():
    # aunque exista un doc homónimo de la tabla, sin RF productor no hay vínculo (no se infiere por coincidencia de nombre)
    assert O._cds_source_from_rfs({}, "ZCDS_DSP_FACT_MATDOC") is None


# ── Resolución del Markdown (reusa el resolver federado) ──
def test_replicated_resolution_requires_markdown(tmp_path):
    apis = tmp_path / "APIs"
    apis.mkdir()
    (apis / "ZCDS_X.md").write_text("# API\n", encoding="utf-8")
    src = O._cds_source_from_rfs(_rf("/CDS_EXTRACTION", "T", "ZCDS_X"), "T")
    assert catalog_cds_docs.resolve(apis, src) == "ZCDS_X"                            # CDS acreditada CON Markdown → vínculo
    src2 = O._cds_source_from_rfs(_rf("/CDS_EXTRACTION", "T", "ZCDS_SIN_DOC"), "T")
    assert catalog_cds_docs.resolve(apis, src2) == ""                                 # CDS acreditada SIN Markdown → sin vínculo


# ── La vía FEDERADA (remota) queda intacta ──
def test_federated_mechanism_is_preserved():
    assert main._cds_name_from_origen("ABAP_CDS.ZCDS_DSP_DIM_T134T$E") == "ZCDS_DSP_DIM_T134T"
    assert main._cds_name_from_origen("ABAPTABLES.T001L") == ""                        # tabla física cruda: no es CDS federada


# ── Paridad localhost / DigitalOcean: el consumo NO toca el backend de persistencia (lee el FS/pool materializado) ──
def test_replicated_resolution_does_not_touch_the_persistence_backend(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("el consumo de documentación CDS NO debe llamar al backend de persistencia (debe leer el FS/pool materializado, igual en local y en Spaces)")
    for fn in ("_get_client", "mirror_up", "push_path", "restore"):
        monkeypatch.setattr(rs, fn, boom, raising=False)
    apis = tmp_path / "APIs"
    apis.mkdir()
    (apis / "ZCDS_X.md").write_text("# API\n", encoding="utf-8")
    src = O._cds_source_from_rfs(_rf("/CDS_EXTRACTION", "T", "ZCDS_X"), "T")
    assert src == "ZCDS_X" and catalog_cds_docs.resolve(apis, src) == "ZCDS_X"         # resuelto desde el FS, sin backend remoto → mismo contrato en ambos entornos
