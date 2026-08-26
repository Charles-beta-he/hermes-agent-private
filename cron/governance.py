"""Pure, read-only identity and governance projections for stored cron jobs.

This module deliberately does not import the scheduler, agent, gateway, or any
provider client.  Its recommendations are audit evidence only; nothing here
mutates a job or grants authority to change cron lifecycle state.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


class CronIdentityError(ValueError):
    """Raised when a job cannot be assigned a trustworthy behavior identity."""


_ORIGIN_TARGET_FIELDS = ("platform", "chat_id", "thread_id", "user_id")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CronIdentityError("cron job contains a non-canonical value") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text(value: Any, *, lower: bool = False) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized.lower() if lower else normalized


def _canonical_schedule(schedule: Any) -> Any:
    if isinstance(schedule, dict):
        return {
            str(key): _canonical_schedule(value)
            for key, value in schedule.items()
            if str(key) != "display"
        }
    if isinstance(schedule, list):
        return [_canonical_schedule(item) for item in schedule]
    if isinstance(schedule, str):
        return " ".join(schedule.split())
    return schedule


def _script_bytes(script: str) -> bytes:
    path = Path(script).expanduser()
    if not path.is_absolute():
        # Match the scheduler's stored relative-script convention without
        # importing scheduler/runtime modules into this audit-only engine.
        try:
            from hermes_constants import get_hermes_home

            path = get_hermes_home() / "scripts" / path
        except Exception as exc:
            raise CronIdentityError("script content is unreadable") from exc
    try:
        return path.read_bytes()
    except (OSError, ValueError) as exc:
        raise CronIdentityError("script content is unreadable") from exc


def _content_identity(job: dict) -> dict:
    no_agent = bool(job.get("no_agent"))
    script = _text(job.get("script"))
    prompt = job.get("prompt")
    prompt_text = "" if prompt is None else str(prompt)

    identity: dict[str, str] = {}
    if script:
        identity["script_sha256"] = hashlib.sha256(_script_bytes(script)).hexdigest()
    if not no_agent:
        identity["prompt_sha256"] = hashlib.sha256(
            prompt_text.encode("utf-8")
        ).hexdigest()

    if not identity:
        # An agent job always receives a prompt digest, including the digest of
        # an empty prompt.  Reaching this branch means a no-agent job has no
        # readable script and therefore no executable behavior to identify.
        raise CronIdentityError("no-agent job has no readable script identity")
    return identity


def _target_projection(job: dict) -> dict:
    origin = job.get("origin")
    if isinstance(origin, dict):
        origin_target: Any = {
            key: origin.get(key)
            for key in _ORIGIN_TARGET_FIELDS
            if origin.get(key) is not None
        }
    else:
        origin_target = origin
    return {"deliver": job.get("deliver"), "origin": origin_target}


def _ordered_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _skills(job: dict) -> list[str]:
    skills = _ordered_strings(job.get("skills"))
    if skills:
        return skills
    legacy = _text(job.get("skill"))
    return [legacy] if legacy else []


def _context_dependencies(job: dict) -> list[str]:
    value = job.get("context_from")
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    return _ordered_strings(value)


def _toolsets(job: dict) -> list[str]:
    # Toolsets are an allowlist, unlike skills whose order controls prompt
    # composition.  Canonicalize this set so storage order is not behavior.
    return sorted(set(_ordered_strings(job.get("enabled_toolsets"))))


def _mode(job: dict) -> str:
    return "no_agent" if bool(job.get("no_agent")) else "agent"


def cron_identity(job: dict) -> str:
    """Return a stable digest of all behavior-affecting cron job fields.

    Display names, enablement/lifecycle state, and mutable run timestamps are
    intentionally absent.  Prompt text, script bytes, routing details, and
    base URLs are represented only inside the digest and never returned.
    """
    if not isinstance(job, dict):
        raise CronIdentityError("cron job must be a mapping")

    base_url = _text(job.get("base_url"))
    projection = {
        "schema": "cron-identity-v1",
        "schedule": _canonical_schedule(job.get("schedule")),
        "content": _content_identity(job),
        "mode": _mode(job),
        "target": _target_projection(job),
        "skills": _skills(job),
        "workdir": _text(job.get("workdir")),
        "inference": {
            "provider": _text(job.get("provider"), lower=True),
            "provider_snapshot": _text(job.get("provider_snapshot"), lower=True),
            "model": _text(job.get("model")),
            "model_snapshot": _text(job.get("model_snapshot")),
            "base_url": base_url.rstrip("/") if base_url else None,
        },
        "context_dependencies": _context_dependencies(job),
        "enabled_toolsets": _toolsets(job),
        # Missing is deliberately distinct from explicit false: missing uses
        # the global mirror-delivery setting at execution time.
        "attach_to_session": (
            job.get("attach_to_session")
            if isinstance(job.get("attach_to_session"), bool)
            else None
        ),
    }
    return f"cron-v1:{_digest(projection)}"


def _safe_evidence_ref(value: Any) -> str | None:
    ref = _text(value)
    if not ref:
        return None
    return f"evidence:sha256:{hashlib.sha256(ref.encode('utf-8')).hexdigest()}"


def classify_owner(job: dict) -> dict:
    """Conservatively classify explicit owner evidence without inference.

    Exactly one distinct, non-empty principal is required for attribution.
    Principal and evidence values are never copied to audit output.
    """
    evidence = job.get("owner_evidence") if isinstance(job, dict) else None
    if not isinstance(evidence, list):
        evidence = []

    principals: set[str] = set()
    refs: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        principal = _text(item.get("principal"))
        if principal:
            principals.add(principal)
        safe_ref = _safe_evidence_ref(item.get("ref"))
        if safe_ref:
            refs.add(safe_ref)

    return {
        "owner_status": "attributed" if len(principals) == 1 else "unattributed",
        "owner_evidence_refs": sorted(refs),
    }


def _schedule_class(job: dict) -> str:
    schedule = job.get("schedule")
    if isinstance(schedule, dict):
        kind = _text(schedule.get("kind"), lower=True)
        return kind or "unknown"
    return "unknown"


def _target_resource(job: dict) -> str:
    return f"target:sha256:{_digest(_target_projection(job))}"


def _side_effect_class(job: dict) -> str:
    if bool(job.get("no_agent")):
        return "script_delivery"
    if _text(job.get("script")):
        return "agent_script_delivery"
    return "agent_delivery"


def audit_jobs(jobs: list[dict]) -> dict:
    """Return deterministic, read-only governance records for *jobs*.

    Exact identity groups are stable across input ordering.  The lexically
    first job in a duplicate group is retained as the deterministic reference;
    later records receive a read-only ``deduplicate`` recommendation.
    """
    if not isinstance(jobs, list):
        raise TypeError("jobs must be a list")

    projected: list[tuple[str, dict, str]] = []
    seen_ids: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise TypeError("each cron job must be a mapping")
        job_id = _text(job.get("id"))
        if not job_id:
            raise CronIdentityError("cron job id is required for audit")
        if job_id in seen_ids:
            raise CronIdentityError("cron job ids must be unique for audit")
        seen_ids.add(job_id)
        projected.append((job_id, job, cron_identity(job)))

    projected.sort(key=lambda item: item[0])
    identity_members: dict[str, list[str]] = defaultdict(list)
    for job_id, _job, identity in projected:
        identity_members[identity].append(job_id)

    group_names = {
        identity: f"duplicate:sha256:{hashlib.sha256(identity.encode('ascii')).hexdigest()}"
        for identity, members in identity_members.items()
        if len(members) > 1
    }

    records = []
    for job_id, job, identity in projected:
        owner = classify_owner(job)
        members = identity_members[identity]
        duplicate_group = group_names.get(identity)
        findings = []
        if owner["owner_status"] == "unattributed":
            findings.append("owner_unattributed")
        if duplicate_group:
            findings.append("exact_behavior_duplicate")

        if duplicate_group and job_id != members[0]:
            recommended_action = "deduplicate"
        elif owner["owner_status"] == "unattributed":
            recommended_action = "pause_unknown"
        else:
            recommended_action = "keep"

        records.append(
            {
                "job_id": job_id,
                "enabled": bool(job.get("enabled")),
                "state": _text(job.get("state")) or "unknown",
                "cron_identity": identity,
                "owner_status": owner["owner_status"],
                "owner_evidence_refs": owner["owner_evidence_refs"],
                "mode": _mode(job),
                "schedule_class": _schedule_class(job),
                "target_resource": _target_resource(job),
                "side_effect_class": _side_effect_class(job),
                "duplicate_group": duplicate_group,
                "findings": findings,
                "recommended_action": recommended_action,
            }
        )

    duplicate_groups = [
        {
            "duplicate_group": group_names[identity],
            "cron_identity": identity,
            "job_ids": members,
        }
        for identity, members in sorted(identity_members.items())
        if len(members) > 1
    ]
    return {"records": records, "duplicate_groups": duplicate_groups}
