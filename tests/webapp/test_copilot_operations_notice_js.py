"""Aviso global de operaciones (copilot_operations.js): A1 «Abrir Assessment» consume el aviso; A2 una sola representación visible por
(tipo, cliente, modelo). Se ejecuta el JS REAL con Node sobre un DOM mínimo simulado (sin dependencias nuevas)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[2] / "webapp" / "static" / "copilot_operations.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="requiere Node")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const store = new Map();
function el(tag) {
  const n = {tag, className: "", textContent: "", dataset: {}, children: [], listeners: {}, removed: false, attrs: {}, href: "",
    appendChild(c) { this.children.push(c); return c; }, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { this.listeners[t] = f; }, remove() { this.removed = true; }};
  return n;
}
const ctx = {
  window: {}, localStorage: {getItem: (k) => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, v)},
  document: {readyState: "complete", querySelector: () => null, querySelectorAll: () => [], body: {addEventListener() {}},
             createElement: el, createTextNode: (t) => ({text: t})},
  JSON, Array, Set, Map, Number, String, setTimeout, clearTimeout, console,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const api = ctx.window.dwCopilotOps;
const op = (id, client, root, state, created, kind) => ({run_id: id, kind: kind || "assessment", client, root_object: root, state, created_at: created,
  kind_label: "Assessment", done_label: "Assessment completado", result_label: "Abrir Assessment", result_url: "/r/" + id, log_url: "/l/" + id});
const ops = [op("old", "Contreras", "AM_Workforce", "RUNNING", 1), op("new", "Contreras", "AM_Workforce", "SUCCESS", 2),
             op("other", "Contreras", "AM_Turnover", "RUNNING", 1.5), op("pub", "Contreras", "AM_Workforce", "SUCCESS", 0.5, "publication")];
const out = {};
out.visible = api._visible(ops, new Set(), new Set()).map((o) => o.run_id);
out.newestDismissed = api._visible(ops, new Set(["new"]), new Set()).map((o) => o.run_id);
out.newestFollowed = api._visible(ops, new Set(), new Set(["new"])).map((o) => o.run_id);
let closed = 0;
const box = api._banner(ops[1], () => { closed++; });
const find = (n, pred) => pred(n) ? n : (n.children || []).reduce((a, c) => a || find(c, pred), null);
const openLink = find(box, (n) => n.dataset && n.dataset.opOpenResult === "1");
out.openHref = openLink.href;
openLink.listeners.click();
out.afterOpen = {removed: box.removed, closed, dismissed: JSON.parse(store.get("dw-copilot-ops-dismissed"))};
out.visibleAfterOpen = api._visible(ops, new Set(out.afterOpen.dismissed), new Set()).map((o) => o.run_id);
const box2 = api._banner(ops[2], () => { closed++; });
const x = find(box2, (n) => n.className === "whatsnew-banner__close");
x.listeners.click();
out.afterClose = {removed: box2.removed, dismissed: JSON.parse(store.get("dw-copilot-ops-dismissed"))};
console.log(JSON.stringify(out));
"""


def _run(tmp_path):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(script), str(JS)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_successive_runs_of_the_same_model_show_one_notice_and_other_models_stay_independent(tmp_path):
    out = _run(tmp_path)
    assert out["visible"] == ["new", "other", "pub"]               # «old» (en curso) no convive con «new» (completada); otros modelos/tipos siguen
    assert out["newestDismissed"] == ["other", "pub"]              # cerrada la más reciente, la anterior no reaparece
    assert out["newestFollowed"] == ["other", "pub"]               # la sigue su propio panel: tampoco se duplica


def test_opening_the_assessment_consumes_the_notice_like_closing_it(tmp_path):
    out = _run(tmp_path)
    assert out["openHref"] == "/r/new"                             # la navegación se mantiene (no se impide el enlace)
    assert out["afterOpen"]["removed"] and out["afterOpen"]["closed"] == 1 and "new" in out["afterOpen"]["dismissed"]
    assert "new" not in out["visibleAfterOpen"] and "old" not in out["visibleAfterOpen"]   # persiste al navegar (misma marca que ✕)
    assert out["afterClose"]["removed"] and "other" in out["afterClose"]["dismissed"]      # ✕ conserva su comportamiento
