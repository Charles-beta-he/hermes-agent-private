from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.voice_clone_policy import validate_manifest_file, validate_voice_clone_manifest


def test_voice_clone_manifest_blocks_missing_consent() -> None:
    result = validate_voice_clone_manifest({"mode": "voice_clone", "reference_audio_paths": []})

    assert result.ok is False
    codes = {finding.code for finding in result.findings}
    assert "missing_explicit_consent" in codes
    assert "missing_tts_voice_clone_scope" in codes
    assert "missing_consent_record_path" in codes
    assert "missing_reference_audio" in codes


def test_voice_clone_manifest_blocks_external_delivery(tmp_path: Path) -> None:
    consent = tmp_path / "consent.md"
    consent.write_text("I consent to this private local TTS voice clone prototype.", encoding="utf-8")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF....WAVEfmt ")

    result = validate_voice_clone_manifest(
        {
            "provider": "voxcpm2",
            "mode": "voice_clone",
            "subject_type": "self",
            "explicit_consent": True,
            "consent_scope": ["tts_voice_clone"],
            "consent_record_path": str(consent),
            "reference_audio_paths": [str(reference)],
            "output_label": "consented_voice_clone",
            "allowed_delivery": ["local_file", "telegram_voice"],
            "purpose": "Private Hermes local prototype with an external delivery attempt.",
        }
    )

    assert result.ok is False
    assert any(finding.code == "external_delivery_not_authorized" for finding in result.findings)


def test_voice_clone_manifest_allows_local_consented_clone(tmp_path: Path) -> None:
    consent = tmp_path / "consent.md"
    consent.write_text("I consent to use this recording for a private local TTS voice clone prototype.", encoding="utf-8")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF....WAVEfmt data")

    result = validate_voice_clone_manifest(
        {
            "provider": "voxcpm2",
            "mode": "voice_clone",
            "subject_type": "self",
            "explicit_consent": True,
            "consent_scope": ["tts_voice_clone"],
            "consent_record_path": str(consent),
            "reference_audio_paths": [str(reference)],
            "output_label": "consented_voice_clone",
            "allowed_delivery": ["local_file"],
            "purpose": "Private Hermes local voice clone prototype; no external delivery or default activation.",
        }
    )

    assert result.ok is True
    assert result.status == "selectable_for_local_clone_prototype"


def test_validate_manifest_file_resolves_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "consent.md").write_text("I consent to a private local prototype.", encoding="utf-8")
    (tmp_path / "reference.wav").write_bytes(b"RIFF....WAVEfmt data")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "provider": "voxcpm2",
                "mode": "voice_clone",
                "subject_type": "self",
                "explicit_consent": True,
                "consent_scope": ["tts_voice_clone"],
                "consent_record_path": "consent.md",
                "reference_audio_paths": ["reference.wav"],
                "output_label": "consented_voice_clone",
                "allowed_delivery": ["local_file"],
                "purpose": "Private Hermes local voice clone prototype; no external delivery or default activation.",
            }
        ),
        encoding="utf-8",
    )

    assert validate_manifest_file(manifest).ok is True
