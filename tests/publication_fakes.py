"""Dobles de prueba de la publicación del Catálogo (fase 0): tenant de Datasphere EN MEMORIA, bucket S3 EN MEMORIA y un contexto de publicación.

Frontera simulada: SOLO el CLI de Datasphere (``list_objects``/``read_object`` y prechequeos) y el bucket. El exportador, la limpieza, los manifiestos, el generador del
Catálogo, ``remote_store`` y el motor de Assessment son los REALES. Todo bajo ``tmp_path``: sin red, sin datos reales, sin Spaces.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import config
from core import remote_store
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog import publication_txn as txn

CLIENT, SPACE = "Acme", "PROD"
HOST = "https://t.eu10.hcs.cloud.sap"


def view(name, srcs, *, cols=("A", "B"), cube=False, label=None, measures=("M",)):
    elements = {c: {"type": "cds.String", "@EndUserText.label": c} for c in cols}
    cols_q = [{"ref": [srcs[0], c], "as": c} for c in cols]
    for m in measures:
        elements[m] = {"type": "cds.Decimal", "precision": 13, "scale": 3, "@AnalyticsDetails.measureType": {"#": "AGGREGATED"}, "@EndUserText.label": m}
        cols_q.append({"ref": [srcs[0], m], "as": m})
    frm = {"ref": [srcs[0]]} if len(srcs) == 1 else {"join": "inner", "args": [{"ref": [s]} for s in srcs], "on": []}
    body = {"kind": "entity", "elements": elements, "query": {"SELECT": {"from": frm, "columns": cols_q}},
            "@DataWarehouse.sqlEditor.query": "SELECT * FROM " + " JOIN ".join(f'"{s}"' for s in srcs)}
    if label:
        body["@EndUserText.label"] = label
    if cube:
        body["@ObjectModel.modelingPattern"] = {"#": "ANALYTICAL_CUBE"}
        body["@Analytics.dataCategory"] = {"#": "CUBE"}
    return {"definitions": {name: body}}


def analytic_model(name, fact, dims=(), *, label=None):
    csn = view(name, [fact, *dims], cube=True, label=label)
    csn["definitions"][name].pop("@DataWarehouse.sqlEditor.query", None)
    csn["businessLayerDefinitions"] = {"L": {"sourceModel": {"factSources": {"f": {"dataEntity": {"key": fact}}},
                                                             "dimensionSources": {str(i): {"dataEntity": {"key": d}} for i, d in enumerate(dims)}}}}
    return csn


def local_table(name):
    return {"definitions": {name: {"kind": "entity", "elements": {c: {"type": "cds.String"} for c in ("A", "B", "M")}}}}


def replication_flow(name, target):
    return {"replicationflows": {name: {"targets": {target: {}}, "connections": {"CONN": {}},
                                        "contents": {"replicationTasks": [{"sourceObject": {"name": "ABAPTABLES.PRECIOS"}}]}}}}


class Tenant:
    def __init__(self) -> None:
        self.spaces: dict[str, dict[str, dict[str, dict]]] = {}
        self.fail_reads: set[tuple[str, str]] = set()
        self.fail_lists: set[str] = set()
        self.incomplete_lists: set[str] = set()

    def put(self, space: str, otype: str, name: str, csn: dict) -> None:
        self.spaces.setdefault(space, {}).setdefault(otype, {})[name] = json.loads(json.dumps(csn))

    def drop(self, space: str, otype: str, name: str) -> None:
        self.spaces.get(space, {}).get(otype, {}).pop(name, None)

    def list(self, space, otype):
        if otype in self.fail_lists:
            raise dsp.DatasphereCliError(f"transient CLI failure listing {otype}")
        return [{"technicalName": n} for n in sorted(self.spaces.get(space, {}).get(otype, {}))]

    def read(self, space, otype, name):
        if (otype, name) in self.fail_reads:
            raise dsp.DatasphereCliError(f"transient CLI failure reading {otype}/{name}")
        return json.loads(json.dumps(self.spaces[space][otype][name]))


def seed_pricing(t: Tenant, space: str = SPACE) -> None:
    """AM_Lista_Precios <- 3VF_LP <- {1VR_LP_BASE, 1VR_LP_EXTRA} <- RAW_PRECIOS (+RF_PRECIOS); dimensión COMPARTIDA 1VR_DIM_PROD <- RAW_PROD; AM_Otro <- 3VF_OT <- RAW_OT + 1VR_DIM_PROD."""
    for n in ("RAW_PRECIOS", "RAW_PROD", "RAW_OT"):
        t.put(space, "local-tables", n, local_table(n))
    t.put(space, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"]))
    t.put(space, "views", "1VR_LP_EXTRA", view("1VR_LP_EXTRA", ["RAW_PRECIOS"]))
    t.put(space, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE", "1VR_LP_EXTRA"]))
    t.put(space, "views", "1VR_DIM_PROD", view("1VR_DIM_PROD", ["RAW_PROD"]))
    t.put(space, "views", "3VF_OT", view("3VF_OT", ["RAW_OT"]))
    t.put(space, "analytic-models", "AM_Lista_Precios", analytic_model("AM_Lista_Precios", "3VF_LP", ["1VR_DIM_PROD"], label="Modelo Analítico - Precios"))
    t.put(space, "analytic-models", "AM_Otro", analytic_model("AM_Otro", "3VF_OT", ["1VR_DIM_PROD"], label="Modelo Analítico - Otros"))
    t.put(space, "replication-flows", "RF_PRECIOS", replication_flow("RF_PRECIOS", "RAW_PRECIOS"))


class _NotFound(Exception):
    """Lo que boto3 lanza ante un objeto inexistente (con el mismo shape de ``response`` que inspecciona ``remote_store``)."""

    def __init__(self) -> None:
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeS3:
    """Bucket en memoria con la superficie boto3 que usa ``remote_store`` y fallos INYECTABLES:

      ``fail_after``/``fail_keys``        subidas que fallan (tras N subidas exitosas / si la clave contiene el texto): el efecto NO ocurre.
      ``lose_response_keys``              la subida/borrado SÍ ocurre y aun así lanza (respuesta perdida): el cliente no puede distinguirlo de un fallo.
      ``fail_get_keys`` / ``fail_head_keys``   lecturas que fallan de forma irresoluble (no es «no existe»).
      ``fail_list``                        el listado completo falla.
      ``down``                             caída total de Spaces: toda operación lanza.
      ``fail_delete_keys``                 borrados que fallan sin efecto.
      ``crash_before_keys`` / ``crash_after_keys``   el PROCESO muere (``SimulatedCrash``, no una ``Exception``) justo antes / justo después de guardar una clave que contiene el texto.
    ``log`` registra (operación, clave) en orden: permite verificar que el marcador de activación se sube AL FINAL y los borrados antes."""

    def __init__(self, *, fail_after: int | None = None, fail_keys: tuple[str, ...] = (), lose_response_keys: tuple[str, ...] = (), fail_get_keys: tuple[str, ...] = (),
                 fail_head_keys: tuple[str, ...] = (), fail_delete_keys: tuple[str, ...] = (), fail_list: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_after, self.fail_keys, self.uploads = fail_after, fail_keys, 0
        self.lose_response_keys, self.fail_get_keys, self.fail_head_keys = lose_response_keys, fail_get_keys, fail_head_keys
        self.fail_delete_keys, self.fail_list, self.down = fail_delete_keys, fail_list, False
        self.order: list[str] = []
        self.log: list[tuple[str, str]] = []
        self.crash_before_keys: tuple[str, ...] = ()
        self.crash_after_keys: tuple[str, ...] = ()

    def _check_down(self) -> None:
        if self.down:
            raise RuntimeError("Spaces caído (inyectado)")

    def upload_file(self, filename, bucket, key):
        self._check_down()
        if (self.fail_after is not None and self.uploads >= self.fail_after) or any(s in key for s in self.fail_keys):
            raise RuntimeError("Spaces caído (inyectado)")
        if any(s in key for s in self.crash_before_keys):
            raise SimulatedCrash(f"el proceso murió antes de guardar {key}")
        self.objects[key] = Path(filename).read_bytes()
        self.order.append(key)
        self.log.append(("put", key))
        self.uploads += 1
        if any(s in key for s in self.crash_after_keys):
            raise SimulatedCrash(f"el proceso murió después de guardar {key}")
        if any(s in key for s in self.lose_response_keys):
            raise RuntimeError("respuesta perdida (inyectada): la escritura SÍ ocurrió")

    def download_file(self, bucket, key, filename):
        self._check_down()
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_bytes(self.objects[key])

    def delete_object(self, Bucket, Key):
        self._check_down()
        if any(s in Key for s in self.fail_delete_keys):
            raise RuntimeError("borrado rechazado (inyectado)")
        self.objects.pop(Key, None)
        self.log.append(("delete", Key))
        if any(s in Key for s in self.lose_response_keys):
            raise RuntimeError("respuesta perdida (inyectada): el borrado SÍ ocurrió")

    def put_object(self, Bucket, Key, Body):
        self._check_down()
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        self._check_down()
        if any(s in Key for s in self.fail_get_keys):
            raise RuntimeError("lectura irresoluble (inyectada)")
        if Key not in self.objects:
            raise _NotFound()
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        self._check_down()
        if any(s in Key for s in self.fail_head_keys):
            raise RuntimeError("HEAD irresoluble (inyectado)")
        if Key not in self.objects:
            raise _NotFound()
        return {"ContentLength": len(self.objects[Key])}

    def get_paginator(self, name):
        outer = self

        class _P:
            def paginate(self, Bucket, Prefix=""):
                outer._check_down()
                if outer.fail_list:
                    raise RuntimeError("listado fallido (inyectado)")
                yield {"Contents": [{"Key": k, "Size": len(v), "ETag": '"' + hashlib.md5(v).hexdigest() + '"'} for k, v in sorted(outer.objects.items()) if k.startswith(Prefix)]}
        return _P()


def enable_remote(monkeypatch, s3: FakeS3, *, governed: Path | None = None) -> None:
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", False)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(config, "SPACES_BUCKET", "audit-bucket")
    monkeypatch.setattr(config, "SPACES_PREFIX", "audit")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local"] + ([config.GOVERNED_ASSESSMENTS_NAME] if governed is not None else []))
    if governed is not None:
        monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(governed))
    monkeypatch.setattr(remote_store, "_get_client", lambda: s3)
    monkeypatch.setattr(remote_store, "_signatures", {}, raising=False)
    monkeypatch.setattr(remote_store, "_digests", {}, raising=False)
    monkeypatch.setattr(remote_store, "_unreconciled", set(), raising=False)
    monkeypatch.setattr(remote_store, "_restore_state", "completed", raising=False)
    monkeypatch.setattr(remote_store, "_restore_degraded", False, raising=False)


class SimulatedCrash(BaseException):
    """Muerte del proceso: NO es ``Exception``, así que el rollback en proceso no corre (como un ``kill -9``); solo la recuperación por journal la deshace."""


class Ctx:
    def __init__(self, tmp: Path, tenant: Tenant) -> None:
        self.tmp, self.tenant = tmp, tenant
        self.catalog = tmp / "catalog_local"
        self.pool = self.catalog / ".csn_cache"
        self.catalog.mkdir(parents=True, exist_ok=True)

    def request(self, root="AM_Lista_Precios", *, space=SPACE, client=CLIENT, features=None, mirror=False, safe=True, host=HOST, stage=False):
        hooks = None
        if stage:                                                          # el assessment gobernado como etapa de la unidad (lo que arma el servicio real)
            from webapp.services import republication_stage
            hooks = republication_stage.AssessmentStage()
        return dsp.ExportRequest(host=host, client_id="id", client_secret="not-a-real-secret", token_url="https://t.authentication.eu10.hana.ondemand.com/oauth/token",
                                 space=space, root_object=root, client=client, catalog_root=self.catalog, csn_cache_dir=self.pool, generate_catalog=True,
                                 mirror_up=mirror, mode="publish", features_requested=features, safe=safe, stage_hooks=hooks)

    def publish(self, root="AM_Lista_Precios", **kw):
        with dsp.capture_output() as cap:
            res = dsp.run_export(self.request(root, **kw))
        return res, list(cap.lines)

    def digest(self) -> dict[str, str]:
        """ruta relativa → sha de contenido, con los manifiestos de linaje normalizados (sin campos volátiles) y sin temporales: el ESTADO del Catálogo."""
        out: dict[str, str] = {}
        for p in sorted(self.catalog.rglob("*")):
            if not p.is_file() or p.name.startswith(".tmp-atomic-"):
                continue
            rel = p.relative_to(self.catalog).as_posix()
            data = p.read_bytes()
            if p.name == "lineage-manifest.json":
                try:
                    out[rel] = txn.semantic_signature(json.loads(data))[:16]
                    continue
                except ValueError:
                    pass
            if p.name.endswith(".publication.json"):                 # bitácora: solo lo SEMÁNTICO de la versión activa (sin operación, instante ni historial ni digests de manifiestos volátiles)
                try:
                    active = json.loads(data)["active"]
                    stable = {k: active.get(k) for k in ("objects", "assessment", "space", "root_object", "root_type")}
                    out[rel] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:16]
                    continue
                except (ValueError, KeyError):
                    pass
            out[rel] = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()[:16]
        return out

    def journals(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(txn.journal_dir(self.catalog).glob("*.json"))] if txn.journal_dir(self.catalog).is_dir() else []

    def pool_names(self) -> list[str]:
        return sorted(f"{p.parent.name}/{p.stem}" for p in self.pool.rglob("*.json"))
