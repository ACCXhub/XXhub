"""Verified local cache for the authenticated Douyin account.

This module intentionally reads only the page's explicit current-login store.
It never inspects friend candidates, conversation rows, or message content.
"""

from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from typing import NewType

from PIL import Image


MANAGED_DOUYIN_ORIGINS = ("https://www.douyin.com",)
_BINDING_STATE_FILE = "account-binding-state.json"

PlatformAccountIdDigest = NewType("PlatformAccountIdDigest", str)
LocalProfileId = NewType("LocalProfileId", str)
RunAccountScope = NewType("RunAccountScope", str)


class AccountProfileUnavailable(RuntimeError):
    """The page did not provide a verifiable authenticated account object."""


@dataclass(frozen=True)
class AccountProfile:
    account_profile_id: str
    account_id_digest: str
    display_name: str
    avatar_cache_key: str
    avatar_version: str
    is_self: bool
    verification_source: str
    profile_status: str
    verified_at: str
    last_updated_at: str
    switched: bool = False


@dataclass(frozen=True)
class AccountScopeEvaluation:
    compatible: bool | None
    reason_code: str | None
    account_comparison: str
    run_scope_comparison: str | None = None


def _scope_comparison(
    scope: str | None,
    *,
    local_profile_id: LocalProfileId,
    platform_account_id_digest: PlatformAccountIdDigest,
    prefix: str,
) -> str:
    if not scope:
        return f"missing_{prefix}"
    if scope == local_profile_id:
        return f"{prefix}_matches_local_profile_id"
    if scope == platform_account_id_digest:
        return f"{prefix}_matches_platform_account_id_digest"
    return f"{prefix}_matches_neither_current_namespace"


def evaluate_account_scope(
    profile: AccountProfile | None,
    *,
    binding_scope: str | None,
    run_scope: RunAccountScope | str | None = None,
) -> AccountScopeEvaluation:
    """Compare current binding identity without treating account namespaces as strings."""
    if profile is None:
        return AccountScopeEvaluation(
            compatible=False,
            reason_code="login_required",
            account_comparison="missing_authenticated_profile",
            run_scope_comparison=None,
        )
    local_profile_id = LocalProfileId(profile.account_profile_id)
    platform_digest = PlatformAccountIdDigest(profile.account_id_digest)
    account_comparison = _scope_comparison(
        binding_scope,
        local_profile_id=local_profile_id,
        platform_account_id_digest=platform_digest,
        prefix="binding_scope",
    )
    run_scope_comparison = _scope_comparison(
        str(run_scope) if run_scope else None,
        local_profile_id=local_profile_id,
        platform_account_id_digest=platform_digest,
        prefix="run_scope",
    )
    if not binding_scope:
        return AccountScopeEvaluation(
            compatible=None,
            reason_code=None,
            account_comparison=account_comparison,
            run_scope_comparison=run_scope_comparison,
        )
    compatible = account_comparison != (
        "binding_scope_matches_neither_current_namespace"
    )
    return AccountScopeEvaluation(
        compatible=compatible,
        reason_code=None if compatible else "account_scope_mismatch",
        account_comparison=account_comparison,
        run_scope_comparison=run_scope_comparison,
    )


def _paths(root: Path) -> tuple[Path, Path]:
    data = root / "data"
    return data / "account-profile.json", data / "account-avatar" / "profile.png"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _binding_state_path(data_root: Path) -> Path:
    return data_root / _BINDING_STATE_FILE


def mark_bindings_for_revalidation(data_root: Path) -> None:
    _atomic_json(
        _binding_state_path(data_root),
        {
            "status": "revalidation_required",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def bindings_revalidation_required(data_root: Path) -> bool:
    try:
        payload = json.loads(_binding_state_path(data_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return payload.get("status") == "revalidation_required"


def complete_binding_revalidation(
    data_root: Path,
    *,
    bindings_proven: bool,
) -> bool:
    """Clear the logout guard only after canonical binding proof succeeds."""
    if not bindings_revalidation_required(data_root):
        return True
    if not bindings_proven:
        return False
    _binding_state_path(data_root).unlink(missing_ok=True)
    return True


@contextmanager
def _managed_browser_context(profile_dir: Path, root: Path):
    from playwright.sync_api import sync_playwright
    from autody.runtime import configure_runtime

    configure_runtime(root)
    playwright = sync_playwright().start()
    context = None
    try:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
        )
        yield context
    finally:
        if context is not None:
            context.close()
        playwright.stop()


def _is_managed_douyin_cookie(domain: str) -> bool:
    normalized = domain.lstrip(".").casefold()
    return normalized == "douyin.com" or normalized.endswith(".douyin.com")


def clear_managed_authentication(
    profile_dir: Path,
    *,
    root: Path,
    data_root: Path,
    context_factory=None,
) -> None:
    """Clear Douyin authentication in one AutoDy-managed browser profile only."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    # Guard sending before touching authentication. If Chromium fails midway,
    # partially cleared credentials can never be treated as a verified session.
    mark_bindings_for_revalidation(data_root)
    supplied = context_factory(profile_dir) if context_factory else None
    context_manager = (
        nullcontext(supplied)
        if supplied is not None
        else _managed_browser_context(profile_dir, root)
    )
    with context_manager as context:
        for cookie in context.cookies():
            domain = str(cookie.get("domain", ""))
            if not _is_managed_douyin_cookie(domain):
                continue
            context.clear_cookies(
                name=str(cookie.get("name", "")),
                domain=domain,
                path=str(cookie.get("path", "/")),
            )
        page = context.pages[0] if context.pages else context.new_page()
        cdp = context.new_cdp_session(page)
        for origin in MANAGED_DOUYIN_ORIGINS:
            cdp.send(
                "Storage.clearDataForOrigin",
                {
                    "origin": origin,
                    "storageTypes": (
                        "local_storage,indexeddb,websql,cache_storage,"
                        "service_workers"
                    ),
                },
            )


def logout_managed_account(
    profile_dir: Path,
    *,
    root: Path,
    data_root: Path,
    context_factory=None,
) -> None:
    """Legacy single-account logout, including its account-scoped cache cleanup."""
    clear_managed_authentication(
        profile_dir,
        root=root,
        data_root=data_root,
        context_factory=context_factory,
    )
    for path in (
        data_root / "account-profile.json",
        data_root / "account-avatar" / "profile.png",
        data_root / "discovered_friends.json",
        data_root / "ignored-friend-bindings.json",
        data_root / "health.json",
        data_root / "friend_scan_progress.json",
    ):
        path.unlink(missing_ok=True)


def load_account_profile(root: Path) -> AccountProfile | None:
    path, avatar = _paths(root)
    if not path.is_file() or not avatar.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("profile_status") != "verified" or payload.get("is_self") is not True:
            return None
        return AccountProfile(**{key: payload[key] for key in AccountProfile.__dataclass_fields__ if key in payload})
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _current_user_from_page(page) -> dict | None:
    # The object name was verified in a real, authenticated Douyin chat session.
    # Its `curLoginUserInfo` ownership is the strong self-user semantic; fields
    # are extracted together from that one object.
    return page.evaluate(
        """() => {
          const user = globalThis.userInfoStore?.curLoginUserInfo;
          if (!user || typeof user !== 'object') return null;
          const stableId = user.secUid || user.sec_uid || user.uid;
          const nickname = user.nickname;
          const avatar = user.avatarUrl || user.avatar300Url ||
            user.avatarThumb?.urlList?.[0] || user.avatar?.urlList?.[0] ||
            user.avatarLarger?.urlList?.[0];
          if (typeof stableId !== 'string' && typeof stableId !== 'number') return null;
          if (typeof nickname !== 'string' || !nickname.trim()) return null;
          if (typeof avatar !== 'string' || !avatar.startsWith('http')) return null;
          return {
            stable_id: String(stableId), display_name: nickname.trim(), avatar_url: avatar,
            source: 'bootstrap_current_login_user', is_self: true
          };
        }"""
    )


def attach_account_observer(page) -> None:
    """Attach read-only login-flow observers before navigation or QR completion.

    Response bodies are deliberately not persisted.  The verified bootstrap store
    remains the source of truth because it explicitly denotes the current login.
    """
    def observe_response(response) -> None:
        content_type = str(response.headers.get("content-type", "")).lower()
        if "json" not in content_type:
            return
        # Parse only to confirm that the listener observes JSON traffic.  Never
        # retain raw objects, URLs, cookies, or identifiers from network data.
        try:
            response.json()
        except Exception:
            return

    page.on("response", observe_response)
    page.on("framenavigated", lambda _frame: None)


def _download_avatar(page, url: str, destination: Path) -> str:
    response = page.context.request.get(url, timeout=10_000)
    content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
    if not getattr(response, "ok", False) or not content_type.startswith("image/"):
        raise AccountProfileUnavailable("当前账号头像下载未通过图片校验")
    raw = response.body()
    try:
        image = Image.open(BytesIO(raw))
        image.verify()
        image = Image.open(BytesIO(raw)).convert("RGBA")
        if image.width < 1 or image.height < 1:
            raise ValueError("empty image")
    except Exception as exc:
        raise AccountProfileUnavailable("当前账号头像文件无效") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.png")
    image.save(temporary, format="PNG")
    content = temporary.read_bytes()
    os.replace(temporary, destination)
    return sha256(content).hexdigest()[:20]


def resolve_account_profile(page, root: Path, now=None) -> AccountProfile:
    """Resolve and persist one verified current-account record from a page."""
    candidate = _current_user_from_page(page)
    if not isinstance(candidate, dict) or candidate.get("is_self") is not True:
        raise AccountProfileUnavailable("未发现可验证的当前登录账号资料")
    stable_id = str(candidate.get("stable_id", "")).strip()
    display_name = str(candidate.get("display_name", "")).strip()
    avatar_url = str(candidate.get("avatar_url", "")).strip()
    if not stable_id or not display_name or not avatar_url.startswith(("https://", "http://")):
        raise AccountProfileUnavailable("当前登录账号资料不完整")

    profile_path, avatar_path = _paths(root)
    previous = load_account_profile(root)
    digest = sha256(stable_id.encode("utf-8")).hexdigest()
    timestamp = (now or datetime.now)().isoformat(timespec="seconds")
    # Download before replacing metadata so an incomplete switch can never mix a
    # new nickname with the previous account's avatar.
    avatar_version = _download_avatar(page, avatar_url, avatar_path)
    switched = bool(previous and previous.account_id_digest != digest)
    profile = AccountProfile(
        account_profile_id=f"account-{digest[:24]}",
        account_id_digest=digest,
        display_name=display_name,
        avatar_cache_key="profile",
        avatar_version=avatar_version,
        is_self=True,
        verification_source="bootstrap_current_login_user",
        profile_status="verified",
        verified_at=timestamp,
        last_updated_at=timestamp,
        switched=switched,
    )
    payload = asdict(profile)
    if switched:
        payload["switch_audit"] = [{
            "at": timestamp,
            "from_account_id_digest": previous.account_id_digest,
            "to_account_id_digest": digest,
        }]
    # Do not persist remote URLs (which may contain short-lived signatures).
    payload["avatar_source"] = "authenticated_browser_image"
    _atomic_json(profile_path, payload)
    return profile


def public_profile_payload(root: Path, logged_in: bool = False, refresh_running: bool = False) -> dict:
    profile = load_account_profile(root)
    if profile is None:
        return {
            "display_name": None, "avatar_url": None, "avatar_version": None,
            "is_self": False, "profile_status": "unverified", "verification_source": None,
            "logged_in": logged_in, "cached": False, "last_updated_at": None,
            "refresh_running": refresh_running,
        }
    return {
        "display_name": profile.display_name,
        "avatar_url": f"/api/account-profile/avatar?v={profile.avatar_version}",
        "avatar_version": profile.avatar_version,
        "is_self": True,
        "profile_status": "verified",
        "verification_source": profile.verification_source,
        "logged_in": logged_in,
        "cached": True,
        "last_updated_at": profile.last_updated_at,
        "refresh_running": refresh_running,
    }
