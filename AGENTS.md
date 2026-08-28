# AGENTS.md

## Project

- Repository: current checkout root.
- Remote: `ACCXhub/XXhub`.
- Product: Windows-local AutoDy dashboard.
- Confirmed public stable baseline before the current release pass: AutoDy `v1.4.4`.
- Current source version line and release target: `1.5.1`. The failed `v1.5.0` tag remains immutable history; treat `v1.5.1` as stable only after its formal workflow publishes and reverifies the GitHub Release assets.
- Read `docs/软件工程/00-文档总览.md` and `docs/codex/PROJECT_HANDOFF.md` before editing, then only files directly related to the task.

## Start Every Session

```powershell
git status --short
git log -8 --oneline --decorate
git remote -v
```

- Preserve uncommitted user work.
- Do not reset, clean, stash, or overwrite unrelated changes.
- Confirm a local listener belongs to this AutoDy installation before restarting/stopping it; a port or PID alone is not ownership proof.
- Use the project `.venv` for source development; do not silently use global Python.
- Do not terminate unrelated Python, Chromium, Node.js, browser, or PowerShell processes.

## Safety

- Normal development, automated tests, preflight, and navigation checks must never type, prepare, paste, simulate, or send content in a real Douyin composer; use fake pages, mocks, fixtures, or read-only checks.
- The only existing Test Center controlled composer dry-run boundary remains user-initiated, browser-lock protected, stable-conversation verified, empty-composer/no-attachment only; it never presses Enter, clicks send, calls the send pipeline, persists the temporary value, or retries.
- Any identity mismatch, existing draft/attachment, changed text, ambiguous cleanup, or uncertain state stops the run.
- Never send a real Douyin message during development/validation.
- Never retry when a send action may have occurred or the result is uncertain.
- Preserve stable identity/account-scope checks, duplicate protection, confirmation, and the global browser lock.
- Core send safety must work when Test Center is absent.
- Never expose real accounts, friends, handles, avatars, messages, cookies, tokens, profiles, logs, or backups.

## Data and Identity Boundaries

- Core runtime data stays outside version-controlled application files.
- Durable friend identity is authoritative binding proof + account scope; `candidate_id`, conversation locator, display name, or avatar are not substitutes.
- Partial/failed/cancelled/stale discovery must not rewrite stable binding.
- Test Center runtime data belongs only under `data/modules/autody-test-center`; uninstall must preserve core targets/messages/history/browser data/schedules/backups/config.
- Validate the exact expected path before recursive deletion.
- ProgramRoot and DataRoot are separate under MSI; ordinary uninstall preserves DataRoot.

## UI

- Normal Dashboard/Friends pages do not duplicate Test Center controls.
- Test Center remains an optional Settings child page.
- Match existing typography, spacing, icons, controls, and page headers.
- Product UI changes require rendered verification in the confirmed production service; inspect console/requests/layout relevant to the Delta.

## Implementation

- Reproduce and identify the root cause before broad refactoring.
- Modify the canonical owner in place; do not create parallel `new/final/fixed/latest` implementations.
- Prefer focused changes and existing module contracts; do not redesign unrelated areas.
- Do not create unnecessary abstractions, fixtures, Markdown files, packages, or release assets.
- Preserve PowerShell 5.1 compatibility and ASCII-safe `.cmd` wrappers.
- Source install may rebuild frontend assets; Portable must not require Node.js.

## Verification

- Run focused validation first, chosen by the actual Delta/risk.
- Ordinary CI owns normal Python/frontend/PowerShell regression.
- Large `release_build` reproducibility tests, MSI/Portable build, privacy/package, lifecycle and public-asset verification belong to the formal Release workflow.
- Do not run the full Release gate for unrelated development or documentation changes.
- UI changes require rendered acceptance; installer/script changes require their focused PowerShell/WiX checks.
- Do not claim success without actual evidence.

## Git and Release

- Never force-push.
- Published tags/assets are immutable history. A tag whose Release has not successfully completed is a release-management decision; do not move it as an unrelated side effect.
- Keep commits focused and run `git diff --check` where applicable.
- Do not commit runtime data, `.venv`, `node_modules`, logs, caches, module registry, private screenshots, or `output/work` staging.
- Release state is not inferred from source version or local artifacts; it requires the formal GitHub Release workflow and public asset verification to succeed.
- Once remote Actions are started, do not burn an interactive session polling repeatedly; return after completion and inspect the result once.
- Remove superseded worktrees/staging/caches when their task no longer needs them; Git/Release preserve history.

## Documentation

- `docs/软件工程/00-文档总览.md` is the canonical documentation map; 01–13 cover feasibility, plan, requirements, architecture, detailed design, interfaces, data dictionary, test plan/report, user/install, operations, security and project summary.
- Keep current agent state in `docs/codex/PROJECT_HANDOFF.md`, public usage in `README.md`, version history in `CHANGELOG.md`, and release body in `docs/RELEASE_NOTES.md`.
- `docs/文档总览.md` and `docs/AUTODY_ENGINEERING_MANUAL.md` are compatibility pointers, not second copies of engineering content.
- Add a new document only when it has a distinct stable responsibility; otherwise update the existing canonical document.
- Documentation must reflect actual release/test state and must not contain real private runtime data.
