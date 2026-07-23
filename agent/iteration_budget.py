"""Thread-safe local and optionally durable model-call budgets.

``IterationBudget(max_total)`` preserves the original in-memory per-turn API.
When ``backend`` and ``budget_id`` are supplied, reservations are delegated to
SessionDB so a cumulative execution-graph budget survives compression and
process resume and remains atomic across concurrent descendants.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Optional


class IterationBudget:
    """Thread-safe counter with an optional durable reservation backend."""

    def __init__(
        self,
        max_total: int,
        *,
        backend: Any = None,
        budget_id: Optional[str] = None,
    ):
        self.max_total = max(0, int(max_total))
        self.budget_id = budget_id or ""
        self._backend = backend
        self._used = 0
        self._lock = threading.Lock()
        self._backend_error: Optional[str] = None
        self._reservation_ids: list[str] = []
        self._owner_id = uuid.uuid4().hex
        if self._backend is not None and self.budget_id:
            try:
                used, effective_max = self._backend.ensure_execution_graph_budget(
                    self.budget_id, self.max_total
                )
                self._used = int(used)
                self.max_total = int(effective_max)
            except Exception as exc:
                # Fail closed on the first consume; construction itself stays
                # compatible with frontends that initialize before SQLite is ready.
                self._backend_error = f"{type(exc).__name__}: {exc}"

    @property
    def persistence_error(self) -> Optional[str]:
        return self._backend_error

    @property
    def is_persistent(self) -> bool:
        return self._backend is not None and bool(self.budget_id)

    def fork_handle(self) -> "IterationBudget":
        """Return a receipt-isolated handle over the same non-expanding ledger.

        Durable graph capacity is shared by ``budget_id`` in the backend, while
        reservation ownership remains local to each agent/consumer.  For the
        legacy in-memory mode there is no backend ledger to coordinate forks,
        so returning ``self`` is the only non-expanding behavior.
        """
        with self._lock:
            if not self.is_persistent:
                return self
            return type(self)(
                self.max_total,
                backend=self._backend,
                budget_id=self.budget_id,
            )

    def consume(self) -> bool:
        """Atomically reserve one call. Persistence failure denies admission."""
        with self._lock:
            if self.is_persistent:
                try:
                    reserved = self._backend.reserve_execution_graph_call(
                        self.budget_id, self.max_total, self._owner_id
                    )
                except Exception as exc:
                    self._backend_error = f"{type(exc).__name__}: {exc}"
                    return False
                self._backend_error = None
                if reserved is None:
                    try:
                        used, effective_max = self._backend.get_execution_graph_budget(
                            self.budget_id
                        )
                        self._used = int(used)
                        self.max_total = int(effective_max)
                    except Exception:
                        pass
                    return False
                used, reservation_id = reserved
                self._used = int(used)
                self._reservation_ids.append(str(reservation_id))
                return True

            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def commit_reservation(self) -> bool:
        """Commit the latest receipt before dispatch, retaining it on failure."""
        with self._lock:
            if not self.is_persistent:
                return True
            if not self._reservation_ids:
                return True
            reservation_id = self._reservation_ids[-1]
            try:
                committed = self._backend.commit_execution_graph_call(
                    self.budget_id, reservation_id, self._owner_id
                )
                if not committed:
                    self._backend_error = (
                        "reservation commit rejected: receipt is not reserved"
                    )
                    return False
                self._reservation_ids.pop()
                self._backend_error = None
                return True
            except Exception as exc:
                # This method runs before provider dispatch. Keep the owned
                # receipt so a caller can retry/reconcile it; never dispatch on
                # an unconfirmed durable commit.
                self._backend_error = f"{type(exc).__name__}: {exc}"
                return False

    def refund(self) -> None:
        """Give back a reservation only when no accountable call was dispatched."""
        with self._lock:
            if self.is_persistent:
                if not self._reservation_ids:
                    return
                reservation_id = self._reservation_ids.pop()
                try:
                    self._used = int(
                        self._backend.refund_execution_graph_call(
                            self.budget_id, reservation_id, self._owner_id
                        )
                    )
                    self._backend_error = None
                except Exception as exc:
                    # A failed refund must remain charged and retryable; never
                    # decrement only the local mirror or lose ownership receipt.
                    self._reservation_ids.append(reservation_id)
                    self._backend_error = f"{type(exc).__name__}: {exc}"
                return
            if self._used > 0:
                self._used -= 1

    def finalize(self, reason: str) -> bool:
        """Persist a durable graph terminal reason when no receipt is live."""
        with self._lock:
            if not self.is_persistent:
                return False
            try:
                finalized = self._backend.finalize_execution_graph_budget(
                    self.budget_id, reason
                )
                self._backend_error = None
                return bool(finalized)
            except Exception as exc:
                self._backend_error = f"{type(exc).__name__}: {exc}"
                return False

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


__all__ = ["IterationBudget"]
