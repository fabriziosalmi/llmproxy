"""Numeric thresholds are validated, not discovered mid-request.

budget.daily_limit is compared with a float on every chat request. A quoted
number ("50", the same slip max_payload_size_kb was already guarded against)
passed startup and hot-reload validation, then raised TypeError inside the
request pipeline on each call.
"""

import pytest

from core.startup_checks import StartupError, validate_config
from tests.conftest import minimal_config


def _config(**sections):
    cfg = minimal_config(auth_enabled=False)
    cfg["server"]["auth"]["enabled"] = False
    cfg.update(sections)
    return cfg


def test_the_shipped_style_budget_is_accepted():
    validate_config(_config(budget={"daily_limit": 50.0, "soft_limit": 40.0}))


@pytest.mark.parametrize(
    "budget,key",
    [
        ({"daily_limit": "50"}, "budget.daily_limit"),
        ({"soft_limit": "40"}, "budget.soft_limit"),
        ({"daily_limit": True}, "budget.daily_limit"),
        ({"daily_limit": -1}, "budget.daily_limit"),
        ({"daily_limit": None}, "budget.daily_limit"),
        ({"daily_limit": [50]}, "budget.daily_limit"),
    ],
)
def test_a_bad_budget_value_is_refused_naming_the_key(budget, key):
    with pytest.raises(StartupError) as exc:
        validate_config(_config(budget=budget))
    assert key in str(exc.value)


def test_a_quoted_number_gets_the_remove_the_quotes_hint():
    with pytest.raises(StartupError, match="Remove the quotes"):
        validate_config(_config(budget={"daily_limit": "50"}))


def test_a_soft_limit_above_the_hard_cap_is_refused():
    with pytest.raises(StartupError, match="soft_limit"):
        validate_config(_config(budget={"daily_limit": 10, "soft_limit": 20}))


def test_a_soft_limit_alone_is_not_compared_with_a_default():
    validate_config(_config(budget={"soft_limit": 500}))


def test_zero_is_a_valid_budget_so_cost_can_be_capped_to_nothing():
    validate_config(_config(budget={"daily_limit": 0, "soft_limit": 0}))


@pytest.mark.parametrize(
    "section,key",
    [
        ({"circuit_breaker": {"failure_threshold": "5"}}, "circuit_breaker.failure_threshold"),
        ({"circuit_breaker": {"failure_threshold": 0}}, "circuit_breaker.failure_threshold"),
        ({"circuit_breaker": {"failure_threshold": 2.5}}, "circuit_breaker.failure_threshold"),
        ({"circuit_breaker": {"recovery_timeout": "60"}}, "circuit_breaker.recovery_timeout"),
        ({"circuit_breaker": {"recovery_timeout": 0}}, "circuit_breaker.recovery_timeout"),
        ({"rate_limiting": {"requests_per_minute": "60"}}, "rate_limiting.requests_per_minute"),
        ({"rate_limiting": {"requests_per_minute": 0}}, "rate_limiting.requests_per_minute"),
        ({"rate_limiting": {"burst": "10"}}, "rate_limiting.burst"),
        ({"caching": {"ttl": "3600"}}, "caching.ttl"),
    ],
)
def test_the_other_reload_thresholds_are_checked_too(section, key):
    with pytest.raises(StartupError) as exc:
        validate_config(_config(**section))
    assert key in str(exc.value)


def test_good_values_for_the_other_thresholds_pass():
    validate_config(
        _config(
            circuit_breaker={"failure_threshold": 5, "recovery_timeout": 60},
            rate_limiting={"enabled": True, "requests_per_minute": 120, "burst": 0},
            caching={"enabled": False, "ttl": 3600},
        )
    )


def test_an_integer_valued_float_is_a_valid_failure_threshold():
    validate_config(_config(circuit_breaker={"failure_threshold": 5.0}))
