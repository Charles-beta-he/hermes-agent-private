"""Canonical additive effect/v2 contracts for HGWK F4 slices 0-3.

These Python types mirror the authored TypeScript contracts. They do not mutate
workflow_execution_event_v1 semantics.

Canonical serialized EffectCommandV1 shape excludes request_digest everywhere.
Public mapping parsers and derive helpers reject ANY present request_digest
(null/valid/malformed/mismatch). The field may exist only as a derived attribute
on the Python dataclass result.

The sole supported serialized command shape is produced by
``effect_command_to_dict`` / ``effect_command_body_v1``. ``dataclasses.asdict`` is
unsupported: nested frozen ``mappingproxy`` values raise ``TypeError`` and must
not be treated as a public serializer.

Admitted strings and object keys are Unicode scalar values only (no unpaired
UTF-16 surrogates). Timestamp calendar domain is year 0001-9999.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping, NoReturn


class EffectContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise EffectContractError(code, message)


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_PORTABLE_CONTROL_RE = re.compile(
    r"[\u0000-\u0008\u000e-\u001b\u007f-\u0084\u0086-\u009f\ud800-\udfff]"
)

# Shared with TypeScript Number.MAX_SAFE_INTEGER / MIN_SAFE_INTEGER
_JS_SAFE_INTEGER_MAX = 9007199254740991
_JS_SAFE_INTEGER_MIN = -9007199254740991


def _utf16_code_units(text: str) -> list[int]:
    """RFC 8785/JCS object key order uses UTF-16 code units, not Unicode code points."""
    units: list[int] = []
    for char in text:
        code = ord(char)
        if code <= 0xFFFF:
            units.append(code)
            continue
        offset = code - 0x10000
        units.append(0xD800 + (offset >> 10))
        units.append(0xDC00 + (offset & 0x3FF))
    return units


def _assert_unicode_scalar_string(text: str, *, label: str = "string") -> str:
    """Reject unpaired UTF-16 surrogate code points before canonicalize/parse/digest.

    Python ``str`` stores Unicode code points. Supplementary-plane scalars such as
    U+10000 are single code points and are accepted. Any code point in
    U+D800..U+DFFF is an unpaired surrogate and is rejected with a controlled
    ``EffectContractError`` (never ``UnicodeEncodeError``).
    """
    for index, char in enumerate(text):
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            _fail(
                "effect_contract_not_canonical",
                f"{label} contains unpaired UTF-16 surrogate U+{code:04X} at index {index}",
            )
    return text


def _normalize_admitted_number(value: object) -> int:
    """Normalize one admitted JS-safe integer (accept integer-valued 1.0 / -0.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("effect_contract_not_canonical", f"unsupported JSON number type: {type(value)!r}")
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("effect_contract_not_canonical", "non-finite JSON number is forbidden")
        if value == 0.0:
            return 0
        as_int = int(value)
        if as_int != value:
            _fail(
                "effect_contract_not_canonical",
                "non-integer JSON number is forbidden in admitted canonical domain",
            )
        value = as_int
    if value < _JS_SAFE_INTEGER_MIN or value > _JS_SAFE_INTEGER_MAX:
        _fail("effect_contract_not_canonical", "integer exceeds JS-safe range")
    if value == 0:
        return 0
    return int(value)


def _admitted_integer_text(value: object) -> str:
    """Admitted-domain number form: JS-safe integers only.

    Fail-closed simplification (shared Python/TS algorithm):
    - reject non-finite numbers
    - reject non-integer JSON numbers (fractions, scientific non-integers, etc.)
    - accept Python/JS integer values and integer-valued floats such as 1.0
    - serialize every admitted number exactly as base-10 integer text
    - serialize -0.0 / 0.0 / 0 exactly as ``0``
    """
    return str(_normalize_admitted_number(value))


def normalize_admitted_json_v1(value: object) -> Any:
    """Recursive public admitted JSON domain (shared with TypeScript).

    Allowed:
    - null, bool, str (Unicode scalar values only; no unpaired surrogates)
    - JS-safe integer numbers (1.0 and -0.0 normalize to 1 / 0)
    - arrays
    - plain mappings with *string* keys only (keys also scalar-checked)

    Rejected at every nesting depth:
    - unpaired UTF-16 surrogates in strings or keys
    - non-integer / non-finite / unsafe numbers
    - non-string keys and key-string collisions after normalization
    - non-JSON types (bytes, set, custom objects, complex, ...)
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _assert_unicode_scalar_string(value, label="string")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _normalize_admitted_number(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(
                    "effect_contract_not_canonical",
                    f"object keys must be strings; got {type(key)!r}",
                )
            key = _assert_unicode_scalar_string(key, label="object key")
            if key in out:
                _fail(
                    "effect_contract_not_canonical",
                    f"duplicate object key after normalization: {key!r}",
                )
            out[key] = normalize_admitted_json_v1(item)
        return out
    if isinstance(value, (list, tuple)):
        return [normalize_admitted_json_v1(item) for item in value]
    _fail("effect_contract_not_canonical", f"unsupported JSON type: {type(value)!r}")


def thaw_admitted_json_v1(value: object) -> Any:
    """Thaw frozen mappings/tuples into plain JSON lists/dicts for equality."""
    if isinstance(value, Mapping):
        return {str(key): thaw_admitted_json_v1(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_admitted_json_v1(item) for item in value]
    if isinstance(value, list):
        return [thaw_admitted_json_v1(item) for item in value]
    return value


def _serialize_admitted_canonical(value: object) -> str:
    """Admitted-domain canonical JSON.

    Algorithm (explicit, shared with TypeScript):
    1. null/true/false/string use standard JSON text (UTF-8, ensure_ascii=False strings).
    2. numbers: admitted JS-safe integers only (see ``_admitted_integer_text``).
    3. arrays: element order preserved; no whitespace.
    4. objects: keys sorted by UTF-16 code units (RFC 8785/JCS), not code points;
       each key emitted via JSON string encoding.
    5. no NaN/Infinity, no non-JSON types.
    """
    normalized = normalize_admitted_json_v1(value)
    if normalized is None:
        return "null"
    if isinstance(normalized, bool):
        return "true" if normalized else "false"
    if isinstance(normalized, int) and not isinstance(normalized, bool):
        return _admitted_integer_text(normalized)
    if isinstance(normalized, str):
        return json.dumps(normalized, ensure_ascii=False)
    if isinstance(normalized, dict):
        items = [(key, _serialize_admitted_canonical(item)) for key, item in normalized.items()]
        items.sort(key=lambda pair: _utf16_code_units(pair[0]))
        inner = ",".join(f"{json.dumps(key, ensure_ascii=False)}:{item}" for key, item in items)
        return "{" + inner + "}"
    if isinstance(normalized, list):
        return "[" + ",".join(_serialize_admitted_canonical(item) for item in normalized) + "]"
    _fail("effect_contract_not_canonical", f"unsupported JSON type: {type(normalized)!r}")


def canonical_json_v1(value: object) -> str:
    """Public admitted-domain canonical JSON text (integer-only numbers, UTF-16 key order)."""
    try:
        return _serialize_admitted_canonical(value)
    except EffectContractError:
        raise
    except UnicodeEncodeError as exc:
        # Belt-and-suspenders: admitted-domain checks reject surrogates earlier.
        _fail(
            "effect_contract_not_canonical",
            f"value contains non-UTF-8 Unicode scalar violation: {exc}",
        )
    except (TypeError, ValueError) as exc:
        _fail("effect_contract_not_canonical", f"value is not canonical JSON: {exc}")

def _compact_json(value: object) -> bytes:
    try:
        return canonical_json_v1(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        _fail(
            "effect_contract_not_canonical",
            f"value contains non-UTF-8 Unicode scalar violation: {exc}",
        )


def canonical_digest_v1(value: object) -> str:
    return f"sha256:{hashlib.sha256(_compact_json(value)).hexdigest()}"


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("effect_contract_invalid", f"{label} must be an object")
    try:
        normalized = normalize_admitted_json_v1(value)
    except EffectContractError as exc:
        # Preserve not_canonical for domain rejects; map only unexpected cases.
        if getattr(exc, "code", "") == "effect_contract_not_canonical":
            raise
        _fail("effect_contract_invalid", f"{label} is not admitted JSON: {exc}")
    if not isinstance(normalized, dict):
        _fail("effect_contract_invalid", f"{label} must be an object")
    return normalized


def _require_text(value: object, *, label: str, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("effect_contract_invalid", f"{label} must be a non-empty string")
    if len(value) > max_len:
        _fail("effect_contract_invalid", f"{label} exceeds max length {max_len}")
    if _PORTABLE_CONTROL_RE.search(value):
        _fail("effect_contract_invalid", f"{label} contains forbidden control chars")
    return value


def _require_identifier(value: object, *, label: str) -> str:
    text = _require_text(value, label=label, max_len=128)
    if not _IDENTIFIER_RE.fullmatch(text):
        _fail(
            "effect_contract_invalid",
            f"{label} must match identifier pattern ^[a-z][a-z0-9._-]{{0,127}}$",
        )
    return text


def _require_digest(value: object, *, label: str) -> str:
    text = _require_text(value, label=label, max_len=80)
    body = text[len("sha256:") :] if text.startswith("sha256:") else ""
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        _fail(
            "effect_contract_invalid",
            f"{label} must be a lowercase sha256:<64 hex> digest",
        )
    return text


# Exact timestamp grammar shared with TS/schema: seconds required, optional fraction, Z or ±HH:MM.
# Year domain is 0001-9999 (year 0000 excluded in pattern when expressible).
# JSON Schema enforces the pattern/format grammar; calendar validity is enforced here and
# in the TypeScript public parser (see generated schema $comment on timestamp fields).
_ISO_OFFSET_RE = re.compile(
    r"^(?:000[1-9]|00[1-9]\d|0[1-9]\d{2}|[1-9]\d{3})-\d{2}-\d{2}"
    r"T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)


def _require_int(
    value: object,
    *,
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    # Normalize integer-valued floats (fence: 1.0) consistently with TS/schema.
    if isinstance(value, bool):
        _fail("effect_contract_invalid", f"{label} must be an integer")
    if isinstance(value, float):
        try:
            value = _normalize_admitted_number(value)
        except EffectContractError:
            _fail("effect_contract_invalid", f"{label} must be an integer")
    if not isinstance(value, int) or isinstance(value, bool):
        _fail("effect_contract_invalid", f"{label} must be an integer")
    if value < _JS_SAFE_INTEGER_MIN or value > _JS_SAFE_INTEGER_MAX:
        _fail(
            "effect_contract_invalid",
            f"{label} exceeds JS-safe integer range",
        )
    if minimum is not None and value < minimum:
        _fail("effect_contract_invalid", f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        _fail("effect_contract_invalid", f"{label} must be <= {maximum}")
    return value


def _require_event_id(value: object, *, prefix: str, label: str) -> str:
    text = _require_text(value, label=label, max_len=len(prefix) + 64)
    body = text[len(prefix) :] if text.startswith(prefix) else ""
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        _fail("effect_contract_invalid", f"{label} must match {prefix}<64 hex>")
    return text


def _require_iso_datetime(value: object, *, label: str) -> str:
    """Calendar-valid normalized UTC/offset timestamp in year 0001-9999.

    Schema/pattern checks the grammar (seconds required, Z or ±HH:MM) and excludes
    year 0000 when expressible. This parser additionally rejects invalid calendar
    dates (e.g. 2026-02-30) via datetime construction and component round-trip.
    """
    text = _require_text(value, label=label, max_len=64)
    if not _ISO_OFFSET_RE.fullmatch(text):
        _fail(
            "effect_contract_invalid",
            f"{label} must be an ISO-8601 datetime with timezone offset",
        )
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})",
        text,
    )
    if match is None:
        _fail("effect_contract_invalid", f"{label} is not a valid datetime")
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    if year < 1 or year > 9999:
        _fail(
            "effect_contract_invalid",
            f"{label} year must be in 0001-9999 (year 0000 rejected)",
        )
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _fail("effect_contract_invalid", f"{label} is not a valid calendar datetime")
    # Distinguish calendar validity from pure regex grammar acceptance.
    if (
        parsed.year != year
        or parsed.month != month
        or parsed.day != day
        or parsed.hour != hour
        or parsed.minute != minute
        or parsed.second != second
    ):
        _fail("effect_contract_invalid", f"{label} is not a valid calendar datetime")
    return text

def _freeze_value(value: Any) -> Any:
    """Recursively freeze mappings and arbitrarily nested lists/tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(v) for v in value)
    return value


def freeze_admitted_json_v1(value: object) -> Any:
    """Public recursive freeze for admitted JSON (MappingProxyType / tuple).

    Descriptor/admission validation runs first via normalize_admitted_json_v1 so
    unpaired surrogates and non-JSON values never freeze into a public result.
    """
    return _freeze_value(normalize_admitted_json_v1(value))


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_value(dict(value))
    if not isinstance(frozen, Mapping):
        _fail("effect_contract_invalid", "freeze_mapping requires a mapping")
    return frozen  # type: ignore[return-value]

def _require_exact_keys(
    data: Mapping[str, Any],
    required: set[str],
    *,
    label: str,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    allowed = set(required) | optional
    extra = sorted(set(data) - allowed)
    if extra:
        _fail("effect_contract_invalid", f"{label} has unknown keys: {extra}")
    missing = sorted(set(required) - set(data))
    if missing:
        _fail("effect_contract_invalid", f"{label} missing keys: {missing}")



@dataclass(frozen=True)
class EffectCommandV1:
    schema_version: str
    command_id: str
    adapter_id: str
    operation: str
    target: Mapping[str, str]
    payload_digest: str
    idempotency_key: str
    issuer_id: str
    policy_revision: str
    config_revision: str
    side_effect_tier: str
    compensation_ref: str
    reconcile_ref: str
    request_digest: str


@dataclass(frozen=True)
class EffectObservationV1:
    schema_version: str
    effect_id: str
    observation_id: str
    classification: str
    fence: int
    observed_at: str
    adapter_result_digest: str | None
    readback: Mapping[str, Any]


@dataclass(frozen=True)
class EffectReceiptV1:
    schema_version: str
    effect_id: str
    command_digest: str
    adapter_id: str
    operation: str
    target: Mapping[str, str]
    payload_digest: str
    authority_binding_digest: str
    idempotency_key: str
    fence: int
    preflight_readback: Mapping[str, Any]
    post_readback: Mapping[str, Any]
    result_classification: str
    compensation_ref: str
    reconcile_ref: str
    receipt_digest: str


@dataclass(frozen=True)
class WorkflowEffectLedgerEventV1:
    schema_version: str
    effect_id: str
    sequence: int
    previous_event_id: str | None
    idempotency_key: str
    occurred_at: str
    event_type: str
    payload: Mapping[str, Any]
    event_id: str


@dataclass(frozen=True)
class WorkflowExecutionEventV2:
    schema_version: str
    run_id: str
    sequence: int
    previous_event_id: str | None
    idempotency_key: str
    occurred_at: str
    event_type: str
    payload: Mapping[str, Any]
    event_id: str


_EFFECT_COMMAND_KEYS = (
    "schema_version",
    "command_id",
    "adapter_id",
    "operation",
    "target",
    "payload_digest",
    "idempotency_key",
    "issuer_id",
    "policy_revision",
    "config_revision",
    "side_effect_tier",
    "compensation_ref",
    "reconcile_ref",
)

_SIDE_EFFECT_TIERS = frozenset(
    {"none", "local_read", "local_write", "external_read", "external_write"}
)
_OBSERVATION_CLASSES = frozenset({"applied", "not_applied", "unknown"})
_RECEIPT_CLASSES = frozenset({"applied", "not_applied"})
_EFFECT_LEDGER_EVENT_TYPES = frozenset(
    {
        "claimed",
        "intent_recorded",
        "observation_recorded",
        "reconciled",
        "receipt_terminal",
        "compensation_intent",
        "compensation_observation",
        "compensation_receipt",
    }
)
_EXECUTION_EVENT_V2_TYPES = frozenset(
    {
        "run_accepted",
        "run_started",
        "run_cancel_requested",
        "budget_reserved",
        "budget_settled",
        "budget_released",
        "leaf_started",
        "leaf_packet",
        "leaf_waiting_reconcile",
        "effect_bound",
        "effect_resolved",
        "run_waiting_reconcile",
        "run_verified",
        "run_completed",
    }
)


def effect_command_body_v1(command: Mapping[str, Any] | EffectCommandV1) -> dict[str, Any]:
    if isinstance(command, EffectCommandV1):
        return {
            "schema_version": command.schema_version,
            "command_id": command.command_id,
            "adapter_id": command.adapter_id,
            "operation": command.operation,
            "target": {
                "target_kind": command.target["target_kind"],
                "target_ref": command.target["target_ref"],
            },
            "payload_digest": command.payload_digest,
            "idempotency_key": command.idempotency_key,
            "issuer_id": command.issuer_id,
            "policy_revision": command.policy_revision,
            "config_revision": command.config_revision,
            "side_effect_tier": command.side_effect_tier,
            "compensation_ref": command.compensation_ref,
            "reconcile_ref": command.reconcile_ref,
        }
    data = _require_mapping(command, label="effect_command")
    if "request_digest" in data:
        _fail(
            "effect_contract_invalid",
            "serialized effect_command must not include request_digest "
            "(derived field only; absence required on public shape)",
        )
    missing = [key for key in _EFFECT_COMMAND_KEYS if key not in data]
    if missing:
        _fail("effect_contract_invalid", f"effect_command missing keys: {missing}")
    extra = sorted(set(data) - set(_EFFECT_COMMAND_KEYS))
    if extra:
        _fail("effect_contract_invalid", f"effect_command has unknown keys: {extra}")
    target = _require_mapping(data["target"], label="target")
    if set(target) != {"target_kind", "target_ref"}:
        _fail("effect_contract_invalid", "target must bind target_kind and target_ref")
    tier = _require_text(data["side_effect_tier"], label="side_effect_tier")
    if tier not in _SIDE_EFFECT_TIERS:
        _fail("effect_contract_invalid", f"unsupported side_effect_tier: {tier}")
    if data["schema_version"] != "effect_command_v1":
        _fail("effect_contract_invalid", "schema_version must be effect_command_v1")
    return {
        "schema_version": "effect_command_v1",
        "command_id": _require_text(data["command_id"], label="command_id"),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "target": {
            "target_kind": _require_text(target["target_kind"], label="target.target_kind"),
            "target_ref": _require_text(target["target_ref"], label="target.target_ref"),
        },
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "issuer_id": _require_text(data["issuer_id"], label="issuer_id"),
        "policy_revision": _require_text(data["policy_revision"], label="policy_revision"),
        "config_revision": _require_text(data["config_revision"], label="config_revision"),
        "side_effect_tier": tier,
        "compensation_ref": _require_text(
            data["compensation_ref"], label="compensation_ref"
        ),
        "reconcile_ref": _require_text(data["reconcile_ref"], label="reconcile_ref"),
    }


def derive_effect_command_request_digest_v1(
    command: Mapping[str, Any] | EffectCommandV1,
) -> str:
    """Derive request digest from canonical public command body only.

    Mapping inputs must omit ``request_digest`` entirely (null/valid/malformed/
    mismatch all reject). Dataclass inputs use body fields only.
    """
    return canonical_digest_v1(effect_command_body_v1(command))


def parse_effect_command_v1(value: object) -> EffectCommandV1:
    # Admitted-domain normalize before every public parse boundary.
    raw = _require_mapping(value, label="effect_command")
    body = effect_command_body_v1(raw)
    derived = canonical_digest_v1(body)
    frozen_body = dict(body)
    frozen_body["target"] = _freeze_mapping(body["target"])
    return EffectCommandV1(**frozen_body, request_digest=derived)


def effect_command_to_dict(command: EffectCommandV1) -> dict[str, Any]:
    """Canonical serialized EffectCommandV1 shape (no request_digest field)."""
    return effect_command_body_v1(command)


def rederive_effect_command_v1(command: EffectCommandV1) -> EffectCommandV1:
    """Public-boundary re-derivation: rebuild and re-hash from body fields only."""
    fresh = parse_effect_command_v1(effect_command_body_v1(command))
    if command.request_digest != fresh.request_digest:
        _fail(
            "effect_contract_invalid",
            "request_digest does not match canonical command body",
        )
    return fresh


def effect_observation_body_v1(
    observation: Mapping[str, Any] | EffectObservationV1,
) -> dict[str, Any]:
    if isinstance(observation, EffectObservationV1):
        return {
            "schema_version": observation.schema_version,
            "effect_id": observation.effect_id,
            "observation_id": observation.observation_id,
            "classification": observation.classification,
            "fence": observation.fence,
            "observed_at": observation.observed_at,
            "adapter_result_digest": observation.adapter_result_digest,
            "readback": thaw_admitted_json_v1(observation.readback),
        }
    data = _require_mapping(observation, label="effect_observation")
    _require_exact_keys(
        data,
        {
            "schema_version",
            "effect_id",
            "observation_id",
            "classification",
            "fence",
            "observed_at",
            "adapter_result_digest",
            "readback",
        },
        label="effect_observation",
    )
    if data.get("schema_version") != "effect_observation_v1":
        _fail("effect_contract_invalid", "schema_version must be effect_observation_v1")
    classification = _require_text(data.get("classification"), label="classification")
    if classification not in _OBSERVATION_CLASSES:
        _fail("effect_contract_invalid", f"unsupported classification: {classification}")
    adapter_result_digest = data.get("adapter_result_digest")
    if adapter_result_digest is not None:
        adapter_result_digest = _require_digest(
            adapter_result_digest, label="adapter_result_digest"
        )
    return {
        "schema_version": "effect_observation_v1",
        "effect_id": _require_text(data.get("effect_id"), label="effect_id"),
        "observation_id": _require_text(data.get("observation_id"), label="observation_id"),
        "classification": classification,
        "fence": _require_int(data.get("fence"), label="fence", minimum=1),
        "observed_at": _require_iso_datetime(data.get("observed_at"), label="observed_at"),
        "adapter_result_digest": adapter_result_digest,
        "readback": _require_mapping(data.get("readback"), label="readback"),
    }


def parse_effect_observation_v1(value: object) -> EffectObservationV1:
    body = effect_observation_body_v1(_require_mapping(value, label="effect_observation"))
    frozen = dict(body)
    # readback already admitted-normalized via _require_mapping; freeze nested shape.
    frozen["readback"] = _freeze_value(normalize_admitted_json_v1(body["readback"]))
    return EffectObservationV1(**frozen)


def effect_receipt_body_v1(receipt: Mapping[str, Any] | EffectReceiptV1) -> dict[str, Any]:
    if isinstance(receipt, EffectReceiptV1):
        return {
            "schema_version": receipt.schema_version,
            "effect_id": receipt.effect_id,
            "command_digest": receipt.command_digest,
            "adapter_id": receipt.adapter_id,
            "operation": receipt.operation,
            "target": {
                "target_kind": receipt.target["target_kind"],
                "target_ref": receipt.target["target_ref"],
            },
            "payload_digest": receipt.payload_digest,
            "authority_binding_digest": receipt.authority_binding_digest,
            "idempotency_key": receipt.idempotency_key,
            "fence": receipt.fence,
            "preflight_readback": thaw_admitted_json_v1(receipt.preflight_readback),
            "post_readback": thaw_admitted_json_v1(receipt.post_readback),
            "result_classification": receipt.result_classification,
            "compensation_ref": receipt.compensation_ref,
            "reconcile_ref": receipt.reconcile_ref,
        }
    data = _require_mapping(receipt, label="effect_receipt")
    allowed = {
            "schema_version",
            "effect_id",
            "command_digest",
            "adapter_id",
            "operation",
            "target",
            "payload_digest",
            "authority_binding_digest",
            "idempotency_key",
            "fence",
            "preflight_readback",
            "post_readback",
            "result_classification",
            "compensation_ref",
            "reconcile_ref",
            "receipt_digest",
        }
    extra = sorted(set(data) - allowed)
    if extra:
        _fail("effect_contract_invalid", f"effect_receipt has unknown keys: {extra}")
    required = allowed - {"receipt_digest"}
    missing = sorted(required - set(data))
    if missing:
        _fail("effect_contract_invalid", f"effect_receipt missing keys: {missing}")
    if data.get("schema_version") != "effect_receipt_v1":
        _fail("effect_contract_invalid", "schema_version must be effect_receipt_v1")
    classification = _require_text(
        data.get("result_classification"), label="result_classification"
    )
    if classification not in _RECEIPT_CLASSES:
        _fail(
            "effect_receipt_classification_invalid",
            "terminal EffectReceiptV1 only allows applied/not_applied",
        )
    target = _require_mapping(data.get("target"), label="target")
    if set(target) != {"target_kind", "target_ref"}:
        _fail("effect_contract_invalid", "target must bind target_kind and target_ref")
    return {
        "schema_version": "effect_receipt_v1",
        "effect_id": _require_text(data.get("effect_id"), label="effect_id"),
        "command_digest": _require_digest(data.get("command_digest"), label="command_digest"),
        "adapter_id": _require_identifier(data.get("adapter_id"), label="adapter_id"),
        "operation": _require_identifier(data.get("operation"), label="operation"),
        "target": {
            "target_kind": _require_text(target["target_kind"], label="target.target_kind"),
            "target_ref": _require_text(target["target_ref"], label="target.target_ref"),
        },
        "payload_digest": _require_digest(data.get("payload_digest"), label="payload_digest"),
        "authority_binding_digest": _require_digest(
            data.get("authority_binding_digest"), label="authority_binding_digest"
        ),
        "idempotency_key": _require_text(
            data.get("idempotency_key"), label="idempotency_key"
        ),
        "fence": _require_int(data.get("fence"), label="fence", minimum=1),
        "preflight_readback": _require_mapping(
            data.get("preflight_readback"), label="preflight_readback"
        ),
        "post_readback": _require_mapping(data.get("post_readback"), label="post_readback"),
        "result_classification": classification,
        "compensation_ref": _require_text(
            data.get("compensation_ref"), label="compensation_ref"
        ),
        "reconcile_ref": _require_text(data.get("reconcile_ref"), label="reconcile_ref"),
    }


def parse_effect_receipt_v1(value: object) -> EffectReceiptV1:
    raw = _require_mapping(value, label="effect_receipt")
    body = effect_receipt_body_v1(raw)
    derived = canonical_digest_v1(body)
    provided = raw.get("receipt_digest")
    if provided is None:
        _fail("effect_contract_invalid", "receipt_digest is required")
    provided_digest = _require_digest(provided, label="receipt_digest")
    if provided_digest != derived:
        _fail(
            "effect_contract_invalid",
            "receipt_digest does not hash the canonical receipt body",
        )
    frozen_body = dict(body)
    frozen_body["target"] = _freeze_mapping(body["target"])
    frozen_body["preflight_readback"] = _freeze_value(
        normalize_admitted_json_v1(body["preflight_readback"])
    )
    frozen_body["post_readback"] = _freeze_value(normalize_admitted_json_v1(body["post_readback"]))
    return EffectReceiptV1(**frozen_body, receipt_digest=derived)


def effect_receipt_to_dict(receipt: EffectReceiptV1) -> dict[str, Any]:
    body = effect_receipt_body_v1(receipt)
    return {**body, "receipt_digest": receipt.receipt_digest}


def effect_observation_to_dict(observation: EffectObservationV1) -> dict[str, Any]:
    return effect_observation_body_v1(observation)


def derive_workflow_effect_ledger_event_id_v1(body: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in body.items() if key != "event_id"}
    return f"wel_{hashlib.sha256(_compact_json(canonical)).hexdigest()}"


def _require_json_object(value: object, *, label: str) -> dict[str, Any]:
    data = _require_mapping(value, label=label)
    # Reject non-JSON-mappable values early by serializing.
    _compact_json(data)
    return data



def _canonical_target_digest_v1(target: Mapping[str, Any]) -> str:
    return canonical_digest_v1(
        {
            "target_kind": target["target_kind"],
            "target_ref": target["target_ref"],
        }
    )


def _require_applied_original_receipt_v1(receipt: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    parsed = effect_receipt_to_dict(parse_effect_receipt_v1(receipt))
    if parsed["result_classification"] != "applied":
        _fail("effect_contract_invalid", f"{label} original receipt must be applied")
    return parsed


def _bind_observation_wrapper_v1(
    *,
    wrapper: Mapping[str, Any],
    observation: Mapping[str, Any],
    label: str,
) -> None:
    if wrapper.get("classification") != observation.get("classification"):
        _fail("effect_contract_invalid", f"{label} classification mismatch")
    if wrapper.get("fence") != observation.get("fence"):
        _fail("effect_contract_invalid", f"{label} fence mismatch")
    if wrapper.get("effect_id") != observation.get("effect_id"):
        _fail("effect_contract_invalid", f"{label} effect_id mismatch")
    if "adapter_result_digest" in wrapper and wrapper.get("adapter_result_digest") != observation.get(
        "adapter_result_digest"
    ):
        _fail("effect_contract_invalid", f"{label} adapter_result_digest mismatch")
    if canonical_digest_v1(wrapper.get("readback")) != canonical_digest_v1(observation.get("readback")):
        _fail("effect_contract_invalid", f"{label} wrapper/body readback mismatch")


def _bind_receipt_wrapper_v1(
    *,
    payload: Mapping[str, Any],
    receipt: Mapping[str, Any],
    label: str,
) -> None:
    if payload.get("receipt_digest") != receipt.get("receipt_digest"):
        _fail("effect_contract_invalid", f"{label} receipt_digest does not match receipt body")
    if "result_classification" in payload and payload.get("result_classification") != receipt.get(
        "result_classification"
    ):
        _fail("effect_contract_invalid", f"{label} result_classification mismatch")
    if payload.get("fence") != receipt.get("fence"):
        _fail("effect_contract_invalid", f"{label} wrapper fence does not match receipt body fence")
    if payload.get("command_digest") != receipt.get("command_digest"):
        _fail("effect_contract_invalid", f"{label} body command_digest mismatch")
    if payload.get("adapter_id") != receipt.get("adapter_id"):
        _fail("effect_contract_invalid", f"{label} adapter_id mismatch")
    if payload.get("operation") != receipt.get("operation"):
        _fail("effect_contract_invalid", f"{label} operation mismatch")
    if payload.get("payload_digest") != receipt.get("payload_digest"):
        _fail("effect_contract_invalid", f"{label} payload_digest mismatch")
    if payload.get("idempotency_key") != receipt.get("idempotency_key"):
        _fail("effect_contract_invalid", f"{label} idempotency_key mismatch")
    if payload.get("authority_binding_digest") != receipt.get("authority_binding_digest"):
        _fail("effect_contract_invalid", f"{label} authority_binding_digest mismatch")
    if payload.get("effect_id") != receipt.get("effect_id"):
        _fail("effect_contract_invalid", f"{label} effect_id mismatch")
    recomputed_target = _canonical_target_digest_v1(receipt["target"])
    if payload.get("target_digest") != recomputed_target:
        # Compensation receipt production boundary (ledger + contract) uses the exact
        # short form without the extra "receipt" word; other labels keep the long form.
        if label == "compensation receipt":
            _fail(
                "effect_contract_invalid",
                "compensation receipt target_digest does not match target body",
            )
        _fail("effect_contract_invalid", f"{label} target_digest does not match receipt target body")


def _validate_claimed_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="claimed.payload")
    _require_exact_keys(
        data,
        {
            "command_digest",
            "target_digest",
            "owner",
            "request_digest",
            "idempotency_key",
            "adapter_id",
            "operation",
            "fence",
            "payload_digest",
            "effect_id",
        },
        label="claimed.payload",
    )
    return {
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "owner": _require_text(data["owner"], label="owner"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "fence": _require_int(data["fence"], label="fence", minimum=1),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "effect_id": _require_text(data["effect_id"], label="effect_id"),
    }


def _validate_intent_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="intent_recorded.payload")
    _require_exact_keys(
        data,
        {
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "authority_binding_digest",
            "preflight_readback",
            "preflight_readback_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
        },
        label="intent_recorded.payload",
    )
    preflight = _require_json_object(data["preflight_readback"], label="preflight_readback")
    preflight_digest = _require_digest(
        data["preflight_readback_digest"], label="preflight_readback_digest"
    )
    if preflight_digest != canonical_digest_v1(preflight):
        _fail("effect_contract_invalid", "preflight_readback_digest mismatch")
    return {
        "fence": _require_int(data["fence"], label="fence", minimum=1),
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "authority_binding_digest": _require_digest(
            data["authority_binding_digest"], label="authority_binding_digest"
        ),
        "preflight_readback": preflight,
        "preflight_readback_digest": preflight_digest,
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "effect_id": _require_text(data["effect_id"], label="effect_id"),
    }


def _validate_observation_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="observation_recorded.payload")
    _require_exact_keys(
        data,
        {
            "fence",
            "classification",
            "adapter_result_digest",
            "readback",
            "readback_digest",
            "observation",
            "command_digest",
            "request_digest",
            "target_digest",
            "authority_binding_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
        },
        label="observation_recorded.payload",
    )
    classification = _require_text(data["classification"], label="classification")
    if classification not in _OBSERVATION_CLASSES:
        _fail("effect_contract_invalid", f"unsupported classification: {classification}")
    adapter_result_digest = data["adapter_result_digest"]
    if adapter_result_digest is not None:
        adapter_result_digest = _require_digest(
            adapter_result_digest, label="adapter_result_digest"
        )
    observation = effect_observation_body_v1(
        _require_mapping(data["observation"], label="observation")
    )
    readback = _require_json_object(data["readback"], label="readback")
    readback_digest = _require_digest(data["readback_digest"], label="readback_digest")
    if readback_digest != canonical_digest_v1(readback):
        _fail("effect_contract_invalid", "readback_digest mismatch")
    fence = _require_int(data["fence"], label="fence", minimum=1)
    effect_id = _require_text(data["effect_id"], label="effect_id")
    wrapper_view = {
        "classification": classification,
        "fence": fence,
        "effect_id": effect_id,
        "adapter_result_digest": adapter_result_digest,
        "readback": readback,
    }
    _bind_observation_wrapper_v1(
        wrapper=wrapper_view,
        observation=observation,
        label="observation",
    )
    return {
        "fence": fence,
        "classification": classification,
        "adapter_result_digest": adapter_result_digest,
        "readback": readback,
        "readback_digest": readback_digest,
        "observation": observation,
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "authority_binding_digest": _require_digest(
            data["authority_binding_digest"], label="authority_binding_digest"
        ),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "effect_id": effect_id,
    }


def _validate_receipt_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="receipt_terminal.payload")
    _require_exact_keys(
        data,
        {
            "fence",
            "result_classification",
            "receipt_digest",
            "command_digest",
            "request_digest",
            "target_digest",
            "authority_binding_digest",
            "observation_digest",
            "receipt",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "observation",
        },
        label="receipt_terminal.payload",
    )
    classification = _require_text(data["result_classification"], label="result_classification")
    if classification not in _RECEIPT_CLASSES:
        _fail(
            "effect_receipt_classification_invalid",
            "terminal EffectReceiptV1 only allows applied/not_applied",
        )
    receipt_raw = _require_mapping(data["receipt"], label="receipt")
    receipt = effect_receipt_to_dict(parse_effect_receipt_v1(receipt_raw))
    observation = effect_observation_body_v1(
        _require_mapping(data["observation"], label="observation")
    )
    fence = _require_int(data["fence"], label="fence", minimum=1)
    effect_id = _require_text(data["effect_id"], label="effect_id")
    receipt_digest = _require_digest(data["receipt_digest"], label="receipt_digest")
    command_digest = _require_digest(data["command_digest"], label="command_digest")
    target_digest = _require_digest(data["target_digest"], label="target_digest")
    authority_binding_digest = _require_digest(
        data["authority_binding_digest"], label="authority_binding_digest"
    )
    observation_digest = _require_digest(data["observation_digest"], label="observation_digest")
    adapter_id = _require_identifier(data["adapter_id"], label="adapter_id")
    operation = _require_identifier(data["operation"], label="operation")
    payload_digest = _require_digest(data["payload_digest"], label="payload_digest")
    idempotency_key = _require_text(data["idempotency_key"], label="idempotency_key")
    out = {
        "fence": fence,
        "result_classification": classification,
        "receipt_digest": receipt_digest,
        "command_digest": command_digest,
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "target_digest": target_digest,
        "authority_binding_digest": authority_binding_digest,
        "observation_digest": observation_digest,
        "receipt": receipt,
        "adapter_id": adapter_id,
        "operation": operation,
        "payload_digest": payload_digest,
        "idempotency_key": idempotency_key,
        "effect_id": effect_id,
        "observation": observation,
    }
    _bind_receipt_wrapper_v1(payload=out, receipt=receipt, label="receipt")
    if observation_digest != canonical_digest_v1(observation):
        _fail("effect_contract_invalid", "receipt observation_digest mismatch")
    if observation.get("effect_id") != effect_id:
        _fail("effect_contract_invalid", "receipt observation effect_id mismatch")
    if observation.get("fence") != fence:
        _fail("effect_contract_invalid", "receipt observation fence/effect binding mismatch")
    # post_readback binds to durable observation/reconcile facts cross-event; not forced
    # equal to embedded observation.readback (unknown->reconcile path keeps original obs body).
    return out


def _validate_reconciled_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="reconciled.payload")
    _require_exact_keys(
        data,
        {
            "classification",
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "evidence_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "authority_binding_digest",
            "readback",
            "readback_digest",
        },
        label="reconciled.payload",
    )
    classification = _require_text(data["classification"], label="classification")
    if classification not in _RECEIPT_CLASSES:
        _fail("effect_contract_invalid", "reconcile classification must be terminal")
    readback = _require_json_object(data["readback"], label="readback")
    readback_digest = _require_digest(data["readback_digest"], label="readback_digest")
    if readback_digest != canonical_digest_v1(readback):
        _fail("effect_contract_invalid", "reconcile readback_digest mismatch")
    return {
        "classification": classification,
        "fence": _require_int(data["fence"], label="fence", minimum=1),
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "evidence_digest": _require_digest(data["evidence_digest"], label="evidence_digest"),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "effect_id": _require_text(data["effect_id"], label="effect_id"),
        "authority_binding_digest": _require_digest(
            data["authority_binding_digest"], label="authority_binding_digest"
        ),
        "readback": readback,
        "readback_digest": readback_digest,
    }


def _validate_compensation_intent_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="compensation_intent.payload")
    _require_exact_keys(
        data,
        {
            "original_receipt_digest",
            "original_receipt",
            "compensation_ref",
            "request_digest",
            "command_digest",
            "target_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "fence",
            "authority_binding_digest",
            "preflight_readback",
            "preflight_readback_digest",
        },
        label="compensation_intent.payload",
    )
    original_receipt = _require_applied_original_receipt_v1(
        _require_mapping(data["original_receipt"], label="original_receipt"),
        label="compensation_intent",
    )
    original_receipt_digest = _require_digest(
        data["original_receipt_digest"], label="original_receipt_digest"
    )
    if original_receipt_digest != original_receipt["receipt_digest"]:
        _fail("effect_contract_invalid", "original_receipt_digest mismatch")
    effect_id = _require_text(data["effect_id"], label="effect_id")
    if original_receipt.get("effect_id") != effect_id:
        _fail("effect_contract_invalid", "compensation original effect_id mismatch")
    preflight = _require_json_object(data["preflight_readback"], label="preflight_readback")
    preflight_digest = _require_digest(
        data["preflight_readback_digest"], label="preflight_readback_digest"
    )
    if preflight_digest != canonical_digest_v1(preflight):
        _fail("effect_contract_invalid", "compensation preflight_readback_digest mismatch")
    return {
        "original_receipt_digest": original_receipt_digest,
        "original_receipt": original_receipt,
        "compensation_ref": _require_text(data["compensation_ref"], label="compensation_ref"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "effect_id": effect_id,
        "fence": _require_int(data["fence"], label="fence", minimum=1),
        "authority_binding_digest": _require_digest(
            data["authority_binding_digest"], label="authority_binding_digest"
        ),
        "preflight_readback": preflight,
        "preflight_readback_digest": preflight_digest,
    }


def _validate_compensation_observation_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="compensation_observation.payload")
    _require_exact_keys(
        data,
        {
            "classification",
            "observation",
            "readback",
            "readback_digest",
            "original_receipt_digest",
            "original_receipt",
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "authority_binding_digest",
        },
        label="compensation_observation.payload",
    )
    classification = _require_text(data["classification"], label="classification")
    if classification not in _OBSERVATION_CLASSES:
        _fail("effect_contract_invalid", f"unsupported classification: {classification}")
    observation = effect_observation_body_v1(
        _require_mapping(data["observation"], label="observation")
    )
    original_receipt = _require_applied_original_receipt_v1(
        _require_mapping(data["original_receipt"], label="original_receipt"),
        label="compensation_observation",
    )
    original_receipt_digest = _require_digest(
        data["original_receipt_digest"], label="original_receipt_digest"
    )
    if original_receipt_digest != original_receipt["receipt_digest"]:
        _fail("effect_contract_invalid", "original_receipt_digest mismatch")
    readback = _require_json_object(data["readback"], label="readback")
    readback_digest = _require_digest(data["readback_digest"], label="readback_digest")
    if readback_digest != canonical_digest_v1(readback):
        _fail("effect_contract_invalid", "compensation readback_digest mismatch")
    fence = _require_int(data["fence"], label="fence", minimum=1)
    effect_id = _require_text(data["effect_id"], label="effect_id")
    _bind_observation_wrapper_v1(
        wrapper={
            "classification": classification,
            "fence": fence,
            "effect_id": effect_id,
            "readback": readback,
        },
        observation=observation,
        label="compensation observation",
    )
    if original_receipt.get("effect_id") != effect_id:
        _fail("effect_contract_invalid", "compensation observation original effect_id mismatch")
    return {
        "classification": classification,
        "observation": observation,
        "readback": readback,
        "readback_digest": readback_digest,
        "original_receipt_digest": original_receipt_digest,
        "original_receipt": original_receipt,
        "fence": fence,
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "effect_id": effect_id,
        "authority_binding_digest": _require_digest(
            data["authority_binding_digest"], label="authority_binding_digest"
        ),
    }


def _validate_compensation_receipt_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = _require_mapping(payload, label="compensation_receipt.payload")
    _require_exact_keys(
        data,
        {
            "receipt_digest",
            "receipt",
            "original_receipt_digest",
            "original_receipt",
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "authority_binding_digest",
            "observation_digest",
            "observation",
        },
        label="compensation_receipt.payload",
    )
    receipt = effect_receipt_to_dict(
        parse_effect_receipt_v1(_require_mapping(data["receipt"], label="receipt"))
    )
    original_receipt = _require_applied_original_receipt_v1(
        _require_mapping(data["original_receipt"], label="original_receipt"),
        label="compensation_receipt",
    )
    observation = effect_observation_body_v1(
        _require_mapping(data["observation"], label="observation")
    )
    fence = _require_int(data["fence"], label="fence", minimum=1)
    effect_id = _require_text(data["effect_id"], label="effect_id")
    out = {
        "receipt_digest": _require_digest(data["receipt_digest"], label="receipt_digest"),
        "receipt": receipt,
        "original_receipt_digest": _require_digest(
            data["original_receipt_digest"], label="original_receipt_digest"
        ),
        "original_receipt": original_receipt,
        "fence": fence,
        "command_digest": _require_digest(data["command_digest"], label="command_digest"),
        "request_digest": _require_digest(data["request_digest"], label="request_digest"),
        "target_digest": _require_digest(data["target_digest"], label="target_digest"),
        "adapter_id": _require_identifier(data["adapter_id"], label="adapter_id"),
        "operation": _require_identifier(data["operation"], label="operation"),
        "payload_digest": _require_digest(data["payload_digest"], label="payload_digest"),
        "idempotency_key": _require_text(data["idempotency_key"], label="idempotency_key"),
        "effect_id": effect_id,
        "authority_binding_digest": _require_digest(
            data["authority_binding_digest"], label="authority_binding_digest"
        ),
        "observation_digest": _require_digest(
            data["observation_digest"], label="observation_digest"
        ),
        "observation": observation,
    }
    _bind_receipt_wrapper_v1(payload=out, receipt=receipt, label="compensation receipt")
    if out["original_receipt_digest"] != original_receipt["receipt_digest"]:
        _fail("effect_contract_invalid", "original_receipt_digest mismatch")
    if original_receipt.get("effect_id") != effect_id:
        _fail("effect_contract_invalid", "compensation receipt original effect_id mismatch")
    if out["observation_digest"] != canonical_digest_v1(observation):
        _fail("effect_contract_invalid", "compensation observation_digest mismatch")
    if observation.get("effect_id") != effect_id:
        _fail("effect_contract_invalid", "compensation receipt observation effect_id mismatch")
    if observation.get("fence") != fence:
        _fail("effect_contract_invalid", "compensation receipt observation fence mismatch")
    if observation.get("classification") != receipt.get("result_classification"):
        _fail("effect_contract_invalid", "compensation receipt observation classification mismatch")
    if canonical_digest_v1(receipt["post_readback"]) != canonical_digest_v1(observation["readback"]):
        _fail(
            "effect_contract_invalid",
            "compensation receipt post_readback does not match observation readback",
        )
    return out


_LEDGER_PAYLOAD_VALIDATORS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "claimed": _validate_claimed_payload_v1,
    "intent_recorded": _validate_intent_payload_v1,
    "observation_recorded": _validate_observation_payload_v1,
    "reconciled": _validate_reconciled_payload_v1,
    "receipt_terminal": _validate_receipt_payload_v1,
    "compensation_intent": _validate_compensation_intent_payload_v1,
    "compensation_observation": _validate_compensation_observation_payload_v1,
    "compensation_receipt": _validate_compensation_receipt_payload_v1,
}


def validate_workflow_effect_ledger_payload_v1(
    event_type: str, payload: object
) -> dict[str, Any]:
    """Canonical per-event payload validator (exact keys/types; no Mapping passthrough)."""
    if event_type not in _LEDGER_PAYLOAD_VALIDATORS:
        _fail("effect_contract_invalid", f"unsupported effect ledger event_type: {event_type}")
    if not isinstance(payload, Mapping):
        _fail("effect_contract_invalid", "payload must be an object")
    return _LEDGER_PAYLOAD_VALIDATORS[event_type](payload)


def parse_workflow_effect_ledger_event_v1(value: object) -> WorkflowEffectLedgerEventV1:
    data = _require_mapping(value, label="effect_ledger_event")
    _require_exact_keys(
        data,
        {
            "schema_version",
            "effect_id",
            "sequence",
            "previous_event_id",
            "idempotency_key",
            "occurred_at",
            "event_type",
            "payload",
        },
        label="effect_ledger_event",
        optional={"event_id"},
    )
    if data.get("schema_version") != "workflow_effect_ledger_event_v1":
        _fail(
            "effect_contract_invalid",
            "schema_version must be workflow_effect_ledger_event_v1",
        )
    event_type = _require_text(data.get("event_type"), label="event_type")
    if event_type not in _EFFECT_LEDGER_EVENT_TYPES:
        _fail("effect_contract_invalid", f"unsupported effect ledger event_type: {event_type}")
    previous = data.get("previous_event_id")
    if previous is not None:
        previous = _require_event_id(previous, prefix="wel_", label="previous_event_id")
    # Exact discriminated payload validation always runs (event_id omitted or supplied).
    payload = validate_workflow_effect_ledger_payload_v1(event_type, data.get("payload"))
    effect_id = _require_text(data.get("effect_id"), label="effect_id")
    if payload.get("effect_id") != effect_id:
        _fail("effect_contract_invalid", "payload.effect_id does not match envelope effect_id")
    body = {
        "schema_version": "workflow_effect_ledger_event_v1",
        "effect_id": effect_id,
        "sequence": _require_int(data.get("sequence"), label="sequence", minimum=1),
        "previous_event_id": previous,
        "idempotency_key": _require_text(
            data.get("idempotency_key"), label="idempotency_key"
        ),
        "occurred_at": _require_iso_datetime(data.get("occurred_at"), label="occurred_at"),
        "event_type": event_type,
        "payload": payload,
    }
    # Presence semantics: derive only when key is ABSENT. Present null/empty/malformed rejects.
    expected = derive_workflow_effect_ledger_event_id_v1(body)
    if "event_id" not in data:
        event_id = expected
    else:
        event_id = _require_event_id(data.get("event_id"), prefix="wel_", label="event_id")
        if event_id != expected:
            _fail("effect_contract_invalid", "event_id does not match canonical body")
    frozen_body = dict(body)
    frozen_body["payload"] = _freeze_value(normalize_admitted_json_v1(body["payload"]))
    return WorkflowEffectLedgerEventV1(**frozen_body, event_id=event_id)


def derive_workflow_execution_event_id_v2(body: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in body.items() if key != "event_id"}
    return f"we2_{hashlib.sha256(_compact_json(canonical)).hexdigest()}"


def parse_workflow_execution_event_v2(value: object) -> WorkflowExecutionEventV2:
    data = _require_mapping(value, label="execution_event_v2")
    _require_exact_keys(
        data,
        {
            "schema_version",
            "run_id",
            "sequence",
            "previous_event_id",
            "idempotency_key",
            "occurred_at",
            "event_type",
            "payload",
        },
        label="execution_event_v2",
        optional={"event_id"},
    )
    if data.get("schema_version") != "workflow_execution_event_v2":
        _fail(
            "effect_contract_invalid",
            "schema_version must be workflow_execution_event_v2",
        )
    event_type = _require_text(data.get("event_type"), label="event_type")
    if event_type not in _EXECUTION_EVENT_V2_TYPES:
        _fail("effect_contract_invalid", f"unsupported execution v2 event_type: {event_type}")
    previous = data.get("previous_event_id")
    if previous is not None:
        previous = _require_event_id(previous, prefix="we2_", label="previous_event_id")
    body = {
        "schema_version": "workflow_execution_event_v2",
        "run_id": _require_text(data.get("run_id"), label="run_id"),
        "sequence": _require_int(data.get("sequence"), label="sequence", minimum=1),
        "previous_event_id": previous,
        "idempotency_key": _require_text(
            data.get("idempotency_key"), label="idempotency_key"
        ),
        "occurred_at": _require_iso_datetime(data.get("occurred_at"), label="occurred_at"),
        "event_type": event_type,
        "payload": _require_mapping(data.get("payload"), label="payload"),
    }
    # Presence semantics: derive only when key is ABSENT. Present null/empty/malformed rejects.
    expected = derive_workflow_execution_event_id_v2(body)
    if "event_id" not in data:
        event_id = expected
    else:
        event_id = _require_event_id(data.get("event_id"), prefix="we2_", label="event_id")
        if event_id != expected:
            _fail("effect_contract_invalid", "event_id does not match canonical body")
    frozen_body = dict(body)
    frozen_body["payload"] = _freeze_value(normalize_admitted_json_v1(body["payload"]))
    return WorkflowExecutionEventV2(**frozen_body, event_id=event_id)
