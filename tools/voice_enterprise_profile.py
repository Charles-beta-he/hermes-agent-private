"""Enterprise readiness profile for Hermes voice mode.

This module captures the production-grade, Jarvis-like voice capability shape
without changing the existing interaction model.  It is intentionally pure and
side-effect free: callers pass the already-loaded config and voice requirements,
and receive an auditable report that can be rendered by CLI/docs/tests.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


ENTERPRISE_VOICE_CAPABILITIES: Dict[str, str] = {
    "vad_conversation_loop": "VAD-driven listen/transcribe/reply loop behind the existing /voice controls",
    "local_first_stt": "Local faster-whisper STT when configured, with cloud STT as explicit fallback",
    "spoken_reply": "TTS reply path through the configured Hermes TTS provider",
    "feedback_guard": "TTS playback pauses/re-arms capture to prevent self-listening feedback loops",
    "audible_state_cues": "Start/stop beeps and status callbacks provide operator-visible state",
    "tool_governance": "Hermes tool approval and side-effect gates remain outside the voice layer",
    "no_interaction_change": "No always-on wake-word listener or new command grammar is enabled by this profile",
}


_REQUIRED_CAPABILITIES = (
    "vad_conversation_loop",
    "local_first_stt",
    "spoken_reply",
    "feedback_guard",
    "tool_governance",
    "no_interaction_change",
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _provider_name(config: Dict[str, Any], section: str, default: str = "") -> str:
    block = _as_dict(config.get(section))
    provider = block.get("provider", default)
    return str(provider or "").strip().lower()


def _missing_required_capabilities(blockers: Iterable[str]) -> List[str]:
    blocked = set(blockers)
    missing: List[str] = []
    if "audio_capture_missing" in blocked or "audio_environment_blocked" in blocked:
        missing.append("vad_conversation_loop")
    if "stt_provider_missing" in blocked:
        missing.append("local_first_stt")
    if "tts_provider_missing" in blocked:
        missing.append("spoken_reply")
    return missing


def build_enterprise_voice_readiness(
    config: Dict[str, Any] | None,
    requirements: Dict[str, Any] | None,
    *,
    platform: str | None = None,
) -> Dict[str, Any]:
    """Return a deterministic enterprise-readiness report for voice mode.

    The report is conservative: missing microphone/STT/TTS are blockers;
    disabled convenience affordances such as auto TTS or beeps are warnings.
    It does **not** enable any feature or mutate config, preserving Hermes'
    current `/voice` interaction logic.
    """

    cfg = _as_dict(config)
    req = _as_dict(requirements)
    voice_cfg = _as_dict(cfg.get("voice"))
    tts_provider = _provider_name(cfg, "tts", default="")
    stt_provider = _provider_name(cfg, "stt", default="")

    blockers: List[str] = []
    warnings: List[str] = []
    notices: List[str] = []

    if not req.get("audio_available", False):
        blockers.append("audio_capture_missing")
    env = _as_dict(req.get("environment"))
    if env.get("available") is False:
        blockers.append("audio_environment_blocked")
    if not req.get("stt_available", False):
        blockers.append("stt_provider_missing")
    if not tts_provider or tts_provider in {"none", "off", "disabled"}:
        blockers.append("tts_provider_missing")

    # Enterprise profile keeps the existing explicit /voice activation.  If the
    # user has not opted into automatic spoken replies, the feature still works
    # but is not as Jarvis-like.
    if not _truthy(voice_cfg.get("auto_tts", False)):
        warnings.append("spoken_reply_requires_/voice_tts_or_config_auto_tts")
    if voice_cfg.get("beep_enabled") is False:
        warnings.append("audible_state_cues_disabled")
    if stt_provider and stt_provider != "local":
        warnings.append(f"stt_not_local_first:{stt_provider}")

    # Preserve safety: YOLO weakens side-effect governance even though it is
    # outside voice mode proper. Treat it as a production blocker when present.
    if _truthy(cfg.get("yolo", False)) or _truthy(voice_cfg.get("yolo", False)):
        blockers.append("tool_governance_weakened_by_yolo")

    if platform:
        notices.append(f"platform:{platform}")
    if voice_cfg:
        notices.append("voice_config_present")
    else:
        notices.append("voice_config_defaulted")

    missing_capabilities = _missing_required_capabilities(blockers)
    ready = not blockers
    status = "ready" if ready else "blocked"

    return {
        "status": status,
        "ready": ready,
        "interaction_logic": "unchanged:/voice on + record_key + VAD continuous loop",
        "capabilities": dict(ENTERPRISE_VOICE_CAPABILITIES),
        "required_capabilities": list(_REQUIRED_CAPABILITIES),
        "missing_capabilities": missing_capabilities,
        "blockers": blockers,
        "warnings": warnings,
        "notices": notices,
        "providers": {
            "stt": stt_provider or "default",
            "tts": tts_provider or "missing",
        },
    }


def render_enterprise_voice_readiness(report: Dict[str, Any]) -> str:
    """Render a compact operator-facing readiness summary."""

    status = "READY" if report.get("ready") else "BLOCKED"
    lines = [
        f"Enterprise voice profile: {status}",
        f"Interaction logic: {report.get('interaction_logic', 'unchanged')}",
    ]
    providers = _as_dict(report.get("providers"))
    if providers:
        lines.append(f"Providers: STT={providers.get('stt', 'default')} TTS={providers.get('tts', 'missing')}")
    for label, key in (("Blockers", "blockers"), ("Warnings", "warnings"), ("Notices", "notices")):
        values = list(report.get(key) or [])
        if values:
            lines.append(f"{label}: " + ", ".join(str(v) for v in values))
    missing = list(report.get("missing_capabilities") or [])
    if missing:
        lines.append("Missing capabilities: " + ", ".join(str(v) for v in missing))
    return "\n".join(lines)
