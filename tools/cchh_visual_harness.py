"""
tools/cchh_visual_harness.py — CCHH Visual QA Harness (localhost-only tooling).

Launches the REAL Danwor Hub (FastAPI + real Jinja templates + real HTMX +
real static JS/CSS + the real shell/topbar) against deterministic, fully
offline fixture data for Capital Humano — no Microsoft Graph, no
DigitalOcean Spaces, no SharePoint, no MSAL/Microsoft login. The app itself
is never modified and has no idea it is running inside a harness: this
script only (a) sets a few environment variables before importing `config`,
(b) monkeypatches the ONE already-lazy config attribute the repositories
read per-call, and (c) seeds fixture data through the repositories' own
internal write helpers. Zero new FastAPI routes, zero forked templates.

SEAMS REUSED (nothing invented):
  - `config.BASE_DIR` is read lazily, per call, by
    `profile_repository`/`human_capital_catalog_repository`
    (`config.BASE_DIR / "human-capital" / ...`) — never captured into a
    module-level constant at import time. This is the EXACT same seam the
    pytest suite already uses via `monkeypatch.setattr(config, "BASE_DIR",
    tmp_path)`; this script does the live-process equivalent, redirecting
    it to a scratch directory OUTSIDE the repo (never the real project's
    `human-capital/`, and never a fork of it).
  - `HUMAN_CAPITAL_ALLOWED_USERS`/`OPERATIONS_ALLOWED_USERS`/
    `COMMERCIAL_ALLOWED_USERS` are left to resolve NATURALLY from whatever
    the process/`.env` already configures (`config.py` auto-loads `.env`
    via `load_dotenv()`) — this script only APPENDS the synthetic QA
    identity to whatever each list already contains (never overwrites),
    patched directly on `auth_service` (the module `human_capital_access`/
    `operations_access`/`commercial_access` actually read at call time —
    `config`'s own copies are a separate, earlier name-binding that those
    three never consult). A real bug this script had before this fix:
    overwriting these three with ONLY the synthetic email silently erased
    whatever the owner's own `.env` already granted.
  - `ALLOWED_USERS` (base admission, never a per-workspace list) is forced
    to `"ALL"` — this only WIDENS who can authenticate at all, it can never
    remove anyone the owner already configured, so overwriting it (instead
    of merging) is safe.
  - `APP_OWNER` is NEVER touched here — it resolves from whatever the real
    environment/`.env` already has (default `"joconnor"`, see
    `config.py`), so the app's own owner-superuser rule
    (`webapp/access.py::is_owner_user`) grants that SAME real identity
    every registered workspace, exactly as it would outside the harness.
  - `auth_service.create_session(...)` mints session cookies — the SAME
    function the real Microsoft-login callback (`POST
    /api/auth/microsoft/token`) calls after MSAL validates an id_token.
    This never bypasses any Hub-side authorization check; it only skips
    the interactive MSAL popup. `SESSION_SECRET` falls back to its
    documented dev default when unset, and since the SAME process signs
    and later verifies the cookie, the harness is self-consistent
    regardless of which secret is active.
  - Fixture data is seeded by calling the repositories' OWN
    schema-producing internals (`human_capital_catalog_repository.
    build_snapshot`/`_persist_snapshot`, `profile_repository.
    _write_atomic`/`_profile_path`) — never hand-crafted JSON guessed at
    the shape, so fixtures can never silently drift from what
    create()/build_snapshot() actually produce.
  - `AZURE_CLIENT_ID`/`AZURE_TENANT_ID` are explicitly blanked so
    `/api/auth/microsoft/config` reports `enabled: false`; `hub_auth.js`'s
    `getApp()` then rejects immediately on first use, so `dwGraphToken()`
    never even attempts a network call — zero possibility of a real MSAL
    popup or Graph request from this harness.

DELIBERATELY NOT DONE (see the CCHH visual-QA ticket this implements):
  - no new route in `webapp/` (no `/dev`, no debug endpoint) — the FastAPI
    app served here is `webapp.main:app`, completely unmodified;
  - no auth bypass inside production code — the bypass is entirely this
    external script minting a session the same way real login does;
  - no fixture JSON written inside the real repo's `human-capital/`
    directory — the runtime copy lives under the OS temp directory,
    rebuilt fresh on every run;
  - no forked/duplicated CCHH templates — every page renders through the
    real `webapp/templates/**` tree.

USAGE
    python tools/cchh_visual_harness.py [--port 8097] [--keep]

`--keep` skips wiping the scratch runtime directory from a previous run
(useful for inspecting persisted localStorage-equivalent server state
between runs); default is always a fresh deterministic reseed.

On start, prints the URL to open and TWO `document.cookie = ...` one-liners
to paste into the browser devtools console — no login form to click
through: one authenticates as the synthetic multi-workspace QA fixture
user, the other as a local session for the real configured `APP_OWNER`
(who, per the Hub's own owner-superuser rule in `webapp/access.py`, reaches
every registered workspace regardless of that workspace's allowlist).

GOTCHA (real, observed): cookies for `127.0.0.1` are NOT port-scoped in any
browser — a `dw_session` cookie set while browsing the canonical Hub on
:8505 (or any other local Danwor instance) is also sent to this harness on
:8097. If that stale cookie decodes successfully (same dev `SESSION_SECRET`
fallback in both), it can silently shadow the harness's own cookie and get
rejected by THIS process's synthetic-only allowlists — a 403 that has
nothing to do with the harness's own env/session logic. If you ever also
run the canonical Hub locally in the same browser, use a private/incognito
window for the harness, or clear cookies for `127.0.0.1` before pasting the
line above.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "human_capital"
RUNTIME_DIR = Path(tempfile.gettempdir()) / "danwor_cchh_visual_harness_runtime"

HARNESS_USER = {
    "email": "cchh.visual.qa@danwor.local",
    "name": "CCHH Visual QA",
    "first_name": "CCHH",
    "family_name": "Visual QA",
}

# Puerto dedicado del harness -- NUNCA 8505 (runtime canónico local) ni 8096
# (usado transitoriamente por otros worktrees de este mismo programa CCHH).
DEFAULT_PORT = 8097


def _configure_env() -> None:
    """Env vars que es SEGURO forzar sin perder configuración existente --
    deben setearse ANTES de `import config`, la primera vez que ese módulo
    se importa en este proceso. Nunca toca el `.env` canónico en disco; sólo
    el `os.environ` de ESTE proceso.

    Las tres allowlists por-vertical (`HUMAN_CAPITAL_/OPERATIONS_/
    COMMERCIAL_ALLOWED_USERS`) NO se tocan acá -- se mergean DESPUÉS, en
    `_merge_qa_identity_into_allowlists()`, una vez que `config` ya corrió
    su propio `load_dotenv()` y sabemos qué hay REALMENTE configurado."""
    os.environ["ALLOWED_USERS"] = "ALL"
    # Blanqueado a propósito -- ver docstring del módulo, sección MSAL.
    os.environ["AZURE_CLIENT_ID"] = ""
    os.environ["AZURE_TENANT_ID"] = ""
    # Deliberadamente NUNCA seteadas acá (deben quedar ausentes):
    #   SPACES_ENDPOINT/SPACES_BUCKET/SPACES_KEY/SPACES_SECRET  -> PERSIST_ENABLED=False
    #   HC_SHAREPOINT_DRIVE_ID/HC_CATALOG_ITEM_ID                -> catalog
    #     refresh (acción, no lectura) reporta config_incomplete si se
    #     dispara -- el catálogo YA MATERIALIZADO (sembrado abajo) se lee
    #     tokenless, sin Graph, y no depende de esto.


def _merge_qa_identity_into_allowlists() -> None:
    """APÉNDICE, nunca reemplazo (bug real corregido -- ver docstring del
    módulo): lee lo que `auth_service` YA resolvió de `config`/`.env` para
    cada allowlist por-vertical, y le suma el email sintético si todavía no
    está. Parchea `auth_service` directo -- es el módulo que
    `human_capital_access`/`operations_access`/`commercial_access`
    realmente consultan en cada llamada (import de `config` es un
    name-binding propio de `auth_service`, separado)."""
    from webapp.services import auth_service

    for attr in ("HUMAN_CAPITAL_ALLOWED_USERS", "OPERATIONS_ALLOWED_USERS", "COMMERCIAL_ALLOWED_USERS"):
        current = (getattr(auth_service, attr) or "").strip()
        if current.upper() == "ALL":
            continue  # ya admite a cualquiera -- nada que sumar.
        emails = [e.strip() for e in current.split(",") if e.strip()]
        if HARNESS_USER["email"] not in emails:
            emails.append(HARNESS_USER["email"])
        setattr(auth_service, attr, ",".join(emails))


def _resolve_owner_emails() -> list[str]:
    """Identidad(es) del owner, derivadas de `APP_OWNER` -- NUNCA
    hardcodeadas acá. Reusa `webapp.access.owner_emails()` (el MISMO
    resolver que `is_owner_user` usa en producción), así que un alias sin
    dominio ("joconnor") se resuelve exactamente igual que en el Hub real."""
    from webapp import access

    return sorted(access.owner_emails())


def _seed_runtime(keep: bool) -> Path:
    """Redirige `config.BASE_DIR` (leído en vivo, por-llamada, por los dos
    repositorios de CCHH) a un directorio de scratch FUERA del repo, y
    siembra ahí los fixtures deterministas vía las funciones internas
    REALES de cada repositorio -- nunca JSON armado a mano adivinando el
    schema, y nunca dentro de `human-capital/` del repo real."""
    import config  # import tardío a propósito: recién acá config.py corre
    # su body de módulo, leyendo YA los env vars seteados en _configure_env().

    if not keep and RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    config.BASE_DIR = RUNTIME_DIR
    # Durabilidad LOCAL para el harness: no hay Spaces (SPACES_* ausentes a
    # propósito), pero el directorio de runtime scratch SÍ persiste entre
    # requests de este proceso. Se trata el write local como durabilidad
    # confirmada patcheando SÓLO `remote_store.push_path` (nunca `enabled()`,
    # para no gatillar ningún otro camino que espere Spaces real) -> así el
    # camino de ESCRITURA (Alta / Edición / Borrado lógico) se puede ejercitar
    # visualmente en el harness, no sólo la lectura de fixtures sembrados.
    from core import remote_store as _remote_store
    _remote_store.push_path = lambda _path: True
    # NOTA para quien lea esto: `config.CONTENT_PACKS_DIR`/`CASE_LIBRARY_DIR`/
    # etc. YA se calcularon en el import de arriba usando el `BASE_DIR`
    # ORIGINAL (son constantes de módulo, no relecturas) -- siguen apuntando
    # al repo real. Esto es INTENCIONAL: el shell/topbar/identidad animada
    # necesitan los content packs reales para verse como el Hub de verdad;
    # sólo el estado propio de CCHH (`human-capital/profiles`,
    # `human-capital/catalogs`) debe aislarse, y ESE es el único que ambos
    # repositorios leen vía `config.BASE_DIR` en vivo dentro de una función,
    # nunca capturado en un import.

    if keep and (RUNTIME_DIR / "human-capital").exists():
        print(f"[harness] --keep: reusando estado existente en {RUNTIME_DIR}")
        return RUNTIME_DIR

    import json as _json

    from webapp.services import human_capital_catalog_repository as catalog_repo
    from webapp.services import profile_repository

    # --- catálogo gobernado: build_snapshot()/_persist_snapshot() reales ---
    catalogs_raw = _json.loads((FIXTURES_DIR / "catalog.json").read_text(encoding="utf-8"))
    snapshot = catalog_repo.build_snapshot(
        catalogs_raw, refreshed_by=HARNESS_USER["email"],
        source={"drive_id": "harness-drive", "item_id": "harness-item"},
    )
    outcome = catalog_repo._persist_snapshot(snapshot)  # SUCCESS local-only, Spaces off
    print(f"[harness] catálogo sembrado ({outcome}): {catalog_repo.snapshot_path()}")

    # --- perfiles: mismo shape EXACTO que _create_locked(), sin el gate de
    #     durabilidad remota (irrelevante para fixtures de un solo proceso
    #     local) -- reusa _write_atomic/_profile_path reales, nunca un
    #     open().write() a mano. ---
    profiles_raw = _json.loads((FIXTURES_DIR / "profiles.json").read_text(encoding="utf-8"))
    now = "2026-09-01T12:00:00+00:00"
    for entry in profiles_raw:
        profile_id = entry["profile_id"]
        fields = {k: v for k, v in entry.items() if k != "profile_id"}
        payload = {
            "schema_version": profile_repository._SCHEMA_VERSION,
            "profile_id": profile_id,
            "revision": 1,
            **fields,
            "created_at": now,
            "updated_at": now,
        }
        profile_repository._write_atomic(profile_repository._profile_path(profile_id), payload)
    print(f"[harness] {len(profiles_raw)} perfiles sembrados bajo {RUNTIME_DIR / 'human-capital' / 'profiles'}")

    # Búsquedas (procesos de selección) — MISMO criterio que los perfiles:
    # se siembran vía los internals reales del repositorio (`_write_atomic`/
    # `_busqueda_path`), nunca JSON adivinado. El documento ya trae candidatos
    # embebidos + historial (ver tests/fixtures/human_capital/busquedas.json).
    from webapp.services import busqueda_repository
    busquedas_raw = _json.loads((FIXTURES_DIR / "busquedas.json").read_text(encoding="utf-8"))
    for entry in busquedas_raw:
        payload = {"schema_version": busqueda_repository._SCHEMA_VERSION, "revision": 1, **entry}
        busqueda_repository._write_atomic(busqueda_repository._busqueda_path(entry["busqueda_id"]), payload)
    print(f"[harness] {len(busquedas_raw)} búsquedas sembradas bajo {RUNTIME_DIR / 'human-capital' / 'busquedas'}")

    # Entrevistas — MISMO criterio (internals reales del repositorio, nunca
    # JSON adivinado). Ver tests/fixtures/human_capital/entrevistas.json:
    # cubre generadas-desde-pipeline (busqueda_id+application_id) y manuales
    # (sin Búsqueda), concretada/no concretada, con/sin feedback técnico, un
    # mismo Profile con más de una entrevista, y textos largos.
    from webapp.services import entrevista_repository
    entrevistas_raw = _json.loads((FIXTURES_DIR / "entrevistas.json").read_text(encoding="utf-8"))
    for entry in entrevistas_raw:
        payload = {"schema_version": entrevista_repository._SCHEMA_VERSION, "revision": 1, **entry}
        entrevista_repository._write_atomic(entrevista_repository._entrevista_path(entry["entrevista_id"]), payload)
    print(f"[harness] {len(entrevistas_raw)} entrevistas sembradas bajo {RUNTIME_DIR / 'human-capital' / 'entrevistas'}")
    return RUNTIME_DIR


def _mint_session_for(email: str, *, label: str) -> str:
    from webapp.services import auth_service
    return auth_service.create_session(
        {"email": email, "name": label, "first_name": label.split()[0], "family_name": " ".join(label.split()[1:]) or label}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--keep", action="store_true", help="No reseedear si ya existe estado de una corrida previa")
    args = parser.parse_args()

    if args.port == 8505:
        print("ERROR: 8505 es el runtime canónico local, no usarlo para el harness.", file=sys.stderr)
        raise SystemExit(1)

    _configure_env()
    runtime_dir = _seed_runtime(args.keep)  # importa config -> corre load_dotenv() -> ya sabemos qué hay REALMENTE configurado
    _merge_qa_identity_into_allowlists()    # recién ACÁ, nunca antes -- apéndice, nunca reemplazo

    owner_emails = _resolve_owner_emails()
    synthetic_token = _mint_session_for(HARNESS_USER["email"], label=HARNESS_USER["name"])
    owner_sessions = [(email, _mint_session_for(email, label="Local Owner Session")) for email in owner_emails]

    import uvicorn
    from webapp.main import app
    from webapp.services import auth_service as _auth_service_mod

    url = f"http://127.0.0.1:{args.port}/cchh"
    identities_line = ", ".join([HARNESS_USER["email"]] + owner_emails) if owner_emails else \
        f"{HARNESS_USER['email']} (APP_OWNER no está configurado -- ninguna sesión de owner disponible)"
    lines = [
        "=" * 78,
        "CCHH VISUAL QA HARNESS -- localhost, sin Graph/Spaces/SharePoint/MSAL real.",
        "=" * 78,
        f"  Identidades para las que este proceso puede emitir sesión local: {identities_line}",
        f"  runtime state (fuera del repo): {runtime_dir}",
        "  workspaces autorizados: Copiloto, Operaciones, Comercial, Capital Humano",
        "",
        f"  1) Abrir: {url}",
        "  2) Pegar en la consola de devtools UNA de las dos líneas (antes de",
        "     navegar, o recargar después):",
        "",
        f"     // usuario sintético de fixtures ({HARNESS_USER['email']}):",
        f'     document.cookie = "{_auth_service_mod.COOKIE_NAME}={synthetic_token}; path=/"',
    ]
    if owner_sessions:
        for email, token in owner_sessions:
            lines += [
                "",
                f"     // sesión local del owner configurado ({email}):",
                f'     document.cookie = "{_auth_service_mod.COOKIE_NAME}={token}; path=/"',
            ]
    else:
        lines += ["", "  (APP_OWNER no resuelve a ningún email en este entorno -- sólo el usuario sintético arriba está disponible.)"]
    lines += [
        "",
        "  Si además usás el Hub canónico local (:8505) en el MISMO navegador: las",
        "  cookies de 127.0.0.1 no se aíslan por puerto -- usá una ventana privada o",
        "  borrá cookies de 127.0.0.1 antes de pegar una línea de arriba, o una",
        "  cookie vieja puede pisar la de este proceso y dar un 403 que no tiene que",
        "  ver con este harness.",
        "",
        "Ctrl+C para detener.",
        "=" * 78,
    ]
    print("\n" + "\n".join(lines) + "\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
