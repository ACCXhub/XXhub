from datetime import datetime
import json
from pathlib import Path

import pytest

from autody.config import AppConfig, Target, load_config, save_config
from autody.message_packs import ImportMode, MessagePackError, MessagePackService
from autody.message_pack_catalog import MessagePackConflict
from autody.native_stickers import (
    GlobalNativeStickerSelectionStore,
    NativeStickerReference,
)


AUTO_NATIVE_STICKER_PACK_ID = "auto-native-stickers"
AUTO_NATIVE_STICKER_PACK_NAME = "自动表情包"


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


def test_read_only_preview_does_not_seed_user_catalog(tmp_path: Path):
    program_parent = tmp_path / "program"
    program_parent.mkdir()
    program_root = make_pack_root(program_parent)
    data_root = tmp_path / "user-data"
    service = MessagePackService(program_root, data_root)

    preview = service.preview("sample", persist_catalog=False)

    assert preview.messages == ["早安呀", "今天顺利"]
    assert not (data_root / "data" / "message-packs" / "catalog.json").exists()
    assert not (data_root / "data" / "locks" / "message-packs.lock").exists()


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


def test_mixed_pack_round_trips_typed_entries_and_keeps_native_provenance_meaning(
    tmp_path: Path,
):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    created = service.create_pack(service.catalog().revision, "混合内容")
    text = service.add_message(created.pack.id, "早安", created.revision)

    sticker = service.add_native_sticker(
        created.pack.id,
        logical_id="sticker-heart",
        display_name="比心",
        resource_key="heart.webp",
        expected_revision=text.revision,
    )
    preview = MessagePackService(tmp_path, data_root).preview(
        created.pack.id
    )

    assert preview.messages == ["早安"]
    assert [entry.kind for entry in preview.entries] == ["text", "native_sticker"]
    assert preview.entries[0].native is True
    assert preview.entries[1].native is True
    assert preview.entries[1].sticker.logical_id == "sticker-heart"
    assert preview.entries[1].sticker.resource_key == "heart.webp"
    assert sticker.entry.id == preview.entries[1].id
    assert preview.pack.count == 2


def test_legacy_catalog_upgrades_centrally_without_changing_ids_or_fusion(
    tmp_path: Path,
):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source, message = create_pack_with_message(service, "来源", "来源文案")
    destination = service.create_pack(service.catalog().revision, "目标").pack
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)
    service.fuse(source.id, destination.id, service.catalog().revision, config_path)

    path = service.store.catalog_path
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy.pop("native_stickers", None)
    for package in legacy["packages"].values():
        for item in package["items"]:
            if item["kind"] == "text":
                item["kind"] = "message"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    restarted = MessagePackService(tmp_path, data_root)
    upgraded = restarted.catalog()

    assert upgraded.schema_version == 2
    assert source.id in upgraded.packages
    assert destination.id in upgraded.packages
    assert message.id in upgraded.messages
    assert restarted.preview(destination.id).messages == ["来源文案"]
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_native_sticker_requires_a_durable_logical_reference(tmp_path: Path):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    created = service.create_pack(service.catalog().revision, "表情")

    with pytest.raises(MessagePackError, match="原生表情"):
        service.add_native_sticker(
            created.pack.id,
            logical_id="",
            display_name="",
            expected_revision=created.revision,
        )


def test_messages_txt_import_uses_only_text_and_blocks_zero_text_replace(
    tmp_path: Path,
):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    mixed = service.create_pack(service.catalog().revision, "混合").pack
    text = service.add_message(mixed.id, "保留文字", service.catalog().revision)
    service.add_native_sticker(
        mixed.id,
        logical_id="sticker-heart",
        display_name="比心",
        expected_revision=text.revision,
    )
    messages = tmp_path / "messages.txt"
    messages.write_text("旧文案\n", encoding="utf-8")

    result = service.import_pack(mixed.id, messages, ImportMode.REPLACE)

    assert result.excluded_non_text_count == 1
    assert messages.read_text(encoding="utf-8") == "保留文字\n"

    sticker_only = service.create_pack(service.catalog().revision, "纯表情").pack
    service.add_native_sticker(
        sticker_only.id,
        logical_id="sticker-fire",
        display_name="续火花",
        expected_revision=service.catalog().revision,
    )
    before = messages.read_bytes()

    with pytest.raises(MessagePackError, match="没有文字"):
        service.import_pack(sticker_only.id, messages, ImportMode.REPLACE)

    assert messages.read_bytes() == before


def test_native_sticker_can_be_reordered_and_removed_as_a_direct_pack_entry(
    tmp_path: Path,
):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    pack = service.create_pack(service.catalog().revision, "混合").pack
    first = service.add_message(pack.id, "第一条", service.catalog().revision)
    sticker = service.add_native_sticker(
        pack.id,
        logical_id="sticker-heart",
        display_name="比心",
        expected_revision=first.revision,
    )
    last = service.add_message(pack.id, "最后一条", sticker.revision)

    reordered = service.reorder_entries(
        pack.id,
        [sticker.entry.id, first.entry.id, last.entry.id],
        last.revision,
    )
    assert [entry.id for entry in service.preview(pack.id).entries] == [
        sticker.entry.id,
        first.entry.id,
        last.entry.id,
    ]

    removed = service.delete_entry(pack.id, sticker.entry.id, reordered.revision)

    assert [entry.kind for entry in service.preview(pack.id).entries] == ["text", "text"]
    assert sticker.entry.id not in service.catalog().native_stickers
    assert removed.pack.count == 2


def test_batch_native_stickers_are_atomic_ordered_and_increment_revision_once(
    tmp_path: Path,
):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    pack = service.create_pack(service.catalog().revision, "混合").pack
    text = service.add_message(pack.id, "保留文字", service.catalog().revision)
    before = service.store.catalog_path.read_bytes()

    with pytest.raises(MessagePackError, match="原生表情"):
        service.add_native_stickers(
            pack.id,
            [
                NativeStickerReference(
                    logical_id="heart",
                    display_name="比心",
                    resource_key="heart.webp",
                ),
                {"logical_id": "   ", "display_name": "无效"},
            ],
            text.revision,
        )

    assert service.store.catalog_path.read_bytes() == before
    added = service.add_native_stickers(
        pack.id,
        [
            NativeStickerReference(
                logical_id="heart",
                display_name="比心",
                resource_key="heart.webp",
            ),
            NativeStickerReference(
                logical_id="fire",
                display_name="续火花",
                resource_key="fire.webp",
            ),
            NativeStickerReference(
                logical_id="heart",
                display_name="重复比心",
                resource_key="duplicate.webp",
            ),
        ],
        text.revision,
    )

    preview = service.preview(pack.id)
    assert added.revision == text.revision + 1
    assert added.added_count == 2
    assert added.duplicate_count == 1
    assert [entry.kind for entry in preview.entries] == [
        "text",
        "native_sticker",
        "native_sticker",
    ]
    assert [entry.sticker.logical_id for entry in preview.entries[1:]] == [
        "heart",
        "fire",
    ]
    assert preview.entries[0].text == "保留文字"

    with pytest.raises(MessagePackConflict):
        service.add_native_stickers(
            pack.id,
            [NativeStickerReference(logical_id="new", display_name="新表情")],
            text.revision,
        )
    assert [entry.sticker.logical_id for entry in service.preview(pack.id).entries[1:]] == [
        "heart",
        "fire",
    ]


def test_auto_native_sticker_pack_uses_reserved_identity_and_reuses_after_rename(
    tmp_path: Path,
):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    duplicate_name = service.create_pack(
        service.catalog().revision,
        AUTO_NATIVE_STICKER_PACK_NAME,
    ).pack

    created = service.add_native_stickers(
        AUTO_NATIVE_STICKER_PACK_ID,
        [NativeStickerReference(logical_id="heart", display_name="比心")],
        service.catalog().revision,
    )
    assert created.pack.id == AUTO_NATIVE_STICKER_PACK_ID
    assert created.pack.name == AUTO_NATIVE_STICKER_PACK_NAME
    assert created.added_count == 1
    assert duplicate_name.id != AUTO_NATIVE_STICKER_PACK_ID

    renamed = service.rename_pack(
        AUTO_NATIVE_STICKER_PACK_ID,
        "我的自动表情",
        created.revision,
    )
    reused = service.add_native_stickers(
        AUTO_NATIVE_STICKER_PACK_ID,
        [
            NativeStickerReference(logical_id="heart", display_name="比心"),
            NativeStickerReference(logical_id="fire", display_name="续火花"),
        ],
        renamed.revision,
    )

    assert reused.pack.id == AUTO_NATIVE_STICKER_PACK_ID
    assert reused.pack.name == "我的自动表情"
    assert reused.added_count == 1
    assert reused.duplicate_count == 1
    assert [
        entry.sticker.logical_id
        for entry in service.preview(AUTO_NATIVE_STICKER_PACK_ID).entries
    ] == ["heart", "fire"]
    assert [
        pack.id
        for pack in service.list_packs().packs
        if pack.id == AUTO_NATIVE_STICKER_PACK_ID
    ] == [AUTO_NATIVE_STICKER_PACK_ID]

    deleted = service.delete_pack(
        AUTO_NATIVE_STICKER_PACK_ID,
        reused.revision,
        set(),
    )
    recreated = service.add_native_stickers(
        AUTO_NATIVE_STICKER_PACK_ID,
        [NativeStickerReference(logical_id="wave", display_name="挥手")],
        deleted.revision,
    )

    assert recreated.pack.id == AUTO_NATIVE_STICKER_PACK_ID
    assert recreated.pack.name == AUTO_NATIVE_STICKER_PACK_NAME
    assert [entry.sticker.logical_id for entry in service.preview(recreated.pack.id).entries] == [
        "wave"
    ]


def test_auto_native_sticker_pack_rejects_incompatible_reserved_id_without_write(
    tmp_path: Path,
):
    ids = iter(["seed-one", "seed-two", AUTO_NATIVE_STICKER_PACK_ID])
    service = MessagePackService(
        make_pack_root(tmp_path),
        tmp_path / "user-data",
        id_factory=lambda: next(ids),
    )
    conflicting = service.create_pack(service.catalog().revision, "用户包")
    before = service.store.catalog_path.read_bytes()

    with pytest.raises(MessagePackError, match="保留 ID"):
        service.add_native_stickers(
            AUTO_NATIVE_STICKER_PACK_ID,
            [NativeStickerReference(logical_id="heart", display_name="比心")],
            conflicting.revision,
        )

    assert service.store.catalog_path.read_bytes() == before


def test_global_sticker_selection_is_independent_from_auto_native_sticker_pack(
    tmp_path: Path,
):
    service = MessagePackService(make_pack_root(tmp_path), tmp_path / "user-data")
    created = service.add_native_stickers(
        AUTO_NATIVE_STICKER_PACK_ID,
        [NativeStickerReference(logical_id="heart", display_name="比心")],
        service.catalog().revision,
    )
    account = "account-" + "a" * 24
    selection_store = GlobalNativeStickerSelectionStore(tmp_path / "user-data")
    selection_store.replace(
        account,
        [NativeStickerReference(logical_id="fire", display_name="续火花")],
    )

    selection_store.replace(
        account,
        [NativeStickerReference(logical_id="heart", display_name="比心")],
    )
    added = service.add_native_stickers(
        AUTO_NATIVE_STICKER_PACK_ID,
        [NativeStickerReference(logical_id="fire", display_name="续火花")],
        created.revision,
    )

    assert [
        entry.sticker.logical_id
        for entry in service.preview(AUTO_NATIVE_STICKER_PACK_ID).entries
    ] == ["heart", "fire"]
    assert [
        item.logical_id for item in selection_store.load(account).stickers
    ] == ["heart"]
    assert added.pack.id == AUTO_NATIVE_STICKER_PACK_ID


def test_fused_child_native_sticker_keeps_identity_and_origin_after_split(
    tmp_path: Path,
):
    data_root = tmp_path / "user-data"
    service = MessagePackService(make_pack_root(tmp_path), data_root)
    source = service.create_pack(service.catalog().revision, "来源").pack
    sticker = service.add_native_sticker(
        source.id,
        logical_id="sticker-fire",
        display_name="续火花",
        expected_revision=service.catalog().revision,
    )
    destination = service.create_pack(sticker.revision, "目标").pack
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)

    fused = service.fuse(
        source.id,
        destination.id,
        service.catalog().revision,
        config_path,
    )
    projected = service.preview(destination.id).entries
    service.split(destination.id, source.id, fused.revision)

    assert [(entry.id, entry.kind, entry.native) for entry in projected] == [
        (sticker.entry.id, "native_sticker", False)
    ]
    assert service.preview(source.id).entries[0].id == sticker.entry.id


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
    unrelated, unrelated_message = create_pack_with_message(
        service,
        "无关",
        "无关文案",
    )
    config_path = data_root / "config.yaml"
    make_config(config_path, source.id)
    service.fuse(source.id, destination.id, service.catalog().revision, config_path)
    messages_path = data_root / "messages.txt"
    messages_path.write_text("保持不变\n", encoding="utf-8")
    sticker_catalog_path = data_root / "data" / "native-stickers" / "catalog.json"
    sticker_catalog_path.parent.mkdir(parents=True)
    sticker_catalog_path.write_text('{"account":"keep"}\n', encoding="utf-8")
    account_path = data_root / "data" / "account-profile.json"
    account_path.write_text('{"profile":"keep"}\n', encoding="utf-8")
    browser_marker = data_root / "browser" / "marker.txt"
    browser_marker.parent.mkdir()
    browser_marker.write_text("keep", encoding="utf-8")

    with pytest.raises(MessagePackError, match="顶层"):
        service.delete_pack(
            source.id,
            service.catalog().revision,
            set(),
        )

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
    assert unrelated.id in service.catalog().packages
    assert unrelated_message.id in service.catalog().messages
    assert messages_path.read_text(encoding="utf-8") == "保持不变\n"
    assert sticker_catalog_path.read_text(encoding="utf-8") == '{"account":"keep"}\n'
    assert account_path.read_text(encoding="utf-8") == '{"profile":"keep"}\n'
    assert browser_marker.read_text(encoding="utf-8") == "keep"


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
