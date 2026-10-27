"""P1 · compuerta de lectores: durante la activación y la recuperación nadie ve una versión sin confirmar."""
from __future__ import annotations

import threading
import time

import pytest

from core import publication_gate as gate


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "catalog_local").mkdir()
    gate.set_resolver(None)
    yield tmp_path / "catalog_local"
    gate.set_resolver(None)
    gate.clear(tmp_path / "catalog_local")


def test_without_a_closed_gate_readers_pass_at_no_cost(root):
    assert gate.read_state(root) is None
    gate.check(root)


def test_readers_wait_for_an_activation_and_pass_when_it_ends(root):
    gate.begin("op1", "Acme", root)
    done = []

    def reader():
        gate.check(root, wait=5)
        done.append(time.monotonic())

    # el hilo de prueba es el DUEÑO; el lector es otro hilo
    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.2)
    assert not done                                                           # espera mientras la compuerta está cerrada
    gate.end("op1", root)
    t.join(5)
    assert done and gate.read_state(root) is None


def test_the_owner_thread_never_blocks_itself(root):
    gate.begin("op1", "Acme", root)
    gate.check(root, wait=0)                                                   # no lanza: es la operación que la cerró
    gate.end("op1", root)


def test_a_reader_that_waits_too_long_gets_a_retryable_error(root):
    gate.begin("op1", "Acme", root)
    errors = []
    t = threading.Thread(target=lambda: errors.append(_raises(lambda: gate.check(root, wait=0.2))))
    t.start()
    t.join(5)
    assert isinstance(errors[0], gate.PublicationInProgress) and errors[0].retry_after > 0
    gate.end("op1", root)


def test_an_uncertain_publication_blocks_readers_immediately_until_resolved(root):
    gate.begin("op1", "Acme", root)
    gate.mark_uncertain("op1", "Acme", root)
    out = []
    t = threading.Thread(target=lambda: out.append(_raises(lambda: gate.check(root, wait=5))))
    t.start()
    t.join(5)
    assert isinstance(out[0], gate.PublicationBlocked) and out[0].operation_id == "op1" and out[0].client_dir == "Acme"
    gate.clear(root)
    other = []
    t = threading.Thread(target=lambda: other.append(_raises(lambda: gate.check(root, wait=0))))
    t.start()
    t.join(5)
    assert other == [None]


def test_the_resolver_can_open_the_gate_and_is_throttled(root, monkeypatch):
    gate.begin("op1", "Acme", root)
    gate.mark_uncertain("op1", "Acme", root)
    calls = []
    gate.set_resolver(lambda r: (calls.append(r), gate.clear(r)))
    monkeypatch.setattr(gate, "_last_resolve", 0.0)
    out = []
    t = threading.Thread(target=lambda: out.append(_raises(lambda: gate.check(root, wait=0))))
    t.start()
    t.join(5)
    assert out == [None] and len(calls) == 1                                   # el lector disparó la recuperación y pasó


def test_a_gate_only_ends_for_the_operation_that_closed_it(root):
    gate.begin("op1", "Acme", root)
    gate.end("otra", root)
    assert gate.read_state(root)["operation_id"] == "op1"
    gate.end("op1", root)
    assert gate.read_state(root) is None


def test_an_unreadable_gate_file_is_a_block_never_free(root):
    gate.gate_path(root).parent.mkdir(parents=True, exist_ok=True)
    gate.gate_path(root).write_text("{no es json", encoding="utf-8")
    assert gate.read_state(root)["state"] == gate.UNCERTAIN
    out = []
    t = threading.Thread(target=lambda: out.append(_raises(lambda: gate.check(root, wait=0))))
    t.start()
    t.join(5)
    assert isinstance(out[0], gate.PublicationBlocked)


def _raises(fn):
    try:
        fn()
    except Exception as exc:                                                    # noqa: BLE001
        return exc
    return None
