from datetime import datetime
import json
from pathlib import Path

import pytest

from autody.message_packs import ImportMode, MessagePackError, MessagePackService


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
