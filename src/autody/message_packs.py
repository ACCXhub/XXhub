from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
import shutil
from typing import Callable


class ImportMode(str, Enum):
    MERGE = "merge"
    REPLACE = "replace"
    PREVIEW_ONLY = "preview_only"


@dataclass(frozen=True)
class MessagePack:
    id: str
    name: str
    description: str
    version: str
    file: str
    count: int
    category: str


@dataclass(frozen=True)
class PackCatalog:
    packs: list[MessagePack]


@dataclass(frozen=True)
class PackPreview:
    pack: MessagePack
    messages: list[str]
    duplicate_count: int


@dataclass(frozen=True)
class ImportResult:
    added_count: int
    duplicate_count: int
    total_count: int
    backup_path: Path | None
    mode: ImportMode


class MessagePackError(RuntimeError):
    pass


class MessagePackService:
    def __init__(
        self,
        root: Path,
        now: Callable[[], datetime] | None = None,
    ):
        self.root = root.resolve()
        self.pack_dir = self.root / "message-packs"
        self.now = now or datetime.now

    def _parse_index(self, text: str) -> list[MessagePack]:
        try:
            payload = json.loads(text)
            raw_packs = payload["packs"]
            packs = [MessagePack(**item) for item in raw_packs]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise MessagePackError(f"文案包索引无效：{exc}") from exc
        if len({pack.id for pack in packs}) != len(packs):
            raise MessagePackError("文案包索引包含重复 id")
        return packs

    def _local_packs(self) -> list[MessagePack]:
        index = self.pack_dir / "index.json"
        if not index.exists():
            raise MessagePackError(f"内置文案包索引不存在：{index}")
        return self._parse_index(index.read_text(encoding="utf-8"))

    def list_packs(self) -> PackCatalog:
        return PackCatalog(packs=self._local_packs())

    def _local_pack_text(self, pack: MessagePack) -> str:
        candidate = (self.pack_dir / pack.file).resolve()
        if self.pack_dir not in candidate.parents or not candidate.is_file():
            raise MessagePackError(f"内置文案包文件不存在：{pack.file}")
        return candidate.read_text(encoding="utf-8")

    def preview(self, pack_id: str) -> PackPreview:
        catalog = self.list_packs()
        pack = next((item for item in catalog.packs if item.id == pack_id), None)
        if pack is None:
            raise MessagePackError(f"未知文案包：{pack_id}")
        text = self._local_pack_text(pack)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        messages = list(dict.fromkeys(lines))
        if not messages:
            raise MessagePackError(f"文案包为空：{pack_id}")
        return PackPreview(
            pack=pack,
            messages=messages,
            duplicate_count=len(lines) - len(messages),
        )

    def _backup(self, messages_file: Path) -> Path | None:
        if not messages_file.exists():
            return None
        backup_dir = self.root / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"messages-{self.now():%Y%m%d-%H%M%S}.txt"
        shutil.copy2(messages_file, backup)
        return backup

    @staticmethod
    def _read_existing(messages_file: Path) -> list[str]:
        if not messages_file.exists():
            return []
        lines = [
            line.strip()
            for line in messages_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return list(dict.fromkeys(lines))

    @staticmethod
    def _write(messages_file: Path, messages: list[str]) -> None:
        messages_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = messages_file.with_suffix(messages_file.suffix + ".tmp")
        temporary.write_text("\n".join(messages) + "\n", encoding="utf-8")
        os.replace(temporary, messages_file)

    def import_pack(
        self,
        pack_id: str,
        messages_file: Path,
        mode: ImportMode,
    ) -> ImportResult:
        preview = self.preview(pack_id)
        existing = self._read_existing(messages_file)
        if mode is ImportMode.PREVIEW_ONLY:
            return ImportResult(
                added_count=0,
                duplicate_count=preview.duplicate_count,
                total_count=len(existing),
                backup_path=None,
                mode=mode,
            )
        backup = self._backup(messages_file)
        if mode is ImportMode.MERGE:
            existing_set = set(existing)
            additions = [item for item in preview.messages if item not in existing_set]
            final = existing + additions
            duplicate_count = preview.duplicate_count + len(preview.messages) - len(additions)
            added_count = len(additions)
        elif mode is ImportMode.REPLACE:
            final = preview.messages
            duplicate_count = preview.duplicate_count
            added_count = len(final)
        else:
            raise MessagePackError(f"不支持的导入模式：{mode}")
        self._write(messages_file, final)
        return ImportResult(
            added_count=added_count,
            duplicate_count=duplicate_count,
            total_count=len(final),
            backup_path=backup,
            mode=mode,
        )
