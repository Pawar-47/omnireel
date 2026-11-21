"""Persistent quota, rate-limit and provider-health tracking.

Everything here is database-backed so that shutting the application down never
loses the picture: which key was used, what it produced, when it may be used
again, and what failed.

Two different limits are tracked deliberately:

* **rate limits** (requests per minute, minimum interval between calls) - the
  model is fine, it is simply too soon;
* **quota exhaustion** (daily window, promotional credits, trial grants) - the
  model is out until the window resets or credits are topped up.

Where a provider publishes a hard number it is stored as authoritative. Where it
does not, the ledger keeps its own estimate and marks ``source`` as
``estimated`` so the UI can say so. No figure here is invented: unset limits
stay ``NULL`` and mean "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .capabilities import ModelSpec
from .config import Settings
from .db import Database

#: Conservative default pause after an unexplained quota error (documented estimate).
QUOTA_PAUSE_SECONDS = 1800.0
#: Default pause after a 429 that carried no Retry-After header.
RATE_LIMIT_PAUSE_SECONDS = 60.0
#: Escalating backoff for repeated transport failures, in seconds.
BACKOFF_LADDER = (30.0, 120.0, 600.0, 1800.0, 3600.0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class QuotaSnapshot:
    provider: str
    model: str
    key_id: str
    kind: str
    limit_total: int | None
    used_total: int
    limit_window: int | None
    used_window: int
    window_seconds: int
    window_start: str | None
    remaining: int | None
    next_eligible_at: str | None
    promo_expires_at: str | None
    source: str
    last_success_at: str | None
    last_error: str | None
    last_error_kind: str | None

    @property
    def estimated(self) -> bool:
        return self.source == "estimated"

    def public_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "key_id": self.key_id,
            "kind": self.kind,
            "limit_total": self.limit_total,
            "used_total": self.used_total,
            "limit_window": self.limit_window,
            "used_window": self.used_window,
            "window_seconds": self.window_seconds,
            "remaining": self.remaining,
            "next_eligible_at": self.next_eligible_at,
            "promo_expires_at": self.promo_expires_at,
            "source": self.source,
            "estimated": self.estimated,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_error_kind": self.last_error_kind,
        }


class QuotaLedger:
    """Read/write access to the ``quotas`` table."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # -- rows ------------------------------------------------------------
    def _row(self, provider: str, model: str, key_id: str) -> dict | None:
        return self.db.one(
            "SELECT * FROM quotas WHERE provider=? AND model=? AND key_id=?",
            (provider, model, key_id),
        )

    def ensure(self, spec: ModelSpec, key_id: str) -> dict:
        """Create the ledger row for this model/key pair when missing."""
        row = self._row(spec.provider, spec.model, key_id)
        now = utcnow()
        if row is None:
            self.db.execute(
                """
                INSERT INTO quotas(provider, model, key_id, kind, limit_total, used_total,
                    limit_window, window_seconds, window_start, used_window,
                    min_interval_seconds, next_eligible_at, promo_expires_at, source,
                    updated_at)
                VALUES(?,?,?,?,?,0,?,?,?,0,?,?,?,?,?)
                ON CONFLICT(provider, model, key_id) DO NOTHING
                """,
                (
                    spec.provider,
                    spec.model,
                    key_id,
                    spec.free_kind,
                    spec.total_free_requests,
                    spec.daily_free_requests,
                    86400,
                    to_iso(now),
                    float(spec.min_interval_seconds),
                    None,
                    None,
                    "authoritative" if spec.verified_at else "estimated",
                    to_iso(now),
                ),
            )
            row = self._row(spec.provider, spec.model, key_id)
        assert row is not None
        return self._roll_window(row)

    def _roll_window(self, row: dict) -> dict:
        """Reset the daily counter once its window has elapsed."""
        window_start = from_iso(row.get("window_start")) or utcnow()
        window_seconds = int(row.get("window_seconds") or 86400)
        now = utcnow()
        if now - window_start >= timedelta(seconds=window_seconds):
            self.db.execute(
                "UPDATE quotas SET window_start=?, used_window=0, updated_at=? "
                "WHERE provider=? AND model=? AND key_id=?",
                (to_iso(now), to_iso(now), row["provider"], row["model"], row["key_id"]),
            )
            row = dict(row)
            row["window_start"] = to_iso(now)
            row["used_window"] = 0
        return row

    # -- queries ---------------------------------------------------------
    def snapshot(self, spec: ModelSpec, key_id: str) -> QuotaSnapshot:
        row = self.ensure(spec, key_id)
        limit_window = row.get("limit_window")
        used_window = int(row.get("used_window") or 0)
        limit_total = row.get("limit_total")
        used_total = int(row.get("used_total") or 0)

        remaining_candidates = []
        if limit_window is not None:
            remaining_candidates.append(max(0, int(limit_window) - used_window))
        if limit_total is not None:
            remaining_candidates.append(max(0, int(limit_total) - used_total))

        return QuotaSnapshot(
            provider=spec.provider,
            model=spec.model,
            key_id=key_id,
            kind=row.get("kind") or spec.free_kind,
            limit_total=limit_total,
            used_total=used_total,
            limit_window=limit_window,
            used_window=used_window,
            window_seconds=int(row.get("window_seconds") or 86400),
            window_start=row.get("window_start"),
            remaining=min(remaining_candidates) if remaining_candidates else None,
            next_eligible_at=row.get("next_eligible_at"),
            promo_expires_at=row.get("promo_expires_at"),
            source=row.get("source") or "estimated",
            last_success_at=row.get("last_success_at"),
            last_error=row.get("last_error"),
            last_error_kind=row.get("last_error_kind"),
        )

    def check(
        self,
        spec: ModelSpec,
        key_id: str,
        *,
        free_only: bool,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Can this model/key be used right now? Returns ``(ok, reason)``."""
        now = now or utcnow()
        if free_only and not spec.free_eligible:
            return False, "not-free"

        row = self.ensure(spec, key_id)
        if row.get("limit_total") is not None and int(row.get("used_total") or 0) >= int(
            row["limit_total"]
        ):
            return False, "credits-exhausted"
        if row.get("limit_window") is not None and int(row.get("used_window") or 0) >= int(
            row["limit_window"]
        ):
            return False, "window-exhausted"

        promo = from_iso(row.get("promo_expires_at"))
        if promo is not None and now >= promo:
            return False, "promo-expired"

        next_at = from_iso(row.get("next_eligible_at"))
        if next_at is not None and now < next_at:
            return False, "rate-limited"
        return True, "ok"

    # -- mutations -------------------------------------------------------
    def reserve(self, spec: ModelSpec, key_id: str) -> None:
        """Count one request against the ledger and start its rate-limit clock."""
        now = utcnow()
        interval = float(spec.min_interval_seconds or 0.0)
        self.ensure(spec, key_id)
        self.db.execute(
            """
            UPDATE quotas
               SET used_total = used_total + 1,
                   used_window = used_window + 1,
                   next_eligible_at = ?,
                   updated_at = ?
             WHERE provider=? AND model=? AND key_id=?
            """,
            (to_iso(now + timedelta(seconds=interval)), to_iso(now), spec.provider, spec.model, key_id),
        )

    def release(self, spec: ModelSpec, key_id: str, *, kind: str) -> None:
        """Give a reservation back when the provider never consumed it."""
        if kind in {"auth", "invalid_request", "unsupported", "not_found"}:
            give_back = True
        elif kind in {"transient", "unavailable"}:
            give_back = True
        else:  # quota / rate_limit / paid_required: the request really was spent or blocked
            give_back = False
        if not give_back:
            return
        now = utcnow()
        self.db.execute(
            """
            UPDATE quotas
               SET used_total = MAX(used_total - 1, 0),
                   used_window = MAX(used_window - 1, 0),
                   updated_at = ?
             WHERE provider=? AND model=? AND key_id=?
            """,
            (to_iso(now), spec.provider, spec.model, key_id),
        )

    def record_success(
        self,
        spec: ModelSpec,
        key_id: str,
        *,
        project_id: str | None = None,
        scene_id: str | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        now = utcnow()
        self.db.execute(
            """
            UPDATE quotas
               SET last_success_at = ?, last_error = NULL, last_error_kind = NULL,
                   next_eligible_at = ?, updated_at = ?
             WHERE provider=? AND model=? AND key_id=?
            """,
            (
                to_iso(now),
                to_iso(now + timedelta(seconds=float(spec.min_interval_seconds or 0.0))),
                to_iso(now),
                spec.provider,
                spec.model,
                key_id,
            ),
        )
        self._log(
            spec,
            key_id,
            kind="success",
            project_id=project_id,
            scene_id=scene_id,
            duration_seconds=duration_seconds,
        )

    def record_error(
        self,
        spec: ModelSpec,
        key_id: str,
        *,
        kind: str,
        message: str,
        retry_after: float | None = None,
        project_id: str | None = None,
        scene_id: str | None = None,
    ) -> None:
        now = utcnow()
        if kind == "rate_limit":
            pause = retry_after if retry_after and retry_after > 0 else RATE_LIMIT_PAUSE_SECONDS
        elif kind == "quota":
            pause = retry_after if retry_after and retry_after > 0 else QUOTA_PAUSE_SECONDS
        elif kind in {"auth", "paid_required"}:
            pause = 6 * 3600.0
        elif kind in {"transient", "unavailable"}:
            pause = retry_after if retry_after and retry_after > 0 else 60.0
        else:
            pause = 30.0

        self.ensure(spec, key_id)
        self.db.execute(
            """
            UPDATE quotas
               SET last_error = ?, last_error_kind = ?, next_eligible_at = ?, updated_at = ?
             WHERE provider=? AND model=? AND key_id=?
            """,
            (
                message[:600],
                kind,
                to_iso(now + timedelta(seconds=pause)),
                to_iso(now),
                spec.provider,
                spec.model,
                key_id,
            ),
        )
        self._log(
            spec,
            key_id,
            kind=kind,
            project_id=project_id,
            scene_id=scene_id,
            note=message[:300],
        )

    def _log(
        self,
        spec: ModelSpec,
        key_id: str,
        *,
        kind: str,
        project_id: str | None,
        scene_id: str | None,
        duration_seconds: float | None = None,
        note: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO usage_events(at, provider, model, key_id, project_id, scene_id, kind,
                                     duration_seconds, note)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                to_iso(utcnow()),
                spec.provider,
                spec.model,
                key_id,
                project_id,
                scene_id,
                kind,
                duration_seconds,
                note,
            ),
        )

    # -- reporting -------------------------------------------------------
    def for_models(self, specs: list[ModelSpec], key_ids: dict[str, list[str]]) -> list[dict]:
        rows: list[dict] = []
        for spec in specs:
            for key_id in key_ids.get(spec.provider, [f"{spec.provider}:none"]):
                rows.append(self.snapshot(spec, key_id).public_dict())
        return rows

    def usage_summary(self, limit: int = 200) -> list[dict]:
        return self.db.query(
            "SELECT * FROM usage_events ORDER BY id DESC LIMIT ?", (limit,)
        )


class ProviderHealth:
    """Per-provider circuit breaker, stored in ``provider_state``."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure(self, provider: str) -> dict:
        row = self.db.one("SELECT * FROM provider_state WHERE provider=?", (provider,))
        if row is None:
            self.db.execute(
                "INSERT INTO provider_state(provider) VALUES(?) "
                "ON CONFLICT(provider) DO NOTHING",
                (provider,),
            )
            row = self.db.one("SELECT * FROM provider_state WHERE provider=?", (provider,)) or {
                "provider": provider,
                "consecutive_failures": 0,
                "cooldown_until": None,
                "last_error": None,
                "last_success_at": None,
                "disabled": 0,
                "disabled_reason": None,
            }
        return row

    def state(self, provider: str) -> dict:
        return self.ensure(provider)

    def is_available(self, provider: str, now: datetime | None = None) -> tuple[bool, str]:
        now = now or utcnow()
        row = self.ensure(provider)
        if int(row.get("disabled") or 0):
            return False, row.get("disabled_reason") or "disabled"
        cooldown = from_iso(row.get("cooldown_until"))
        if cooldown is not None and now < cooldown:
            return False, f"cooling down until {to_iso(cooldown)}"
        return True, "ok"

    def record_success(self, provider: str) -> None:
        now = utcnow()
        self.ensure(provider)
        self.db.execute(
            "UPDATE provider_state SET consecutive_failures=0, cooldown_until=NULL, "
            "last_success_at=?, last_checked_at=? WHERE provider=?",
            (to_iso(now), to_iso(now), provider),
        )

    def record_failure(
        self,
        provider: str,
        message: str,
        *,
        kind: str,
        retry_after: float | None = None,
    ) -> None:
        now = utcnow()
        row = self.ensure(provider)
        failures = int(row.get("consecutive_failures") or 0) + 1
        if retry_after and retry_after > 0:
            pause = retry_after
        elif kind in {"auth", "paid_required"}:
            pause = 6 * 3600.0
        else:
            pause = BACKOFF_LADDER[min(failures - 1, len(BACKOFF_LADDER) - 1)]
        self.db.execute(
            "UPDATE provider_state SET consecutive_failures=?, cooldown_until=?, last_error=?, "
            "last_checked_at=? WHERE provider=?",
            (failures, to_iso(now + timedelta(seconds=pause)), message[:600], to_iso(now), provider),
        )

    def mark_disabled(self, provider: str, reason: str) -> None:
        self.ensure(provider)
        self.db.execute(
            "UPDATE provider_state SET disabled=1, disabled_reason=? WHERE provider=?",
            (reason[:300], provider),
        )

    def reset(self, provider: str) -> None:
        self.ensure(provider)
        self.db.execute(
            "UPDATE provider_state SET consecutive_failures=0, cooldown_until=NULL, "
            "disabled=0, disabled_reason=NULL WHERE provider=?",
            (provider,),
        )

    def all_states(self) -> dict[str, dict]:
        return {row["provider"]: row for row in self.db.query("SELECT * FROM provider_state")}
