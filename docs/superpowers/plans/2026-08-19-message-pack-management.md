# AutoDy Reversible Message Pack Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留全局 `messages.txt` 和现有发送兼容链路的前提下，实现具有稳定 ID、显式排序、可逆融合谱系和一次性内置包迁移的用户文案包系统。

**Architecture:** 使用 `<DataRoot>/data/message-packs/catalog.json` 作为单一版本化权威目录，以包内有序消息/融合节点表达内容和谱系，并通过跨进程锁与原子替换提交。旧内置包只在 `builtin_seed_v1` 未完成且目录文件不存在时种入一次；融合需要迁移外部目标引用时使用固定 pending journal 提供回滚和崩溃恢复。

**Tech Stack:** Python 3.11、Pydantic 2、FastAPI、pytest、React 19、TypeScript、Vitest、原生 HTML drag events、PowerShell 5.1 兼容启动环境。

**Spec:** `docs/superpowers/specs/2026-08-19-message-pack-management-design.md`

## Global Constraints

- `messages.txt` 保持独立，不能迁移成普通包，也不能改变全局消息选择和发送安全链路。
- 旧内置包 ID 必须保留；内置包只种入一次，明确依赖 `migrations.builtin_seed_v1.completed`，不得按空目录重种。
- 包名不是身份；包和消息使用稳定 ID；相同正文不得去重。
- 融合不得复制或扁平化来源；拆分恢复当前分组，不恢复历史正文或已删除消息。
- 融合时当前目标引用迁到目标包；拆分时不得自动迁回。
- 所有目录写入必须原子；融合跨文件变更必须可回滚并可崩溃恢复。
- 不新增数据库、拖放框架或重型 UI 依赖。
- 不修改 Dashboard 其他页面、Douyin composer、发送确认、重试资格、Scheduler、Test Center 页面或 BrowserWeave。
- 测试只能使用临时 DataRoot、fixture 和只读页面；绝不打开、输入或发送真实 Douyin 消息。
- A 与 B 使用不同提交；不得修改或重写 `v1.4.4` 标签、Release 资产或 MSI。

## File Structure

- Create `src/autody/message_pack_catalog.py`: 目录 schema、完整性验证、原子存储、跨进程锁和固定 journal 恢复。
- Modify `src/autody/message_packs.py`: 对外领域服务、一次性内置包种入、CRUD、TXT 导入、融合/拆分和旧 import-to-global 兼容。
- Modify `src/autody/web_api.py`: 请求模型、领域端点、revision/错误映射、目标引用事务和 Dashboard 包数量。
- Modify `src/autody/runner.py`: 只把已选文案包的读取切换到新目录，保留现有选择算法。
- Modify `src/autody/transfer.py`: 在现有 `message-packs.json` 备份槽位导出、预览和导入真实目录。
- Modify `frontend/src/types.ts`: 新目录、包详情、消息条目和 mutation 响应类型。
- Modify `frontend/src/api.ts`: 文案包 CRUD、导入、排序、融合和拆分请求。
- Modify `frontend/src/pages/MessagePacksPage.tsx`: 列表、同页编辑、原生拖动和领域操作 UI。
- Modify `frontend/src/styles.css`: 只增加文案包列表、编辑器和来源标签样式。
- Modify `tests/test_message_packs.py`, `tests/test_web_api.py`, `tests/test_runner.py`, `tests/test_transfer.py`, `frontend/src/pages/MessagePacksPage.test.tsx`: 聚焦行为覆盖。
- Modify `README.md`, `CHANGELOG.md`, `docs/codex/PROJECT_HANDOFF.md`: 使用方式、未发布变更和当前工程事实。

---

### Task 1: 版本化目录、完整性验证与一次性种入

**Files:**
- Create: `src/autody/message_pack_catalog.py`
- Modify: `src/autody/message_packs.py`
- Test: `tests/test_message_packs.py`

**Interfaces:**
- Produces: `CatalogDocument`, `PackageRecord`, `MessageRecord`, `MessageItem`, `FusedSourceItem`, `MessagePackCatalogStore`。
- Produces: `MessagePackService(program_root: Path, data_root: Path | None = None, *, now=None, id_factory=None)`；后续任务均通过该 facade 访问目录。
- Produces: `MessagePackService.catalog() -> CatalogDocument`, `list_packs() -> PackCatalog`, `preview(pack_id: str) -> PackPreview`。

- [ ] **Step 1: 写首次种入和空目录不重种的失败测试**

```python
def test_first_catalog_load_seeds_builtin_ids_once(tmp_path: Path):
    program_root = make_pack_root(tmp_path)
    data_root = tmp_path / "data-root"
    ids = iter(["message-seeded-1", "message-seeded-2"])
    service = MessagePackService(program_root, data_root, id_factory=lambda: next(ids))

    catalog = service.catalog()

    assert catalog.top_level_pack_ids == ["sample"]
    assert catalog.migrations.builtin_seed_v1.completed is True
    assert service.preview("sample").messages == ["早安呀", "今天顺利"]


def test_deleting_every_pack_does_not_seed_again(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "data-root")
    catalog = service.catalog()
    empty = catalog.model_copy(
        update={"revision": catalog.revision + 1, "top_level_pack_ids": [], "packages": {}, "messages": {}}
    )
    service.store.catalog_path.write_text(empty.model_dump_json(indent=2), encoding="utf-8")

    restarted = MessagePackService(service.program_root, service.data_root)

    assert restarted.list_packs().packs == []
    assert restarted.catalog().migrations.builtin_seed_v1.completed is True
```

- [ ] **Step 2: 运行测试并确认失败来自新目录接口尚不存在**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py -k "first_catalog_load or deleting_every_pack"`

Expected: FAIL，提示 `MessagePackService` 不接受 `data_root` 或不存在 `catalog`。

- [ ] **Step 3: 实现精确目录模型和完整性验证**

```python
class MessageItem(BaseModel):
    kind: Literal["message"] = "message"
    message_id: str


class FusedSourceItem(BaseModel):
    kind: Literal["fused_source"] = "fused_source"
    pack_id: str
    fused_at: datetime
    restore_index: int = Field(ge=0)


PackItem = Annotated[MessageItem | FusedSourceItem, Field(discriminator="kind")]


class PackageRecord(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: datetime
    items: list[PackItem] = Field(default_factory=list)


class MessageRecord(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=500)
    created_at: datetime
    updated_at: datetime


class CatalogDocument(BaseModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=1, ge=1)
    migrations: CatalogMigrations
    top_level_pack_ids: list[str]
    packages: dict[str, PackageRecord]
    messages: dict[str, MessageRecord]
```

在 `CatalogDocument` 的 after-validator 中收集所有融合 child ID 和 message ID，逐项验证唯一父节点、唯一消息成员、无孤儿，并对每个顶层根做 DFS；访问到灰色节点立即抛出 `MessagePackError("文案包融合关系包含循环")`。

- [ ] **Step 4: 实现原子读写、锁和首次 seed**

```python
class MessagePackCatalogStore:
    def __init__(self, program_root: Path, data_root: Path, *, now, id_factory):
        self.program_root = program_root.resolve()
        self.data_root = data_root.resolve()
        self.catalog_path = self.data_root / "data/message-packs/catalog.json"
        self.lock_path = self.data_root / "data/locks/message-packs.lock"

    def load_or_seed(self) -> CatalogDocument:
        with SingleInstanceLock(self.lock_path, timeout_seconds=5):
            self.recover_pending_transaction()
            if self.catalog_path.exists():
                return self._read_validated()
            seeded = self._seed_from_builtin_index()
            self._atomic_write(seeded)
            return seeded
```

`_seed_from_builtin_index()` 保留 index pack ID 和当前预览去重语义；为消息生成稳定测试可注入 ID；同一原子文档写入 `builtin_seed_v1.completed = true`。解析失败时不得创建目录文件。

- [ ] **Step 5: 增加损坏目录、重复父节点、环和旧 ID 预览测试并运行**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py -k "catalog or seed or cycle or orphan or duplicate_parent or preview"`

Expected: PASS；损坏目录测试断言文件字节保持不变且不会重新 seed。

- [ ] **Step 6: 提交目录基础**

```powershell
git add src/autody/message_pack_catalog.py src/autody/message_packs.py tests/test_message_packs.py
git commit -m "feat: add versioned message pack catalog"
```

### Task 2: 空包、TXT 导入、编辑与顶层排序

**Files:**
- Modify: `src/autody/message_pack_catalog.py`
- Modify: `src/autody/message_packs.py`
- Test: `tests/test_message_packs.py`

**Interfaces:**
- Consumes: Task 1 的 `MessagePackService` 和 `CatalogDocument`。
- Produces: `create_pack(expected_revision: int, name: str = "新建文案包") -> PackMutationResult`。
- Produces: `import_text(raw: bytes, filename: str, expected_revision: int) -> PackMutationResult`。
- Produces: `rename_pack`, `reorder_packs`, `add_message`, `update_message`, `delete_message`，全部接收 `expected_revision` 并返回新 revision。

- [ ] **Step 1: 写 CRUD、重复正文和排序失败测试**

```python
def test_import_uses_first_valid_line_as_truncated_name_and_keeps_duplicates(service):
    first = "甲" * 100
    result = service.import_text(
        f"\n{first}\n早安\n早安\n".encode("utf-8"),
        "morning.txt",
        expected_revision=service.catalog().revision,
    )
    detail = service.preview(result.pack.id)
    assert result.pack.name == "甲" * 79 + "…"
    assert detail.messages == [first, "早安", "早安"]
    assert len({entry.id for entry in detail.entries}) == 3


def test_reorder_persists_after_service_restart(service):
    ids = [pack.id for pack in service.list_packs().packs]
    service.reorder_packs(list(reversed(ids)), expected_revision=service.catalog().revision)
    restarted = MessagePackService(service.program_root, service.data_root)
    assert [p.id for p in restarted.list_packs().packs] == list(reversed(ids))
```

- [ ] **Step 2: 运行聚焦测试确认 RED**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py -k "import_uses or create_empty or rename_preserves or reorder_persists or duplicate_text or stale_revision"`

Expected: FAIL，缺少 mutation 方法。

- [ ] **Step 3: 实现 revision 守卫和目录 mutation helper**

```python
def _mutate(self, expected_revision: int, change: Callable[[CatalogDocument], T]) -> tuple[T, CatalogDocument]:
    with SingleInstanceLock(self.store.lock_path, timeout_seconds=5):
        current = self.store.load_locked()
        if current.revision != expected_revision:
            raise MessagePackConflict("文案包已被其他页面修改，请刷新后重试")
        candidate = current.model_copy(deep=True)
        result = change(candidate)
        candidate.revision += 1
        candidate = CatalogDocument.model_validate(candidate.model_dump(mode="json"))
        self.store.write_locked(candidate)
        return result, candidate
```

`load_locked()` 不得再次获取同一文件锁；公开 `load_or_seed()` 与 mutation 必须明确区分锁内/锁外入口。

- [ ] **Step 4: 实现空包、TXT 解析、改名和消息 CRUD**

```python
def parse_pack_text(raw: bytes, filename: str) -> list[str]:
    if Path(filename).suffix.lower() != ".txt":
        raise MessagePackError("文案包导入仅支持 TXT")
    text = raw.decode("utf-8")
    rows = [line for line in text.splitlines() if line.strip()]
    if not rows:
        raise MessagePackError("TXT 中没有有效文案")
    too_long = next((index for index, line in enumerate(rows, 1) if len(line) > 500), None)
    if too_long is not None:
        raise MessagePackError(f"第 {too_long} 条文案超过 500 字符")
    return rows
```

导入不使用旧 `parse_message_import()`，因为旧函数会按正文去重。包名使用 `rows[0].strip()`，超过 80 码点时截为 79 加省略号；消息保存 `rows` 原文。

- [ ] **Step 5: 实现完整顶层顺序提交**

`reorder_packs()` 必须验证 `len(ids) == len(set(ids))` 且 `set(ids) == set(current.top_level_pack_ids)`；不能接受部分排序、隐藏来源 ID 或未知 ID。

- [ ] **Step 6: 运行 Task 2 全部测试和 Task 1 回归**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py`

Expected: PASS。

- [ ] **Step 7: 提交基础管理能力**

```powershell
git add src/autody/message_pack_catalog.py src/autody/message_packs.py tests/test_message_packs.py
git commit -m "feat: add message pack editing and ordering"
```

### Task 3: 可逆融合、拆分、安全删除与引用事务

**Files:**
- Modify: `src/autody/message_pack_catalog.py`
- Modify: `src/autody/message_packs.py`
- Modify: `src/autody/config.py`
- Test: `tests/test_message_packs.py`

**Interfaces:**
- Consumes: Task 2 的 revision mutation 和有序 `items`。
- Produces: `fuse(source_id: str, destination_id: str, expected_revision: int, config_path: Path) -> PackMutationResult`。
- Produces: `split(destination_id: str, source_id: str, expected_revision: int) -> PackMutationResult`。
- Produces: `delete_pack(pack_id: str, expected_revision: int, referenced_pack_ids: set[str]) -> PackMutationResult`。
- Produces: `MessagePackCatalogStore.commit_external_transaction(changes: dict[Path, bytes])` 和 `recover_pending_transaction()`。

- [ ] **Step 1: 写融合、当前内容拆分和嵌套谱系失败测试**

```python
def test_fuse_split_keeps_current_source_content_and_moves_target_reference(service, config_path):
    source, destination, message = make_two_packs(service)
    save_config(config_path, AppConfig(targets=[Target(name="目标", message_pack=source.id)]))
    service.fuse(source.id, destination.id, service.catalog().revision, config_path)
    service.update_message(destination.id, message.id, "早呀", service.catalog().revision)
    service.split(destination.id, source.id, service.catalog().revision)

    assert service.preview(source.id).messages == ["早呀"]
    assert load_config(config_path).targets[0].message_pack == destination.id


def test_nested_fusion_split_preserves_child_lineage(service, config_path):
    a, b, c = make_three_packs(service)
    service.fuse(a.id, b.id, service.catalog().revision, config_path)
    service.fuse(b.id, c.id, service.catalog().revision, config_path)
    service.split(c.id, b.id, service.catalog().revision)
    assert service.direct_fused_sources(b.id)[0].id == a.id
```

- [ ] **Step 2: 写环、递归删除、被引用删除和 journal 恢复失败测试**

```python
def test_pending_fusion_transaction_rolls_forward_after_restart(service, config_path, monkeypatch):
    source, destination, _message = make_two_packs(service)
    class SimulatedCrash(BaseException):
        pass
    monkeypatch.setattr(
        service.store,
        "_replace_target",
        fail_after_first_replace(SimulatedCrash("simulated process exit")),
    )
    with pytest.raises(SimulatedCrash):
        service.fuse(source.id, destination.id, service.catalog().revision, config_path)

    recovered = MessagePackService(service.program_root, service.data_root)
    assert source.id not in recovered.catalog().top_level_pack_ids
    assert load_config(config_path).targets[0].message_pack == destination.id
    assert not recovered.store.pending_path.exists()


def test_fusion_write_error_rolls_back_catalog_and_config(service, config_path, monkeypatch):
    source, destination, _message = make_two_packs(service)
    catalog_before = service.store.catalog_path.read_bytes()
    config_before = config_path.read_bytes()
    monkeypatch.setattr(
        service.store,
        "_replace_target",
        fail_after_first_replace(OSError("disk error")),
    )
    with pytest.raises(MessagePackError, match="已回滚"):
        service.fuse(source.id, destination.id, service.catalog().revision, config_path)
    assert service.store.catalog_path.read_bytes() == catalog_before
    assert config_path.read_bytes() == config_before
    assert not service.store.pending_path.exists()
```

测试辅助函数 `fail_after_first_replace(error)` 返回一个计数 closure：第一次调用真实 `_replace_target`，第二次抛出传入异常。`make_two_packs()` 只通过 Task 2 公共 create/add API 创建两个包和一条来源消息。

- [ ] **Step 3: 运行测试确认 RED**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py -k "fuse or split or nested or cycle or referenced_delete or pending"`

Expected: FAIL，缺少融合和 transaction API。

- [ ] **Step 4: 实现树操作和内容投影**

```python
def _flatten_entries(catalog: CatalogDocument, pack_id: str) -> list[PackEntry]:
    result: list[PackEntry] = []
    for item in catalog.packages[pack_id].items:
        if isinstance(item, MessageItem):
            result.append(entry_from(catalog.messages[item.message_id], origin_pack_id=pack_id))
        else:
            result.extend(_flatten_entries(catalog, item.pack_id))
    return result
```

`fuse()` 只把 `FusedSourceItem` 追加到目标 `items`，记录源原顶层索引；`split()` 只移除直接 child 节点并按 `min(restore_index, len(top_level))` 恢复；新增消息仍追加到目标自身 `items` 末尾。

- [ ] **Step 5: 实现固定 journal 的 old/new payload、回滚和恢复**

journal 必须包含规范化绝对目标路径、base64 编码 old/new bytes、SHA-256 和事务状态。写 journal、替换每个目标、删除 journal 都使用同目录临时文件加 `os.replace`。恢复逻辑先验证所有 payload 哈希；目标若已等于 new hash 则跳过，否则写 new bytes。普通异常路径写回所有 old bytes并验证。

- [ ] **Step 6: 在融合事务内迁移 `Target.message_pack` 引用**

```python
candidate = load_config(config_path)
for target in candidate.targets:
    if target.message_pack == source_id:
        target.message_pack = destination_id
config_bytes = serialize_config(candidate, root=config_path.parent)
self.store.commit_external_transaction({self.store.catalog_path: catalog_bytes, config_path: config_bytes})
```

在 `config.py` 提取 `serialize_config(config, root) -> bytes`，让 `save_config()` 和 journal 共用相同 YAML 序列化，避免先写真实文件才能获得 bytes。

- [ ] **Step 7: 实现安全删除并运行完整领域回归**

删除前递归收集子树包 ID 和消息 ID；若 `pack_id in referenced_pack_ids` 返回冲突。提交候选目录前由完整性 validator 再确认无孤儿。

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py tests/test_config.py`

Expected: PASS。

- [ ] **Step 8: 提交融合领域能力**

```powershell
git add src/autody/message_pack_catalog.py src/autody/message_packs.py src/autody/config.py tests/test_message_packs.py tests/test_config.py
git commit -m "feat: add reversible message pack fusion"
```

### Task 4: FastAPI 领域端点与发送兼容

**Files:**
- Modify: `src/autody/web_api.py`
- Modify: `src/autody/runner.py`
- Test: `tests/test_web_api.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: Task 3 的完整 `MessagePackService`。
- Produces: spec 第 13 节的集合、消息、排序、融合和拆分端点。
- Preserves: `POST /api/message-packs/{pack_id}/import` 的旧 import-to-global 语义和 `PackPreview.messages`。

- [ ] **Step 1: 写 API 合同失败测试**

```python
def test_message_pack_api_creates_renames_and_reorders(client):
    catalog = client.get("/api/message-packs").json()
    created = client.post("/api/message-packs", json={"expected_revision": catalog["revision"]}).json()
    renamed = client.patch(
        f"/api/message-packs/{created['pack']['id']}",
        json={"name": "早安", "expected_revision": created["revision"]},
    ).json()
    assert renamed["pack"]["name"] == "早安"


def test_message_pack_api_rejects_stale_revision_with_409(client):
    revision = client.get("/api/message-packs").json()["revision"]
    client.post("/api/message-packs", json={"expected_revision": revision})
    response = client.post("/api/message-packs", json={"expected_revision": revision})
    assert response.status_code == 409
```

另覆盖 multipart TXT import、消息 CRUD、融合、拆分、被引用删除 409、非法环 422、损坏目录 503 和旧 import-to-global 结果。

- [ ] **Step 2: 写 runner 兼容失败测试**

```python
def test_target_pack_uses_new_catalog_without_changing_selection(monkeypatch, tmp_path):
    config = configured_target(tmp_path, message_pack="daily", message_selection="one_for_all")
    seed_catalog(tmp_path, "daily", ["第一条", "第二条"])
    monkeypatch.setenv("AUTODY_PROGRAM_ROOT", str(tmp_path / "program"))
    assert _target_base_message(config.targets[0], config, {}, ["全局"], date(2026, 8, 19)) == "第一条"
```

同时保留无 `message_pack` 时仍返回全局 daily message 的既有测试。

- [ ] **Step 3: 运行 API/runner 聚焦测试确认 RED**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_web_api.py tests/test_runner.py -k "message_pack or target_pack"`

Expected: FAIL，新端点不存在或 runner 仍读取 ProgramRoot 旧包。

- [ ] **Step 4: 添加请求模型和统一服务工厂**

```python
def message_pack_service() -> MessagePackService:
    return MessagePackService(program_root, initial_config.state_file.parent)


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class RenamePackRequest(RevisionRequest):
    name: str = Field(min_length=1, max_length=80)
```

所有端点只调用 service 领域方法；`MessagePackConflict` 映射 409，输入/环映射 422，损坏 schema 映射 503。Dashboard `active_message_pack_count` 改为 `len(service.list_packs().packs)`，不再直接读发布 index。

- [ ] **Step 5: 实现全部端点并保留旧预览/import 兼容**

TXT import 使用 `UploadFile`；详情响应同时返回旧 `messages: string[]` 和新 `entries`。DELETE 请求携带 revision，服务端从当前 config 计算被引用顶层 ID。融合把 `config_path` 传给 service transaction。

- [ ] **Step 6: 切换 runner 的包读取根**

```python
pack_service = MessagePackService(
    Path(os.environ.get("AUTODY_PROGRAM_ROOT", config.messages_file.parent)),
    config.state_file.parent,
)
pack_messages = pack_service.preview(target.message_pack).messages
```

不改 `_selection_rng()`、`one_for_all`、`per_friend`、daily cache key、失败映射或发送阶段。

- [ ] **Step 7: 运行 API、runner、消息与发送前安全回归**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_web_api.py tests/test_runner.py tests/test_messages.py -k "message or pack or target"`

Expected: PASS；测试不得创建浏览器或真实发送动作。

- [ ] **Step 8: 提交 API 与兼容读取**

```powershell
git add src/autody/web_api.py src/autody/runner.py tests/test_web_api.py tests/test_runner.py
git commit -m "feat: expose managed message pack APIs"
```

### Task 5: 备份导出、预览、merge 与 replace

**Files:**
- Modify: `src/autody/message_packs.py`
- Modify: `src/autody/transfer.py`
- Test: `tests/test_transfer.py`

**Interfaces:**
- Consumes: Task 3 的目录验证与原子 mutation。
- Produces: `export_catalog_bytes() -> bytes`、`preview_catalog_import(raw: bytes) -> dict`、`import_catalog(raw: bytes, mode: transfer.ImportMode) -> CatalogImportResult`。
- Preserves: 旧备份 `message-packs.json == {}` 不改变当前目录。

- [ ] **Step 1: 写备份失败测试**

```python
def test_backup_exports_real_message_pack_catalog(tmp_path: Path):
    config = _config(tmp_path)
    service = seeded_catalog_for_config(config)
    package = create_backup(config, {ExportCategory.MESSAGE_PACKS})
    with zipfile.ZipFile(BytesIO(package)) as archive:
        payload = json.loads(archive.read("message-packs.json"))
    assert payload["schema_version"] == 1
    assert payload["top_level_pack_ids"] == service.catalog().top_level_pack_ids


def test_old_empty_pack_backup_does_not_clear_catalog(tmp_path: Path):
    config, package = backup_with_message_packs_json(tmp_path, {})
    before = catalog_bytes(config)
    apply_backup(package, tmp_path / "config.yaml", config, mode=ImportMode.REPLACE)
    assert catalog_bytes(config) == before
```

另写 merge ID 冲突重映射、replace 验证失败不变更、migration completed 不被清除的测试。

- [ ] **Step 2: 运行测试确认 RED**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_transfer.py -k "message_pack or pack_backup"`

Expected: FAIL，当前备份仍写 `{}`。

- [ ] **Step 3: 实现目录导出和预览计数**

`create_backup()` 在 MESSAGE_PACKS 类别读取现有 `catalog.json`；若尚未初始化，通过 ProgramRoot 环境与 config DataRoot 创建 service 并完成一次 seed。`preview_backup()` 验证非空目录文档并返回包数、消息数和 ID 冲突数。

- [ ] **Step 4: 实现 replace 和 merge**

replace 候选必须保留 `builtin_seed_v1.completed = true`。merge 对相同且结构一致 ID 跳过；冲突包和消息使用新 UUID，并在导入子树的顶层数组、items 和消息引用中一次性重写映射。merge 后将导入顶层包追加到当前顶层顺序。

- [ ] **Step 5: 把目录纳入现有备份回滚边界**

在 `apply_backup()` mutation 前保存 catalog bytes；任何 config/messages/state/catalog 写入失败时使用已有回滚块恢复四类文件。不能让旧 `{}` payload 进入 replace 分支。

- [ ] **Step 6: 运行 transfer 和 config 回归**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_transfer.py tests/test_config.py`

Expected: PASS。

- [ ] **Step 7: 提交备份兼容**

```powershell
git add src/autody/message_packs.py src/autody/transfer.py tests/test_transfer.py
git commit -m "feat: include message packs in backups"
```

### Task 6: React 文案包管理界面

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/MessagePacksPage.tsx`
- Modify: `frontend/src/pages/MessagePacksPage.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 4 API。
- Produces: 顶层列表、空包创建、TXT 导入、内联改名/消息 CRUD、拖动与上下移动、融合/拆分和安全删除 UI。

- [ ] **Step 1: 更新测试 mock 并写关键交互失败测试**

```tsx
test("creates an empty pack and opens its editor", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "新建文案包" }));
  expect(api.createMessagePack).toHaveBeenCalledWith(7);
  expect(await screen.findByRole("heading", { name: "新建文案包" })).toBeInTheDocument();
});

test("shows split only for packs with fused sources", async () => {
  render(<MessagePacksPage notify={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "拆出已融合包" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "拆出已融合包" })).toHaveLength(1);
});
```

另写：首行导入结果、改名 ID 不变、消息编辑/删除、拖动 reorder payload、上下移动、融合确认和递归删除提示。

- [ ] **Step 2: 运行页面测试确认 RED**

Run: `npm test -- --run frontend/src/pages/MessagePacksPage.test.tsx`

Workdir: `frontend`

Expected: FAIL，新 API 和控件尚不存在。

- [ ] **Step 3: 定义前端类型和 API**

```ts
export interface MessagePackSummary {
  id: string;
  name: string;
  message_count: number;
  direct_fused_sources: Array<{ id: string; name: string; message_count: number }>;
}

export interface PackCatalog {
  revision: number;
  packs: MessagePackSummary[];
}

export interface PackEntry {
  id: string;
  text: string;
  origin_pack_id: string;
  origin_pack_name: string;
  native: boolean;
}
```

在 `api.ts` 增加 `createMessagePack`、`importMessagePackFile`、`renameMessagePack`、`reorderMessagePacks`、消息 CRUD、`fuseMessagePack`、`splitMessagePack` 和 `deleteMessagePack`。所有写调用携带当前 revision；409 继续由统一 `request()` 显示后端中文错误。

- [ ] **Step 4: 重写页面为列表加同页详情编辑器**

状态只保留 `catalog`、`selectedPackId`、`detail`、`busy`、`draggedPackId` 和正在编辑的单项。每次 mutation 用响应更新 revision 后重新加载选中详情；冲突时通知并 reload，不在客户端合并陈旧状态。

- [ ] **Step 5: 实现原生拖动和按钮替代**

```tsx
const commitOrder = async (ids: string[]) => {
  if (!catalog) return;
  setCatalog(await api.reorderMessagePacks(ids, catalog.revision));
};

<article
  draggable
  onDragStart={() => setDraggedPackId(pack.id)}
  onDrop={() => void moveBefore(draggedPackId, pack.id)}
  onDragOver={(event) => event.preventDefault()}
>
```

每行同时提供 `上移`、`下移`，边界按钮 disabled。drop 后提交完整 ID 数组。

- [ ] **Step 6: 实现融合、拆分和删除确认文案**

融合目标 select 排除自身。拆分只渲染 `direct_fused_sources`。删除确认包含 `message_count` 和递归 `fused_source_count`；409 时保留 UI 状态并展示“仍被目标使用”。

- [ ] **Step 7: 添加局部样式并运行页面与 App 回归**

Run: `npm test -- --run frontend/src/pages/MessagePacksPage.test.tsx frontend/src/App.test.tsx`

Workdir: `frontend`

Expected: PASS。

- [ ] **Step 8: 类型检查并构建生产资源**

Run: `npm run build`

Workdir: `frontend`

Expected: PASS，生成资源同步到 `src/autody/web/static`；不提交旧 hash 资源残留。

- [ ] **Step 9: 提交 UI**

```powershell
git add frontend/src/types.ts frontend/src/api.ts frontend/src/pages/MessagePacksPage.tsx frontend/src/pages/MessagePacksPage.test.tsx frontend/src/styles.css src/autody/web/static
git commit -m "feat: add message pack management UI"
```

### Task 7: 文档、完整验证与临时 DataRoot 实机验收

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/codex/PROJECT_HANDOFF.md`
- Test: all focused and full existing suites relevant to A

**Interfaces:**
- Consumes: Tasks 1–6 完成的软件。
- Produces: 用户用法、未发布状态、可复现验证记录和干净工作树。

- [ ] **Step 1: 更新当前文档**

README 的文案包段落说明：全局文案库仍独立；可新建/导入/编辑/排序/融合/拆分；删除全部包不会恢复内置包。CHANGELOG 新增 `Unreleased` 的文案包管理条目。PROJECT_HANDOFF 记录新目录路径、一次性 seed 规则、稳定 ID、融合树和禁止重种约束，不写真实包名或正文。

- [ ] **Step 2: 运行后端聚焦套件**

Run: `.\.venv\Scripts\pytest.exe -q tests/test_message_packs.py tests/test_web_api.py tests/test_runner.py tests/test_transfer.py tests/test_config.py -k "message or pack or backup or config"`

Expected: PASS。

- [ ] **Step 3: 运行前端完整测试和构建**

Run: `npm test`

Workdir: `frontend`

Expected: PASS。

Run: `npm run build`

Workdir: `frontend`

Expected: PASS。

- [ ] **Step 4: 运行后端完整测试和 doctor**

Run: `.\.venv\Scripts\pytest.exe -q`

Expected: PASS。

Run: `.\.venv\Scripts\python.exe -m autody.cli doctor`

Expected: exit 0；输出不得包含真实目标、消息、Cookie 或 profile 内容。

- [ ] **Step 5: 使用临时 DataRoot 在 8765 验收生产构建**

先调用 `/api/service-identity`，确认 8765 的 PID、解释器、ProgramRoot、DataRoot 和包路径属于 `D:\AutoDy`，再只停止该精确托管实例。使用 `New-Item` 创建唯一临时目录，复制 `config.example.yaml` 和 `messages.example.txt`，以项目 `.venv`、源码 ProgramRoot 和生产静态资源在 8765 启动；不得指向真实 DataRoot。

验收项：首次 seed 只发生一次；空包、TXT 导入、改名、消息编辑、重复正文、上下移动/拖动、A→B、编辑来源后拆出、A→B→C 后拆 B、被引用删除提示；刷新后顺序和谱系保持；浏览器控制台无错误；请求无循环；1280、1440 和 1920 宽度无横向溢出；键盘可使用上移/下移。测试文件只含临时虚构文案。

停止源码实例、验证 PID 后恢复原安装入口；再次确认 `/api/service-identity` 返回 `D:\AutoDy\runtime\python\python.exe` 和真实 DataRoot。删除临时目录前解析绝对路径并确认它位于系统临时目录下且名称带本次唯一前缀。

- [ ] **Step 6: 检查隐私、差异和工作树**

```powershell
rg -n "C:\\Users\\|真实|cookie|token|profile" src frontend tests docs README.md CHANGELOG.md --glob '!src/autody/web/static/assets/*.js'
git diff --check
git status --short
```

人工确认只包含预期源码、测试、生产静态资源和文档；不包含 DataRoot、日志、临时 catalog、`.venv`、`node_modules` 或截图。

- [ ] **Step 7: 提交文档和最终 A 验证修正**

```powershell
git add README.md CHANGELOG.md docs/codex/PROJECT_HANDOFF.md
git commit -m "docs: document managed message packs"
```

- [ ] **Step 8: 记录 A 的提交边界**

Run: `git log --oneline --decorate -n 12`

Expected: A 的设计、领域、API、备份、UI 和文档提交连续且不包含 B 的启动性能修改；`v1.4.4` 标签仍指向原提交，未构建 MSI，未修改 Release 资产。
