import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig


def _make_adapter(now=1_700_000_000.0):
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="***",
        extra={"allowed_chats": [], "group_allowed_chats": [], "allowed_topics": []},
    )
    adapter._bot = SimpleNamespace(id=999, username="hermes_bot")
    adapter._message_handler = AsyncMock()
    adapter._mention_patterns = adapter._compile_mention_patterns()
    adapter._forum_lock = asyncio.Lock()
    adapter._forum_command_registered = set()
    adapter._dm_topics = {}
    adapter._active_sessions = {}
    adapter._pending_messages = {}
    adapter._is_callback_user_authorized = lambda user_id, **_kw: True
    adapter._live_locations = {}
    adapter._live_location_min_interval_seconds = 120.0
    adapter._live_location_min_distance_meters = 250.0
    adapter._live_location_now = lambda: now
    adapter.handle_message = AsyncMock()
    return adapter


def _location(*, lat=34.261, lon=108.942, live_period=None, accuracy=None, heading=None):
    return SimpleNamespace(
        latitude=lat,
        longitude=lon,
        live_period=live_period,
        horizontal_accuracy=accuracy,
        heading=heading,
        proximity_alert_radius=None,
    )


def _dm_location_message(
    *,
    message_id=77,
    lat=34.261,
    lon=108.942,
    live_period=None,
    accuracy=None,
    heading=None,
):
    return SimpleNamespace(
        message_id=message_id,
        text=None,
        caption=None,
        entities=[],
        caption_entities=[],
        message_thread_id=None,
        is_topic_message=False,
        chat=SimpleNamespace(id=123, type="private", title=None, full_name="Alice Example", is_forum=False),
        from_user=SimpleNamespace(id=123, full_name="Alice Example", first_name="Alice"),
        reply_to_message=None,
        date=None,
        location=_location(
            lat=lat,
            lon=lon,
            live_period=live_period,
            accuracy=accuracy,
            heading=heading,
        ),
        venue=None,
        sticker=None,
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=None,
    )


def _update(*, update_id=9001, message=None, edited=False):
    if edited:
        return SimpleNamespace(update_id=update_id, message=None, edited_message=message, effective_message=message)
    return SimpleNamespace(update_id=update_id, message=message, edited_message=None, effective_message=message)


def test_first_live_location_share_dispatches_one_metadata_event():
    async def _run():
        adapter = _make_adapter()
        msg = _dm_location_message(live_period=3600, accuracy=12.5, heading=91)

        await adapter._handle_location_message(_update(message=msg), SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert "[The user started sharing a live location.]" in event.text
        assert "live_period_seconds: 3600" in event.text
        assert "horizontal_accuracy_m: 12.5" in event.text
        assert "heading_degrees: 91" in event.text
        assert "latitude: 34.261" in event.text
        assert "longitude: 108.942" in event.text
        assert ("123", "77") in adapter._live_locations

    asyncio.run(_run())


def test_minor_live_location_edit_updates_state_without_dispatching_again():
    async def _run():
        now = 1_700_000_000.0
        adapter = _make_adapter(now=now)
        first = _dm_location_message(live_period=3600, lat=34.261, lon=108.942)
        await adapter._handle_location_message(_update(message=first), SimpleNamespace())
        adapter.handle_message.reset_mock()
        adapter._live_location_now = lambda: now + 30
        edited = _dm_location_message(message_id=77, live_period=3600, lat=34.2611, lon=108.9421)

        await adapter._handle_location_message(_update(update_id=9002, message=edited, edited=True), SimpleNamespace())

        adapter.handle_message.assert_not_awaited()
        state = adapter._live_locations[("123", "77")]
        assert state.latitude == 34.2611
        assert state.longitude == 108.9421
        assert state.update_count == 2

    asyncio.run(_run())


def test_live_location_edit_after_interval_dispatches_update_event():
    async def _run():
        now = 1_700_000_000.0
        adapter = _make_adapter(now=now)
        first = _dm_location_message(live_period=3600, lat=34.261, lon=108.942)
        await adapter._handle_location_message(_update(message=first), SimpleNamespace())
        adapter.handle_message.reset_mock()
        adapter._live_location_now = lambda: now + 121
        edited = _dm_location_message(message_id=77, live_period=3600, lat=34.2611, lon=108.9421)

        await adapter._handle_location_message(_update(update_id=9003, message=edited, edited=True), SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert "[Live location update.]" in event.text
        assert "update_count: 2" in event.text
        assert "latitude: 34.2611" in event.text

    asyncio.run(_run())


def test_live_location_edit_beyond_distance_threshold_dispatches_update_event():
    async def _run():
        now = 1_700_000_000.0
        adapter = _make_adapter(now=now)
        first = _dm_location_message(live_period=3600, lat=34.261, lon=108.942)
        await adapter._handle_location_message(_update(message=first), SimpleNamespace())
        adapter.handle_message.reset_mock()
        adapter._live_location_now = lambda: now + 30
        edited = _dm_location_message(message_id=77, live_period=3600, lat=34.271, lon=108.952)

        await adapter._handle_location_message(_update(update_id=9004, message=edited, edited=True), SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert "[Live location update.]" in event.text
        assert "moved_since_last_notification_m:" in event.text

    asyncio.run(_run())


def test_plain_location_pin_keeps_existing_prompt_shape():
    async def _run():
        adapter = _make_adapter()
        msg = _dm_location_message(live_period=None)

        await adapter._handle_location_message(_update(message=msg), SimpleNamespace())

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert "[The user shared a location pin.]" in event.text
        assert "Ask what they'd like to find nearby" in event.text
        assert "live_period_seconds" not in event.text

    asyncio.run(_run())
