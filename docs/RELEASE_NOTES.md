# AutoDy v1.3.0

- 测试中心 1.1.0 在“设置”下作为完整子页，宽度填满工作区，并以受限同源高度协商避免裁切。
- 完成模块内的移除确认、模块数据清理、夹具和受控失败历史。
- 整合用户、开发、软件工程与隐私安全截图文档。

- Repairs the desktop dashboard launcher for Windows command-shell encoding and quoting.
- Waits for the local dashboard identity endpoint before opening the browser.
- Reuses the verified current AutoDy service, stops only a confirmed stale AutoDy listener, and reports unrelated port conflicts without terminating them.
- Keeps launcher failures visible with a safe local diagnostic log.

No local account, browser, cache, message, history, or log data is included in source or portable artifacts.
