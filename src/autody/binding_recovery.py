from __future__ import annotations

from collections import defaultdict


AUTHORITATIVE_BINDING_IDENTITY_SOURCES = frozenset({"row_attribute"})
_BINDING_REASSOCIATION_REASONS = frozenset(
    {
        "binding_missing",
        "binding_stale",
        "binding_revalidation_required",
        "identity_ambiguous",
    }
)
_FAILED_DISCOVERY_STATUSES = frozenset(
    {
        "failed",
        "lock_busy",
        "login_unavailable",
        "page_load_failed",
        "partial_timeout",
        "cancelled",
    }
)


def _authoritative_candidates(discovered):
    if discovered is None or not getattr(discovered, "account_scope", None):
        return []
    return [
        candidate
        for candidate in getattr(discovered, "candidates", [])
        if getattr(candidate, "presence_status", None) == "current"
        and getattr(candidate, "identity_key", None)
        and getattr(candidate, "identity_source", None)
        in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
    ]


def _complete_authoritative_scan(discovered) -> bool:
    if discovered is None or not getattr(discovered, "account_scope", None):
        return False
    last_result = getattr(discovered, "last_result", {}) or {}
    if last_result.get("partial") is True:
        return False
    if last_result.get("completed_bottom_reached") is not True:
        return False
    return last_result.get("status") not in _FAILED_DISCOVERY_STATUSES


def remember_binding_evidence(config, discovered) -> bool:
    """Persist strong evidence for bindings that are already proven current."""
    account_scope = getattr(discovered, "account_scope", None)
    if not account_scope:
        return False
    by_candidate_id: dict[str, list] = defaultdict(list)
    for candidate in _authoritative_candidates(discovered):
        by_candidate_id[candidate.candidate_id].append(candidate)

    changed = False
    for target in getattr(config, "targets", []):
        candidate_id = getattr(target, "candidate_id", None)
        if not getattr(target, "stable_id", None) or not candidate_id:
            continue
        matches = by_candidate_id.get(candidate_id, [])
        if len(matches) != 1:
            continue
        candidate = matches[0]
        values = {
            "binding_identity_key": candidate.identity_key,
            "binding_identity_source": candidate.identity_source,
            "binding_account_scope": account_scope,
        }
        for field, value in values.items():
            if getattr(target, field, None) != value:
                setattr(target, field, value)
                changed = True
    return changed


def reconcile_stable_bindings(config, discovered) -> set[str]:
    """Recover stale candidate IDs only from unique same-account row identities."""
    if not _complete_authoritative_scan(discovered):
        return set()
    account_scope = discovered.account_scope
    by_identity: dict[str, list] = defaultdict(list)
    for candidate in _authoritative_candidates(discovered):
        by_identity[candidate.identity_key].append(candidate)

    occupied = {
        getattr(target, "candidate_id", None): target
        for target in getattr(config, "targets", [])
        if getattr(target, "candidate_id", None)
    }
    recovered: set[str] = set()
    for target in getattr(config, "targets", []):
        stable_id = getattr(target, "stable_id", None)
        identity_key = getattr(target, "binding_identity_key", None)
        identity_source = getattr(target, "binding_identity_source", None)
        binding_scope = getattr(target, "binding_account_scope", None)
        if (
            not stable_id
            or not identity_key
            or identity_source not in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
            or binding_scope != account_scope
        ):
            continue
        matches = by_identity.get(identity_key, [])
        if len(matches) != 1:
            continue
        candidate = matches[0]
        if candidate.candidate_id == getattr(target, "candidate_id", None):
            continue
        other = occupied.get(candidate.candidate_id)
        if other is not None and other is not target:
            continue
        old_candidate_id = getattr(target, "candidate_id", None)
        if old_candidate_id and occupied.get(old_candidate_id) is target:
            occupied.pop(old_candidate_id, None)
        target.candidate_id = candidate.candidate_id
        occupied[candidate.candidate_id] = target
        recovered.add(stable_id)
    return recovered


def binding_issue_requires_reassociation(friends: list[dict]) -> bool:
    """Use current authoritative health, never historical failure wording."""
    return any(
        friend.get("status") == "failed"
        and (friend.get("current_health") or {}).get("reason_code")
        in _BINDING_REASSOCIATION_REASONS
        for friend in friends
    )
