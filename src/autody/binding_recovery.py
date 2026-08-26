from __future__ import annotations

from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from datetime import datetime

from autody.account_profile import evaluate_account_scope
from autody.chat import conversation_candidate_id
from autody.friend_discovery import is_discovery_stale


AUTHORITATIVE_BINDING_IDENTITY_SOURCES = frozenset(
    {"participant_sec_user_id", "row_attribute"}
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


@dataclass(frozen=True)
class StableBindingResolution:
    """One fail-closed answer for a target's current conversation binding."""

    status: str
    candidate_id: str | None = None
    conversation_id: str | None = None
    proven: bool = False
    account_comparison: str | None = None

    @property
    def valid(self) -> bool:
        return self.status == "valid"


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


def _target_refresh_status(target, discovered, now: datetime | None) -> str | None:
    refresh = getattr(discovered, "target_refresh", {}) or {}
    target_id = getattr(target, "stable_id", None)
    if not target_id or target_id not in refresh.get("requested_target_ids", []):
        return None
    if refresh.get("account_scope") != getattr(discovered, "account_scope", None):
        return "scan_failed"
    completed_at = refresh.get("completed_at")
    if now is not None and is_discovery_stale(completed_at, now):
        return "scan_stale"
    if target_id in refresh.get("unresolved_target_ids", []):
        return "binding_missing"
    if target_id in refresh.get("found_target_ids", []):
        return "found"
    if target_id in refresh.get("missing_target_ids", []):
        return "stale_locator"
    if refresh.get("partial") is True:
        return "scan_incomplete"
    return "scan_incomplete"


def resolve_stable_binding(
    target,
    discovered,
    profile,
    *,
    revalidation_required: bool = False,
    now: datetime | None = None,
) -> StableBindingResolution:
    """Resolve one target to its exact current conversation without guessing."""
    if not getattr(target, "stable_id", None) or not getattr(target, "candidate_id", None):
        return StableBindingResolution("binding_missing")
    if discovered is None:
        return StableBindingResolution("scan_unavailable")
    target_refresh_status = _target_refresh_status(target, discovered, now)
    if target_refresh_status != "found":
        last_result = getattr(discovered, "last_result", {}) or {}
        if target_refresh_status is not None:
            return StableBindingResolution(target_refresh_status)
        if last_result.get("status") in _FAILED_DISCOVERY_STATUSES:
            return StableBindingResolution("scan_failed")
        if not _complete_authoritative_scan(discovered):
            return StableBindingResolution("scan_incomplete")
        if now is not None and is_discovery_stale(
            getattr(discovered, "scanned_at", None), now
        ):
            return StableBindingResolution("scan_stale")

    evaluation = evaluate_account_scope(
        profile,
        binding_scope=getattr(discovered, "account_scope", None),
    )
    if evaluation.reason_code == "account_scope_mismatch":
        return StableBindingResolution(
            "account_mismatch", account_comparison=evaluation.account_comparison
        )
    if evaluation.reason_code == "login_required" or evaluation.compatible is None:
        return StableBindingResolution(
            "account_unverified", account_comparison=evaluation.account_comparison
        )

    identity_key = getattr(target, "binding_identity_key", None)
    identity_source = getattr(target, "binding_identity_source", None)
    binding_scope = getattr(target, "binding_account_scope", None)
    if (
        not identity_key
        or identity_source not in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
    ):
        return StableBindingResolution("binding_missing")

    if binding_scope != discovered.account_scope:
        binding_evaluation = evaluate_account_scope(
            profile,
            binding_scope=binding_scope,
        )
        return StableBindingResolution(
            "account_mismatch",
            account_comparison=binding_evaluation.account_comparison,
        )

    matches = [
        candidate
        for candidate in discovered.candidates
        if candidate.presence_status == "current"
        and candidate.identity_source in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
        and candidate.identity_key == identity_key
    ]
    if not matches:
        return StableBindingResolution("stale_locator")
    if len(matches) > 1:
        return StableBindingResolution("identity_ambiguous")
    candidate = matches[0]
    conversation_id = getattr(candidate, "conversation_id", None)
    if not conversation_id:
        # Compatibility for caches produced before locator and durable identity
        # were modeled separately.  Current real discovery always persists the
        # explicit conversation locator.
        conversation_id = conversation_candidate_id(candidate.identity_key)
    if not conversation_id:
        return StableBindingResolution("stale_locator")
    if revalidation_required:
        return StableBindingResolution(
            "revalidation_required",
            candidate_id=candidate.candidate_id,
            conversation_id=conversation_id,
            proven=True,
        )
    return StableBindingResolution(
        "valid",
        candidate_id=candidate.candidate_id,
        conversation_id=conversation_id,
        proven=True,
    )


def all_stable_bindings_proven(targets, discovered, profile) -> bool:
    """Whether one complete current scan proves every configured binding."""
    return all(
        resolve_stable_binding(target, discovered, profile).proven
        for target in targets
    )


def reassociate_stable_binding(
    config,
    target,
    candidate_id: str,
    discovered,
    profile,
    *,
    now: datetime | None = None,
) -> StableBindingResolution:
    """Explicitly establish durable proof from one complete current discovery."""
    if discovered is None:
        return StableBindingResolution("scan_unavailable")
    candidates = [
        candidate
        for candidate in getattr(discovered, "candidates", [])
        if getattr(candidate, "candidate_id", None) == candidate_id
        and getattr(candidate, "presence_status", None) == "current"
    ]
    if not candidates:
        return StableBindingResolution("candidate_missing")
    if len(candidates) != 1:
        return StableBindingResolution("candidate_ambiguous")
    candidate = candidates[0]
    if (
        not getattr(candidate, "identity_key", None)
        or getattr(candidate, "identity_source", None)
        not in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
    ):
        return StableBindingResolution("binding_missing")

    stable_id = getattr(target, "stable_id", None)
    if not stable_id:
        return StableBindingResolution("binding_missing")
    occupied = any(
        getattr(other, "stable_id", None) != stable_id
        and getattr(other, "candidate_id", None) == candidate_id
        for other in getattr(config, "targets", [])
    )
    if occupied:
        return StableBindingResolution("candidate_occupied")

    proposed = copy(target)
    proposed.candidate_id = candidate.candidate_id
    proposed.name = candidate.display_name
    proposed.binding_identity_key = candidate.identity_key
    proposed.binding_identity_source = candidate.identity_source
    proposed.binding_account_scope = getattr(discovered, "account_scope", None)
    resolution = resolve_stable_binding(
        proposed,
        discovered,
        profile,
        now=now,
    )
    if not resolution.valid:
        return resolution

    target.candidate_id = proposed.candidate_id
    target.name = proposed.name
    target.binding_identity_key = proposed.binding_identity_key
    target.binding_identity_source = proposed.binding_identity_source
    target.binding_account_scope = proposed.binding_account_scope
    return resolution


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
        if (
            not getattr(target, "binding_identity_key", None)
            or getattr(target, "binding_identity_source", None)
            not in AUTHORITATIVE_BINDING_IDENTITY_SOURCES
            or getattr(target, "binding_account_scope", None) != account_scope
        ):
            # A cache association is not continuity proof.  Missing/weak
            # legacy bindings must be explicitly reassociated; only an
            # already-authoritative same-account binding may be upgraded to
            # a newer authoritative identity through its proven candidate.
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
    if not _complete_authoritative_scan(discovered) and not getattr(
        discovered, "target_refresh", {}
    ):
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
        if not _complete_authoritative_scan(discovered):
            if _target_refresh_status(target, discovered, datetime.now()) != "found":
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
