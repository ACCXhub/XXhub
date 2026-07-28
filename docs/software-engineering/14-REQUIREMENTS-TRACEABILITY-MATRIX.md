# 需求追踪矩阵

| 需求 | 实现 | 验证 | 状态 |
|---|---|---|---|
| 全页测试中心 | `ModuleHostPage` + iframe ResizeObserver | Vitest 与本地 UI | 已验证 |
| 模块自移除 | `ModuleManager.uninstall` + 模态框 | API/模块测试 | 已验证 |
| 不发送保证 | `preflight` inspector | 无发送守卫 | 已验证 |
| 文档截图 | 安全夹具脚本 | 链接与隐私扫描 | 已验证 |
