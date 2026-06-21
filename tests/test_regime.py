import regime


def _rising(n=260, start=100.0, step=0.1):
    return [start + i * step for i in range(n)]


def _falling(n=260, start=200.0, step=0.1):
    return [start - i * step for i in range(n)]


def test_green_no_signals(config):
    r = regime.assess(_rising(), vix=15.0, breadth=0.60, config=config)
    assert r.light == regime.GREEN
    assert r.tripped == []
    assert r.is_red is False


def test_yellow_one_signal(config):
    # Only breadth below floor.
    r = regime.assess(_rising(), vix=15.0, breadth=0.30, config=config)
    assert r.light == regime.YELLOW
    assert r.tripped == ["breadth_below_floor"]


def test_red_two_or_more(config):
    # SPY below 200DMA (falling series) + VIX high + SPY falling.
    r = regime.assess(_falling(), vix=35.0, breadth=0.55, config=config)
    assert r.signals["spy_below_200dma"] is True
    assert r.signals["vix_high_and_spy_falling"] is True
    assert r.light == regime.RED
    assert r.is_red is True


def test_vix_high_but_spy_not_falling_does_not_trip(config):
    r = regime.assess(_rising(), vix=40.0, breadth=0.60, config=config)
    assert r.signals["vix_high_and_spy_falling"] is False
    assert r.light == regime.GREEN
