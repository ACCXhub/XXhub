from types import SimpleNamespace

from autody.binding_recovery import (
    binding_issue_requires_reassociation,
    reconcile_stable_bindings,
    remember_binding_evidence,
)


def candidate(candidate_id, identity_key, identity_source="row_attribute", presence_status="current"):
    return SimpleNamespace(
        candidate_id=candidate_id,
        identity_key=identity_key,
        identity_source=identity_source,
        presence_status=presence_status,
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


def test_binding_issue_warning_depends_on_current_health_not_historical_failure():
    historical_failure = {
        "safe_retry_available": False,
        "suggested_action": "reassociate",
    }
    healthy_friend = {
        "status": "failed",
        "failure": historical_failure,
        "current_health": {"reason_code": "binding_valid"},
    }
    stale_friend = {
        "status": "failed",
        "failure": historical_failure,
        "current_health": {"reason_code": "binding_stale"},
    }

    assert binding_issue_requires_reassociation([healthy_friend]) is False
    assert binding_issue_requires_reassociation([healthy_friend, stale_friend]) is True
