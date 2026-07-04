import pytest

from passsh import ratelimit


def test_check_passes_with_no_prior_failures(tmp_path):
    vault = tmp_path / "vault.json"
    ratelimit.check(vault)  # must not raise


def test_failure_triggers_lockout(tmp_path):
    vault = tmp_path / "vault.json"
    ratelimit.record_failure(vault)

    with pytest.raises(ratelimit.RateLimitedError):
        ratelimit.check(vault)


def test_backoff_grows_with_repeated_failures(tmp_path):
    vault = tmp_path / "vault.json"

    delays = []
    for _ in range(4):
        ratelimit.record_failure(vault)
        try:
            ratelimit.check(vault)
        except ratelimit.RateLimitedError as exc:
            delays.append(exc.retry_after)

    assert delays == sorted(delays)
    assert delays[-1] > delays[0]


def test_backoff_is_capped(tmp_path):
    vault = tmp_path / "vault.json"
    for _ in range(20):
        ratelimit.record_failure(vault)

    with pytest.raises(ratelimit.RateLimitedError) as exc_info:
        ratelimit.check(vault)
    assert exc_info.value.retry_after <= ratelimit.MAX_DELAY


def test_success_clears_lockout(tmp_path):
    vault = tmp_path / "vault.json"
    ratelimit.record_failure(vault)
    ratelimit.record_success(vault)

    ratelimit.check(vault)  # must not raise
