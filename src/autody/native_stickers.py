from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Callable, Literal
import uuid

from pydantic import BaseModel, Field, ValidationError, model_validator


class NativeStickerCatalogError(RuntimeError):
    pass


class NativeStickerReference(BaseModel):
    """Account-neutral logical sticker descriptor stored by a shared pack."""

    logical_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    resource_key: str | None = Field(default=None, max_length=500)
    machine_id: str | None = Field(default=None, max_length=500)
    accessible_name: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=160)


class NativeStickerDescriptor(NativeStickerReference):
    """Account-scoped resolver evidence discovered from the current page."""

    preview_url: str | None = Field(default=None, max_length=2000)
    diagnostic_index: int | None = Field(default=None, ge=0)
    last_seen_at: datetime | None = None


class NativeStickerCatalog(BaseModel):
    schema_version: Literal[1] = 1
    account_profile_id: str = Field(pattern=r"^account-[a-f0-9]{24}$")
    revision: int = Field(default=1, ge=1)
    scanned_at: datetime
    stickers: list[NativeStickerDescriptor]

    @model_validator(mode="after")
    def validate_unique_logical_ids(self):
        logical_ids = [item.logical_id for item in self.stickers]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("native sticker logical IDs must be unique")
        return self


class NativeStickerCatalogStore:
    def __init__(
        self,
        root: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ):
        self.root = root.resolve()
        self.path = self.root / "data" / "native-stickers" / "catalog.json"
        self.now = now or datetime.now

    def load(self, account_profile_id: str) -> NativeStickerCatalog | None:
        if not self.path.exists():
            return None
        try:
            catalog = NativeStickerCatalog.model_validate_json(
                self.path.read_bytes()
            )
        except (OSError, ValidationError) as exc:
            raise NativeStickerCatalogError("原生表情目录无效") from exc
        if catalog.account_profile_id != account_profile_id:
            raise NativeStickerCatalogError(
                "原生表情目录不属于当前账号，请刷新原生表情"
            )
        return catalog

    def replace(
        self,
        account_profile_id: str,
        stickers: list[NativeStickerDescriptor],
    ) -> NativeStickerCatalog:
        try:
            current = self.load(account_profile_id)
            catalog = NativeStickerCatalog(
                account_profile_id=account_profile_id,
                revision=(current.revision + 1 if current else 1),
                scanned_at=self.now(),
                stickers=[
                    item.model_copy(
                        update={"last_seen_at": item.last_seen_at or self.now()}
                    )
                    for item in stickers
                ],
            )
        except (ValidationError, ValueError) as exc:
            raise NativeStickerCatalogError("原生表情目录包含重复或无效标识") from exc
        payload = (
            json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return catalog

    def resolve(
        self,
        account_profile_id: str,
        reference: NativeStickerReference,
    ) -> NativeStickerDescriptor:
        catalog = self.load(account_profile_id)
        if catalog is None:
            raise NativeStickerCatalogError("当前账号尚未扫描原生表情")

        def unique(matches: list[NativeStickerDescriptor]):
            return matches[0] if len(matches) == 1 else None

        lookups = []
        if reference.resource_key:
            lookups.append(
                [
                    item
                    for item in catalog.stickers
                    if item.resource_key == reference.resource_key
                ]
            )
        if reference.machine_id:
            lookups.append(
                [
                    item
                    for item in catalog.stickers
                    if item.machine_id == reference.machine_id
                ]
            )
        exact_accessible = reference.accessible_name or reference.display_name
        lookups.append(
            [
                item
                for item in catalog.stickers
                if item.accessible_name == exact_accessible
            ]
        )
        if reference.category:
            lookups.append(
                [
                    item
                    for item in catalog.stickers
                    if item.category == reference.category
                    and item.display_name == reference.display_name
                ]
            )
        for matches in lookups:
            if resolved := unique(matches):
                return resolved
            if len(matches) > 1:
                break
        raise NativeStickerCatalogError(
            f"已保存的原生表情「{reference.display_name}」当前无法在抖音页面中可靠定位，请刷新原生表情。"
        )
