# 测试报告

v1.3.0 发布前执行 `.venv\Scripts\pytest.exe -q`、`frontend/npm test`、`frontend/npm run build` 与 `scripts/build-portable.ps1`。测试中心回归包括受限高度消息、全页标题、显式移除对话框、模块数据删除和未安装默认状态。真实本地视觉验收使用安全夹具，禁止访问真实抖音资料或发送消息。
