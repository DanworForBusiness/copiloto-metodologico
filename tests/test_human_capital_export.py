"""Descarga múltiple de CVs (`webapp/services/human_capital_export.py`): límites, plan, auditoría durable fail-closed, lectura acotada con presupuesto, errores parciales con segunda confirmación,
reintentos, cancelación, concurrencia, limpieza del temporal y ZIP real con Graph SIMULADO (un `downloader` falso con la misma firma que `core.microsoft365.graph_download_capped`)."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import zipfile
from pathlib import Path

import pytest

import config
from core import remote_store
from webapp.services import human_capital_export as ex

TOKEN = "tok-sintetico-NO-REAL-abc123"
ACTOR = "cchh-sintetico@danwor.com"
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    base, tmp = tmp_path / "base", tmp_path / "tmp"
    base.mkdir()
    tmp.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", base)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(ex.tempfile, "tempdir", str(tmp))
    return {"base": base, "tmp": tmp}


def profiles(n=3):
    return {f"p{i}": {"first_name": f"Cand{i}", "last_name": "Apellido", "profile_id": f"p{i}"} for i in range(1, n + 1)}


def doc(doc_id, profile_id, item=None, size=1000, filename=None, uploaded="2026-10-01T10:00:00+00:00", **extra):
    return {"document_id": doc_id, "profile_id": profile_id, "drive_id": "b!drive", "item_id": item or f"item-{doc_id}", "size_bytes": size, "filename": filename or f"CV_{doc_id}.pdf",
            "uploaded_at": uploaded, "web_url": "https://danwor.sharepoint.com/secreto", **extra}


class Store:
    def __init__(self, profs, docs):
        self.profs, self.docs = profs, docs
        self.profile_calls: list[str] = []

    def get_profile(self, pid):
        self.profile_calls.append(pid)
        return self.profs.get(pid)

    def list_documents(self, pid):
        return list(self.docs.get(pid, []))


class Graph:
    """Downloader falso: `plan[item]` = lista de resultados por llamada ('ok', 'http_403', 'http_404', 'http_429', 'timeout', 'http_401', ...); la última se repite."""

    def __init__(self, plan=None, payload=lambda item: f"%PDF-{item}".encode() * 50, clock=None, cost=0.0):
        self.plan, self.payload, self.calls, self.urls, self.tokens, self.clock, self.cost = plan or {}, payload, [], [], [], clock, cost

    def __call__(self, url, token, sink, *, cap_bytes, socket_timeout, deadline_s, cancel=None, declared_size=None, **_):
        item = re.search(r"/items/([^/]+)/content", url).group(1)
        self.calls.append(item)
        self.urls.append(url)
        self.tokens.append(token)
        if self.clock is not None:
            self.clock.t += self.cost
        seq = self.plan.get(item, ["ok"])
        outcome = seq[min(self.calls.count(item) - 1, len(seq) - 1)]
        res = {"outcome": outcome, "http_status": None, "bytes": 0, "retry_after_s": None}
        if outcome == "ok":
            data = self.payload(item)
            sink.write(data)
            res.update(http_status=200, bytes=len(data))
        elif outcome.startswith("http_"):
            res["http_status"] = int(outcome.split("_")[1])
            if outcome == "http_429":
                res["retry_after_s"] = 7
        return res


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def run(store, graph, ids, *, plan=None, ack_omitted=True, **kw):
    plan = plan or ex.build_plan(ids, get_profile=store.get_profile, list_documents=store.list_documents)
    kw.setdefault("sleep", lambda s: None)
    return ex.execute(actor=kw.pop("actor", ACTOR), raw_ids=ids, token=kw.pop("token", TOKEN), expected_digest=kw.pop("digest", plan.digest), ack_omitted=ack_omitted,
                      downloader=graph, get_profile=store.get_profile, list_documents=store.list_documents, **kw)


def names_in(outcome):
    with zipfile.ZipFile(outcome.zip_stream) as z:
        return sorted(z.namelist()), {n: z.read(n) for n in z.namelist()}, [i.compress_type for i in z.infolist()]


def temp_dirs(env):
    return [p.name for p in env["tmp"].iterdir()]


# ───────────────────────── plan y límites ─────────────────────────
def test_plan_includes_every_cv_of_every_candidate_and_totals_declared_bytes():
    store = Store(profiles(2), {"p1": [doc("a", "p1", size=2000), doc("b", "p1", size=3000, uploaded="2026-09-01T00:00:00+00:00")], "p2": [doc("c", "p2", size=500)], })
    plan = ex.build_plan(["p1", "p2"], get_profile=store.get_profile, list_documents=store.list_documents)
    assert plan.ok and [d["document_id"] for d in plan.docs] == ["a", "b", "c"] and plan.declared_bytes == 5500
    assert [(c["name"], c["docs_total"], c["docs_included"]) for c in plan.candidates] == [("Cand1 Apellido", 2, 2), ("Cand2 Apellido", 1, 1)]
    assert plan.digest and len(plan.digest) == 64 and plan.estimate_s > 0


def test_candidate_limit_is_checked_before_reading_any_profile():
    store = Store(profiles(30), {})
    plan = ex.build_plan([f"p{i}" for i in range(1, 27)], get_profile=store.get_profile, list_documents=store.list_documents)
    assert plan.reason == ex.REASON_OVER_CANDIDATES and store.profile_calls == [] and ex.MAX_CANDIDATES == 25


def test_file_and_total_limits_block_the_plan():
    many = {"p1": [doc(f"d{i}", "p1", size=10) for i in range(61)]}
    plan = ex.build_plan(["p1"], get_profile=Store(profiles(1), many).get_profile, list_documents=Store(profiles(1), many).list_documents)
    assert plan.reason == ex.REASON_OVER_FILES and ex.MAX_FILES == 60
    big = {"p1": [doc(f"d{i}", "p1", size=24 * MB) for i in range(5)]}                       # 120 MB declarados, cada uno ≤ 25 MB
    s = Store(profiles(1), big)
    assert ex.build_plan(["p1"], get_profile=s.get_profile, list_documents=s.list_documents).reason == ex.REASON_OVER_TOTAL and ex.MAX_TOTAL_BYTES == 100 * MB
    ok = {"p1": [doc(f"d{i}", "p1", size=24 * MB) for i in range(4)]}                         # 96 MB: dentro del tope
    s = Store(profiles(1), ok)
    assert ex.build_plan(["p1"], get_profile=s.get_profile, list_documents=s.list_documents).ok


def test_oversized_or_unlocatable_documents_are_reported_as_omitted_not_downloaded():
    docs = {"p1": [doc("ok", "p1"), doc("big", "p1", size=25 * MB + 1), doc("loc", "p1", item="../..//x"), doc("edge", "p1", size=25 * MB)]}
    s = Store(profiles(1), docs)
    plan = ex.build_plan(["p1"], get_profile=s.get_profile, list_documents=s.list_documents)
    assert sorted(d["document_id"] for d in plan.docs) == ["edge", "ok"] and ex.MAX_FILE_BYTES == 25 * MB                    # 25 MB justos entran
    assert sorted((o["document_id"], o["reason"]) for o in plan.omitted) == [("big", "too_large"), ("loc", "no_location")]


def test_unavailable_deleted_empty_and_no_document_selections():
    s = Store({**profiles(2), "gone": {"first_name": "Baja", "deleted_at": "2026-01-01"}}, {"p1": [doc("a", "p1")]})
    plan = ex.build_plan(["p1", "gone", "nope", "../x"], get_profile=s.get_profile, list_documents=s.list_documents)
    assert plan.ok and plan.unavailable == ["gone", "nope"] and [c["id"] for c in plan.candidates] == ["p1"]
    assert ex.build_plan([], get_profile=s.get_profile, list_documents=s.list_documents).reason == ex.REASON_EMPTY
    assert ex.build_plan(["gone"], get_profile=s.get_profile, list_documents=s.list_documents).reason == ex.REASON_NONE_AVAILABLE
    assert ex.build_plan(["p2"], get_profile=s.get_profile, list_documents=s.list_documents).reason == ex.REASON_NO_DOCUMENTS


def test_plan_digest_changes_when_the_documents_change_and_is_order_independent():
    docs = {"p1": [doc("a", "p1")], "p2": [doc("b", "p2")]}
    s = Store(profiles(2), docs)
    d1 = ex.build_plan(["p1", "p2"], get_profile=s.get_profile, list_documents=s.list_documents).digest
    assert d1 == ex.build_plan(["p2", "p1"], get_profile=s.get_profile, list_documents=s.list_documents).digest
    docs["p2"].append(doc("c", "p2"))
    assert d1 != ex.build_plan(["p1", "p2"], get_profile=s.get_profile, list_documents=s.list_documents).digest


def test_a_source_failure_is_reported_without_details():
    def boom(_pid):
        raise OSError("detalle interno de disco /srv/secreto")
    with pytest.raises(ex.ExportSourceError) as err:
        ex.build_plan(["p1"], get_profile=lambda p: {"first_name": "A"}, list_documents=boom)
    assert "secreto" not in str(err.value)


# ───────────────────────── nombres seguros ─────────────────────────
def test_zip_names_are_sanitized_unique_and_contained():
    for evil in ("../../etc/passwd", "..\\..\\win.ini", "a/b\\c", "CON", "nul.txt", "x\x00y\x1fz", "<>:\"|?*", "   ...   ", "", "a" * 500):
        out = ex.safe_component(evil)
        assert "/" not in out and "\\" not in out and ".." not in out and not any(ord(c) < 32 for c in out) and len(out) <= 80 and out.strip(" .") == out and out
    assert ex.safe_component("CON").upper().startswith("_CON") and ex.safe_filename("../../x.PDF") == "_x.pdf" and ex.safe_filename("sin extensión") == "sin extensión"
    assert ex.safe_filename("a" * 300 + ".pdf").endswith(".pdf") and len(ex.safe_filename("a" * 300 + ".pdf")) <= 75
    alloc = ex._NameAllocator()
    assert [alloc.allocate("C", "cv.pdf"), alloc.allocate("C", "CV.pdf"), alloc.allocate("c", "cv.pdf")] == ["C/cv.pdf", "C/CV (2).pdf", "c/cv (3).pdf"]


# ───────────────────────── ejecución: camino feliz, auditoría, privacidad ─────────────────────────
def standard_store():
    return Store(profiles(2), {"p1": [doc("a", "p1", filename="Cand1 CV.pdf"), doc("b", "p1", filename="Cand1 CV.pdf", uploaded="2026-09-01T00:00:00+00:00")], "p2": [doc("c", "p2", filename="CV Cand2.docx")]})


def test_successful_export_delivers_a_stored_zip_with_the_exact_bytes_and_sanitized_unique_names(env):
    store, graph = standard_store(), Graph()
    out = run(store, graph, ["p1", "p2"])
    assert out.status == "delivered_full" and out.files_ok == 3 and out.zip_name.startswith("cvs-candidatos-") and out.zip_name.endswith(".zip") and out.zip_size == len(out.zip_stream.read()) and not out.failed
    out.zip_stream.seek(0)
    names, content, kinds = names_in(out)
    assert names == ["Cand1 Apellido/Cand1 CV (2).pdf", "Cand1 Apellido/Cand1 CV.pdf", "Cand2 Apellido/CV Cand2.docx"] and set(kinds) == {zipfile.ZIP_STORED}
    assert content["Cand1 Apellido/Cand1 CV.pdf"] == Graph().payload("item-a") and content["Cand2 Apellido/CV Cand2.docx"] == Graph().payload("item-c")
    assert set(graph.tokens) == {TOKEN} and all(u.startswith("https://graph.microsoft.com/v1.0/drives/b!drive/items/item-") and u.endswith("/content") for u in graph.urls)
    out.cleanup()
    assert temp_dirs(env) == []


def test_zip_is_never_left_on_disk_after_delivery_and_nothing_persists_outside_the_audit(env):
    out = run(standard_store(), Graph(), ["p1", "p2"])
    if os.name == "posix":
        assert temp_dirs(env) == []                                                          # desvinculado antes de entregar: no hay ZIP en disco aunque el cliente se desconecte
    out.cleanup()
    assert temp_dirs(env) == []
    files = sorted(str(p.relative_to(env["base"])).replace("\\", "/") for p in env["base"].rglob("*") if p.is_file())
    assert files == [f"human-capital/cv-exports/{out.operation_id}/intent.json", f"human-capital/cv-exports/{out.operation_id}/result.json"]
    assert not list(env["base"].rglob("*.zip")) and not list(env["base"].rglob("*.pdf")) and not list(env["base"].rglob("*.docx"))


def test_audit_records_actor_ids_counts_and_codes_but_no_names_filenames_urls_or_tokens(env):
    out = run(standard_store(), Graph({"item-b": ["http_403"]}), ["p1", "p2"], allow_partial=False)
    audit = ex.read_audit(out.operation_id)
    intent, result = audit["intent"], audit["result"]
    assert intent["actor"] == ACTOR and intent["kind"] == "cv_export" and intent["mode"] == "full" and intent["profile_ids"] == ["p1", "p2"] and sorted(intent["document_ids"]) == ["a", "b", "c"]
    assert intent["counts"] == {"candidates": 2, "documents": 3, "declared_bytes": 3000, "omitted": 0} and intent["limits"]["files"] == 60 and intent["plan_digest"]
    assert result["outcome"] == "withheld_partial" and result["files_ok"] == 2 and {d["document_id"]: d["outcome"] for d in result["documents"]} == {"a": "ok", "b": "forbidden", "c": "ok"}
    raw = json.dumps(audit, ensure_ascii=False)
    for forbidden in ("Cand1", "Cand2", "Apellido", ".pdf", ".docx", "CV_", "sharepoint", "secreto", "https://", TOKEN, "b!drive", "item-"):
        assert forbidden not in raw, forbidden


def test_logs_contain_no_names_filenames_or_tokens(caplog, env):
    with caplog.at_level(logging.DEBUG):
        run(standard_store(), Graph({"item-b": ["http_403"]}), ["p1", "p2"])
    text = "\n".join(r.getMessage() for r in caplog.records)
    for forbidden in ("Cand1", "Apellido", ".pdf", "sharepoint", TOKEN, "b!drive"):
        assert forbidden not in text, forbidden


def test_token_is_required_and_a_stale_plan_or_unacknowledged_omissions_do_nothing(env):
    store, graph = standard_store(), Graph()
    with pytest.raises(ex.ExportInputError):
        run(store, graph, ["p1"], token="")
    assert graph.calls == []
    assert run(store, graph, ["p1", "p2"], digest="0" * 64).status == "stale_plan" and graph.calls == [] and not (env["base"] / "human-capital").exists()
    omit = Store(profiles(1), {"p1": [doc("ok", "p1"), doc("big", "p1", size=26 * MB)]})
    assert run(omit, graph, ["p1"], ack_omitted=False).status == "stale_plan" and graph.calls == [] and not (env["base"] / "human-capital").exists()
    done = run(omit, graph, ["p1"], ack_omitted=True)
    assert done.status == "delivered_full" and graph.calls == ["item-ok"]
    done.cleanup()


def test_known_omissions_acknowledged_are_listed_in_a_readme_inside_the_zip(env):
    omit = Store(profiles(1), {"p1": [doc("ok", "p1", filename="a.pdf"), doc("big", "p1", size=26 * MB, filename="enorme.pdf")]})
    out = run(omit, Graph(), ["p1"])
    names, content, _ = names_in(out)
    assert "LEEME_OMITIDOS.txt" in names and b"enorme.pdf" in content["LEEME_OMITIDOS.txt"] and b"25 MB" in content["LEEME_OMITIDOS.txt"] and out.status == "delivered_full"
    out.cleanup()


def test_blocked_selection_returns_the_reason_without_audit_or_graph(env):
    graph = Graph()
    out = run(Store(profiles(1), {}), graph, ["p1"], plan=None, digest="x")
    assert out.status == "blocked" and out.plan.reason == ex.REASON_NO_DOCUMENTS and graph.calls == [] and not (env["base"] / "human-capital").exists()


# ───────────────────────── auditoría fail-closed ─────────────────────────
def test_export_does_not_start_when_the_audit_cannot_be_confirmed_in_remote_mode(env, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(remote_store, "local_only", lambda: False)
    monkeypatch.setattr(remote_store, "read_only_mode", lambda: False)
    monkeypatch.setattr(remote_store, "write_through", lambda path, data, **k: remote_store.DURABLE_LOCAL)         # Spaces no confirmó
    graph = Graph()
    with pytest.raises(ex.ExportAuditUnavailable):
        run(standard_store(), graph, ["p1", "p2"])
    assert graph.calls == [] and temp_dirs(env) == []                                        # ni una llamada a Graph, ni un directorio temporal


def test_export_does_not_start_in_read_only_mode_or_on_local_io_errors(env, monkeypatch):
    graph = Graph()
    monkeypatch.setattr(remote_store, "read_only_mode", lambda: True)
    with pytest.raises(ex.ExportAuditUnavailable):
        run(standard_store(), graph, ["p1"])
    monkeypatch.undo()
    monkeypatch.setattr(config, "BASE_DIR", env["base"])
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(ex.tempfile, "tempdir", str(env["tmp"]))
    monkeypatch.setattr(remote_store, "write_through", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denegado")))
    with pytest.raises(ex.ExportAuditUnavailable):
        run(standard_store(), graph, ["p1"])
    assert graph.calls == []


def test_a_confirmed_remote_audit_lets_the_export_proceed(env, monkeypatch):
    written = []
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(remote_store, "local_only", lambda: False)
    monkeypatch.setattr(remote_store, "read_only_mode", lambda: False)

    def confirmed(path, data, **k):
        written.append(Path(path).name)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(data, encoding="utf-8")
        return remote_store.DURABLE_REMOTE
    monkeypatch.setattr(remote_store, "write_through", confirmed)
    out = run(standard_store(), Graph(), ["p1", "p2"])
    assert out.status == "delivered_full" and written == ["intent.json", "result.json"]
    out.cleanup()


def test_if_the_result_cannot_be_confirmed_the_zip_is_not_delivered_and_the_temp_is_cleaned(env, monkeypatch):
    calls = []
    real = remote_store.write_through

    def flaky(path, data, **k):
        calls.append(Path(path).name)
        if Path(path).name == "result.json":
            raise PermissionError("sin espacio")
        return real(path, data, **k)
    monkeypatch.setattr(remote_store, "write_through", flaky)
    with pytest.raises(ex.ExportAuditResultUnavailable):                                   # subclase de ExportAuditUnavailable: se distingue de «no pudo ni empezar»
        run(standard_store(), Graph(), ["p1", "p2"])
    assert issubclass(ex.ExportAuditResultUnavailable, ex.ExportAuditUnavailable) and calls[0] == "intent.json" and temp_dirs(env) == []


# ───────────────────────── errores parciales y segunda confirmación ─────────────────────────
def test_a_failed_document_withholds_the_zip_and_names_the_failures(env):
    store = standard_store()
    out = run(store, Graph({"item-b": ["http_404"], "item-c": ["http_403"]}), ["p1", "p2"])
    assert out.status == "withheld_partial" and out.zip_stream is None and out.files_ok == 1
    assert sorted((f["document_id"], f["code"]) for f in out.failed) == [("b", "not_found"), ("c", "forbidden")] and all(f["candidate"] and f["filename"] and f["message"] for f in out.failed)
    assert temp_dirs(env) == []


def test_partial_is_delivered_only_with_the_explicit_second_confirmation_covering_exactly_those_failures(env):
    store, plan = standard_store(), None
    graph = Graph({"item-b": ["http_404"]})
    first = run(store, graph, ["p1", "p2"])
    ack = [f["document_id"] for f in first.failed]
    assert first.status == "withheld_partial" and ack == ["b"]
    # allow_partial SIN reconocer los fallos: no entrega
    assert run(store, graph, ["p1", "p2"], allow_partial=True, ack_failed=[]).status == "withheld_partial"
    # con la confirmación: entrega parcial + LEEME con lo omitido y operación enlazada con la anterior
    second = run(store, graph, ["p1", "p2"], allow_partial=True, ack_failed=ack, parent_operation_id=first.operation_id)
    assert second.status == "delivered_partial" and second.files_ok == 2
    names, content, _ = names_in(second)
    assert "LEEME_OMITIDOS.txt" in names and b"Cand1 Apellido" in content["LEEME_OMITIDOS.txt"] and b"404" in content["LEEME_OMITIDOS.txt"] and len(names) == 3
    intent = ex.read_audit(second.operation_id)["intent"]
    assert intent["mode"] == "partial_consented" and intent["parent_operation_id"] == first.operation_id and intent["acknowledged_failed_ids"] == ["b"]
    assert ex.read_audit(second.operation_id)["result"]["outcome"] == "prepared_partial"
    second.cleanup()


def test_a_new_failure_not_covered_by_the_consent_withholds_again(env):
    store = standard_store()
    out = run(store, Graph({"item-b": ["http_404"], "item-c": ["http_403"]}), ["p1", "p2"], allow_partial=True, ack_failed=["b"])
    assert out.status == "withheld_partial" and {f["document_id"] for f in out.failed} == {"b", "c"}


def test_when_the_second_attempt_succeeds_completely_a_full_zip_is_delivered(env):
    store = standard_store()
    out = run(store, Graph(), ["p1", "p2"], allow_partial=True, ack_failed=["b"])
    assert out.status == "delivered_full" and not out.failed
    out.cleanup()


def test_nothing_obtained_is_a_failure_not_an_empty_zip(env):
    out = run(standard_store(), Graph({"item-a": ["http_403"], "item-b": ["http_403"], "item-c": ["http_403"]}), ["p1", "p2"], allow_partial=True, ack_failed=["a", "b", "c"])
    assert out.status == "failed" and out.zip_stream is None and out.files_ok == 0 and ex.read_audit(out.operation_id)["result"]["outcome"] == "failed"


def test_an_expired_token_stops_the_run_instead_of_hammering_graph(env):
    graph = Graph({"item-a": ["http_401"]})
    out = run(standard_store(), graph, ["p1", "p2"])
    assert out.status == "failed" and graph.calls == ["item-a"] and {f["document_id"]: f["code"] for f in out.failed} == {"a": "token_expired", "b": "not_attempted", "c": "not_attempted"}


# ───────────────────────── reintentos, presupuesto y topes ─────────────────────────
def test_a_transient_failure_is_retried_once_honoring_a_capped_retry_after(env):
    sleeps = []
    graph = Graph({"item-a": ["http_429", "ok"]})
    out = run(Store(profiles(1), {"p1": [doc("a", "p1")]}), graph, ["p1"], sleep=sleeps.append)
    assert out.status == "delivered_full" and graph.calls == ["item-a", "item-a"] and sleeps == [ex.RETRY_AFTER_CAP_S] and out.results[0]["attempts"] == 2          # Retry-After 7 → tope 5
    out.cleanup()


def test_retries_are_bounded_and_not_applied_to_permanent_errors(env):
    graph = Graph({"item-a": ["http_429", "http_429", "ok"], "item-b": ["http_404"], "item-c": ["timeout", "timeout", "ok"]})
    out = run(standard_store(), graph, ["p1", "p2"])
    assert graph.calls.count("item-a") == 2 and graph.calls.count("item-b") == 1 and graph.calls.count("item-c") == 2
    assert {f["document_id"]: f["code"] for f in out.failed} == {"a": "throttled", "b": "not_found", "c": "timeout"}
    assert {r["document_id"]: r["attempts"] for r in out.results} == {"a": 2, "b": 1, "c": 2}


def test_no_new_file_is_started_after_the_time_budget_and_the_rest_are_reported(env):
    clock = Clock()
    docs = {"p1": [doc(f"d{i}", "p1", uploaded=f"2026-10-{20 - i:02d}T00:00:00+00:00") for i in range(10)]}
    graph = Graph(clock=clock, cost=10.0)                                                   # cada CV «tarda» 10 s en el reloj simulado
    out = run(Store(profiles(1), docs), graph, ["p1"], clock=clock)
    assert len(graph.calls) == 5 and out.status == "withheld_partial" and [f["code"] for f in out.failed] == ["time_budget"] * 5      # 0,10,20,30,40 s < 45 s
    assert ex.NO_NEW_FILE_AFTER_S < ex.TIME_BUDGET_S < 100


def test_each_read_is_bounded_by_the_per_file_and_total_caps_and_the_remaining_budget(env, monkeypatch):
    seen = []

    def spy(url, token, sink, *, cap_bytes, socket_timeout, deadline_s, cancel=None, declared_size=None):
        seen.append((cap_bytes, socket_timeout, deadline_s, declared_size))
        sink.write(b"x" * 2000)
        return {"outcome": "ok", "http_status": 200, "bytes": 2000}
    out = run(Store(profiles(1), {"p1": [doc("a", "p1", size=2000), doc("b", "p1", size=2000, uploaded="2026-01-01T00:00:00+00:00")]}), spy, ["p1"])
    assert seen[0][0] == ex.MAX_FILE_BYTES and seen[1][0] == ex.MAX_FILE_BYTES and seen[0][1] <= ex.SOCKET_TIMEOUT_S and seen[0][2] <= ex.PER_FILE_DEADLINE_S and seen[0][3] == 2000
    out.cleanup()
    monkeypatch.setattr(ex, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(ex, "MAX_TOTAL_BYTES", 25)                                          # el tope total restante acota cada lectura posterior
    four = Store(profiles(1), {"p1": [doc(f"d{i}", "p1", size=1, uploaded=f"2026-0{9 - i}-01T00:00:00+00:00") for i in range(4)]})
    caps = []

    def fat(url, token, sink, *, cap_bytes, **k):
        caps.append(cap_bytes)
        data = b"x" * cap_bytes
        sink.write(data)
        return {"outcome": "ok", "http_status": 200, "bytes": len(data)}
    run(four, fat, ["p1"])
    assert caps == [10, 10, 5, 0]


def test_a_file_that_exceeds_the_cap_while_streaming_is_a_failure_not_a_truncated_entry(env):
    out = run(Store(profiles(1), {"p1": [doc("a", "p1", size=10)]}), Graph({"item-a": ["oversize_stream"]}), ["p1"])
    assert out.status == "failed" and out.failed[0]["code"] == "too_large" and out.zip_stream is None


# ───────────────────────── cancelación y limpieza ─────────────────────────
def test_cancellation_stops_the_run_cleans_up_and_records_it(env):
    cancel = threading.Event()
    graph = Graph()

    class Cancelling(Graph):
        def __call__(self, *a, **k):
            r = super().__call__(*a, **k)
            cancel.set()                                                                  # el cliente se desconecta tras el primer CV
            return r
    graph = Cancelling()
    out = run(standard_store(), graph, ["p1", "p2"], cancel=cancel)
    assert out.status == "cancelled" and out.zip_stream is None and len(graph.calls) == 1 and temp_dirs(env) == []
    assert ex.read_audit(out.operation_id)["result"]["outcome"] == "cancelled"
    assert {f["code"] for f in out.failed} == {"cancelled"}


def test_an_exception_mid_run_cleans_the_temp_records_an_error_result_and_releases_the_slot(env):
    def boom(url, token, sink, **k):
        sink.write(b"parcial de un CV")
        raise RuntimeError("fallo con https://leak.example y " + TOKEN)
    with pytest.raises(RuntimeError):
        run(standard_store(), boom, ["p1", "p2"])
    assert temp_dirs(env) == []
    op = [p.name for p in (env["base"] / "human-capital" / "cv-exports").iterdir()][0]
    result = ex.read_audit(op)["result"]
    assert result["outcome"] == "error" and TOKEN not in json.dumps(result) and "leak" not in json.dumps(result)
    assert ex._active_actors == set()
    ok = run(standard_store(), Graph(), ["p1", "p2"])                                      # el slot quedó libre
    assert ok.status == "delivered_full"
    ok.cleanup()


def test_keyboard_interrupt_also_cleans_up(env):
    def interrupted(url, token, sink, **k):
        sink.write(b"parcial")
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run(standard_store(), interrupted, ["p1"])
    assert temp_dirs(env) == [] and ex._active_actors == set()


def test_stale_temp_leftovers_are_swept_but_only_ours_and_only_old(env):
    old, young, foreign = env["tmp"] / "dwcvx-old", env["tmp"] / "dwcvx-young", env["tmp"] / "otra-cosa"
    for d in (old, young, foreign):
        d.mkdir()
        (d / "x").write_text("x")
    past = time.time() - 2 * ex.STALE_TEMP_S
    os.utime(old, (past, past))
    os.utime(foreign, (past, past))
    assert ex._sweep_stale_temp() == 1 and not old.exists() and young.exists() and foreign.exists()


def test_a_temp_dir_inside_the_application_is_refused_before_any_work(env, monkeypatch):
    monkeypatch.setattr(ex.tempfile, "tempdir", str(env["base"] / "tmpdentro"))
    (env["base"] / "tmpdentro").mkdir()
    graph = Graph()
    with pytest.raises(ex.ExportAuditUnavailable):
        run(standard_store(), graph, ["p1"])
    assert graph.calls == [] and not (env["base"] / "human-capital").exists()


# ───────────────────────── concurrencia ─────────────────────────
def test_one_export_per_user_and_a_global_maximum_with_slots_released_afterwards(env):
    gate, entered = threading.Event(), threading.Event()
    results = []

    class Blocking(Graph):
        def __call__(self, *a, **k):
            entered.set()
            gate.wait(5)
            return super().__call__(*a, **k)

    def worker(actor):
        try:
            out = run(standard_store(), Blocking(), ["p1"], actor=actor)
            results.append(("ok", out.status))
            out.cleanup()
        except Exception as exc:  # noqa: BLE001
            results.append(("err", type(exc).__name__))
    t1 = threading.Thread(target=worker, args=("a@x.com",))
    t1.start()
    assert entered.wait(5)
    with pytest.raises(ex.ExportBusy):
        run(standard_store(), Graph(), ["p1"], actor="A@X.com")                            # mismo usuario (sin distinguir mayúsculas)
    entered.clear()
    t2 = threading.Thread(target=worker, args=("b@x.com",))
    t2.start()
    assert entered.wait(5)
    with pytest.raises(ex.ExportBusy):
        run(standard_store(), Graph(), ["p1"], actor="c@x.com")                            # tope global (2)
    gate.set()
    t1.join(10)
    t2.join(10)
    assert sorted(results) == [("ok", "delivered_full"), ("ok", "delivered_full")] and ex._active_actors == set()
    again = run(standard_store(), Graph(), ["p1"], actor="a@x.com")
    assert again.status == "delivered_full"
    again.cleanup()
    assert ex.MAX_CONCURRENT == 2
