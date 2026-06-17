"""TelegramAdapter send-path health gating after reconnect storms.

After sustained Bad Gateway / TimedOut reconnect cycles, the PTB httpx client
can enter a wedged state where ``bot.send_message()`` returns a valid Message
but nothing reaches the recipient.  ``_send_path_degraded`` short-circuits
``send()`` so cron's live-adapter branch falls through to standalone HTTP.
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return adapter


@pytest.mark.asyncio
async def test_send_succeeds_when_path_healthy():
    """Healthy adapter delivers normally; send_message is called."""
    adapter = _make_adapter()
    assert adapter._send_path_degraded is False

    result = await adapter.send("123", "hello")

    assert result.success is True
    adapter._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_short_circuits_when_path_degraded():
    """Degraded adapter returns failure WITHOUT calling send_message,
    so cron's live-adapter branch falls through to standalone HTTP."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "send_path_degraded"
    assert result.retryable is True
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_recovers_degraded_path_with_bounded_probe():
    """A transient degraded flag should not drop a reply after bot API recovery."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True
    adapter._app = MagicMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(return_value=MagicMock())

    result = await adapter.send("123", "hello")

    assert result.success is True
    assert adapter._send_path_degraded is False
    adapter._app.bot.get_me.assert_awaited_once()
    adapter._bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_keeps_degraded_flag_when_bounded_probe_fails():
    """If the bot API probe still fails, keep fallback semantics for callers."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True
    adapter._app = MagicMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(side_effect=OSError("proxy still down"))

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "send_path_degraded"
    assert result.retryable is True
    assert adapter._send_path_degraded is True
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_storm_clears_degraded_flag_after_immediate_probe(monkeypatch):
    """Once polling restarts and bot API is reachable, replies may send immediately."""
    adapter = _make_adapter()
    adapter._app = MagicMock()
    adapter._app.updater = MagicMock()
    adapter._app.updater.running = True
    adapter._app.updater.stop = AsyncMock()
    adapter._app.updater.start_polling = AsyncMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._polling_error_callback_ref = AsyncMock()
    monkeypatch.setattr(
        "gateway.platforms.telegram.Update", MagicMock(ALL_TYPES=[])
    )

    with patch("gateway.platforms.telegram.asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(OSError("Bad Gateway"))

    assert adapter._send_path_degraded is False
    adapter._app.bot.get_me.assert_awaited()


@pytest.mark.asyncio
async def test_reconnect_storm_keeps_degraded_flag_when_immediate_probe_fails(monkeypatch):
    """A resumed poller with an unhealthy bot API remains protected by heartbeat."""
    adapter = _make_adapter()
    adapter._app = MagicMock()
    adapter._app.updater = MagicMock()
    adapter._app.updater.running = True
    adapter._app.updater.stop = AsyncMock()
    adapter._app.updater.start_polling = AsyncMock()
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(side_effect=OSError("proxy still down"))
    adapter._polling_error_callback_ref = AsyncMock()
    monkeypatch.setattr(
        "gateway.platforms.telegram.Update", MagicMock(ALL_TYPES=[])
    )

    with patch("gateway.platforms.telegram.asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(OSError("Bad Gateway"))

    assert adapter._send_path_degraded is True


@pytest.mark.asyncio
async def test_heartbeat_still_clears_degraded_flag_after_late_recovery(monkeypatch):
    """Keep the existing late heartbeat recovery path for wedged reconnects."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True
    adapter._app = MagicMock()
    adapter._app.updater = MagicMock()
    adapter._app.updater.running = True
    adapter._app.bot = MagicMock()
    adapter._app.bot.get_me = AsyncMock(return_value=MagicMock())

    with patch("gateway.platforms.telegram.asyncio.sleep", new_callable=AsyncMock):
        await adapter._verify_polling_after_reconnect()

    assert adapter._send_path_degraded is False
