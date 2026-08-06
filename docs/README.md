# AutoDy 文档索引

适用版本：AutoDy `1.4.2` 预发布工作树。`v1.4.2` 已完成本地准备，但尚未发布，最终 Release CI 仍是发布门禁；已存在的 `v1.4.0` 与 `v1.4.1` 标签不应移动。

## 文档地图

| 文档 | 目的 | 适合读者 |
| --- | --- | --- |
| [软件需求规格说明](software-engineering/software-requirements-specification.md) | 说明问题边界、需求、验收条件与限制。 | 产品、评审、开发 |
| [系统设计](software-engineering/system-design.md) | 说明组件职责、启动、端口、安装和扩展边界。 | 开发、维护 |
| [安装与使用指南](software-engineering/installation-and-user-guide.md) | 说明 MSI、portable、托盘及常见操作。 | 用户、支持人员 |
| [测试与验收报告](software-engineering/test-and-acceptance-report.md) | 汇总已验证证据、发布门禁和未覆盖范围。 | 测试、发布负责人 |
| [维护与排障指南](software-engineering/maintenance-and-troubleshooting.md) | 给出安全、可执行的诊断与恢复步骤。 | 支持、维护 |
| [隐私与安全设计](software-engineering/privacy-and-security.md) | 说明本地边界、隐私排除、包完整性与限制。 | 安全评审、维护 |
| [项目交接](PROJECT_HANDOFF.md) | 记录当前分支、版本状态、构建命令与后续工作。 | 下一位开发者 |
| [发布说明](RELEASE_NOTES.md) | 记录待发布版本的用户可见变更。 | 发布负责人、用户 |

旧路径 [AUTODY_ENGINEERING_MANUAL.md](AUTODY_ENGINEERING_MANUAL.md) 保留为构建兼容入口，不再承载重复内容。

## 建议阅读顺序

1. 初次了解项目：根目录 `README.md`，再读本索引和软件需求规格说明。
2. 开发或排障：系统设计 → 维护与排障指南 → 项目交接。
3. 安装、演示或支持用户：安装与使用指南 → 隐私与安全设计。
4. 发布前：测试与验收报告 → 发布说明 → 项目交接中的发布流程。

## 维护规则

- `README.md` 只保留项目入口、安装摘要和链接；详细操作应写入对应专题文档。
- `PROJECT_HANDOFF.md` 只记录当前工作状态、已完成事项和下一步，不重复完整设计。
- `CHANGELOG.md` 记录版本历史；`RELEASE_NOTES.md` 记录待发布版本的面向用户说明。
- 只记录代码、测试或已完成验证能够支持的行为。计划工作必须标为“未来工作”或“待验证”。
- 文档不得包含真实账号、目标、消息、Cookie、浏览器资料、运行时日志或机器私有路径；用 `%LocalAppData%\AutoDy`、`D:\AutoDy` 与 `127.0.0.1:<selected-port>` 等占位表示。
- 修改安装、端口、数据路径、发布包或安全边界时，必须同步检查本索引关联的专题文档和发布说明。
