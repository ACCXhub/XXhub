# 部署与发布

CI 在 Windows 上构建前端、运行 Python 测试并检查便携包敏感路径。发布标签 `v1.3.0` 生成 `AutoDy-Windows-Portable.zip`、其 SHA-256、`AutoDy-Test-Center.autody-module.zip` 及其 SHA-256；发布后重新下载并校验哈希。
