from io import BytesIO
from pathlib import Path

from PIL import Image

from autody import account_profile as account_profile_module
from autody.account_profile import (
    AccountProfile,
    AccountProfileUnavailable,
    bindings_revalidation_required,
    clear_managed_authentication,
    complete_binding_revalidation,
    load_account_profile,
    logout_managed_account,
    mark_bindings_for_revalidation,
    resolve_account_profile,
)


def _image_bytes(color: str) -> bytes:
    image = Image.new("RGB", (3, 3), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _Response:
    ok = True
    headers = {"content-type": "image/png"}

    def __init__(self, content: bytes):
        self._content = content

    def body(self):
        return self._content


class _Request:
    def __init__(self, content: bytes):
        self.content = content
        self.urls: list[str] = []

    def get(self, url: str, timeout: int):
        self.urls.append(url)
        return _Response(self.content)


class _Context:
    def __init__(self, content: bytes):
        self.request = _Request(content)


class _Page:
    def __init__(self, payload, content: bytes = b""):
        self.payload = payload
        self.context = _Context(content)

    def evaluate(self, _script):
        return self.payload


def _verified_user(name: str, stable_id: str, avatar_url: str = "https://image.example/avatar.png?token=secret"):
    return {
        "stable_id": stable_id,
        "display_name": name,
        "avatar_url": avatar_url,
        "source": "bootstrap_current_login_user",
        "is_self": True,
    }


def _account_profile() -> AccountProfile:
    return AccountProfile(
        account_profile_id="account-" + "a" * 24,
        account_id_digest="a" * 64,
        display_name="测试账号",
        avatar_cache_key="profile",
        avatar_version="v1",
        is_self=True,
        verification_source="test",
        profile_status="verified",
        verified_at="2026-07-30T08:00:00",
        last_updated_at="2026-07-30T08:00:00",
    )


def test_account_scope_evaluation_keeps_digest_local_profile_and_run_scope_distinct():
    profile = _account_profile()

    assert hasattr(account_profile_module, "evaluate_account_scope")
    local_match = account_profile_module.evaluate_account_scope(
        profile,
        binding_scope=account_profile_module.LocalProfileId(profile.account_profile_id),
        run_scope=account_profile_module.RunAccountScope("legacy-run-scope"),
    )
    digest_match = account_profile_module.evaluate_account_scope(
        profile,
        binding_scope=account_profile_module.PlatformAccountIdDigest(
            profile.account_id_digest
        ),
        run_scope=account_profile_module.RunAccountScope("legacy-run-scope"),
    )

    assert local_match.compatible is True
    assert local_match.account_comparison == "binding_scope_matches_local_profile_id"
    assert digest_match.compatible is True
    assert digest_match.account_comparison == (
        "binding_scope_matches_platform_account_id_digest"
    )
    assert local_match.run_scope_comparison == (
        "run_scope_matches_neither_current_namespace"
    )
    assert digest_match.run_scope_comparison == (
        "run_scope_matches_neither_current_namespace"
    )


def test_account_scope_evaluation_reports_sanitized_genuine_mismatch():
    assert hasattr(account_profile_module, "evaluate_account_scope")
    evaluation = account_profile_module.evaluate_account_scope(
        _account_profile(),
        binding_scope=account_profile_module.RunAccountScope(
            "account-" + "b" * 24
        ),
    )

    assert evaluation.compatible is False
    assert evaluation.reason_code == "account_scope_mismatch"
    assert evaluation.account_comparison == (
        "binding_scope_matches_neither_current_namespace"
    )


def test_resolve_verified_current_user_writes_atomic_self_profile_and_local_avatar(tmp_path: Path):
    page = _Page(_verified_user("本人昵称", "current-user"), _image_bytes("red"))

    profile = resolve_account_profile(page, tmp_path)

    assert profile.is_self is True
    assert profile.display_name == "本人昵称"
    assert profile.verification_source == "bootstrap_current_login_user"
    assert not profile.account_id_digest.endswith("current-user")
    assert (tmp_path / "data" / "account-avatar" / "profile.png").is_file()
    assert load_account_profile(tmp_path) == profile
    assert "token=secret" not in (tmp_path / "data" / "account-profile.json").read_text(encoding="utf-8")


def test_unverified_or_chat_user_payload_cannot_create_a_profile(tmp_path: Path):
    page = _Page({"stable_id": "friend", "display_name": "聊天用户", "avatar_url": "https://image.example/friend.png", "is_self": False}, _image_bytes("blue"))

    try:
        resolve_account_profile(page, tmp_path)
    except AccountProfileUnavailable:
        pass
    else:
        raise AssertionError("chat-list user must not be accepted as the current account")

    assert load_account_profile(tmp_path) is None
    assert not (tmp_path / "data" / "account-avatar" / "profile.png").exists()


def test_account_switch_replaces_name_and_avatar_together(tmp_path: Path):
    first = resolve_account_profile(_Page(_verified_user("账号一", "user-one"), _image_bytes("red")), tmp_path)
    second = resolve_account_profile(_Page(_verified_user("账号二", "user-two"), _image_bytes("blue")), tmp_path)

    assert first.account_id_digest != second.account_id_digest
    assert second.display_name == "账号二"
    assert Image.open(tmp_path / "data" / "account-avatar" / "profile.png").getpixel((0, 0))[:3] == (0, 0, 255)
    stored = (tmp_path / "data" / "account-profile.json").read_text(encoding="utf-8")
    assert "账号一" not in stored
    assert "user-one" not in stored


def test_refresh_failure_preserves_the_previous_verified_profile(tmp_path: Path):
    original = resolve_account_profile(_Page(_verified_user("账号一", "user-one"), _image_bytes("red")), tmp_path)
    avatar = (tmp_path / "data" / "account-avatar" / "profile.png").read_bytes()

    try:
        resolve_account_profile(_Page({"stable_id": "friend", "display_name": "聊天用户", "is_self": False}), tmp_path)
    except AccountProfileUnavailable:
        pass
    else:
        raise AssertionError("unverified refresh must fail")

    assert load_account_profile(tmp_path) == original
    assert (tmp_path / "data" / "account-avatar" / "profile.png").read_bytes() == avatar


def test_managed_logout_clears_only_douyin_auth_and_account_scoped_cache(tmp_path: Path):
    data = tmp_path / "data"
    profile_dir = data / "browser-profile"
    profile_dir.mkdir(parents=True)
    (data / "account-avatar").mkdir()
    (data / "account-avatar" / "profile.png").write_bytes(b"avatar")
    (data / "account-profile.json").write_text('{"profile_status":"verified"}', encoding="utf-8")
    (data / "discovered_friends.json").write_text('{"candidates":[]}', encoding="utf-8")
    (data / "ignored-friend-bindings.json").write_text('{"target_ids":[]}', encoding="utf-8")
    (data / "health.json").write_text('{"status":"success"}', encoding="utf-8")
    protected = {
        tmp_path / "config.yaml": b"targets: []",
        tmp_path / "messages.txt": b"hello",
        data / "state.json": b"{}",
        data / "logs" / "autody-2026-07-30.log": b"log",
        data / "modules" / "autody-test-center" / "data" / "settings.json": b"{}",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    class Cdp:
        def __init__(self):
            self.calls = []

        def send(self, method, payload):
            self.calls.append((method, payload))

    class Context:
        def __init__(self):
            self.pages = [object()]
            self.cdp = Cdp()
            self.cleared = []

        def cookies(self):
            return [
                {"name": "auth", "domain": ".douyin.com", "path": "/"},
                {"name": "other", "domain": ".example.com", "path": "/"},
            ]

        def clear_cookies(self, **kwargs):
            self.cleared.append(kwargs)

        def new_cdp_session(self, _page):
            return self.cdp

    context = Context()

    logout_managed_account(
        profile_dir,
        root=tmp_path,
        data_root=data,
        context_factory=lambda _profile_dir: context,
    )

    assert context.cleared == [{"name": "auth", "domain": ".douyin.com", "path": "/"}]
    assert context.cdp.calls == [
        (
            "Storage.clearDataForOrigin",
            {
                "origin": "https://www.douyin.com",
                "storageTypes": "local_storage,indexeddb,websql,cache_storage,service_workers",
            },
        )
    ]
    assert not (data / "account-profile.json").exists()
    assert not (data / "account-avatar" / "profile.png").exists()
    assert not (data / "discovered_friends.json").exists()
    assert not (data / "ignored-friend-bindings.json").exists()
    assert not (data / "health.json").exists()
    assert bindings_revalidation_required(data) is True
    for path, content in protected.items():
        assert path.read_bytes() == content


def test_authentication_only_logout_preserves_account_metadata_and_runtime_cache(
    tmp_path: Path,
):
    data = tmp_path / "data"
    profile_dir = data / "account-profiles" / ("account-" + "a" * 24) / "browser-profile"
    profile_dir.mkdir(parents=True)
    preserved = {
        data / "account-profile.json": b'{"profile_status":"verified"}',
        data / "account-avatar" / "profile.png": b"avatar",
        data / "discovered_friends.json": b'{"candidates":[]}',
        data / "ignored-friend-bindings.json": b'{"target_ids":[]}',
        data / "health.json": b'{"status":"success"}',
        data / "state.json": b'{"daily":{}}',
    }
    for path, content in preserved.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    class Cdp:
        def __init__(self):
            self.calls = []

        def send(self, method, payload):
            self.calls.append((method, payload))

    class Context:
        def __init__(self):
            self.pages = [object()]
            self.cdp = Cdp()
            self.cleared = []

        def cookies(self):
            return [
                {"name": "session", "domain": ".douyin.com", "path": "/"},
                {"name": "unrelated", "domain": ".example.com", "path": "/"},
            ]

        def clear_cookies(self, **kwargs):
            self.cleared.append(kwargs)

        def new_cdp_session(self, _page):
            return self.cdp

    context = Context()

    clear_managed_authentication(
        profile_dir,
        root=tmp_path,
        data_root=data,
        context_factory=lambda _profile_dir: context,
    )

    assert context.cleared == [
        {"name": "session", "domain": ".douyin.com", "path": "/"}
    ]
    assert bindings_revalidation_required(data) is True
    for path, content in preserved.items():
        assert path.read_bytes() == content


def test_binding_guard_clears_only_after_verified_scope_resolves_every_target(
    tmp_path: Path,
):
    data = tmp_path / "data"
    mark_bindings_for_revalidation(data)

    unresolved = complete_binding_revalidation(
        data,
        bindings_proven=False,
    )
    resolved = complete_binding_revalidation(
        data,
        bindings_proven=True,
    )

    assert unresolved is False
    assert resolved is True
    assert bindings_revalidation_required(data) is False


def test_partial_managed_logout_failure_still_blocks_scheduled_sending(
    tmp_path: Path,
):
    class FailingCdp:
        def send(self, _method, _payload):
            raise RuntimeError("isolated protocol failure")

    class Context:
        pages = [object()]

        def cookies(self):
            return []

        def new_cdp_session(self, _page):
            return FailingCdp()

    try:
        logout_managed_account(
            tmp_path / "data" / "browser-profile",
            root=tmp_path,
            data_root=tmp_path / "data",
            context_factory=lambda _profile: Context(),
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("the isolated protocol failure must surface")

    assert bindings_revalidation_required(tmp_path / "data") is True
