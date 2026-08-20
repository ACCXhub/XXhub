# AGENTS.md

## Project

- Repository: current checkout root.
- Remote: `ACCXhub/hlhub`
- Product: Windows-local AutoDy dashboard.
- Current published baseline: AutoDy `1.4.4`, Test Center `1.2.0`.
- Read `docs/codex/PROJECT_HANDOFF.md` before editing.
- Then read only files directly related to the current task.

## Start Every Session

```powershell
git status --short
git log -8 --oneline --decorate
git remote -v
```

- Preserve uncommitted user work.
- Do not reset, clean, stash, or overwrite unrelated changes.
- Confirm the process on `127.0.0.1:8765` belongs to this repository before restarting it.
- Use the project `.venv`; do not silently use global Python.
- Do not terminate unrelated Python, Chromium, Node.js, or browser processes.

## Safety

- Normal development, automated tests, preflight, and navigation checks must never type, prepare, paste, simulate, or send content in a real Douyin composer; use fake pages, mocks, fixtures, or read-only checks.
- The only exception is one user-initiated Test Center controlled composer dry run explicitly requested for a user-selected target. It is allowed only after the browser lock is held, navigation-only acceptance has passed, the selected and visible stable conversation IDs match, display names are consistent, and the composer is proved empty with no attachment.
- That controlled dry run may type only an in-memory temporary test value, must never press Enter, click a send control, call the send pipeline, or create a send attempt, and must clear exactly its own unchanged value and verify the composer is empty before reporting success.
- The temporary value must never be persisted, logged, printed, returned by an API response, included in status/history, or captured in a screenshot. The exception is never automatic and must not retry.
- Any identity mismatch must stop before the composer is focused or inspected. Existing drafts, attachments, changed text, ambiguous cleanup, or uncertain state must be preserved and stop the run.
- Never send a real Douyin message.
- Never retry when a send action may have occurred or the result is uncertain.
- Preserve identity checks, duplicate protection, confirmation, and the global browser lock.
- Core send safety must remain functional when Test Center is absent.
- Never expose real accounts, friends, handles, avatars, messages, cookies, tokens, profiles, logs, or backups.

## Data Boundaries

- Core runtime data stays outside version-controlled application files.
- Test Center runtime data belongs only under `data/modules/autody-test-center`.
- Test Center uninstall must preserve core targets, messages, histories, browser data, schedules, backups, and config.
- Do not store module settings or overrides in `config.yaml` or normal target records.
- Validate the exact expected path before recursive deletion.
- Never delete friend cache after an incomplete, failed, cancelled, timed-out, or ambiguous scan.

## UI

- Normal Dashboard and Friends pages must not contain manual preflight or Test Center controls.
- Test Center is an optional Settings child page, not a primary sidebar item.
- When uninstalled, it must not mount an iframe, load assets, poll APIs, or expose a working route.
- Match existing typography, spacing, icons, controls, and page headers.
- Verify UI changes in the live production build at `127.0.0.1:8765`.
- Rebuild frontend assets and restart only the confirmed AutoDy service.
- Inspect console errors, requests, dimensions, dialogs, scrolling, and common desktop widths.

## Implementation

- Reproduce and identify the root cause before broad refactoring.
- Prefer focused changes; do not redesign unrelated areas.
- Do not create unnecessary abstractions, fixtures, Markdown files, packages, or release assets.
- Do not leave placeholder controls or buttons that do not affect real state.
- Keep module compatibility consistent across manifest, backend, frontend, bundled ZIP, and registry.
- Preserve PowerShell 5.1 compatibility and ASCII-safe `.cmd` wrappers.
- Preserve valid `.venv` reuse and visible launcher failures.
- Source install may rebuild the frontend; portable install must not require Node.js.

## Verification

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
cd frontend
npm test
npm run build
cd ..
.\scripts\build-portable.ps1
```

- Run focused tests first and full suites before release.
- Recheck PowerShell parsing when installer, launcher, or packaging scripts change.
- Recheck privacy scans when release contents change.
- UI changes require fresh-browser acceptance.
- Do not claim success without command output or live-page evidence.

## Git and Release

- Never move, delete, or force-update a published tag.
- Never force-push.
- Wait for main CI before creating a new release tag.
- Use a new version when a tag or release already exists.
- Keep commits focused and run `git diff --check`.
- Do not commit runtime data, `.venv`, `node_modules`, logs, caches, module registry, or private screenshots.
- Do not publish duplicate copies of the same module unless explicitly required.
- Leave the working tree clean after a completed release task.
- After feature work is committed, pushed, merged when applicable, and no longer needs isolation, remove its obsolete worktree with Git, prune worktree metadata, and delete its obsolete dependency, build, test-cache, and packaging staging artifacts; retain current source, active environments, necessary release artifacts, and the installed/user-data copies.
- Do not let finished Codex worktrees, duplicated `node_modules`, browser/runtime bundles, package staging directories, or test/build caches accumulate indefinitely.

## Documentation

- Keep this file limited to durable rules.
- Keep current state in `docs/codex/PROJECT_HANDOFF.md`.
- Keep usage in `README.md` and history in `CHANGELOG.md`.
- Prefer one substantial engineering manual over many one-paragraph documents.
