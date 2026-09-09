# ShudaoLe · Roadmap

[简体中文](ROADMAP.md) | [English](ROADMAP_EN.md)

This roadmap is based on a full code review of v1.2.0 (Sep 2026), ordered by priority. Each item carries its rationale and expected outcome, with checkboxes for tracking. The direction is confirmed: **full engineering maturity** + four feature tracks (download experience, catalog extension, scripting, update checks).

> Already healthy (no need to rebuild): local server binds 127.0.0.1 only, request-origin validation (anti DNS-rebinding), path-traversal protection, HTML escaping, SSRF URL validation, a classified exception hierarchy, and request retries — these are in good shape.

---

## P0 · Correctness fixes (immediate, zero architectural risk)

All doable in one patch release (v1.2.1) without touching the architecture.

- [ ] **P0-1 Fix browser-profile directory naming leak**
  `auto_fetch_token.py:279-281` hardcodes the dedicated browser profile to `~/.workbuddy/smartedu-token-profile` — `.workbuddy` is the AI-tool directory name from the dev machine, and it gets created as an unexplained hidden directory on **end-user machines**.
  Change to `~/.config/shudaole/token-profile` (on Windows, `%USERPROFILE%\.config\shudaole\` works fine — no registry needed).
  *Why*: brand consistency + no inexplicable directories left on user machines. *Outcome*: clean, predictable user-machine layout.
  *Migration*: if the old path exists at startup, silently move it or simply abandon it (one re-login restores the session).

- [ ] **P0-2 Atomic catalog-cache writes**
  `smartedu_downloader.py:811-820` calls `write_text` directly — an interrupted process leaves a truncated JSON; and `except OSError: pass` swallows the error silently.
  Change to "write `.tmp` → `os.replace()`" atomic replacement; log failures.
  *Why*: a corrupt cache only triggers a re-download, but truncated files plus silent failure are bad hygiene — a one-line-cost fix. *Outcome*: cache is never half-written; failures are visible.

- [ ] **P0-3 WebSocket continuation-frame support**
  The `_WS.recv` in `auto_fetch_token.py` does not handle `OP_CONTINUATION` (opcode 0x0) fragmentation, so long CDP messages (e.g. large Network events) can be truncated, causing sporadic token-capture misses.
  *Why*: a protocol-correctness bug, not an optimization. *Outcome*: token capture is stable on complex pages.

- [ ] **P0-4 Token storage location & permissions**
  `token.txt` is currently written next to the script/exe, in plaintext. Prefer the user config directory (`~/.config/shudaole/token.txt`), `chmod 600` on POSIX; the old same-directory file stays readable for compatibility.
  *Why*: the script directory may be synced or shared — plaintext credentials should not travel with the project. *Outcome*: a smaller credential-exposure surface.

- [ ] **P0-5 Narrow bare `except`s**
  Several `except Exception: pass` blocks in `auto_fetch_token.py` discard exception context. Catch specific exceptions and log them (to stderr at minimum).
  *Why*: silent failure is the most expensive kind to debug. *Outcome*: failures become diagnosable.

---

## P1 · Engineering foundation (prerequisite for all features)

Goal: turn "three big single files + zero tests + zero gates" into a sustainable engineering structure. **Keep the Python 3.8 baseline** (already promised in README; use `from __future__ import annotations` throughout).

- [ ] **P1-1 pyproject.toml + package split**
  New package `shudaole/`: `catalog.py` (catalog fetch/normalization/cascading filters/constants), `download.py` (download core), `net.py` (Session/retry/URL validation), `cli.py`, `config.py` (config loading), `token.py` (current auto_fetch_token moves in), `gui/` (server + extracted static page).
  The old entry points `smartedu_downloader.py` / `smartedu_downloader_gui.py` remain as **thin shims** (forwarding to the package) — `python smartedu_downloader.py` usage and all docs stay unchanged.
  *Why*: in the 1806-line single file, constants sit at L1164-1211, `normalize_catalog` spans 127 lines, and `process_one` contains three duplicated download blocks — piling features on top only gets worse. *Outcome*: clear module boundaries; every future change has a controllable scope.

- [ ] **P1-2 build.spec / CI adaptation for the split**
  Adjust `hiddenimports` to package modules; add package data files to datas; add `cache: pip` to release.yml; add a post-split smoke-build job (guarantee PyInstaller collection completeness before tagging).
  *Why*: the biggest risk of splitting is PyInstaller missing modules — CI must catch it. *Outcome*: no more "works locally, missing modules after packaging".

- [ ] **P1-3 Logging system (logging migration)**
  Core uses `logging.getLogger("shudaole")` uniformly; the GUI keeps the existing `on_log` callback facade — add a custom Handler that feeds log records into the UI log buffer, achieving a seamless migration.
  *Why*: prints/callbacks are scattered; no levels, timestamps, or dedup possible. *Outcome*: level-based filtering; GUI logs and CLI logs share one source.

- [ ] **P1-4 pytest foundation**
  First round covers pure functions: `normalize_title` / `build_filename` / `parse_content_id` / `normalize_catalog` / cascading filters / `validate_public_http_url` (target ≥90% branch coverage); network layer mocked with `requests-mock` for catalog/detail/PDF (including 401, Range 206, retry paths). Overall target: 60% first, 75-80% when stable.
  *Why*: the naming/filtering logic is this project's most complex and regression-prone part — and the most test-worthy. *Outcome*: safe to touch the download core in P2.

- [ ] **P1-5 CI quality gate (ci.yml)**
  New workflow: triggered on push/PR, matrix Python 3.8/3.10/3.12, `setup-python` with `cache: pip`, steps ruff (lint + format check) → mypy (lenient at first, tighten gradually) → pytest --cov.
  *Why*: gates belong in the daily flow, not the release flow — problems get caught at commit time. *Outcome*: sustainable quality, even with multiple contributors.

---

## P2 · Feature enhancements (in dependency order)

### Batch 2: download core

- [ ] **P2-1 Concurrent downloads (worker pool, 3-5)**
  Implemented in the downloader core (`download_many`); the GUI's `run_task` only orchestrates. Default concurrency 3, adjustable up to 5 — friendly to the textbook platform, avoiding rate limits.
  Cancel upgrades from a bool flag to a `threading.Event`: workers check between files; an in-flight file winds down gracefully (close the stream, keep the .part).
  *Why*: downloads are strictly sequential today; batch-downloading a whole grade set is the biggest speed pain. *Outcome*: 3-5× speedup in batch scenarios (bounded by platform bandwidth/rate limits).

- [ ] **P2-2 Resumable downloads + task persistence**
  Downloads write to `xxx.pdf.part` first, renamed on completion; on restart, a `.part`'s size seeds a `Range` request (fall back to a full re-download if the server rejects 206).
  Persist the task list to `~/.config/shudaole/tasks.json` (links, target dir, status, byte counts, timestamps); on startup the GUI detects unfinished tasks and offers "resume / discard".
  *Why*: task state is memory-only today — close the window and everything is lost; a 90%-downloaded PDF lost to a network blip restarts from zero. *Outcome*: a qualitative reliability jump; the UI can be closed and reopened freely.

- [ ] **P2-3 Catalog extension (publisher config externalized)**
  Externalize `PUB_GROUPS` / `SUPPORTED_SCOPE`: in-package `data/pubs.json` as defaults, user-level `~/.config/shudaole/pubs.json` deep-merged over them; add a "manage publishers" panel in the web UI (add/remove groups and labels, writes the user file).
  *Why*: adding the BSD publisher today requires source edits and a restart — impossible for ordinary users. *Outcome*: any publisher/phase opens up without touching code.

- [ ] **P2-4 Scripting capabilities**
  - Book-list export: filter results to CSV / Markdown (all dimension fields, for printing or sharing)
  - CLI batch mode: enhanced `--list-file` semantics + `--dry-run` + normalized exit codes (for external scripts)
  - Scheduled sync: a `--watch` mode (polls catalog updates, auto-enqueues new textbooks, configurable interval)
  *Why*: teacher/research-group users need batch and unattended scenarios. *Outcome*: from a "manual tool" to an "orchestratable tool".

### Batch 3: integration & updates

- [ ] **P2-5 In-app update check + one-click download (dual-channel, optional CN mirror)** 【decision locked】
  Asynchronously query the latest version after startup (version number only — no telemetry, no PII, honors the system proxy):
  - **Version source**: `https://api.github.com/repos/tau625/ShudaoLe/releases/latest`; auto-fallback to a mirror prefix when the direct GitHub connection is unreliable (mirror URL configurable in settings)
  - **Newer version → top banner** with two actions:
    - "View release": opens the Release page (the zero-risk baseline)
    - "One-click download": fetches `ShudaoLe-X.Y.Z-windows-x64.zip` to the local Downloads folder — official GitHub link by default, with an optional ghproxy-style mirror prefix for mainland-China acceleration; on completion, **prompt the user to extract and replace manually** (no auto-replacement)
  - **No silent auto-update**: a self-replacing unsigned exe is a double minefield (AV false positives + legal risk for a legally-sensitive tool). Default is a manual "check for updates" button; network checks can be fully disabled in settings.
  *Why*: without an update channel, security fixes never reach scattered users; mainland users often can't reach GitHub directly, so the mirror channel decides whether this feature is usable at all. *Outcome*: version fragmentation converges; P0-grade fixes reach users.

- [ ] **P2-6 Windows installer (Inno Setup)** 【decision locked】
  GitHub Actions windows-latest runners ship with Inno Setup 6; CI adds an `ISCC.exe installer.iss` step after zipping, producing `ShudaoLe-X.Y.Z-setup.exe` attached to the Release. The installer provides Start-menu/desktop shortcuts, a standard uninstaller, and a registered default download directory.
  **The portable zip stays alongside** — many teachers prefer the no-install version; both channels ship.
  *Why*: "extract a zip and double-click" doesn't look like real software to ordinary teachers, and an app with no uninstaller is more readily flagged by security software; setup.exe markedly lowers the adoption barrier. *Outcome*: distribution polish on par with commercial software; a winget community manifest can follow (`winget install` to install/upgrade — Microsoft's "real software" channel for free).

> **Code signing: decided — not purchasing for now** (Windows OV ≈ ¥500-900/yr, EV ≈ ¥2500+/yr, macOS $99/yr).
> The README's AV-whitelisting guidance stays; if false-positive reports cluster in the future, revisit Windows OV first.

---

## P3 · Nice-to-have

- [ ] **P3-1 Frontend poll consolidation**
  Merge the 4 independent timers (700ms status / 1000ms token / 3000ms leftovers / 1500ms counts) into one refresh coordinator that adapts frequency to page visibility and state; deduplicate the three copies of token-polling code.
  *Why*: lower steady-state overhead and code duplication. *Outcome*: predictable, debuggable frontend behavior.

- [ ] **P3-2 GUI route table**
  Replace the if-else hardcoded routing in `do_GET`/`do_POST` with dictionary dispatch; merge the three platform-specific copies of `pick_folder` into one function with platform branches.
  *Why*: 10+ endpoints already — if-else doesn't scale. *Outcome*: adding an endpoint becomes adding a table row.

- [ ] **P3-3 Docs & dev-notes sync**
  After the split, update the README structure section and the "Development Notes" chapter; start a CHANGELOG.md (currently only Release notes exist).
  *Why*: doc-code drift is the biggest maintenance cost. *Outcome*: a newcomer (or future you) gets up to speed in an hour.

---

## Execution batches & dependencies

| Batch | Content | Prerequisite |
|---|---|---|
| Batch 0 | P0-1 ~ P0-5 (ship as v1.2.1 patch) | none |
| Batch 1 | P1 engineering foundation (split/logging/tests/CI) | after the P0 release |
| Batch 2 | P2-1/2/3/4 (download core + config externalization + scripting) | P1 done |
| Batch 3 | P2-5 update check + one-click download, P2-6 Windows installer | P1 done (can partly parallel Batch 2) |
| Batch 4 | all of P3 | no hard dependency; interleaved |

## Risk register

- **Split vs. PyInstaller collection**: dynamic imports may be missed → triple insurance: thin shims + explicit hiddenimports + CI smoke build.
- **Python 3.8 baseline**: keep the promise (uniform `from __future__ import annotations`); only revisit if a required dependency drops 3.8 — and then announce prominently in Release notes.
- **Update-check compliance**: this tool is legally sensitive (PolyForm Noncommercial + textbook copyright). Public GitHub API only, zero user data transmitted, manual by default, fully disableable; never bundle an auto-updater.
- **Mainland-mirror dependency**: if the update-download mirror prefix points to a third-party public accelerator (ghproxy-style), it may go stale or be polluted — official links stay the default; mirrors are an explicit user opt-in, and the actual source is shown in the download prompt.
- **Concurrency vs. platform rate limits**: concurrency capped at 5 with a "use in moderation" note in the docs; on a platform-side 429, throttle automatically and notify.