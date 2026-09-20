from datetime import datetime
import json
from pathlib import Path

import pytest

from autody.native_stickers import (
    NativeStickerCatalogError,
    NativeStickerCatalogStore,
    NativeStickerDescriptor,
    NativeStickerReference,
)


def descriptor(
    logical_id: str,
    name: str,
    *,
    resource_key: str | None = None,
    machine_id: str | None = None,
    accessible_name: str | None = None,
    category: str | None = None,
    diagnostic_index: int | None = None,
) -> NativeStickerDescriptor:
    return NativeStickerDescriptor(
        logical_id=logical_id,
        display_name=name,
        resource_key=resource_key,
        machine_id=machine_id,
        accessible_name=accessible_name,
        category=category,
        diagnostic_index=diagnostic_index,
    )


def test_catalog_round_trip_is_bound_to_the_active_account(tmp_path: Path):
    store = NativeStickerCatalogStore(
        tmp_path,
        now=lambda: datetime(2026, 9, 20, 8, 0, 0),
    )

    saved = store.replace(
        "account-" + "a" * 24,
        [descriptor("heart", "比心", resource_key="heart.webp")],
    )

    assert saved.account_profile_id == "account-" + "a" * 24
    assert store.load("account-" + "a" * 24).stickers[0].logical_id == "heart"
    with pytest.raises(NativeStickerCatalogError, match="当前账号"):
        store.load("account-" + "b" * 24)


def test_failed_refresh_preserves_previous_valid_catalog(tmp_path: Path):
    store = NativeStickerCatalogStore(tmp_path)
    account = "account-" + "a" * 24
    store.replace(account, [descriptor("heart", "比心", resource_key="heart.webp")])
    before = store.path.read_bytes()

    with pytest.raises(NativeStickerCatalogError, match="重复"):
        store.replace(
            account,
            [
                descriptor("heart", "比心", resource_key="heart.webp"),
                descriptor("heart", "另一个", resource_key="other.webp"),
            ],
        )

    assert store.path.read_bytes() == before


def test_resolution_prefers_stable_resource_and_fails_closed_on_ambiguity(
    tmp_path: Path,
):
    store = NativeStickerCatalogStore(tmp_path)
    account = "account-" + "a" * 24
    store.replace(
        account,
        [
            descriptor("heart-a", "比心", resource_key="heart-a.webp"),
            descriptor("heart-b", "比心", resource_key="heart-b.webp"),
        ],
    )

    resolved = store.resolve(
        account,
        NativeStickerReference(
            logical_id="shared-heart",
            display_name="比心",
            resource_key="heart-b.webp",
        ),
    )
    assert resolved.logical_id == "heart-b"

    with pytest.raises(NativeStickerCatalogError, match="可靠定位"):
        store.resolve(
            account,
            NativeStickerReference(logical_id="shared-heart", display_name="比心"),
        )


def test_diagnostic_index_is_never_used_as_sticker_identity(tmp_path: Path):
    store = NativeStickerCatalogStore(tmp_path)
    account = "account-" + "a" * 24
    store.replace(
        account,
        [descriptor("fire", "续火花", diagnostic_index=3)],
    )
    payload = json.loads(store.path.read_text(encoding="utf-8"))

    assert payload["stickers"][0]["logical_id"] == "fire"
    with pytest.raises(NativeStickerCatalogError, match="可靠定位"):
        store.resolve(
            account,
            NativeStickerReference(logical_id="other", display_name="不存在"),
        )
