# 详细设计

`ModuleHostPage` 初始高度 760 px，并仅接受 760–4000 px 的模块 resize 消息。模块的 `ResizeObserver` 以 `window.location.origin` 回传自然内容高度。`ModuleManager` 原子安装、校验清单和包校验，安全卸载前验证最终目录名与父目录。
