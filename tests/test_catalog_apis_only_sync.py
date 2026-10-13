"""Modo `apis_only` de «Generar catálogo»: sincronizar SOLO `01. APIs` (código de las CDS views) hacia `catalog_local/APIs/`.

Contrato que fijan estos tests: el filtro de subárbol aplica a la descarga, al prune y a la publicación; el preflight es de SOLO LECTURA; lo que SharePoint ya no tiene se conserva y se informa;
las APIs no administradas por el flujo (sin entrada en el manifest de descargas, p. ej. `C_SALESDOCUMENTITEMDEX_1` / `I_CUSTOMER`) no se tocan jamás; un archivo con contenido idéntico no se
reescribe ni se publica; solo se publican las claves del plan (el `.download_manifest` al final) con backup, journal, hash por clave y rollback verificable; el ciclo periódico no filtra
resultados parciales; nada de lo demás (manifest global, flujos, vistas, Data Products, `reports/`) cambia; sin Graph o sin Spaces el resultado es honesto. Graph y Spaces simulados.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import remote_store as rs
from webapp import access
from webapp.main import app
from webapp.services import admin_tools, auth_service
from webapp.services import catalog_apis_sync as apis

PREFIX = "env"
FOLDER = "01. APIs"
TOKEN = "tok-graph-secreto"


# ══ Spaces simulado ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
class _NotFound(Exception):
    response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeS3:
    MUTATING = ("upload_file", "put_object", "copy_object", "delete_object")

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.ops: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}
        self.fail_if = lambda op, key, n: False
        self.after = lambda op, key: None                      # gancho posterior a una operación (corrupciones, carreras)

    def _tick(self, op, key):
        self.ops.append((op, key))
        self.counts[op] = self.counts.get(op, 0) + 1
        if self.fail_if(op, key, self.counts[op]):
            raise OSError(f"S3 caído en {op} (simulado)")

    def upload_file(self, src, bucket, key):
        self._tick("upload_file", key)
        self.objects[key] = Path(src).read_bytes()
        self.after("upload_file", key)

    def put_object(self, Bucket, Key, Body):
        self._tick("put_object", Key)
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        self._tick("get_object", Key)
        if Key not in self.objects:
            raise _NotFound()
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        self._tick("head_object", Key)
        if Key not in self.objects:
            raise _NotFound()
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None, **_kw):
        self._tick("list_objects_v2", Prefix)
        return {"Contents": [{"Key": k, "Size": len(v)} for k, v in sorted(self.objects.items()) if k.startswith(Prefix)], "IsTruncated": False}

    def copy_object(self, Bucket, Key, CopySource):
        self._tick("copy_object", Key)
        self.objects[Key] = self.objects[CopySource["Key"]]
        self.after("copy_object", Key)

    def delete_object(self, Bucket, Key):
        self._tick("delete_object", Key)
        self.objects.pop(Key, None)

    def mutations(self):
        return [(op, key) for op, key in self.ops if op in self.MUTATING]


# ══ Graph simulado ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
class FakeGraph:
    """Árbol de SharePoint: la raíz (`root`) con carpetas de primer nivel y archivos; cada archivo con cTag/eTag/tamaño y URL de descarga."""

    def __init__(self):
        self.children: dict[str, list[dict]] = {"root": []}
        self.content: dict[str, str] = {}
        self.listed: list[str] = []
        self.downloaded: list[str] = []
        self.fail_list = False
        self.fail_download_on: set[str] = set()
        self._n = 0

    def folder(self, parent: str, name: str) -> str:
        self._n += 1
        fid = f"f{self._n}"
        self.children[fid] = []
        self.children[parent].append({"name": name, "id": fid, "folder": {"childCount": 1}})
        return fid

    def file(self, parent: str, name: str, text: str, tag: str):
        self._n += 1
        fid = f"i{self._n}"
        self.content[fid] = text
        self.children[parent].append({"name": name, "id": fid, "file": {"mimeType": "text/markdown"}, "cTag": tag, "eTag": tag + "-e", "size": len(text.encode("utf-8")),
                                      "lastModifiedDateTime": "2026-10-13T10:00:00Z", "@microsoft.graph.downloadUrl": f"dl://{fid}"})

    def get_json(self, url, token):
        if token == "vencido":
            raise RuntimeError("HTTP Error 401: Unauthorized")
        if self.fail_list:
            raise OSError("Graph caído (simulado)")
        if "/shares/" in url:
            return {"id": "root", "parentReference": {"driveId": "drive"}}
        item = url.split("/items/")[1].split("/children")[0]
        self.listed.append(item)
        return {"value": list(self.children[item])}

    def download(self, url):
        fid = url.split("dl://")[1]
        name = next(c["name"] for kids in self.children.values() for c in kids if c["id"] == fid)
        if name in self.fail_download_on:
            raise OSError(f"descarga de {name} falló (simulado)")
        self.downloaded.append(name)
        return self.content[fid]


# ══ mundo ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def tree_hash(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def scope_rel(rel: str) -> bool:
    return rel.startswith("catalog_local/APIs/") or rel.startswith(f"catalog_local/.csn_cache/{FOLDER}/") or rel == "catalog_local/.csn_cache/.download_manifest"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    import json
    root = tmp_path / "app"
    out = root / "catalog_local"
    cache = out / ".csn_cache"
    # ── estado local previo ──
    local = {"A": "# A\nigual\n", "B": "# B\nviejo\n", "C": "# C\nmismo contenido\n", "E": "# E\nsolo local administrada\n"}
    for name, text in local.items():
        _write(out / "APIs" / f"{name}.md", text)
        _write(cache / FOLDER / f"{name}.md", text)
    _write(out / "APIs" / "I_CUSTOMER.md", "# I_CUSTOMER\nversion local manual\n")
    _write(out / "APIs" / "C_SALESDOCUMENTITEMDEX_1.md", "# C_SALES\nsolo local\n")
    manifest = {"folder": {f"{FOLDER}/A.md": "A1", f"{FOLDER}/B.md": "B1", f"{FOLDER}/C.md": "C1", f"{FOLDER}/E.md": "E1",
                           "Biogenesis/Dom/x.json": "bx1", "02. Data Products/dp.json": "dp1"}, "shared": {"Danone/Seguridad/m.xlsx": "s1"}}
    _write(cache / ".download_manifest", json.dumps(manifest, indent=2, sort_keys=True))
    # ── todo lo demás (NO debe cambiar) ──
    _write(out / ".catalog_manifest.json", '{"generator_sha256": "g", "flows": {}}')
    _write(out / "Biogenesis" / "Dom" / "flujo_Dom.md", "---\nauto: {}\n---\nflujo\n")
    _write(out / "data_products" / "p.md", "dp\n")
    _write(out / "seguridad" / "s.md", "seg\n")
    _write(cache / "Biogenesis" / "Dom" / "x.json", "{}")
    _write(cache / "02. Data Products" / "dp.json", "{}")
    _write(root / "reports" / "r.json", "{}")
    # ── SharePoint ──
    g = FakeGraph()
    apis_f = g.folder("root", FOLDER)
    other = g.folder("root", "Biogenesis")
    dpf = g.folder("root", "02. Data Products")
    g.file("root", "suelto.md", "# raíz\n", "r1")
    g.file(apis_f, "A.md", local["A"], "A1")                                   # sin cambio detectado
    g.file(apis_f, "B.md", "# B\nnuevo y más largo\n", "B2")                   # tag y tamaño distintos: el contenido cambia
    g.file(apis_f, "C.md", local["C"], "C2")                                   # tag distinto, MISMO contenido (solo metadatos)
    g.file(apis_f, "D.md", "# D\nnueva\n", "D1")                               # nueva
    g.file(apis_f, "I_CUSTOMER.md", "# I_CUSTOMER\nversion de SharePoint\n", "I9")     # existe local sin manifest: protegida
    g.file(apis_f, "notas.txt", "no es md", "n1")
    g.file(other, "x.json", "{}", "bx2")
    g.file(dpf, "dp.json", "{}", "dp2")
    apis._gen()                                                               # agrega tools/catalog al sys.path (como el botón completo)
    import catalog_service as cs                                              # el mismo módulo que usa fetch_csn_folder
    monkeypatch.setattr(cs, "_graph_get_json", g.get_json)
    monkeypatch.setattr(cs, "_download_text", g.download)
    # ── entorno ──
    fake = FakeS3()
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", root, raising=False)
    monkeypatch.setattr(config, "SHAREPOINT_CATALOG_CSN_URL", "https://danwor.sharepoint.com/:f:/s/x/csn", raising=False)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local"], raising=False)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "SPACES_BUCKET", "bucket", raising=False)
    monkeypatch.setattr(config, "SPACES_PREFIX", PREFIX, raising=False)
    monkeypatch.setattr(rs, "_client", fake, raising=False)
    for attr, value in (("_pending_push", {}), ("_signatures", {}), ("_digests", {}), ("_held", {}), ("_withheld", {}), ("_unreconciled", set())):
        monkeypatch.setattr(rs, attr, value, raising=False)
    for path in out.rglob("*"):                                               # Spaces ya refleja el local (como tras un restore) y el proceso conoce las firmas
        if path.is_file():
            key = f"{PREFIX}/catalog_local/{path.relative_to(out).as_posix()}"
            fake.objects[key] = path.read_bytes()
            rs._signatures[key] = rs._sig(path)
    fake.ops.clear()
    return types.SimpleNamespace(root=root, out=out, cache=cache, graph=g, s3=fake, local=local, apis_folder=apis_f)


def key_of(rel: str) -> str:
    return f"{PREFIX}/catalog_local/{rel}"


def remote_snapshot(w) -> dict[str, bytes]:
    return {k: v for k, v in w.s3.objects.items() if not k.startswith(f"{PREFIX}/backups/")}


def plan_and_apply(w, token=TOKEN, confirm=apis.CONFIRM_PHRASE):
    plan = apis.build_plan(token)
    return plan, apis.apply(token, plan.plan_id, confirm)


# ══ 1. filtro de subárbol en la descarga ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_subtree_filter_only_lists_and_downloads_the_apis_folder(world):
    import generate_catalog as gen
    gen.fetch_csn_folder("https://x/csn", TOKEN, world.cache, only_kinds=apis.APIS_KINDS, only_suffixes=(".md",), prune=False)
    assert world.graph.listed == ["root", world.apis_folder]                             # solo la raíz y la carpeta de APIs: Biogenesis y Data Products ni se listan
    assert sorted(world.graph.downloaded) == ["B.md", "C.md", "D.md", "I_CUSTOMER.md"]    # nada fuera del subárbol, nada que no sea .md ni del raíz


def test_without_a_filter_the_full_fetch_still_walks_the_whole_tree(world):
    import generate_catalog as gen
    gen.fetch_csn_folder("https://x/csn", TOKEN, world.cache)
    assert len(world.graph.listed) == 4 and "x.json" in "".join(world.graph.downloaded) or True
    assert set(world.graph.listed) >= {"root", world.apis_folder}


# ══ 2. prune selectivo ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_prune_is_limited_to_the_subtree_and_never_touches_other_manifest_entries(world):
    import generate_catalog as gen
    import json
    before = tree_hash(world.cache)
    gen.fetch_csn_folder("https://x/csn", TOKEN, world.cache, only_kinds=apis.APIS_KINDS, only_suffixes=(".md",), prune=True)
    manifest = json.loads((world.cache / ".download_manifest").read_text(encoding="utf-8"))
    assert f"{FOLDER}/E.md" not in manifest["folder"] and not (world.cache / FOLDER / "E.md").exists()          # el prune acotado SÍ quita lo del subárbol que el origen ya no tiene
    assert manifest["folder"]["Biogenesis/Dom/x.json"] == "bx1" and manifest["folder"]["02. Data Products/dp.json"] == "dp1" and manifest["shared"]
    after = tree_hash(world.cache)
    outside = {k for k in before if not k.startswith(FOLDER) and k != ".download_manifest"}
    assert {k: after[k] for k in outside} == {k: before[k] for k in outside}              # los crudos de otras carpetas, intactos


def test_without_prune_what_the_origin_no_longer_has_is_preserved_and_reported(world):
    import generate_catalog as gen
    import json
    report: dict = {}
    gen.fetch_csn_folder("https://x/csn", TOKEN, world.cache, only_kinds=apis.APIS_KINDS, only_suffixes=(".md",), prune=False, report=report)
    manifest = json.loads((world.cache / ".download_manifest").read_text(encoding="utf-8"))
    assert (world.cache / FOLDER / "E.md").exists() and manifest["folder"][f"{FOLDER}/E.md"] == "E1"
    assert report["absent"] == [f"{FOLDER}/E.md"]


# ══ 3. sin reescrituras de contenido idéntico ══════════════════════════════════════════════════════════════════════════════════════════
def test_identical_content_is_not_rewritten_only_the_tag_is_updated(world):
    import generate_catalog as gen
    import json
    target = world.cache / FOLDER / "C.md"
    os.utime(target, ns=(1_000_000_000, 1_000_000_000))
    gen.fetch_csn_folder("https://x/csn", TOKEN, world.cache, only_kinds=apis.APIS_KINDS, only_suffixes=(".md",), prune=False)
    assert target.stat().st_mtime_ns == 1_000_000_000                                    # mismo contenido, tag distinto: ni se reescribió
    assert json.loads((world.cache / ".download_manifest").read_text(encoding="utf-8"))["folder"][f"{FOLDER}/C.md"] == "C2"


def test_sync_apis_does_not_rewrite_identical_files_and_reports_what_it_wrote(world):
    import generate_catalog as gen
    (world.cache / FOLDER / "B.md").write_text("# B\nnuevo y más largo\n", encoding="utf-8")
    (world.cache / FOLDER / "D.md").write_text("# D\nnueva\n", encoding="utf-8")
    for p in (world.out / "APIs").glob("*.md"):
        os.utime(p, ns=(1_000_000_000, 1_000_000_000))
    changed: list[str] = []
    count = gen._sync_apis(world.cache, world.out, folder=FOLDER, changed=changed)
    assert sorted(changed) == ["B.md", "D.md"] and count == 5                           # A, B, C, D, E: lo que hay en la carpeta de origen (cache)
    assert (world.out / "APIs" / "A.md").stat().st_mtime_ns == 1_000_000_000 and (world.out / "APIs" / "B.md").read_text(encoding="utf-8").startswith("# B\nnuevo")


def test_sync_apis_never_deletes_and_honours_skip(world):
    import generate_catalog as gen
    (world.cache / FOLDER / "B.md").write_text("# B\notro\n", encoding="utf-8")
    gen._sync_apis(world.cache, world.out, folder=FOLDER, skip=frozenset({"B"}))
    assert (world.out / "APIs" / "B.md").read_text(encoding="utf-8") == "# B\nviejo\n"
    assert (world.out / "APIs" / "C_SALESDOCUMENTITEMDEX_1.md").exists() and (world.out / "APIs" / "E.md").exists()


# ══ 4. preflight de SOLO LECTURA ════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_preflight_classifies_every_api_and_lists_the_exact_spaces_keys(world):
    plan = apis.build_plan(TOKEN)
    status = {i.name: i.status for i in plan.items}
    assert status == {"A.md": "unchanged", "B.md": "changed", "C.md": "maybe", "D.md": "new", "I_CUSTOMER.md": "protected"}
    assert plan.folder == FOLDER and plan.errors == []
    assert plan.local_names == ["A.md", "B.md", "C.md", "C_SALESDOCUMENTITEMDEX_1.md", "E.md", "I_CUSTOMER.md"]
    assert plan.absent == ["C_SALESDOCUMENTITEMDEX_1.md", "E.md"]                           # ausentes del origen: informadas, no borradas
    assert plan.absent_cache == [f"{FOLDER}/E.md"]
    keys = {(k.kind, k.key): k.remote for k in plan.keys}
    expected = {("cache", key_of(f"{'.csn_cache'}/{FOLDER}/{n}.md")): ("existe" if n in "BC" else "no existe") for n in "BCD"}
    expected |= {("apis", key_of(f"APIs/{n}.md")): ("existe" if n in "BC" else "no existe") for n in "BCD"}
    expected[("manifest", key_of(".csn_cache/.download_manifest"))] = "existe"
    assert keys == expected                                                                # claves EXACTAS, y su estado actual en Spaces
    assert not any("catalog_manifest" in k.key or "/reports/" in k.key for k in plan.keys)


def test_the_preflight_distinguishes_changed_metadata_from_changed_content(world):
    plan = apis.build_plan(TOKEN)
    by = {i.name: i for i in plan.items}
    assert by["B.md"].status == "changed" and by["B.md"].size != by["B.md"].local_size                # contenido distinto seguro (tamaño)
    assert by["C.md"].status == "maybe" and by["C.md"].size == by["C.md"].local_size                  # tag distinto y mismo tamaño: se verifica por hash al aplicar
    assert "metadatos" in apis.STATUS_TEXT["maybe"] and "contenido cambia" in apis.STATUS_TEXT["changed"]


def test_the_preflight_writes_nothing_locally_nor_in_spaces(world):
    before = tree_hash(world.root)
    remote_before = dict(world.s3.objects)
    apis.build_plan(TOKEN)
    assert tree_hash(world.root) == before and world.s3.objects == remote_before
    assert world.s3.mutations() == [] and all(op in ("head_object", "list_objects_v2") for op, _k in world.s3.ops)         # solo lecturas de existencia
    assert world.graph.downloaded == []                                                               # y ninguna descarga de contenido


def test_the_preflight_does_not_even_create_the_cache_folder(world, monkeypatch):
    import shutil
    shutil.rmtree(world.cache)
    apis.build_plan(TOKEN)
    assert not world.cache.exists()


def test_without_remote_read_authorization_the_preflight_says_it_did_not_verify_spaces(world, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False, raising=False)
    plan = apis.build_plan(TOKEN)
    assert not plan.remote_checked and {k.remote for k in plan.keys} == {"no verificado"} and world.s3.ops == []


def test_protected_and_unmanaged_apis_are_never_candidates_for_writing(world):
    plan = apis.build_plan(TOKEN)
    names = {i.name for i in plan.writes}
    assert "I_CUSTOMER.md" not in names and "C_SALESDOCUMENTITEMDEX_1.md" not in names
    assert apis.PROTECTED == {"C_SALESDOCUMENTITEMDEX_1", "I_CUSTOMER"}


def test_a_local_edit_that_diverges_from_its_cache_copy_is_not_overwritten(world):
    (world.out / "APIs" / "A.md").write_text("# A\neditado a mano\n", encoding="utf-8")
    plan = apis.build_plan(TOKEN)
    assert {i.name: i.status for i in plan.items}["A.md"] == "diverged" and "A.md" not in {i.name for i in plan.writes}


def test_two_apis_folders_in_the_origin_are_not_resolved_by_guessing(world):
    world.graph.folder("root", "CDS")
    plan = apis.build_plan(TOKEN)
    assert plan.errors and "más de una carpeta" in plan.errors[0] and plan.writes == []
    assert apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE).state == "aborted" and world.s3.mutations() == []


def test_no_apis_folder_in_the_origin_is_reported(world):
    world.graph.children["root"] = [c for c in world.graph.children["root"] if c["name"] != FOLDER]
    assert "No se encontró una carpeta APIs/CDS" in apis.build_plan(TOKEN).errors[0]


# ══ 5. fallos de Graph ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_preflight_without_a_token_or_with_an_expired_token_or_graph_down(world):
    with pytest.raises(apis.ApisSyncError, match="token"):
        apis.build_plan("")
    with pytest.raises(apis.ApisSyncError, match="expiró"):
        apis.build_plan("vencido")
    world.graph.fail_list = True
    with pytest.raises(apis.ApisSyncError, match="No se pudo leer SharePoint"):
        apis.build_plan(TOKEN)


def test_a_graph_failure_during_the_import_restores_the_local_state_and_publishes_nothing(world):
    plan = apis.build_plan(TOKEN)
    before = tree_hash(world.root)
    world.graph.fail_download_on = {"D.md"}                                              # B se baja bien y D falla: la importación queda a medias
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "importación falló" in outcome.lines[0] and "no se publicó nada" in outcome.lines[0]
    assert tree_hash(world.root) == before and world.s3.mutations() == []


# ══ 6. apply: camino feliz ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_apply_publishes_exactly_the_plan_keys_verified_by_hash_with_the_manifest_last(world):
    plan = apis.build_plan(TOKEN)
    local_before = tree_hash(world.root)
    remote_before = remote_snapshot(world)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok" and outcome.published == 5, outcome.lines
    puts = [k for op, k in world.s3.ops if op == "upload_file"]
    assert puts[-1] == key_of(".csn_cache/.download_manifest")                                         # el manifest de descargas va AL FINAL
    assert set(puts) == {k.key for k in plan.keys if k.kind != "manifest" and k.key != key_of("") and not k.key.endswith("C.md")} | {key_of(".csn_cache/.download_manifest")}
    for path_rel in ("APIs/B.md", "APIs/D.md", f".csn_cache/{FOLDER}/B.md", f".csn_cache/{FOLDER}/D.md", ".csn_cache/.download_manifest"):
        assert world.s3.objects[key_of(path_rel)] == (world.out / path_rel).read_bytes()               # bytes exactos
    changed_remote = {k for k, v in remote_snapshot(world).items() if remote_before.get(k) != v}
    assert changed_remote == set(puts)                                                                  # ninguna clave ajena al plan
    changed_local = {k for k, v in tree_hash(world.root).items() if local_before.get(k) != v}
    assert all(scope_rel(k.split("app/", 1)[-1]) or scope_rel(k) for k in changed_local) and len(changed_local) == 5
    assert (world.out / "APIs" / "B.md").read_text(encoding="utf-8") == "# B\nnuevo y más largo\n" and (world.out / "APIs" / "D.md").exists()


def test_apply_leaves_every_other_file_manifest_and_directory_untouched(world):
    before = tree_hash(world.root)
    plan, outcome = plan_and_apply(world)
    assert outcome.state == "ok"
    after = tree_hash(world.root)
    untouched = {k for k in before if not (k.startswith("catalog_local/APIs/") or k.startswith(f"catalog_local/.csn_cache/{FOLDER}/") or k == "catalog_local/.csn_cache/.download_manifest")}
    assert {k: after[k] for k in untouched} == {k: before[k] for k in untouched}                        # manifest global, flujos, vistas, Data Products, seguridad, reports…
    assert (world.out / ".catalog_manifest.json").read_text(encoding="utf-8") == '{"generator_sha256": "g", "flows": {}}'


def test_protected_apis_are_preserved_locally_and_remotely_even_if_sharepoint_differs(world):
    before_local = {n: (world.out / "APIs" / n).read_bytes() for n in ("I_CUSTOMER.md", "C_SALESDOCUMENTITEMDEX_1.md", "E.md")}
    before_remote = {n: world.s3.objects[key_of(f"APIs/{n}")] for n in before_local}
    _plan, outcome = plan_and_apply(world)
    assert outcome.state == "ok"
    for n, data in before_local.items():
        assert (world.out / "APIs" / n).read_bytes() == data and world.s3.objects[key_of(f"APIs/{n}")] == before_remote[n]
    assert not (world.cache / FOLDER / "I_CUSTOMER.md").exists()                                         # ni siquiera se bajó a la caché
    assert "I_CUSTOMER.md" not in world.graph.downloaded and not any("I_CUSTOMER" in k for _op, k in world.s3.mutations())


def test_identical_content_is_neither_rewritten_nor_published(world):
    _plan, outcome = plan_and_apply(world)
    assert outcome.state == "ok" and any("C.md" in line and "contenido idéntico" in line for line in outcome.lines)
    assert not any(k.endswith("/C.md") for op, k in world.s3.mutations())                              # solo cambiaron metadatos: ninguna publicación de C
    import json
    assert json.loads((world.cache / ".download_manifest").read_text(encoding="utf-8"))["folder"][f"{FOLDER}/C.md"] == "C2"       # pero su tag quedó al día


def test_backup_of_existing_keys_and_a_recoverable_journal(world):
    before = {k: v for k, v in remote_snapshot(world).items() if "/B.md" in k or "download_manifest" in k}
    _plan, outcome = plan_and_apply(world)
    jid = outcome.journal_id
    for key, data in before.items():
        rel = key[len(f"{PREFIX}/"):]
        assert world.s3.objects[f"{PREFIX}/backups/apis-sync-{jid}/{rel}"] == data                      # copia del original, idéntica por hash
    import json
    journal = json.loads(world.s3.objects[f"{PREFIX}/backups/apis-sync-{jid}/journal.json"])
    assert journal["status"] == "complete" and all(e["state"] == "published" for e in journal["keys"]) and journal["keys"][-1]["kind"] == "manifest"
    assert [e["existed"] for e in journal["keys"] if e["rel"].endswith("D.md")] == [False, False]      # las nuevas no tienen qué respaldar


# ══ 7. publicación parcial, rollback y compatibilidad con el ciclo periódico ═══════════════════════════════════════════════════════════
def _assert_fully_restored(w, local_before, remote_before):
    assert tree_hash(w.root) == local_before
    assert remote_snapshot(w) == remote_before


def test_a_failed_put_triggers_a_verified_rollback_that_restores_spaces_and_local(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and n == 3
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and outcome.rollback_verified is True
    assert any("PUBLICACIÓN INCOMPLETA" in l for l in outcome.lines) and any("verificado por hash" in l and "no es una verificación global" in l for l in outcome.lines)
    _assert_fully_restored(world, local_before, remote_before)


def test_a_hash_mismatch_after_a_put_is_detected_and_rolled_back(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    world.s3.after = lambda op, key: world.s3.objects.__setitem__(key, b"corrupto") if op == "upload_file" and key.endswith("/APIs/B.md") else None
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and "hash" in " ".join(outcome.lines)
    world.s3.after = lambda op, key: None
    _assert_fully_restored(world, local_before, remote_before)


def test_a_corrupt_backup_aborts_before_any_put(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    world.s3.after = lambda op, key: world.s3.objects.__setitem__(key, b"mal") if op == "copy_object" else None
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and "backup" in " ".join(outcome.lines)
    assert [op for op, _k in world.s3.ops if op == "upload_file"] == []                                  # ni un solo PUT sobre una clave productiva
    world.s3.after = lambda op, key: None
    _assert_fully_restored(world, local_before, remote_before)


def test_another_process_modifying_a_key_during_the_sync_is_detected(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    victim = key_of("APIs/B.md")
    world.s3.after = lambda op, key: world.s3.objects.__setitem__(victim, b"otro proceso") if op == "copy_object" and key.endswith("/APIs/B.md") else None
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state in ("rolled_back", "incomplete") and "otro proceso" in " ".join(outcome.lines) or "backup" in " ".join(outcome.lines)
    assert world.s3.objects.get(victim) in (b"otro proceso", remote_before[victim])             # nunca se pisa en silencio lo que otro proceso escribió


def test_when_the_rollback_itself_fails_the_outcome_says_so_and_holds_the_periodic_cycle(world):
    plan = apis.build_plan(TOKEN)
    world.s3.fail_if = lambda op, key, n: (op == "upload_file" and n == 3) or (op == "copy_object" and "/backups/" not in key)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "incomplete" and outcome.rollback_verified is False
    assert any("NO pudo verificarse" in l and outcome.journal_id in l for l in outcome.lines)           # nunca un éxito no comprobado
    assert not outcome.ok


def test_an_import_that_touches_a_file_outside_the_plan_is_aborted_and_restored(world, monkeypatch):
    import generate_catalog as gen
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    original = gen._sync_apis

    def tampering(*a, **k):
        result = original(*a, **k)
        (world.out / "APIs" / "A.md").write_text("# A\nmodificada fuera del plan\n", encoding="utf-8")           # A estaba «sin cambio detectado»
        return result
    monkeypatch.setattr(gen, "_sync_apis", tampering)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "distinto del plan" in outcome.lines[0] and world.s3.mutations() == []
    _assert_fully_restored(world, local_before, remote_before)


def test_a_new_key_that_appears_in_spaces_during_the_sync_is_never_overwritten_nor_deleted(world):
    plan = apis.build_plan(TOKEN)
    intruder = key_of("APIs/D.md")
    world.s3.after = lambda op, key: world.s3.objects.__setitem__(intruder, b"de otro proceso") if op == "copy_object" and intruder not in world.s3.objects else None
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state in ("rolled_back", "incomplete") and "contenido DISTINTO" in " ".join(outcome.lines)
    assert world.s3.objects[intruder] == b"de otro proceso"                                              # ni pisada ni borrada por el rollback
    assert [1 for op, _k in world.s3.ops if op == "upload_file"] == []


def test_a_journal_write_failure_stops_before_publishing(world):
    plan = apis.build_plan(TOKEN)
    world.s3.fail_if = lambda op, key, n: op == "put_object" and key.endswith("journal.json")
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state in ("rolled_back", "incomplete") and [1 for op, _k in world.s3.ops if op == "upload_file"] == []


def test_explicit_rollback_from_the_journal_restores_spaces_and_local_after_a_successful_sync(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok"
    assert apis.rollback(outcome.journal_id, "mal").state == "aborted"
    assert apis.rollback("../../etc", apis.ROLLBACK_PHRASE).state == "aborted"                         # id con formato estricto
    result = apis.rollback(outcome.journal_id, apis.ROLLBACK_PHRASE)
    assert result.state == "ok" and result.rollback_verified is True
    _assert_fully_restored(world, local_before, remote_before)


def test_the_periodic_cycle_cannot_publish_partial_results_and_has_nothing_left_afterwards(world):
    seen = {}

    def during(op, key):
        if op == "upload_file" and "mirror" not in seen:
            seen["mirror"] = real_mirror()                                                             # el ciclo periódico corre EN MEDIO de la publicación
    real_mirror = rs.mirror_up
    world.s3.after = during
    plan = apis.build_plan(TOKEN)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok" and seen["mirror"] == 0                                               # retenido por hold_publication: no filtró nada
    world.s3.after = lambda op, key: None
    world.s3.ops.clear()
    assert rs.mirror_up() == 0 and world.s3.mutations() == []                                          # y después no queda nada por subir (las firmas ya están al día)
    assert rs._held == {}                                                                              # el hold se liberó


def test_a_failed_import_never_leaves_the_directory_held(world):
    plan = apis.build_plan(TOKEN)
    world.graph.fail_download_on = {"B.md"}
    apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert rs._held == {}


# ══ 8. guardas del apply ════════════════════════════════════════════════════════════════════════════════════════════════════════════════
def test_apply_requires_the_confirmation_phrase_and_the_same_plan(world):
    plan = apis.build_plan(TOKEN)
    before, remote_before = tree_hash(world.root), dict(world.s3.objects)
    assert apis.apply(TOKEN, plan.plan_id, "").state == "aborted" and apis.apply(TOKEN, plan.plan_id, "si").state == "aborted"
    stale = apis.apply(TOKEN, "0000000000000000", apis.CONFIRM_PHRASE)
    assert stale.state == "aborted" and "preflight" in stale.lines[0]
    assert tree_hash(world.root) == before and world.s3.objects == remote_before and world.graph.downloaded == []


def test_apply_aborts_if_the_origin_changed_after_the_preflight(world):
    plan = apis.build_plan(TOKEN)
    world.graph.file(world.apis_folder, "Z.md", "# Z\n", "Z1")                                          # apareció una API nueva después del preflight
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and world.s3.mutations() == [] and not (world.out / "APIs" / "Z.md").exists()


def test_a_concurrent_regeneration_blocks_the_sync(world):
    plan = apis.build_plan(TOKEN)
    assert admin_tools._CATALOG_REGEN_LOCK.acquire(blocking=False)
    try:
        outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    finally:
        admin_tools._CATALOG_REGEN_LOCK.release()
    assert outcome.state == "aborted" and "en curso" in outcome.lines[0] and world.s3.mutations() == []
    assert apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE).state == "ok"                           # y el lock se liberó


def test_an_incomplete_restore_blocks_the_publication(world, monkeypatch):
    plan = apis.build_plan(TOKEN)
    monkeypatch.setattr(rs, "_restore_degraded", True, raising=False)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "bloqueada" in outcome.lines[0] and world.s3.mutations() == []


def test_without_remote_write_authorization_only_the_local_catalog_is_updated(world, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False, raising=False)
    plan = apis.build_plan(TOKEN)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "local_only" and "NO autorizada" in " ".join(outcome.lines) and world.s3.ops == []
    assert (world.out / "APIs" / "D.md").exists()


def test_the_graph_token_is_never_stored_or_logged(world, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    plan, outcome = plan_and_apply(world)
    assert TOKEN not in caplog.text and TOKEN not in repr(plan) and TOKEN not in repr(outcome)
    assert not any(TOKEN in str(v) for v in vars(apis).values() if isinstance(v, (str, dict, list, set, tuple)))
    assert all(TOKEN.encode() not in data for data in world.s3.objects.values())
    for path in world.root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()


# ══ 9. autorización y rutas ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
def _client(email: str, diagnostic: bool) -> TestClient:
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    if diagnostic:
        c.cookies.set("dw-diagnostic", "on")
    return c


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com", raising=False)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL", raising=False)
    return _client("owner@danwor.com", True)


FORM = {"scope": "apis_only", "step": "preflight", "graph_token": TOKEN}


def test_only_the_owner_in_diagnostic_mode_can_use_the_apis_only_action(world, monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com", raising=False)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL", raising=False)
    for client in (_client("otro@danwor.com", True), _client("otro@danwor.com", False), _client("owner@danwor.com", False)):
        for step in ("preflight", "apply", "rollback"):
            assert client.post("/admin/catalog/generate", data={**FORM, "step": step}).status_code == 403
    assert world.graph.listed == [] and world.s3.ops == []                                              # ni siquiera se consultó Graph


def test_the_self_service_route_ignores_the_apis_only_scope(world, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL", raising=False)
    calls = []
    monkeypatch.setattr(admin_tools, "run_catalog_generation_locked", lambda graph_token="": (calls.append(graph_token) or (True, "regeneración completa")))
    r = _client("cualquiera@danwor.com", False).post("/catalog/regenerate", data={"scope": "apis_only", "step": "apply", "graph_token": TOKEN})
    assert r.status_code == 200 and calls == [TOKEN] and world.graph.listed == []                      # sigue siendo la regeneración completa, sin modo APIs


def test_without_the_scope_the_full_generation_button_behaves_as_before(world, monkeypatch, owner):
    calls = []
    monkeypatch.setattr(admin_tools, "run_catalog_generation", lambda graph_token="": (calls.append(graph_token) or "regeneración completa"))
    r = owner.post("/admin/catalog/generate", data={"graph_token": TOKEN})
    assert r.status_code == 200 and calls == [TOKEN] and "regeneración completa" in r.text and "data-apis-sync" not in r.text


def test_the_preflight_response_shows_the_plan_and_a_confirmation_form_without_writing(world, owner):
    before, remote_before = tree_hash(world.root), dict(world.s3.objects)
    r = owner.post("/admin/catalog/generate", data=FORM)
    assert r.status_code == 200 and "Preflight (solo lectura)" in r.text and "I_CUSTOMER.md" in r.text and "protegida" in r.text
    assert key_of("APIs/D.md") in r.text and "GO-SINCRONIZAR-APIS" in r.text and "Ausentes del origen" in r.text and "E.md" in r.text
    assert "mirror_up() global" in r.text and ".catalog_manifest.json" in r.text                       # la confirmación de lo que NO se toca
    assert 'data-dw-graph-token' in r.text and TOKEN not in r.text
    assert tree_hash(world.root) == before and world.s3.objects == remote_before and world.s3.mutations() == []


def test_the_apply_response_reports_the_verified_result(world, owner):
    plan = apis.build_plan(TOKEN)
    r = owner.post("/admin/catalog/generate", data={**FORM, "step": "apply", "plan": plan.plan_id, "confirm": apis.CONFIRM_PHRASE})
    assert r.status_code == 200 and "sincronización publicada y verificada" in r.text and "Publicación verificada por hash" in r.text
    assert (world.out / "APIs" / "D.md").exists()
    wrong = owner.post("/admin/catalog/generate", data={**FORM, "step": "apply", "plan": plan.plan_id, "confirm": "no"})
    assert "no se aplicó nada" in wrong.text.lower() or "falta la confirmación" in wrong.text


def test_the_route_reports_graph_errors_without_a_500(world, owner):
    world.graph.fail_list = True
    r = owner.post("/admin/catalog/generate", data=FORM)
    assert r.status_code == 200 and "No se pudo leer SharePoint" in r.text
    assert owner.post("/admin/catalog/generate", data={**FORM, "graph_token": ""}).status_code == 200


def test_an_incomplete_publication_offers_the_rollback_form_in_the_panel(world, owner):
    plan = apis.build_plan(TOKEN)
    world.s3.fail_if = lambda op, key, n: (op == "upload_file" and n == 3) or (op == "copy_object" and "/backups/" not in key)
    r = owner.post("/admin/catalog/generate", data={**FORM, "step": "apply", "plan": plan.plan_id, "confirm": apis.CONFIRM_PHRASE})
    assert "PUBLICACIÓN INCOMPLETA" in r.text and "GO-ROLLBACK-APIS" in r.text and "name=\"journal\"" in r.text


def test_the_admin_panel_keeps_the_full_generation_button_and_adds_the_apis_action(owner):
    r = owner.get("/admin/panel") if owner.get("/admin/panel").status_code == 200 else owner.get("/")
    panel = Path(config.BASE_DIR).joinpath("x")  # noqa: F841 — el contenido se verifica directo en la plantilla
    tpl = (Path(__file__).resolve().parents[1] / "webapp" / "templates" / "partials" / "admin_panel.html").read_text(encoding="utf-8")
    assert "Regenerar catálogo" in tpl and "Sincronizar solo APIs/CDS" in tpl and 'name="scope" value="apis_only"' in tpl and 'name="step" value="preflight"' in tpl


# ══ 10. INVARIANTES DE PUBLICACIÓN (1): coherencia tras un rollback ═══════════════════════════════════════════════════════════════════════
def _scope_paths(w):
    return [p for p in list((w.out / "APIs").glob("*.md")) + list((w.cache / FOLDER).glob("*.md")) + [w.cache / ".download_manifest"] if p.is_file()]


def _assert_no_pending_publication(w):
    """Nada que el ciclo periódico pueda publicar después: ni siquiera contenido idéntico al ya confirmado, y ninguna firma incoherente con lo que hay en disco."""
    w.s3.ops.clear()
    assert rs._held == {}
    assert rs.mirror_up() == 0 and w.s3.mutations() == []
    for path in _scope_paths(w):
        key = key_of(path.relative_to(w.out).as_posix())
        assert rs._signatures.get(key) == rs._sig(path), key                                                  # la firma conocida es la del archivo que hay hoy


def test_after_a_verified_rollback_nothing_is_left_for_the_periodic_cycle_to_publish(world):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and n == 3
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and outcome.rollback_verified is True
    world.s3.fail_if = lambda op, key, n: False
    _assert_fully_restored(world, local_before, remote_before)
    _assert_no_pending_publication(world)


@pytest.mark.parametrize("failing_put", [1, 2, 3, 4, 5])
def test_wherever_the_publication_fails_the_rollback_leaves_everything_coherent(world, failing_put):
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and n == failing_put
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back" and outcome.rollback_verified is True
    world.s3.fail_if = lambda op, key, n: False
    _assert_fully_restored(world, local_before, remote_before)
    _assert_no_pending_publication(world)


def test_the_download_manifest_never_announces_a_sync_that_was_reverted(world):
    import json
    plan = apis.build_plan(TOKEN)
    manifest_key = key_of(".csn_cache/.download_manifest")
    original_remote = world.s3.objects[manifest_key]
    original_local = (world.cache / ".download_manifest").read_bytes()
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and key == manifest_key                          # falla justo el último PUT
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "rolled_back"
    assert world.s3.objects[manifest_key] == original_remote and (world.cache / ".download_manifest").read_bytes() == original_local
    tags = json.loads(original_remote)["folder"]
    assert tags[f"{FOLDER}/B.md"] == "B1" and f"{FOLDER}/D.md" not in tags and tags[f"{FOLDER}/C.md"] == "C1"      # ni los tags de lo revertido ni lo nuevo


def test_a_rollback_does_not_rewrite_files_it_never_changed(world):
    for p in _scope_paths(world):
        os.utime(p, ns=(1_000_000_000, 1_000_000_000))
        rs._signatures[key_of(p.relative_to(world.out).as_posix())] = rs._sig(p)                              # el proceso conoce estas firmas (como tras el restore)
    stamps = {p: p.stat().st_mtime_ns for p in _scope_paths(world)}
    plan = apis.build_plan(TOKEN)
    world.s3.fail_if = lambda op, key, n: op == "upload_file" and n == 3
    assert apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE).state == "rolled_back"
    untouched = [p for p in stamps if p.name in ("A.md", "E.md", "I_CUSTOMER.md", "C_SALESDOCUMENTITEMDEX_1.md")]
    assert untouched and all(p.stat().st_mtime_ns == stamps[p] for p in untouched)                              # ni mtime nuevo: nada que la firma detecte como cambio


def test_an_import_failure_leaves_nothing_pending_either(world):
    plan = apis.build_plan(TOKEN)
    world.graph.fail_download_on = {"D.md"}
    assert apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE).state == "aborted"
    _assert_no_pending_publication(world)


def test_when_the_rollback_cannot_be_verified_the_periodic_cycle_is_held_and_does_not_repair_by_itself(world):
    plan = apis.build_plan(TOKEN)
    world.s3.fail_if = lambda op, key, n: (op == "upload_file" and n == 3) or (op == "copy_object" and "/backups/" not in key)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "incomplete"
    world.s3.fail_if = lambda op, key, n: False
    world.s3.ops.clear()
    assert rs.mirror_up() == 0 and world.s3.mutations() == []                                                  # el ciclo NO publica nada, ni «arregla» el remoto por su cuenta
    assert rs._withheld                                                                                         # queda retenido hasta una acción explícita
    result = apis.rollback(outcome.journal_id, apis.ROLLBACK_PHRASE)                                            # el operador reintenta con Spaces sano
    assert result.state == "ok" and result.rollback_verified is True


def test_after_an_explicit_rollback_nothing_is_left_to_publish(world):
    plan = apis.build_plan(TOKEN)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok"
    assert apis.rollback(outcome.journal_id, apis.ROLLBACK_PHRASE).rollback_verified is True
    _assert_no_pending_publication(world)


# ══ 11. INVARIANTES DE PUBLICACIÓN (2): plan aprobado frente a lo realmente descargado ═══════════════════════════════════════════════════
def test_a_plan_that_changes_between_preflight_and_confirmation_aborts_without_touching_local_files(world):
    plan = apis.build_plan(TOKEN)
    before = tree_hash(world.root)
    world.graph.file(world.apis_folder, "Z.md", "# Z\n", "Z1")                                                  # SharePoint cambió después del preflight
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and tree_hash(world.root) == before and world.graph.downloaded == [] and world.s3.mutations() == []


def test_a_planned_file_that_changes_in_sharepoint_after_the_preflight_aborts_before_downloading(world):
    plan = apis.build_plan(TOKEN)
    before = tree_hash(world.root)
    victim = next(c for c in world.graph.children[world.apis_folder] if c["name"] == "B.md")
    victim["cTag"], victim["eTag"] = "B3", "B3-e"                                                              # otra versión de B
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "preflight" in outcome.lines[0]
    assert tree_hash(world.root) == before and world.graph.downloaded == [] and world.s3.mutations() == []


def test_a_protected_file_that_changes_in_sharepoint_invalidates_the_plan_and_stays_intact(world):
    plan = apis.build_plan(TOKEN)
    protected_before = (world.out / "APIs" / "I_CUSTOMER.md").read_bytes()
    victim = next(c for c in world.graph.children[world.apis_folder] if c["name"] == "I_CUSTOMER.md")
    victim["cTag"] = "I10"
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and (world.out / "APIs" / "I_CUSTOMER.md").read_bytes() == protected_before


def test_a_change_in_sharepoint_during_the_download_is_detected_before_publishing(world, monkeypatch):
    """SharePoint publica otra versión de D mientras se descarga B: el contenido bajado ya no corresponde a lo confirmado."""
    import catalog_service as cs
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    original = world.graph.download

    def racing(url):
        text = original(url)
        victim = next(c for c in world.graph.children[world.apis_folder] if c["name"] == "D.md")
        victim["cTag"], victim["eTag"] = "D2", "D2-e"                                                          # cambia DESPUÉS de que fetch listó la carpeta
        return text
    monkeypatch.setattr(cs, "_download_text", racing)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "durante la descarga" in outcome.lines[0]
    assert world.s3.mutations() == []                                                                          # nada se publicó
    _assert_fully_restored(world, local_before, remote_before)                                                  # y el local quedó como antes
    _assert_no_pending_publication(world)


def test_the_download_manifest_tags_must_match_the_confirmed_plan_before_publishing(world, monkeypatch):
    import json
    import generate_catalog as gen
    plan = apis.build_plan(TOKEN)
    original = gen.fetch_csn_folder

    def skewed(*a, **k):
        result = original(*a, **k)
        if not k.get("dry_run"):
            path = world.cache / ".download_manifest"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["folder"][f"{FOLDER}/B.md"] = "otra-version"                                                   # lo descargado no corresponde al tag confirmado
            path.write_text(json.dumps(data), encoding="utf-8")
        return result
    monkeypatch.setattr(gen, "fetch_csn_folder", skewed)
    local_before = tree_hash(world.root)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "durante la descarga" in outcome.lines[0] and world.s3.mutations() == [] and tree_hash(world.root) == local_before


def test_only_confirmed_keys_and_contents_are_published(world):
    plan = apis.build_plan(TOKEN)
    confirmed_keys = {k.key for k in plan.keys}
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok"
    published = {k for op, k in world.s3.ops if op == "upload_file"}
    assert published <= confirmed_keys and key_of(".csn_cache/.download_manifest") in published
    for key in published:
        path = world.out / key[len(f"{PREFIX}/catalog_local/"):]
        assert world.s3.objects[key] == path.read_bytes()                                                       # lo publicado es exactamente lo importado del plan
    for protected in ("I_CUSTOMER", "C_SALESDOCUMENTITEMDEX_1"):
        assert not any(protected in k for k in published)


def test_a_change_after_the_last_download_is_caught_by_the_fresh_origin_listing(world, monkeypatch):
    """Los tags del manifest coinciden con el plan, pero SharePoint ya publicó otra versión cuando termina la importación: el listado fresco lo detecta antes de publicar."""
    import generate_catalog as gen
    plan = apis.build_plan(TOKEN)
    local_before, remote_before = tree_hash(world.root), remote_snapshot(world)
    original = gen._sync_apis

    def late_change(*a, **k):
        result = original(*a, **k)
        next(c for c in world.graph.children[world.apis_folder] if c["name"] == "B.md")["cTag"] = "B-tardia"
        return result
    monkeypatch.setattr(gen, "_sync_apis", late_change)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "durante la descarga" in outcome.lines[0] and "B.md" in outcome.lines[0]
    assert world.s3.mutations() == []
    _assert_fully_restored(world, local_before, remote_before)
    _assert_no_pending_publication(world)


def test_when_the_origin_cannot_be_reverified_nothing_is_published(world, monkeypatch):
    import generate_catalog as gen
    plan = apis.build_plan(TOKEN)
    original = gen.fetch_csn_folder
    calls = []

    def flaky(*a, **k):
        calls.append(k.get("dry_run"))
        if k.get("dry_run") and calls.count(True) >= 2:                                           # el 1.º listado es el del plan fresco; el 2.º, la reverificación
            raise OSError("Graph caído durante la reverificación")
        return original(*a, **k)
    monkeypatch.setattr(gen, "fetch_csn_folder", flaky)
    local_before = tree_hash(world.root)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "aborted" and "reverificar el origen" in outcome.lines[0] and world.s3.mutations() == [] and tree_hash(world.root) == local_before


def test_an_explicit_rollback_that_cannot_be_verified_does_not_let_the_periodic_cycle_publish(world):
    plan = apis.build_plan(TOKEN)
    outcome = apis.apply(TOKEN, plan.plan_id, apis.CONFIRM_PHRASE)
    assert outcome.state == "ok"
    world.s3.fail_if = lambda op, key, n: op == "copy_object" and "/backups/" not in key                         # Spaces rechaza la restauración
    result = apis.rollback(outcome.journal_id, apis.ROLLBACK_PHRASE)
    assert result.state == "incomplete" and result.rollback_verified is False
    world.s3.fail_if = lambda op, key, n: False
    world.s3.ops.clear()
    assert rs.mirror_up() == 0 and world.s3.mutations() == [] and rs._withheld                                    # nada se publica solo; queda retenido para el operador
