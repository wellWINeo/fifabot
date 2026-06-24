from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.models import Fill, Side
from core.risk import (
    RiskConfig,
    RiskOrder,
    RiskState,
    on_fill,
    on_mark,
    on_order_placed,
    pretrade_check,
    roll_day,
    suppress_market,
    trip,
)

_T0 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def _config(**overrides: object) -> RiskConfig:
    base: dict[str, object] = dict(
        max_position_usd=Decimal("5"),
        max_daily_loss_usd=Decimal("10"),
        max_orders_per_run=3,
        resubmit_window_seconds=60.0,
        max_orders_per_market_in_window=2,
    )
    base.update(overrides)
    return RiskConfig(**base)


def _order(notional: str = "1", market: str = "m") -> RiskOrder:
    return RiskOrder(market_id=market, notional=Decimal(notional))


def test_allows_clean_order() -> None:
    state = RiskState.start(_T0)
    new_state, result = pretrade_check(state, _config(), _order(), _T0)
    assert result.allowed is True
    assert new_state.halted is False


def test_global_halt_is_sticky_and_blocks_every_order() -> None:
    state = trip(RiskState.start(_T0), "boom")
    new_state, result = pretrade_check(state, _config(), _order(), _T0)
    assert result.allowed is False
    assert result.scope == "global"
    # tripping again does not change the original reason
    assert trip(state, "second").halt_reason == "boom"


def test_market_suppression_blocks_only_that_market() -> None:
    state = suppress_market(RiskState.start(_T0), "m", "no allowance")
    _, blocked = pretrade_check(state, _config(), _order(market="m"), _T0)
    _, allowed = pretrade_check(state, _config(), _order(market="other"), _T0)
    assert blocked.allowed is False and blocked.scope == "market"
    assert allowed.allowed is True


def test_position_cap_denies_without_global_halt() -> None:
    state = on_order_placed(RiskState.start(_T0), _order("4"), _T0)
    new_state, result = pretrade_check(state, _config(), _order("2"), _T0)
    assert result.allowed is False
    assert result.scope == "market"
    assert new_state.halted is False  # cap deny is not a kill


def test_order_count_ceiling_trips_global() -> None:
    state = RiskState.start(_T0)
    config = _config(max_orders_per_run=1, max_orders_per_market_in_window=99)
    state = on_order_placed(state, _order(market="a"), _T0)  # 1 placed
    new_state, result = pretrade_check(state, config, _order(market="b"), _T0)
    assert result.allowed is False and result.scope == "global"
    assert new_state.halted is True


def test_rapid_resubmission_trips_global() -> None:
    config = _config(max_orders_per_market_in_window=2, max_orders_per_run=99)
    state = RiskState.start(_T0)
    state = on_order_placed(state, _order(market="m"), _T0)
    state = on_order_placed(state, _order(market="m"), _T0 + timedelta(seconds=1))
    new_state, result = pretrade_check(
        state, config, _order(market="m"), _T0 + timedelta(seconds=2)
    )
    assert result.allowed is False and result.scope == "global"
    assert new_state.halted is True


def test_resubmission_window_expires() -> None:
    config = _config(max_orders_per_market_in_window=2, max_orders_per_run=99)
    state = RiskState.start(_T0)
    state = on_order_placed(state, _order(market="m"), _T0)
    state = on_order_placed(state, _order(market="m"), _T0 + timedelta(seconds=1))
    # two minutes later the window has cleared
    _, result = pretrade_check(
        state, config, _order(market="m"), _T0 + timedelta(seconds=120)
    )
    assert result.allowed is True


def test_on_fill_accrues_realized_and_releases_exposure() -> None:
    state = on_order_placed(RiskState.start(_T0), _order("4", market="m"), _T0)
    fill = Fill(
        side=Side.BUY_YES,
        entry_price=Decimal("0.40"),
        exit_price=Decimal("0.50"),
        shares=Decimal("10"),
        costs_usd=Decimal("0"),
    )
    new_state = on_fill(state, "m", fill, _T0)
    assert new_state.day_realized_pnl == Decimal("1.0")  # (0.50-0.40)*10
    assert new_state.exposure.get("m", Decimal(0)) == Decimal(0)


def test_on_fill_releases_exposure_correctly_for_buy_no() -> None:
    # exposure is recorded in YES-quote-price space (price * size) at admission
    # time; a BUY_NO fill's entry_price is the *token*-space price (1 - yes_price),
    # so releasing exposure must convert back to the space it was recorded in.
    state = on_order_placed(RiskState.start(_T0), _order("8", market="m"), _T0)
    fill = Fill(
        side=Side.BUY_NO,
        entry_price=Decimal("0.20"),  # yes-price 0.80 -> notional 0.80*10 = 8
        exit_price=Decimal("0.10"),
        shares=Decimal("10"),
        costs_usd=Decimal("0"),
    )
    new_state = on_fill(state, "m", fill, _T0)
    assert new_state.exposure.get("m", Decimal(0)) == Decimal(0)


def test_on_mark_trips_global_when_loss_exceeds_cap() -> None:
    config = _config(max_daily_loss_usd=Decimal("0.50"))
    state = RiskState.start(_T0)
    new_state = on_mark(state, config, Decimal("-0.75"), _T0)
    assert new_state.halted is True
    assert new_state.unrealized_pnl == Decimal("-0.75")


def test_on_mark_does_not_trip_within_cap() -> None:
    config = _config(max_daily_loss_usd=Decimal("0.50"))
    new_state = on_mark(RiskState.start(_T0), config, Decimal("-0.25"), _T0)
    assert new_state.halted is False


def test_roll_day_resets_realized_at_new_utc_day() -> None:
    state = RiskState.start(_T0).model_copy(
        update={"day_realized_pnl": Decimal("-3"), "unrealized_pnl": Decimal("-1")}
    )
    rolled = roll_day(state, _T0 + timedelta(days=1))
    assert rolled.day_realized_pnl == Decimal("0")
    assert rolled.unrealized_pnl == Decimal("0")
    assert rolled.day == (_T0 + timedelta(days=1)).date()


def test_roll_day_noop_same_day() -> None:
    state = RiskState.start(_T0).model_copy(update={"day_realized_pnl": Decimal("-3")})
    assert roll_day(state, _T0 + timedelta(hours=1)).day_realized_pnl == Decimal("-3")
