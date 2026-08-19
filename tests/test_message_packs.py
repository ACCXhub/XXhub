from datetime import datetime
import json
from pathlib import Path

import pytest

from autody.config import AppConfig, Target, load_config, save_config
from autody.message_packs import ImportMode, MessagePackError, MessagePackService
from autody.message_pack_catalog import MessagePackConflict


def make_pack_root(tmp_path: Path) -> Path:
    packs = tmp_path / "message-packs"
    packs.mkdir()
    (packs / "sample.txt").write_text("早安呀\n今天顺利\n早安呀\n", encoding="utf-8")
    (packs / "index.json").write_text(
        json.dumps(
            {
                "packs": [
                    {
                        "id": "sample",
                        "name": "示例文案",
                        "description": "测试",
                        "version": "1.0.0",
                        "file": "sample.txt",
                        "count": 3,
                        "category": "daily",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_repository_index_contains_five_fifty_message_packs():
    service = MessagePackService(Path.cwd())

    catalog = service.list_packs()

    assert len(catalog.packs) == 5
    assert {pack.count for pack in catalog.packs} == {50}
    for pack in catalog.packs:
        preview = service.preview(pack.id)
        assert len(preview.messages) == 50
        assert len(set(preview.messages)) == 50


def test_preview_deduplicates_pack_lines(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path))

    preview = service.preview("sample")

    assert preview.messages == ["早安呀", "今天顺利"]
    assert preview.duplicate_count == 1


def test_merge_import_deduplicates_and_creates_backup(tmp_path: Path):
    service = MessagePackService(
        make_pack_root(tmp_path),
        now=lambda: datetime(2026, 7, 4, 8, 30, 15),
    )
    messages = tmp_path / "messages.txt"
    messages.write_text("已有文案\n早安呀\n", encoding="utf-8")

    result = service.import_pack("sample", messages, ImportMode.MERGE)

    assert result.added_count == 1
    assert result.duplicate_count == 2
    assert result.total_count == 3
    assert result.mode is ImportMode.MERGE
    assert result.backup_path == tmp_path / "data/backups/messages-20260704-083015.txt"
    assert result.backup_path.read_text(encoding="utf-8") == "已有文案\n早安呀\n"
    assert messages.read_text(encoding="utf-8") == "已有文案\n早安呀\n今天顺利\n"


def test_replace_import_backs_up_and_replaces(tmp_path: Path):
    service = MessagePackService(
        make_pack_root(tmp_path),
        now=lambda: datetime(2026, 7, 4, 9, 0, 0),
    )
    messages = tmp_path / "messages.txt"
    messages.write_text("旧文案\n", encoding="utf-8")

    result = service.import_pack("sample", messages, ImportMode.REPLACE)

    assert result.added_count == 2
    assert result.duplicate_count == 1
    assert result.total_count == 2
    assert result.backup_path is not None and result.backup_path.exists()
    assert messages.read_text(encoding="utf-8") == "早安呀\n今天顺利\n"


def test_preview_rejects_pack_paths_outside_builtin_directory(tmp_path: Path):
    root = make_pack_root(tmp_path)
    (root / "outside.txt").write_text("不应读取\n", encoding="utf-8")
    index = root / "message-packs" / "index.json"
    payload = json.loads(index.read_text(encoding="utf-8"))
    payload["packs"][0]["file"] = "../outside.txt"
    index.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    service = MessagePackService(root)

    with pytest.raises(MessagePackError, match="内置文案包文件不存在"):
        service.preview("sample")


def test_preview_only_never_writes_or_backs_up(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path))
    messages = tmp_path / "messages.txt"
    messages.write_text("原文案\n", encoding="utf-8")

    result = service.import_pack("sample", messages, ImportMode.PREVIEW_ONLY)

    assert result.backup_path is None
    assert messages.read_text(encoding="utf-8") == "原文案\n"
    assert not (tmp_path / "data/backups").exists()


def test_first_catalog_load_seeds_builtin_ids_once(tmp_path: Path):
    program_root = make_pack_root(tmp_path)
    data_root = tmp_path / "user-data"
    message_ids = iter(["message-seeded-1", "message-seeded-2"])
    service = MessagePackService(
        program_root,
        data_root,
        id_factory=lambda: next(message_ids),
        now=lambda: datetime(2026, 8, 19, 20, 0, 0),
    )

    catalog = service.catalog()

    assert catalog.top_level_pack_ids == ["sample"]
    assert catalog.migrations.builtin_seed_v1.completed is True
    assert service.preview("sample").messages == ["早安呀", "今天顺利"]
    assert (data_root / "data/message-packs/catalog.json").is_file()


def test_empty_completed_catalog_is_not_seeded_again(tmp_path: Path):
    program_root = make_pack_root(tmp_path)
    data_root = tmp_path / "user-data"
    service = MessagePackService(program_root, data_root)
    catalog_path = data_root / "data/message-packs/catalog.json"
    service.catalog()
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "revision": payload["revision"] + 1,
            "top_level_pack_ids": [],
            "packages": {},
            "messages": {},
        }
    )
    catalog_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    restarted = MessagePackService(program_root, data_root)

    assert restarted.list_packs().packs == []
    assert restarted.catalog().migrations.builtin_seed_v1.completed is True


def test_create_empty_pack_and_rename_preserve_stable_id(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")

    created = service.create_pack(expected_revision=service.catalog().revision)
    renamed = service.rename_pack(
        created.pack.id,
        "晨间",
        expected_revision=created.revision,
    )

    assert created.pack.name == "新建文案包"
    assert renamed.pack.id == created.pack.id
    assert renamed.pack.name == "晨间"
    assert service.preview(created.pack.id).messages == []


def test_import_uses_first_valid_line_for_name_and_keeps_duplicate_entries(
    tmp_path: Path,
):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    first = "甲" * 100

    imported = service.import_text(
        f"\n{first}\n早安\n早安\n".encode("utf-8"),
        "morning.txt",
        expected_revision=service.catalog().revision,
    )
    detail = service.preview(imported.pack.id)

    assert imported.pack.name == "甲" * 79 + "…"
    assert detail.messages == [first, "早安", "早安"]
    assert len({entry.id for entry in detail.entries}) == 3


def test_message_edit_and_delete_keep_identity_until_deletion(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    created = service.create_pack(expected_revision=service.catalog().revision)
    added = service.add_message(
        created.pack.id,
        "早安",
        expected_revision=created.revision,
    )

    edited = service.update_message(
        created.pack.id,
        added.entry.id,
        "早呀",
        expected_revision=added.revision,
    )
    deleted = service.delete_message(
        created.pack.id,
        added.entry.id,
        expected_revision=edited.revision,
    )

    assert edited.entry.id == added.entry.id
    assert edited.entry.text == "早呀"
    assert deleted.pack.count == 0
    assert service.preview(created.pack.id).messages == []


def test_reorder_persists_after_service_restart(tmp_path: Path):
    program_root = make_pack_root(tmp_path)
    data_root = tmp_path / "user-data"
    service = MessagePackService(program_root, data_root)
    first = service.create_pack(service.catalog().revision, "一")
    second = service.create_pack(first.revision, "二")
    original = [pack.id for pack in service.list_packs().packs]

    reordered = service.reorder_packs(
        list(reversed(original)),
        expected_revision=second.revision,
    )
    restarted = MessagePackService(program_root, data_root)

    assert [pack.id for pack in reordered.catalog.packs] == list(reversed(original))
    assert [pack.id for pack in restarted.list_packs().packs] == list(reversed(original))


def test_stale_revision_does_not_overwrite_newer_catalog(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    revision = service.catalog().revision
    service.create_pack(expected_revision=revision)

    with pytest.raises(MessagePackConflict, match="刷新"):
        service.create_pack(expected_revision=revision)


def make_config(path: Path, pack_id: str) -> None:
    save_config(
        path,
        AppConfig(targets=[Target(name="测试目标", message_pack=pack_id)]),
    )


def create_pack_with_message(
    service: MessagePackService,
    name: str,
    text: str,
):
    created = service.create_pack(service.catalog().revision, name)
    added = service.add_message(created.pack.id, text, created.revision)
    return created.pack, added.entry


def test_fuse_split_keeps_current_content_and_migrates_target_once(tmp_path: Path):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source, message = create_pack_with_message(service, "来源", "早安")
    destination = service.create_pack(service.catalog().revision, "目标").pack
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)

    fused = service.fuse(
        source.id,
        destination.id,
        service.catalog().revision,
        config_path,
    )
    edited = service.update_message(
        destination.id,
        message.id,
        "早呀",
        fused.revision,
    )
    split = service.split(
        destination.id,
        source.id,
        edited.revision,
    )

    assert source.id not in [pack.id for pack in fused.catalog.packs]
    assert service.preview(source.id).messages == ["早呀"]
    assert [pack.id for pack in service.direct_fused_sources(destination.id)] == []
    assert load_config(config_path).targets[0].message_pack == destination.id
    assert split.revision == edited.revision + 1


def test_nested_fusion_split_preserves_child_lineage(tmp_path: Path):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    a, _message = create_pack_with_message(service, "A", "A 文案")
    b = service.create_pack(service.catalog().revision, "B").pack
    c = service.create_pack(service.catalog().revision, "C").pack
    config_path = data_root / "config.yaml"
    make_config(config_path, a.id)

    service.fuse(a.id, b.id, service.catalog().revision, config_path)
    service.fuse(b.id, c.id, service.catalog().revision, config_path)
    service.split(c.id, b.id, service.catalog().revision)

    assert [pack.id for pack in service.direct_fused_sources(b.id)] == [a.id]
    assert service.preview(b.id).messages == ["A 文案"]
    assert load_config(config_path).targets[0].message_pack == c.id


def test_message_added_after_fusion_belongs_to_destination_and_is_last(tmp_path: Path):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source, _message = create_pack_with_message(service, "来源", "来源文案")
    destination, _destination_message = create_pack_with_message(
        service,
        "目标",
        "目标文案",
    )
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)
    fused = service.fuse(
        source.id,
        destination.id,
        service.catalog().revision,
        config_path,
    )

    added = service.add_message(destination.id, "融合后新增", fused.revision)
    preview = service.preview(destination.id)

    assert preview.messages == ["目标文案", "来源文案", "融合后新增"]
    assert added.entry.native is True
    assert preview.entries[-1].origin_pack_id == destination.id


def test_delete_pack_rejects_reference_then_recursively_removes_subtree(tmp_path: Path):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source, source_message = create_pack_with_message(service, "来源", "来源文案")
    destination, destination_message = create_pack_with_message(
        service,
        "目标",
        "目标文案",
    )
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)
    service.fuse(source.id, destination.id, service.catalog().revision, config_path)

    with pytest.raises(MessagePackConflict, match="目标使用"):
        service.delete_pack(
            destination.id,
            service.catalog().revision,
            {destination.id},
        )

    deleted = service.delete_pack(
        destination.id,
        service.catalog().revision,
        set(),
    )

    assert deleted.pack is None
    assert source.id not in service.catalog().packages
    assert destination.id not in service.catalog().packages
    assert source_message.id not in service.catalog().messages
    assert destination_message.id not in service.catalog().messages


def test_pending_fusion_transaction_rolls_forward_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source, _message = create_pack_with_message(service, "来源", "来源文案")
    destination = service.create_pack(service.catalog().revision, "目标").pack
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)
    original_replace = service.store._replace_target
    replacements = 0

    class SimulatedCrash(BaseException):
        pass

    def fail_after_first_replace(path: Path, payload: bytes) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise SimulatedCrash("simulated process exit")
        original_replace(path, payload)

    monkeypatch.setattr(service.store, "_replace_target", fail_after_first_replace)

    with pytest.raises(SimulatedCrash):
        service.fuse(
            source.id,
            destination.id,
            service.catalog().revision,
            config_path,
        )

    assert service.store.pending_path.exists()
    recovered = MessagePackService(service.program_root, data_root)
    assert source.id not in recovered.catalog().top_level_pack_ids
    assert load_config(config_path).targets[0].message_pack == destination.id
    assert not recovered.store.pending_path.exists()


def test_fusion_write_error_rolls_back_catalog_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source, _message = create_pack_with_message(service, "来源", "来源文案")
    destination = service.create_pack(service.catalog().revision, "目标").pack
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)
    catalog_before = service.store.catalog_path.read_bytes()
    config_before = config_path.read_bytes()
    original_replace = service.store._replace_target
    replacements = 0

    def fail_after_first_replace(path: Path, payload: bytes) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("disk error")
        original_replace(path, payload)

    monkeypatch.setattr(service.store, "_replace_target", fail_after_first_replace)

    with pytest.raises(MessagePackError, match="已回滚"):
        service.fuse(
            source.id,
            destination.id,
            service.catalog().revision,
            config_path,
        )

    assert service.store.catalog_path.read_bytes() == catalog_before
    assert config_path.read_bytes() == config_before
    assert not service.store.pending_path.exists()
