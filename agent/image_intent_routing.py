"""Default-off image output intent contract and shadow observer.

This module deliberately does not mutate ``tool_choice`` or execute tools.  It
turns a conservative intent observation plus the existing provider capability
surface into a versioned, inspectable recommendation.  Runtime enforcement is
a separate certification boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


CONTRACT_VERSION = "hermes_image_intent_contract_v1"


class ImageIntent(str, Enum):
    GENERATE_ASSET = "generate_asset"
    EDIT_ASSET = "edit_asset"
    ANALYZE_ASSET = "analyze_asset"
    PROMPT_ONLY = "prompt_only"
    EXPLAIN_ONLY = "explain_only"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ImageIntentDecision:
    schema_version: str
    intent: ImageIntent
    confidence: str
    source: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["intent"] = self.intent.value
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True)
class ImageProviderCapabilityContract:
    schema_version: str
    provider: str
    model: str | None
    text_to_image: bool
    image_edit: bool
    reference_images: bool
    max_reference_images: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImageIntentRoute:
    schema_version: str
    intent: ImageIntent
    artifact_required: bool
    # Evidence-only shadow recommendation. No runtime tool-selection consumer
    # is authorized by this contract version.
    recommended_tool_policy: str
    eligible: bool
    blocker: str | None
    runtime_mutation_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["intent"] = self.intent.value
        return payload


_NEGATIVE_PATTERNS: tuple[tuple[ImageIntent, tuple[str, ...]], ...] = (
    (
        ImageIntent.PROMPT_ONLY,
        (
            r"(?:只|仅).{0,8}(?:写|生成|给).{0,8}(?:提示词|prompt)",
            r"(?:不要|无需|别).{0,5}(?:生成|出|创建).{0,5}(?:图片|图像|image)",
            r"prompt\s+only",
            r"write\s+(?:me\s+)?(?:an?\s+)?image\s+prompt",
            r"do\s+not\s+generate",
        ),
    ),
    (
        ImageIntent.EXPLAIN_ONLY,
        (
            r"(?:解释|说明|介绍|原理).{0,20}(?:图片生成|图像生成|image generator|image generation)",
            r"(?:image generator|image generation).{0,20}(?:原理|工作|works?)",
            r"explain.{0,30}(?:image generator|image generation)",
            r"how.{0,15}(?:image generator|image generation).{0,15}works?",
        ),
    ),
    (
        ImageIntent.ANALYZE_ASSET,
        (
            r"(?:分析|识别|检查|审查).{0,20}(?:这张|该|现有|上传|attached)?.{0,5}(?:图片|图像|截图|照片)",
            r"(?:analy[sz]e|inspect|review).{0,20}(?:this|the|attached)?.{0,5}(?:image|screenshot|photo)",
        ),
    ),
)

_EDIT_PATTERNS = (
    r"(?:编辑|修改|改成|变成|去掉|移除|替换).{0,30}(?:这张|该|现有|上传)?.{0,5}(?:图片|图像|截图|照片)",
    r"(?:把|将).{0,8}(?:这张|该|现有)?.{0,5}(?:图片|图像|截图|照片).{0,20}(?:改成|变成|编辑|修改|去掉|移除)",
    r"(?:edit|modify|transform|remove|replace).{0,25}(?:this|the|attached)?.{0,5}(?:image|screenshot|photo)",
)

_GENERATE_PATTERNS = (
    r"(?:生成|画|绘制|创建|做).{0,12}(?:一张|一个|这张)?.{0,8}(?:图片|图像|插画|海报|照片|头像)",
    r"(?:图片|图像|插画|海报|照片|头像).{0,10}(?:生成|创作|制作|画出来|出图)",
    r"(?:create|generate|draw|render|make).{0,20}(?:an?\s+)?(?:image|picture|illustration|poster|photo|avatar)",
)


def _first_pattern(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            return pattern
    return None


def classify_image_intent_shadow(user_message: Any) -> ImageIntentDecision:
    """Conservatively classify obvious image-output intent for shadow use.

    Negative intents are evaluated before generation.  Unclear requests stay
    ``ambiguous``; this deterministic observer is evidence, not an enforcement
    authority or replacement for the main model's semantic tool selection.
    """

    text = user_message if isinstance(user_message, str) else str(user_message or "")
    text = " ".join(text.strip().split())

    for intent, patterns in _NEGATIVE_PATTERNS:
        match = _first_pattern(text, patterns)
        if match:
            return ImageIntentDecision(
                schema_version=CONTRACT_VERSION,
                intent=intent,
                confidence="high",
                source="deterministic_shadow_v1",
                evidence=(match,),
            )

    match = _first_pattern(text, _EDIT_PATTERNS)
    if match:
        return ImageIntentDecision(
            schema_version=CONTRACT_VERSION,
            intent=ImageIntent.EDIT_ASSET,
            confidence="high",
            source="deterministic_shadow_v1",
            evidence=(match,),
        )

    match = _first_pattern(text, _GENERATE_PATTERNS)
    if match:
        return ImageIntentDecision(
            schema_version=CONTRACT_VERSION,
            intent=ImageIntent.GENERATE_ASSET,
            confidence="high",
            source="deterministic_shadow_v1",
            evidence=(match,),
        )

    return ImageIntentDecision(
        schema_version=CONTRACT_VERSION,
        intent=ImageIntent.AMBIGUOUS,
        confidence="low",
        source="deterministic_shadow_v1",
        evidence=(),
    )


def build_provider_capability_contract(
    provider: Any,
    *,
    model: str | None = None,
    capabilities: Mapping[str, Any] | None = None,
) -> ImageProviderCapabilityContract:
    raw: Mapping[str, Any]
    if isinstance(capabilities, Mapping):
        raw = capabilities
    else:
        try:
            candidate = provider.capabilities()
            raw = candidate if isinstance(candidate, Mapping) else {}
        except Exception:
            raw = {}
    modalities = {
        str(value).strip().lower()
        for value in (raw.get("modalities") or [])
        if isinstance(value, str)
    }
    try:
        max_refs = max(0, int(raw.get("max_reference_images") or 0))
    except (TypeError, ValueError):
        max_refs = 0
    provider_name = raw.get("provider") or getattr(provider, "name", "unknown") or "unknown"
    resolved_model = model if model is not None else raw.get("model")
    return ImageProviderCapabilityContract(
        schema_version=CONTRACT_VERSION,
        provider=str(provider_name),
        model=str(resolved_model) if resolved_model else None,
        text_to_image="text" in modalities,
        image_edit="image" in modalities,
        reference_images=max_refs > 0,
        max_reference_images=max_refs,
    )


def evaluate_image_intent_contract(
    decision: ImageIntentDecision,
    *,
    provider: ImageProviderCapabilityContract | None,
    image_tool_available: bool,
) -> ImageIntentRoute:
    intent = decision.intent
    if intent in {
        ImageIntent.PROMPT_ONLY,
        ImageIntent.EXPLAIN_ONLY,
        ImageIntent.ANALYZE_ASSET,
    }:
        return ImageIntentRoute(
            schema_version=CONTRACT_VERSION,
            intent=intent,
            artifact_required=False,
            recommended_tool_policy="forbid_image_generate",
            eligible=True,
            blocker=None,
        )

    if intent is ImageIntent.AMBIGUOUS:
        return ImageIntentRoute(
            schema_version=CONTRACT_VERSION,
            intent=intent,
            artifact_required=False,
            recommended_tool_policy="preserve_auto",
            eligible=True,
            blocker=None,
        )

    if not image_tool_available:
        blocker = "image_tool_unavailable"
    elif provider is None:
        blocker = "provider_unavailable"
    elif intent is ImageIntent.GENERATE_ASSET and not provider.text_to_image:
        blocker = "provider_missing_text_to_image"
    elif intent is ImageIntent.EDIT_ASSET and not provider.image_edit:
        blocker = "provider_missing_image_edit"
    else:
        blocker = None

    return ImageIntentRoute(
        schema_version=CONTRACT_VERSION,
        intent=intent,
        artifact_required=True,
        recommended_tool_policy="prefer_image_generate",
        eligible=blocker is None,
        blocker=blocker,
    )


def _collect_current_turn_tool_names(messages: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(messages, list):
        return names
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "user":
            break
        if message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in reversed(calls):
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    names.reverse()
    return names


def finalize_image_intent_shadow(
    receipt: Mapping[str, Any] | None,
    *,
    messages: Any,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compare a shadow recommendation with current-turn tool observations.

    A conflict only blocks a *future production promotion*.  It never changes
    the current response or tool execution.
    """
    if not isinstance(receipt, Mapping):
        return None
    final = dict(receipt)
    if receipt.get("status") != "observed":
        return final

    observed = _collect_current_turn_tool_names(messages)
    image_observed = "image_generate" in observed
    decision_value = receipt.get("decision")
    route_value = receipt.get("route")
    decision: Mapping[str, Any] = decision_value if isinstance(decision_value, Mapping) else {}
    route: Mapping[str, Any] = route_value if isinstance(route_value, Mapping) else {}
    intent = str(decision.get("intent") or "ambiguous")
    artifact_required = bool(route.get("artifact_required"))
    eligible = bool(route.get("eligible"))

    negative_intents = {
        ImageIntent.PROMPT_ONLY.value,
        ImageIntent.EXPLAIN_ONLY.value,
        ImageIntent.ANALYZE_ASSET.value,
    }
    if receipt.get("schema_version") != CONTRACT_VERSION:
        alignment = {
            "status": "conflict",
            "reason": "contract_version_mismatch",
            "review_required": True,
            "production_gate": "block",
        }
    elif intent in negative_intents and image_observed:
        alignment = {
            "status": "conflict",
            "reason": "generation_observed_for_negative_intent",
            "review_required": True,
            "production_gate": "block",
        }
    elif artifact_required and eligible and not image_observed:
        alignment = {
            "status": "conflict",
            "reason": "artifact_required_but_generation_not_observed",
            "review_required": True,
            "production_gate": "block",
        }
    elif artifact_required and not eligible and image_observed:
        alignment = {
            "status": "conflict",
            "reason": "generation_observed_despite_capability_blocker",
            "review_required": True,
            "production_gate": "block",
        }
    elif intent == ImageIntent.AMBIGUOUS.value:
        alignment = {
            "status": "not_applicable",
            "reason": "ambiguous_shadow_intent",
            "review_required": False,
            "production_gate": "observe",
        }
    else:
        alignment = {
            "status": "aligned",
            "reason": "observation_matches_shadow_contract",
            "review_required": False,
            "production_gate": "observe",
        }

    final["status"] = "finalized"
    final["observed_tool_names"] = observed
    final["alignment"] = alignment
    final["runtime_mutation_authorized"] = False

    root = config if isinstance(config, Mapping) else {}
    raw_section = root.get("image_intent_routing")
    section = raw_section if isinstance(raw_section, Mapping) else {}
    if section.get("ledger_enabled"):
        configured_path = section.get("ledger_path")
        ledger_path = Path(str(configured_path)).expanduser() if configured_path else _default_ledger_path()
        _append_jsonl_private(ledger_path, final)
    return final


def _default_ledger_path() -> Path:
    try:
        from hermes_constants import get_hermes_home

        root = get_hermes_home()
    except Exception:
        root = Path.home() / ".hermes"
    return root / "state" / "image-intent-routing" / "shadow-events.jsonl"


def _append_jsonl_private(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")


def run_image_intent_shadow(
    *,
    user_message: Any,
    config: Mapping[str, Any] | None,
    image_tool_available: bool,
    provider: Any = None,
    model: str | None = None,
    capabilities: Mapping[str, Any] | None = None,
    turn_id: str | None = None,
) -> dict[str, Any] | None:
    """Observe one turn under a default-off, non-mutating shadow contract."""

    root = config if isinstance(config, Mapping) else {}
    raw = root.get("image_intent_routing")
    section = raw if isinstance(raw, Mapping) else {}
    if not section.get("enabled"):
        return None
    if section.get("kill_switch"):
        return {
            "schema_version": CONTRACT_VERSION,
            "status": "disabled_by_kill_switch",
            "runtime_mutation_authorized": False,
            "turn_id": turn_id,
        }

    mode = str(section.get("mode") or "shadow").strip().lower()
    if mode != "shadow":
        return {
            "schema_version": CONTRACT_VERSION,
            "status": "unsupported_mode",
            "requested_mode": mode,
            "runtime_mutation_authorized": False,
            "turn_id": turn_id,
        }

    decision = classify_image_intent_shadow(user_message)
    provider_contract = (
        build_provider_capability_contract(
            provider,
            model=model,
            capabilities=capabilities,
        )
        if provider is not None or isinstance(capabilities, Mapping)
        else None
    )
    route = evaluate_image_intent_contract(
        decision,
        provider=provider_contract,
        image_tool_available=image_tool_available,
    )
    normalized_text = user_message if isinstance(user_message, str) else str(user_message or "")
    receipt: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION,
        "status": "observed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "turn_id": turn_id,
        "message_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        "message_chars": len(normalized_text),
        "decision": decision.to_dict(),
        "provider": provider_contract.to_dict() if provider_contract else None,
        "route": route.to_dict(),
        "runtime_mutation_authorized": False,
    }
    if section.get("ledger_enabled"):
        configured_path = section.get("ledger_path")
        ledger_path = Path(str(configured_path)).expanduser() if configured_path else _default_ledger_path()
        _append_jsonl_private(ledger_path, receipt)
    return receipt
