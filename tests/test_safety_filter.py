from rootzone_mpc.supervision import IrrigationSafetyFilter, IrrigationSafetyFilterConfig


def test_wet_guard_blocks_irrigation():
    guard = IrrigationSafetyFilter(IrrigationSafetyFilterConfig(0.30, 3, 4.0))
    assert guard.apply(2.0, 0.30) == (0.0, "wet_guard")


def test_rolling_budget_caps_recent_irrigation():
    guard = IrrigationSafetyFilter(IrrigationSafetyFilterConfig(0.30, 3, 4.0))
    assert guard.apply(2.0, 0.20)[0] == 2.0
    assert guard.apply(2.0, 0.20)[0] == 2.0
    assert guard.apply(2.0, 0.20) == (0.0, "rolling_budget")
    assert guard.apply(2.0, 0.20)[0] == 2.0
