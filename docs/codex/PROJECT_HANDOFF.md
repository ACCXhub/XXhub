# AutoDy 项目交接

## Outcome

维护现有 AutoDy Windows 本机产品，使其在好友身份、发送安全、服务恢复、Scheduler、安装/升级、DataRoot 和 Release 供应链上保持可验证、可恢复、fail-closed。当前产品功能不从零重构；优先关闭阻断性可靠性/安装/发布问题。

## Master

当前代码线与正式发布目标为 1.5.2，核心已接受能力包括：

- durable friend proof、discovery candidate、conversation locator 明确分层；
- `binding_recovery.resolve_stable_binding` 是唯一权威运行时解析，`reassociate_stable_binding` 是显式重新关联写入口；
- Friends 重新关联、取消/重新加入与 Overview executable target set 投影一致；
- Runner/Chat 保持 stable identity、账号范围、重复保护、确认、全局 browser lock；`uncertain` 不自动重试；
- 多账号本地 profile/snapshot 隔离；账号写操作与 browser action 互斥；
- Scheduler 使用原交互用户 Principal、`RunLevel Limited`，Dashboard 常态非提升，写操作走一次性受约束 UAC；
- 托盘以 service identity + 用户/路径/解释器/进程事实验证服务 ownership，端口冲突安全回退 8766–8799；
- watchdog 对 verified service 有界恢复：进程退出或约 15–20 秒连续 health failure、graceful first、exact PID 再验证、3 次/10 分钟熔断、manual full exit suppression；
- MSI/Portable standalone runtime，MSI ProgramRoot/DataRoot 分离，普通卸载默认保留 DataRoot；
- 普通 CI 与正式 Release validation 已分层。

## Locked

以下边界是当前维护必须保持的安全契约：

1. `candidate_id`/conversation locator 不是 durable friend identity；名称/头像不能建立权威 proof。
2. 自动/显式 binding 恢复只接受完整当前 discovery、唯一权威 identity、匹配 account scope、candidate 未占用。
3. 部分/失败/取消/陈旧 discovery、登录/账号异常不修改 stable binding。
4. 历史失败保持历史；binding 恢复不能把旧 `uncertain` 改成可重试。
5. 只有明确发生在发送动作前的失败可自动 retry；可能已发送/确认不确定必须停止。
6. 浏览器任务共享全局锁；账号切换不能绕过发送/浏览器互斥。
7. 不按端口、PID 或进程名终止未知进程；尤其禁止按 `python.exe` 名称批量 kill。
8. Watchdog 自动恢复只管理服务，不发送、不扫描好友、不修 Scheduler、不修改无关 DataRoot。
9. Scheduler 始终使用原交互用户/Limited；提升后的管理员环境不得重新推断 DataRoot。
10. MSI/Repair/Upgrade/Uninstall 不把普通程序维护变成用户数据删除；DataRoot 默认保留。
11. Git/Release/普通诊断不得包含真实账号、目标、消息、Cookie、browser profile、备份或未经审查的原始日志。
12. 已发布历史 tag/Release asset 保持不可变；候选版本的部分验证不能冒充完整 Release。

## 当前发布事实

- 已确认公开稳定 Release：**v1.4.4**。
- v1.4.4 MSI：`AutoDy-1.4.4-x64.msi`，`301,540,220` bytes，SHA-256 `bea7a7e7495c0137d33463f504d2999dcef250e7df7766c90eb0dbcb4a1daa10`。
- 当前源码元数据版本：`1.5.2`。
- `v1.5.0` tag 固定在 `4cf2620805f37e4c9fe8f221806b88c70f592ea8`，`v1.5.1` tag 固定在其历史提交；两者均未完成公开 Release，保留为历史。
- `v1.5.1` lifecycle 报告显示 verifier 曾误判 WiX `WixQuietExec` CustomActionData contract；修复后 hosted runner 仍可能在 fresh install 返回 1603，本版本不再把该诊断作为发布硬门禁。
- 当前唯一 release target 为 `v1.5.2`；source/build、MSI/Portable、privacy/package、manifest、publish 和 public asset verify 仍为硬门禁。

## 当前 Delta

本次发布 Delta 是在已收敛的 lifecycle verifier 修复与 Dashboard 自适应布局之上执行一次 `v1.5.2` formal Release：

1. 版本、Release workflow、发布说明与 source tag 收敛为 `v1.5.2`；
2. 从 v1.4.4 固定 MSI baseline 执行 clean-source build、Portable/MSI、privacy/package 与 manifest；
3. hosted-runner lifecycle 失败保留诊断但不阻断 publish；
4. 重新下载公开资产并复核集合、hash、MSI 与 privacy；
5. 对当前已安装运行时执行一次升级后的只读验收。

不要因为 runner 已构建出 MSI 就绕过 lifecycle 后仍宣称完整验收。

## CI/Release 结构

普通 push/PR：

- 常规 Python tests（当前 selection 基线 529）；
- frontend tests；
- PowerShell parsing。

正式 Release：

- 2 个 `release_build` 大型 reproducibility tests；
- clean-source Portable/MSI；
- privacy/package verifier；
- MSI lifecycle；
- guarded manifest；
- publish；
- public assets re-download/hash/MSI/privacy verify。

Tag push 不再重复触发 ordinary CI。不要用交互代理持续轮询 GitHub Actions；远端任务启动后等待完成，再一次读取结果。

## 数据/模块 owner 摘要

| 责任 | canonical owner |
| --- | --- |
| AppConfig / Target | `src/autody/config.py` |
| 当前好友 discovery | `friend_discovery.py` |
| stable binding/reassociation | `binding_recovery.py` |
| 当前账号/认证 revalidation | `account_profile.py` |
| 多账号 snapshot/activation | `account_profiles.py` |
| 发送状态机 | `runner.py` + `chat.py` |
| 只读 preflight | `preflight.py` |
| state/history/retry/failure | `state.py`、`history.py`、`retry_state.py`、`failures.py` |
| Scheduler | `scheduler.py` + existing task PowerShell scripts |
| 本地 API | `web_api.py` |
| Dashboard | `frontend/src/` |
| Tray/watchdog | `scripts/` 的现有 canonical scripts |
| MSI | `packaging/wix/` + build/lifecycle scripts |
| Release | `.github/workflows/release.yml` + build/verify scripts |

完整字段见 `docs/软件工程/07-数据字典与数据存储设计.md`；API 见 06；需求—测试追踪见 08/09。

## 文档 owner

当前完整软件工程文档入口：`docs/软件工程/00-文档总览.md`。正文按 00–13 管理：可行性、计划、需求、概要、详细设计、接口、数据字典、测试计划、测试报告、安装用户、运维、安全、项目总结。

`docs/文档总览.md` 和 `docs/AUTODY_ENGINEERING_MANUAL.md` 只保留兼容入口；不要再形成第二套长文。`docs/archive` 只保存历史。

## 接手顺序

1. 查看当前 `main`、工作树和 remote，先保留用户未提交工作。
2. 读取 `AGENTS.md`、`docs/软件工程/00-文档总览.md`、本文件以及与任务直接相关的 03/05/06/07/08/09/12。
3. 若 hosted-runner lifecycle 失败，保留报告但不重复整个发布构建来猜问题。
4. 涉及本机服务时先验证 service identity、用户、ProgramRoot、DataRoot、解释器和 exact PID ownership。
5. 涉及真实浏览器时优先 fixture/read-only，验证不执行真实发送。
6. 按风险做 focused validation；正式 Release 才执行重型 release gate。
7. 修改当前 canonical owner，Git 保存历史；不创建 `final/fixed/latest` 副本。

## Deliverables

当前待交付只有：最终 v1.5.2 release source/tag → GitHub Release canonical assets/hash/manifest → public asset reverify → installed-runtime acceptance。
