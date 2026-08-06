# AutoDy 维护与排障指南

适用版本：AutoDy 1.4.2 预发布工作树。本文给出不会批量终止进程、不会删除运行时资料、不会触碰真实消息的诊断方法。发布状态仍为 prepared；最终 Release CI 通过前不应把候选安装包当作正式版本。

## 1. 诊断原则

先确认“谁在监听、它是否属于当前安装、页面实际使用哪个端口”，再考虑重启或修复。不要用 Stop-Process -Name python、taskkill /IM chrome.exe、清空浏览器资料或删除整个 data 目录作为排障手段。这些操作会误伤无关工作、丢失用户资料，且不能证明服务身份。

托盘是推荐入口。它会复用已验证服务并在 8765 不安全时选用 8766–8799 的空闲端口。若托盘无法打开管理台，按下列顺序检查端口、服务身份、安装目录和 MSI 日志。

## 2. Dashboard 未打开或 AutoDy 已在运行

1. 从通知区域的 AutoDy 图标选择“打开 AutoDy 管理台”。这会使用当前经验证的端口。
2. 如仍失败，先检查首选端口是否有监听：

~~~powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, OwningProcess
~~~

3. 对已确认的候选端口做只读身份检查。把 8765 替换为实际选择的端口：

~~~powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/service-identity' -TimeoutSec 2
~~~

结果应表明应用为 AutoDy，并与当前安装的版本和路径一致。连接失败、应用名不符、路径不符或当前用户不符时，不要结束该 PID；它可能属于无关服务。

4. 端口选择会持久化在 AutoDy 本地数据目录的服务端口状态中。若曾发生回退，优先通过托盘重新打开，而不是把浏览器固定到 8765。持久端口在下次启动还会再次验证。

## 3. 8765 被占用与重复启动

8765 被无关监听者占用时，正确结果是 AutoDy 选择 8766–8799 中的空闲端口，无关监听者继续运行。若所有候选端口均不可用，应用应给出可操作错误。不要关闭所有本地服务器来“让出端口”。

重复启动出现“已在运行”通常是正常复用：托盘比较 service identity、监听 PID、当前用户、项目/包路径和 Python 解释器，只复用健康的一致服务。若需要重启，请使用托盘“重启管理台”；它只停止由自己验证和管理的 AutoDy 服务。普通“退出托盘”不会停止计划任务或服务。

## 4. 受管浏览器登录与计划任务

登录丢失时，在 AutoDy 打开的受管浏览器窗口中重新完成登录。不要复制 Cookie、导出 profile、把登录文件放入安装目录，或把认证材料附在日志中。若发现已有草稿、附件、身份不一致或不确定页面状态，停止操作并保留现场。

计划未执行时，先在 Dashboard 的历史与状态中确认计划配置、上次运行和失败分类，再检查 Windows Task Scheduler 中仅与 AutoDy 对应的任务。计划任务可独立于托盘运行；关闭浏览器窗口或退出托盘不是禁用计划的证明。不要通过删除 data 目录或重装来重置计划。

## 5. MSI 安装、修复与升级

**安装目录无效。** 确认目标目录对当前用户可写，且不是被删除后仍残留的旧登记路径。首次安装可选择 D:\AutoDy；若 D: 不可用，使用 %LocalAppData%\Programs\AutoDy。不要选择网络共享、系统目录或另一个应用的目录。

**D3DCompiler_47.dll 写入或载入失败。** 先确认下载的 MSI SHA-256 与发布 sidecar 一致，检查磁盘空间和安全软件隔离记录。已验证发行 payload 包含匹配的 D3DCompiler_47.dll；不要从随机网站下载 DLL 覆盖安装目录。仍失败时使用 Repair 或重新获取同一已校验 MSI。

**修复。** 在 Windows 设置的应用页面或 MSI 维护界面选择 Repair。修复针对程序 payload 与快捷方式，设计上不清空 %LocalAppData%\AutoDy。

**v1.4.1 升级到 v1.4.2。** 对同一用户运行新 MSI；不要手动删除原程序目录或 data 目录。升级复用有效登记的程序目录，并保留数据。若安装器提示目录陈旧或升级失败，收集 verbose log 后停止继续操作。

**安全记录 MSI 日志。** 下面命令只生成安装日志；路径应替换为已校验的 MSI 文件：

~~~powershell
msiexec.exe /i '.\AutoDy-1.4.2-x64.msi' /L*v '.\AutoDy-msi-install.log'
~~~

日志可能含环境诊断信息，应仅通过受控支持渠道共享，并先检查其中没有账号、认证或私有文件路径。

## 6. 卸载、保留数据与安全清理

开始菜单的“卸载 AutoDy”快捷方式和 Windows 应用管理入口都可卸载。卸载删除程序目录和快捷方式，默认保留 %LocalAppData%\AutoDy 下的用户数据、计划、浏览器资料、历史和备份。这是设计行为，不是卸载失败。

只有用户明确决定永久移除数据、已退出经验证的 AutoDy 服务、且已完成需要的备份后，才考虑清理该单一数据目录。可安全审查的候选位置是当前用户的 %LocalAppData%\AutoDy 与不再使用的明确程序目录，例如 D:\AutoDy；不要递归删除 %LocalAppData%、用户主目录、浏览器通用 profile、仓库根目录、output 或任何不确定路径。

## 7. 发布产物校验

下载候选或正式 Release 后先执行：

~~~powershell
Get-FileHash -Algorithm SHA256 '.\AutoDy-1.4.2-x64.msi'
~~~

只接受与同名 .sha256 sidecar 完全一致的值。工程人员还可在受控构建环境运行 scripts/verify-release-artifacts.ps1 对指定 artifact 目录执行 allowlist、manifest 和隐私检查。该验证不等于最终 Release CI；当前 v1.4.2 仍需等待该门禁。
