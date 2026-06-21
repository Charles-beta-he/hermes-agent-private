"""Voice-cloning safety gate helpers.

This module is deliberately provider-agnostic. It does not synthesize audio,
download model weights, or contact external APIs. It validates whether a
reference-audio based voice-cloning request has enough local provenance,
consent, audit, and delivery-boundary metadata to be *eligible* for a later
provider-specific prototype.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ALLOWED_SUBJECT_TYPES = frozenset({"self", "synthetic", "third_party"})
ALLOWED_OUTPUT_LABELS = frozenset({"synthetic_clone", "consented_voice_clone"})
ALLOWED_DELIVERY = frozenset({"local_file", "telegram_voice", "telegram_audio", "none"})
REQUIRED_CONSENT_SCOPE = "tts_voice_clone"
MAX_REFERENCE_AUDIO_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class VoiceCloneGateFinding:
    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class VoiceCloneGateResult:
    ok: bool
    status: str
    findings: tuple[VoiceCloneGateFinding, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "findings": [finding.as_dict() for finding in self.findings],
        }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _path_from_manifest(raw: Any, *, base_dir: Path | None) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def _file_finding(path: Path, *, code_prefix: str, label: str) -> VoiceCloneGateFinding | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return VoiceCloneGateFinding("error", f"{code_prefix}_missing", f"{label} does not exist: {path}")
    except OSError as exc:
        return VoiceCloneGateFinding("error", f"{code_prefix}_unreadable", f"{label} cannot be inspected: {path}: {exc}")
    if not path.is_file():
        return VoiceCloneGateFinding("error", f"{code_prefix}_not_file", f"{label} is not a regular file: {path}")
    if stat.st_size <= 0:
        return VoiceCloneGateFinding("error", f"{code_prefix}_empty", f"{label} is empty: {path}")
    return None


def validate_voice_clone_manifest(
    manifest: dict[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> VoiceCloneGateResult:
    """Validate a local voice-clone consent/provenance manifest.

    Expected minimal shape::

        {
          "provider": "voxcpm2",
          "mode": "voice_clone",
          "subject_type": "self|synthetic|third_party",
          "explicit_consent": true,
          "consent_scope": ["tts_voice_clone"],
          "consent_record_path": "/path/to/consent.md",
          "reference_audio_paths": ["/path/to/reference.wav"],
          "output_label": "consented_voice_clone",
          "allowed_delivery": ["local_file"],
          "purpose": "Hermes private voice prototype"
        }

    The result being ``ok=True`` means only that a later prototype is eligible
    to start. It does not authorize production/default activation, external
    sending, payment, deployment, or cloning a person without their evidence.
    """
    findings: list[VoiceCloneGateFinding] = []
    base = Path(base_dir).expanduser() if base_dir is not None else None

    if not isinstance(manifest, dict):
        return VoiceCloneGateResult(
            ok=False,
            status="blocked",
            findings=(VoiceCloneGateFinding("error", "manifest_not_object", "manifest must be a JSON object"),),
        )

    mode = str(manifest.get("mode") or "").strip().lower()
    if mode != "voice_clone":
        findings.append(VoiceCloneGateFinding("error", "mode_not_voice_clone", "mode must be voice_clone"))

    subject_type = str(manifest.get("subject_type") or "").strip().lower()
    if subject_type not in ALLOWED_SUBJECT_TYPES:
        findings.append(VoiceCloneGateFinding(
            "error",
            "invalid_subject_type",
            f"subject_type must be one of {sorted(ALLOWED_SUBJECT_TYPES)}",
        ))

    if not _truthy(manifest.get("explicit_consent")):
        findings.append(VoiceCloneGateFinding("error", "missing_explicit_consent", "explicit_consent must be true"))

    scopes = {str(item).strip() for item in _as_list(manifest.get("consent_scope")) if str(item).strip()}
    if REQUIRED_CONSENT_SCOPE not in scopes:
        findings.append(VoiceCloneGateFinding(
            "error",
            "missing_tts_voice_clone_scope",
            f"consent_scope must include {REQUIRED_CONSENT_SCOPE!r}",
        ))

    consent_path = _path_from_manifest(manifest.get("consent_record_path"), base_dir=base)
    if consent_path is None:
        findings.append(VoiceCloneGateFinding("error", "missing_consent_record_path", "consent_record_path is required"))
    else:
        finding = _file_finding(consent_path, code_prefix="consent_record", label="consent record")
        if finding:
            findings.append(finding)

    reference_paths_raw = _as_list(manifest.get("reference_audio_paths"))
    reference_paths = [p for p in (_path_from_manifest(item, base_dir=base) for item in reference_paths_raw) if p is not None]
    if not reference_paths:
        findings.append(VoiceCloneGateFinding("error", "missing_reference_audio", "at least one reference audio file is required"))
    for path in reference_paths:
        finding = _file_finding(path, code_prefix="reference_audio", label="reference audio")
        if finding:
            findings.append(finding)
            continue
        try:
            if path.stat().st_size > MAX_REFERENCE_AUDIO_BYTES:
                findings.append(VoiceCloneGateFinding(
                    "error",
                    "reference_audio_too_large",
                    f"reference audio exceeds {MAX_REFERENCE_AUDIO_BYTES} bytes: {path}",
                ))
        except OSError as exc:
            findings.append(VoiceCloneGateFinding("error", "reference_audio_unreadable", f"reference audio cannot be inspected: {path}: {exc}"))

    output_label = str(manifest.get("output_label") or "").strip().lower()
    if output_label not in ALLOWED_OUTPUT_LABELS:
        findings.append(VoiceCloneGateFinding(
            "error",
            "invalid_output_label",
            f"output_label must be one of {sorted(ALLOWED_OUTPUT_LABELS)}",
        ))

    delivery = {str(item).strip() for item in _as_list(manifest.get("allowed_delivery")) if str(item).strip()}
    if not delivery:
        findings.append(VoiceCloneGateFinding("error", "missing_allowed_delivery", "allowed_delivery must include at least one delivery boundary"))
    unknown_delivery = sorted(delivery - ALLOWED_DELIVERY)
    if unknown_delivery:
        findings.append(VoiceCloneGateFinding("error", "invalid_allowed_delivery", f"unsupported delivery boundary: {unknown_delivery}"))
    if "telegram_voice" in delivery or "telegram_audio" in delivery:
        findings.append(VoiceCloneGateFinding(
            "error",
            "external_delivery_not_authorized",
            "Telegram/external delivery is not selectable in the clone gate; validate local_file first and request a separate send gate",
        ))

    purpose = str(manifest.get("purpose") or "").strip()
    if len(purpose) < 12:
        findings.append(VoiceCloneGateFinding("error", "missing_purpose", "purpose must describe the private prototype use case"))

    provider = str(manifest.get("provider") or "").strip().lower()
    if provider and provider != "voxcpm2":
        findings.append(VoiceCloneGateFinding("warning", "non_voxcpm2_provider", f"manifest provider is {provider!r}; policy is provider-agnostic but current task targets voxcpm2"))

    error_count = sum(1 for finding in findings if finding.level == "error")
    status = "selectable_for_local_clone_prototype" if error_count == 0 else "blocked"
    return VoiceCloneGateResult(ok=error_count == 0, status=status, findings=tuple(findings))


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser()
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("voice clone manifest must be a JSON object")
    return data


def validate_manifest_file(path: str | Path) -> VoiceCloneGateResult:
    manifest_path = Path(path).expanduser()
    return validate_voice_clone_manifest(load_manifest(manifest_path), base_dir=manifest_path.parent)


def render_template() -> dict[str, Any]:
    return {
        "provider": "voxcpm2",
        "mode": "voice_clone",
        "subject_type": "self",
        "explicit_consent": True,
        "consent_scope": [REQUIRED_CONSENT_SCOPE],
        "consent_record_path": "./consent.md",
        "reference_audio_paths": ["./reference.wav"],
        "output_label": "consented_voice_clone",
        "allowed_delivery": ["local_file"],
        "purpose": "Private Hermes local voice-clone prototype; no external delivery or default activation.",
    }


__all__ = [
    "VoiceCloneGateFinding",
    "VoiceCloneGateResult",
    "validate_voice_clone_manifest",
    "validate_manifest_file",
    "load_manifest",
    "render_template",
]
