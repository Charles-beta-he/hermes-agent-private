import json
from pathlib import Path


def test_enqueue_pending_delivery_is_idempotent_and_preserves_payload(tmp_path, monkeypatch):
    from cron import delivery_queue

    queue_file = tmp_path / "cron" / "pending_deliveries.json"
    receipts_file = tmp_path / "cron" / "delivery_receipts.jsonl"
    monkeypatch.setattr(delivery_queue, "PENDING_DELIVERIES_FILE", queue_file)
    monkeypatch.setattr(delivery_queue, "DELIVERY_RECEIPTS_FILE", receipts_file)

    job = {"id": "job-1", "name": "daily"}
    target = {"platform": "telegram", "chat_id": "123", "thread_id": "77"}

    first = delivery_queue.enqueue_pending_delivery(
        job=job,
        target=target,
        content="report body",
        error="Timed out",
        output_file="/tmp/out.md",
    )
    second = delivery_queue.enqueue_pending_delivery(
        job=job,
        target=target,
        content="report body",
        error="Timed out again",
        output_file="/tmp/out.md",
    )

    assert first["id"] == second["id"]
    items = delivery_queue.list_pending_deliveries()
    assert len(items) == 1
    assert items[0]["status"] == "pending"
    assert items[0]["job_id"] == "job-1"
    assert items[0]["target"] == target
    assert items[0]["content"] == "report body"
    assert items[0]["attempts"] == 0
    assert items[0]["last_error"] == "Timed out again"


def test_drain_pending_deliveries_records_receipt_and_removes_success(tmp_path, monkeypatch):
    from cron import delivery_queue

    queue_file = tmp_path / "cron" / "pending_deliveries.json"
    receipts_file = tmp_path / "cron" / "delivery_receipts.jsonl"
    monkeypatch.setattr(delivery_queue, "PENDING_DELIVERIES_FILE", queue_file)
    monkeypatch.setattr(delivery_queue, "DELIVERY_RECEIPTS_FILE", receipts_file)

    delivery_queue.enqueue_pending_delivery(
        job={"id": "job-1", "name": "daily"},
        target={"platform": "telegram", "chat_id": "123", "thread_id": None},
        content="report body",
        error="network down",
        output_file="/tmp/out.md",
    )

    calls = []

    def sender(item):
        calls.append(item)
        return {"success": True, "message_id": "42"}

    receipts = delivery_queue.drain_pending_deliveries(sender=sender, limit=5)

    assert len(calls) == 1
    assert calls[0]["content"] == "report body"
    assert receipts[0]["status"] == "sent_verified"
    assert receipts[0]["message_id"] == "42"
    assert delivery_queue.list_pending_deliveries() == []
    persisted_receipts = [json.loads(line) for line in receipts_file.read_text().splitlines()]
    assert persisted_receipts[0]["pending_delivery_id"] == receipts[0]["pending_delivery_id"]


def test_drain_pending_deliveries_keeps_failed_item_with_attempt_count(tmp_path, monkeypatch):
    from cron import delivery_queue

    queue_file = tmp_path / "cron" / "pending_deliveries.json"
    receipts_file = tmp_path / "cron" / "delivery_receipts.jsonl"
    monkeypatch.setattr(delivery_queue, "PENDING_DELIVERIES_FILE", queue_file)
    monkeypatch.setattr(delivery_queue, "DELIVERY_RECEIPTS_FILE", receipts_file)

    delivery_queue.enqueue_pending_delivery(
        job={"id": "job-1", "name": "daily"},
        target={"platform": "telegram", "chat_id": "123"},
        content="report body",
        error="network down",
    )

    receipts = delivery_queue.drain_pending_deliveries(
        sender=lambda item: {"success": False, "error": "still down"},
        limit=5,
    )

    assert receipts[0]["status"] == "send_failed"
    items = delivery_queue.list_pending_deliveries()
    assert len(items) == 1
    assert items[0]["attempts"] == 1
    assert items[0]["last_error"] == "still down"
