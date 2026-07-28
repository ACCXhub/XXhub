# API 规格

核心：`GET /api/status`、`GET/PUT /api/config`、`GET /api/modules`、`POST /api/modules/autody-test-center/install`。安装后模块路由包括 `today-plan`、`failed-targets`、`preflight/status|run|cancel`、`diagnostics`、`history`、`fixtures`、`simulate-failure`、`targets/{id}/settings`、`uninstall` 与 `frontend/{path}`。未安装时模块路由均返回 404。
