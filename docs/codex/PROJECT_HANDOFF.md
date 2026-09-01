# AutoDy 项目交接

## 当前检查点

- canonical 分支为 `main`；本轮开始时 `HEAD` 与 `origin/main` 都是 `ff4789ef6dedfb8833a704a3983aab2513157381`。当前 v1.5.2 Master 仍在同一工作树中，尚未形成 release checkpoint；恢复时必须重新查询 Git，不把此快照当作长期仓库指令。
- 已恢复并收敛的 Master 包含：全 enabled-target daily denominator、定向/虚拟列表安全恢复、发送后 provenance、P0 隐私 failure evidence、P1 audit-only 与账号/安全验证分类、ProgramRoot/DataRoot/BrowserRoot 分离，以及 Dashboard 的显式 stale 状态。
- 本轮 Phase A 的唯一产品修正位于 `runner.py`：实时 audit 得到 `confirmed_missing` 时清除陈旧 failure，并仅通过既有 `TaskOutcomeStore.resume_confirmed_missing()` 把未过期 `UNCERTAIN`/`FINAL_FAILED` 日恢复为 `RETRY_PENDING`。它不跨越发送边界；`UNKNOWN` 仍不发送。
- RuntimeContext/message-pack root 的旧失败已分类为过时测试：内置默认 pack 的稳定 ID 是 `daily-greeting`，临时 program/data 根必须显式传入 `RuntimeContext`。当前 `src/autody/runtime.py`、CLI/API 和 `scripts/resolve-runtime-roots.ps1` 均保持 ProgramRoot、DataRoot、BrowserRoot 分离。
- 源码与已安装 BrowserRoot 都已有 Chromium；本轮只运行了 headless `doctor_playwright` 启动检查，没有访问抖音、没有输入或发送。

## 已执行的 Phase A 证据

- focused Python integration：`337 passed`；覆盖 runner/chat、friend discovery、P0 evidence、P1 audit/classification、CLI/API、runtime/message packs、安装脚本。
- frontend：Vitest `66 passed`；`npm run build` 成功，并把 tracked static 输出收敛到一组对应当前源码的 JS/CSS hash 资产。
- `git diff --check` 已通过。FastAPI/TestClient 有一条既有 deprecation warning，不影响测试结果。

## 继续发布的顺序

1. 对即将 checkpoint 的精确 source 运行当前 pre-tag gate：clean-source preflight、完整非 `release_build` Python suite、frontend tests/build、PowerShell 解析与 `git diff --check`。
2. 只有所有 pre-tag gate 通过后，提交 v1.5.2 release source；fetch 并确认与 `origin/main` 的非破坏性同步；若 source SHA 变化，按实际差异重验受影响 gate。
3. 在 `release_source_sha == validated_source_sha` 后创建并推送 `v1.5.2`。正式 workflow 从 tag 的 clean source 运行 MSI/Portable、privacy/package、manifest、发布与 public asset reverify；长时 `release_build` 保留给专门诊断，hosted MSI lifecycle 为非阻断诊断。
4. 只有 workflow 终态成功且公开资产复核通过后，再对 `D:\AutoDy` 做 identity-aware、保留 DataRoot 的只读安装态验收；绝不把真实抖音发送当作验收步骤。

## 稳定指针

- 项目边界、运行时根、module owners 与副作用限制：`AGENTS.md`。
- 发布/验证 owner：`.github/workflows/release.yml`、`scripts/build-release-from-clean-source.ps1`、`scripts/verify-release-artifacts.ps1`、`scripts/verify-msi-lifecycle.ps1`。
- Release/version truth：`pyproject.toml`、`frontend/package.json`、`.github/workflows/release.yml`、`CHANGELOG.md`、`docs/RELEASE_NOTES.md`。
