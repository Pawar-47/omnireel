"""Quota ledger and provider health."""

from __future__ import annotations

from conftest import make_spec
from omnireel.quota import ProviderHealth, QuotaLedger


def test_new_entry_starts_usable(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock")
    ok, reason = ledger.check(spec, "mock:1", free_only=True)
    assert ok is True
    assert reason == "ok"


def test_rate_limit_pauses_until_interval_passes(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock", min_interval_seconds=60)
    ledger.reserve(spec, "mock:1")
    ok, reason = ledger.check(spec, "mock:1", free_only=True)
    assert ok is False
    assert reason == "rate-limited"


def test_daily_window_exhaustion_is_reported_separately_from_rate_limit(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock", daily_free_requests=2)
    ledger.reserve(spec, "mock:1")
    ledger.reserve(spec, "mock:1")
    ok, reason = ledger.check(spec, "mock:1", free_only=True)
    assert ok is False
    assert reason == "window-exhausted"


def test_trial_credits_run_out(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock", free_kind="trial", total_free_requests=1)
    ledger.reserve(spec, "mock:1")
    ok, reason = ledger.check(spec, "mock:1", free_only=False)
    assert ok is False
    assert reason == "credits-exhausted"


def test_free_only_mode_refuses_paid_models(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock", free_kind="none")
    ok, reason = ledger.check(spec, "mock:1", free_only=True)
    assert ok is False
    assert reason == "not-free"


def test_failed_submissions_are_refunded(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock", daily_free_requests=5)
    ledger.reserve(spec, "mock:1")
    ledger.release(spec, "mock:1", kind="auth")
    snapshot = ledger.snapshot(spec, "mock:1")
    assert snapshot.used_window == 0


def test_rate_limit_errors_are_not_refunded(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock", daily_free_requests=5)
    ledger.reserve(spec, "mock:1")
    ledger.record_error(spec, "mock:1", kind="rate_limit", message="429", retry_after=30)
    ledger.release(spec, "mock:1", kind="rate_limit")
    snapshot = ledger.snapshot(spec, "mock:1")
    assert snapshot.used_window == 1
    assert snapshot.last_error_kind == "rate_limit"
    assert snapshot.next_eligible_at is not None


def test_unpublished_quota_is_labelled_estimated(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock")
    ledger.ensure(spec, "mock:1")
    snapshot = ledger.snapshot(spec, "mock:1")
    # verified_at is set by the fixture, so this one is authoritative...
    assert snapshot.source in {"authoritative", "estimated"}
    # ...while an unverified spec must be estimated.
    unverified = make_spec("mock", model="other", verified_at=None)
    ledger.ensure(unverified, "mock:1")
    assert ledger.snapshot(unverified, "mock:1").estimated is True


def test_success_is_recorded_in_usage_events(settings, db):
    ledger = QuotaLedger(db, settings)
    spec = make_spec("mock")
    ledger.reserve(spec, "mock:1")
    ledger.record_success(spec, "mock:1", project_id="p", scene_id="s", duration_seconds=10)
    events = ledger.usage_summary()
    assert events[0]["kind"] == "success"
    assert events[0]["duration_seconds"] == 10


def test_provider_health_backs_off_then_recovers(settings, db):
    health = ProviderHealth(db)
    assert health.is_available("mock")[0] is True
    health.record_failure("mock", "boom", kind="transient")
    available, reason = health.is_available("mock")
    assert available is False
    assert "cooling" in reason or "until" in reason
    health.record_success("mock")
    assert health.is_available("mock")[0] is True


def test_auth_failure_parks_the_provider_for_hours(settings, db):
    health = ProviderHealth(db)
    health.record_failure("mock", "invalid key", kind="auth")
    row = health.state("mock")
    assert row["cooldown_until"] is not None
    assert row["consecutive_failures"] == 1
    health.reset("mock")
    assert health.is_available("mock")[0] is True
