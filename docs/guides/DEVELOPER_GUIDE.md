# 开发指南

```powershell
.\.venv\Scripts\python.exe -m autody.cli doctor
.\.venv\Scripts\pytest.exe -q
cd frontend; npm test; npm run build; cd ..
.\scripts\build-portable.ps1
python scripts/capture-doc-screenshots.py
```

测试使用伪页面和本地安全夹具，不连接抖音，也不发送消息。模块包由 `autody.modules.build_module_archive` 生成并校验。
