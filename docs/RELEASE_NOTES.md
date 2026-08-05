# 发布说明

## AutoDy 1.4.2

发布日期：2026-08-03。官方 Test Center 版本：`1.2.0`。

### 安装包调查结论

- 用户误发的 `obj\x64\Debug` MSI 与官方 v1.4.1 MSI 字节完全相同；该具体文件没有缺 CAB、截断或损坏的 DLL，但 `obj` 仍是 WiX/MSBuild 中间目录，不能作为分发入口。
- 三份同字节 MSI 均完成行政解包；6 个内嵌 CAB 可完整读取，`D3DCompiler_47.dll` 的大小和 SHA-256 与 staging 源一致。
- v1.4.1 的历史生命周期报告已通过 fresh、默认/自定义路径、repair、upgrade 和 uninstall。接收方失败副本的哈希与完整 MSI 日志未取得，因此不能把具体接收方故障归因于包内结构，也不能无证据归因于权限或安全软件。

### 发布与源码构建修复

- MSI 使用原生目录解析：新安装优先选择当前用户可写的 `D:\AutoDy`，D 盘不可用或不可写时回退到 `%LocalAppData%\Programs\AutoDy`；无效的旧注册表路径不会被复用，升级与修复继续使用已安装路径。
- 开始菜单增加“卸载 AutoDy”快捷方式，调用当前 MSI ProductCode；正常卸载移除程序与快捷方式，默认保留 `%LocalAppData%\AutoDy` 用户数据。
- 启动器会先严格验证 `/api/service-identity`；同一用户、安装和数据目录的已有 AutoDy 服务会被复用。8765 被无关服务占用时才从 8766–8799 选择安全回退端口。
- MSI 的生成标识和 OLE 元数据已固定，使相同输入的 Release MSI 字节可复现；v1.4.1 可原路径升级。
- 正式 MSI 构建显式使用 `Release`；手工 Debug 输出带 `UNOFFICIAL-DEBUG`，避免被误认为发布包。
- 所有公开文件只从 `output\release\v1.4.2` 发布；`obj`、Debug、work 和测试目录会被守卫拒绝。
- 增加机器可读 `release-manifest.json`，记录版本、提交、Release 配置、文件大小/SHA-256、ProductCode、UpgradeCode、MSI Summary、隐私扫描和生命周期结果。
- 增加幂等 `scripts\bootstrap-source.ps1`、固定 Python 开发依赖和 `scripts\build-release-from-clean-source.ps1`。
- portable 与官方模块 ZIP 使用排序、固定时间戳和规范行尾，避免构建时间及 Windows/GitHub archive 行尾造成漂移。
- 修复 PowerShell 5.1 将成功 native 命令的 stderr warning 误判为构建失败的问题。

### 下载选择

- 普通用户：下载 `AutoDy-1.4.2-x64.msi`。
- 便携源码包：下载 `AutoDy-Windows-Portable-1.4.2.zip`，需要 Python 3.11 与网络，不需要 Node.js。
- 开发者源码：GitHub 自动 Source ZIP/TAR，先运行 `scripts\bootstrap-source.ps1`。
- 不要分发 `obj`、`bin\Debug`、`output\work` 或测试目录中的文件。

### 公开资产

- `AutoDy-1.4.2-x64.msi`
- `AutoDy-1.4.2-x64.msi.sha256`
- `AutoDy-Windows-Portable-1.4.2.zip`
- `AutoDy-Windows-Portable-1.4.2.zip.sha256`
- `release-manifest.json`

发布前必须在干净 Windows runner 完成默认/自定义安装、repair、v1.4.1 upgrade、uninstall、远程 artifact 传输哈希、MSI/CAB/DLL 完整性、隐私扫描和 clean-source 构建。旧 v1.4.0/v1.4.1 标签与资产保持不变。
