from datetime import datetime
from types import SimpleNamespace

from autody.binding_recovery import (
    reconcile_stable_bindings,
    remember_binding_evidence,
    resolve_stable_binding,
)


def candidate(
    candidate_id,
    identity_key,
    identity_source="row_attribute",
    presence_status="current",
    conversation_id=None,
):
    return SimpleNamespace(
        candidate_id=candidate_id,
        identity_key=identity_key,
        identity_source=identity_source,
        presence_status=presence_status,
        conversation_id=conversation_id,
    )


def discovered(account_scope, candidates, *, complete=True):
    return SimpleNamespace(
        account_scope=account_scope,
        candidates=candidates,
        last_result={
            "status": "completed_bottom_reached" if complete else "partial_timeout",
            "completed_bottom_reached": complete,
            "partial": not complete,
        },
        scanned_at="2026-08-25T08:00:00",
        target_refresh={},
    )


def target(candidate_id="candidate-old", *, key=None, source=None, scope=None, stable_id="target-a"):
    return SimpleNamespace(
        stable_id=stable_id,
        candidate_id=candidate_id,
        binding_identity_key=key,
        binding_identity_source=source,
        binding_account_scope=scope,
    )


def config(*targets):
    return SimpleNamespace(targets=list(targets))


def profile(account_scope="account-a"):
    return SimpleNamespace(
        account_profile_id=account_scope,
        account_id_digest="d" * 64,
    )


def test_remember_binding_evidence_uses_only_authoritative_row_identity():
    authoritative = target("candidate-a")
    avatar_only = target("candidate-b", stable_id="target-b")
    cfg = config(authoritative, avatar_only)
    result = discovered(
        "account-a",
        [
            candidate("candidate-a", "row:key-a"),
            candidate("candidate-b", "avatar:key-b", "avatar_source"),
        ],
    )

    assert remember_binding_evidence(cfg, result) is True
    assert authoritative.binding_identity_key == "row:key-a"
    assert authoritative.binding_identity_source == "row_attribute"
    assert authoritative.binding_account_scope == "account-a"
    assert avatar_only.binding_identity_key is None


def test_reconcile_updates_only_unique_same_account_authoritative_identity():
    item = target(
        "candidate-old",
        key="row:friend-a",
        source="row_attribute",
        scope="account-a",
    )
    cfg = config(item)

    recovered = reconcile_stable_bindings(
        cfg,
        discovered("account-a", [candidate("candidate-new", "row:friend-a")]),
    )

    assert recovered == {"target-a"}
    assert item.candidate_id == "candidate-new"


def test_reconcile_refuses_nickname_avatar_ambiguity_and_account_switches():
    avatar_based = target(
        "candidate-old",
        key="avatar:same-looking",
        source="avatar_source",
        scope="account-a",
    )
    wrong_account = target(
        "candidate-old-2",
        key="row:friend-b",
        source="row_attribute",
        scope="account-a",
        stable_id="target-b",
    )
    ambiguous = target(
        "candidate-old-3",
        key="row:friend-c",
        source="row_attribute",
        scope="account-b",
        stable_id="target-c",
    )
    cfg = config(avatar_based, wrong_account, ambiguous)

    recovered = reconcile_stable_bindings(
        cfg,
        discovered(
            "account-b",
            [
                candidate("candidate-new-a", "avatar:same-looking", "avatar_source"),
                candidate("candidate-new-b", "row:friend-b"),
                candidate("candidate-new-c1", "row:friend-c"),
                candidate("candidate-new-c2", "row:friend-c"),
            ],
        ),
    )

    assert recovered == set()
    assert avatar_based.candidate_id == "candidate-old"
    assert wrong_account.candidate_id == "candidate-old-2"
    assert ambiguous.candidate_id == "candidate-old-3"


def test_reconcile_refuses_partial_scan_and_candidate_already_bound_elsewhere():
    recovering = target(
        "candidate-old",
        key="row:friend-a",
        source="row_attribute",
        scope="account-a",
    )
    occupied = target(
        "candidate-new",
        key="row:friend-z",
        source="row_attribute",
        scope="account-a",
        stable_id="target-z",
    )
    cfg = config(recovering, occupied)

    assert reconcile_stable_bindings(
        cfg,
        discovered("account-a", [candidate("candidate-new", "row:friend-a")], complete=False),
    ) == set()
    assert reconcile_stable_bindings(
        cfg,
        discovered("account-a", [candidate("candidate-new", "row:friend-a")]),
    ) == set()
    assert recovering.candidate_id == "candidate-old"


def test_authoritative_recovery_converges_to_one_current_conversation_locator():
    item = target(
        "candidate-stale",
        key="row:friend-a",
        source="row_attribute",
        scope="account-a",
    )
    cfg = config(item)
    result = discovered(
        "account-a", [candidate("candidate-current", "row:friend-a")]
    )

    # The candidate ID is only a discovery-cache association.  The saved row
    # identity must resolve the current locator before the cache key is
    # persisted by recovery, so UI and Runner cannot disagree in that window.
    resolution = resolve_stable_binding(item, result, profile())

    assert resolution.status == "valid"
    assert resolution.candidate_id == "candidate-current"
    assert resolution.conversation_id is not None

    assert reconcile_stable_bindings(cfg, result) == {"target-a"}
    assert item.candidate_id == "candidate-current"


def test_participant_identity_resolves_the_separate_current_locator():
    item = target(
        "candidate-cache-old",
        key="participant:friend-a",
        source="participant_sec_user_id",
        scope="account-a",
    )
    result = discovered(
        "account-a",
        [
            candidate(
                "candidate-cache-current",
                "participant:friend-a",
                "participant_sec_user_id",
                conversation_id="candidate-conversation-current",
            )
        ],
    )

    resolution = resolve_stable_binding(item, result, profile())

    assert resolution.status == "valid"
    assert resolution.candidate_id == "candidate-cache-current"
    assert resolution.conversation_id == "candidate-conversation-current"


def test_targeted_refresh_proves_one_stale_binding_without_claiming_full_scan():
    item = target(
        "candidate-cache-old",
        key="participant:friend-a",
        source="participant_sec_user_id",
        scope="account-a",
    )
    result = discovered(
        "account-a",
        [
            candidate(
                "candidate-cache-current",
                "participant:friend-a",
                "participant_sec_user_id",
                conversation_id="conversation-current",
            )
        ],
        complete=False,
    )
    result.target_refresh = {
        "status": "completed",
        "completed_at": "2026-08-25T08:00:00",
        "account_scope": "account-a",
        "requested_target_ids": ["target-a"],
        "found_target_ids": ["target-a"],
        "missing_target_ids": [],
        "unresolved_target_ids": [],
        "partial": False,
    }

    resolution = resolve_stable_binding(
        item,
        result,
        profile(),
        now=datetime(2026, 8, 25, 8, 0, 5),
    )

    assert resolution.status == "valid"
    assert resolution.candidate_id == "candidate-cache-current"
    assert reconcile_stable_bindings(config(item), result) == {"target-a"}
