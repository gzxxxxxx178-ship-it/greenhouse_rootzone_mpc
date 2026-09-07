from rootzone_mpc.controllers import (
    InternalModel,
    RuleController,
    RuleParameters,
    ZoneMPC,
    ZoneMPCParameters,
)


def test_rule_respects_pulse_bound():
    controller = RuleController(RuleParameters(0.205, 0.235, 4.0, 1, 1))
    assert controller.act(0.19) == 4.0
    assert controller.act(0.24) == 0.0


def test_mpc_action_is_from_feasible_candidates():
    controller = ZoneMPC(
        InternalModel(300.0, 0.86, 8.0, 0.275),
        ZoneMPCParameters(
            lower=0.20,
            upper=0.26,
            safety_lower=0.14,
            safety_upper=0.32,
            prediction_horizon=6,
            control_horizon=2,
            irrigation_candidates_mm=(0.0, 2.0, 4.0),
            max_step_irrigation_mm=4.0,
            dry_weight=8000.0,
            wet_weight=2500.0,
            water_weight=0.08,
            movement_weight=0.25,
            terminal_weight=2.0,
        ),
    )
    action = controller.act(0.18, [0.1] * 6)
    assert action in {0.0, 2.0, 4.0}
