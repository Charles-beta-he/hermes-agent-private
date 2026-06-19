"""Tests for the read-only enterprise voice readiness profile."""

from tools.voice_enterprise_profile import (
    build_enterprise_voice_readiness,
    render_enterprise_voice_readiness,
)


def _requirements(**overrides):
    base = {
        "audio_available": True,
        "stt_available": True,
        "environment": {"available": True},
    }
    base.update(overrides)
    return base


def test_ready_profile_preserves_existing_interaction_logic():
    report = build_enterprise_voice_readiness(
        {
            "stt": {"provider": "local"},
            "tts": {"provider": "edge"},
            "voice": {"auto_tts": True, "beep_enabled": True},
        },
        _requirements(),
        platform="Darwin",
    )

    assert report["ready"] is True
    assert report["status"] == "ready"
    assert report["blockers"] == []
    assert report["interaction_logic"] == "unchanged:/voice on + record_key + VAD continuous loop"
    assert report["providers"] == {"stt": "local", "tts": "edge"}
    assert "no_interaction_change" in report["capabilities"]


def test_missing_audio_stt_and_tts_are_blockers():
    report = build_enterprise_voice_readiness(
        {"stt": {"provider": "none"}, "tts": {"provider": "none"}},
        _requirements(audio_available=False, stt_available=False),
    )

    assert report["ready"] is False
    assert report["status"] == "blocked"
    assert "audio_capture_missing" in report["blockers"]
    assert "stt_provider_missing" in report["blockers"]
    assert "tts_provider_missing" in report["blockers"]
    assert "vad_conversation_loop" in report["missing_capabilities"]
    assert "local_first_stt" in report["missing_capabilities"]
    assert "spoken_reply" in report["missing_capabilities"]


def test_auto_tts_and_beeps_are_warnings_not_blockers():
    report = build_enterprise_voice_readiness(
        {
            "stt": {"provider": "groq"},
            "tts": {"provider": "openai"},
            "voice": {"auto_tts": False, "beep_enabled": False},
        },
        _requirements(),
    )

    assert report["ready"] is True
    assert "spoken_reply_requires_/voice_tts_or_config_auto_tts" in report["warnings"]
    assert "audible_state_cues_disabled" in report["warnings"]
    assert "stt_not_local_first:groq" in report["warnings"]


def test_yolo_config_blocks_enterprise_profile():
    report = build_enterprise_voice_readiness(
        {
            "yolo": True,
            "stt": {"provider": "local"},
            "tts": {"provider": "edge"},
            "voice": {"auto_tts": True},
        },
        _requirements(),
    )

    assert report["ready"] is False
    assert "tool_governance_weakened_by_yolo" in report["blockers"]


def test_renderer_is_operator_readable():
    report = build_enterprise_voice_readiness(
        {"tts": {"provider": "edge"}, "voice": {"auto_tts": False}},
        _requirements(),
    )

    rendered = render_enterprise_voice_readiness(report)

    assert "Enterprise voice profile: READY" in rendered
    assert "Interaction logic: unchanged:/voice on" in rendered
    assert "Providers:" in rendered
    assert "Warnings:" in rendered
