import json
from pathlib import Path

import numpy as np
import yaml

from rootzone_mpc.controllers.two_layer import (
    TwoLayerMPC,
    TwoLayerMPCParameters,
    TwoLayerRuleController,
    TwoLayerRuleParameters,
)
from rootzone_mpc.experiments.two_layer_control_development import (
    _load_fitted_model,
    build_two_layer_control_scenarios,
)
from rootzone_mpc.experiments.two_layer_bridge_data import load_protocol


ROOT = Path(__file__).resolve().parents[1]


def protocol():
    return load_protocol(ROOT / "configs/two_layer_control_development_v1.yaml")


def fitted_model():
    result = json.loads(
        (ROOT / "data/processed/two_layer_bridge_identification_v1.json").read_text()
    )
    return _load_fitted_model(result)


def test_two_layer_rule_starts_when_either_layer_is_dry():
    rule = TwoLayerRuleController(TwoLayerRuleParameters(
        start=(0.20, 0.21), stop=(0.23, 0.24), pulse_mm=0.5,
        minimum_on_steps=1, minimum_off_steps=1,
    ))
    assert rule.act((0.22, 0.205)) == 0.5
    assert rule.act((0.231, 0.241)) == 0.0


def test_two_layer_mpc_returns_only_frozen_candidate_actions():
    cfg = protocol()
    interface = cfg["controller_interface"]
    candidate = cfg["development_candidates"]["mpc"][0]
    controller = TwoLayerMPC(fitted_model(), TwoLayerMPCParameters(
        prediction_horizon_steps=cfg["time"]["prediction_horizon_steps"],
        control_horizon_steps=cfg["time"]["control_horizon_steps"],
        action_candidates_mm=tuple(interface["action_candidates_mm"]),
        maximum_step_command_mm=interface["maximum_step_command_mm"],
        nominal_command_to_delivered_efficiency=interface["nominal_command_to_delivered_efficiency"],
        target_lower_m3_m3=tuple(interface["target_lower_m3_m3"]),
        target_upper_m3_m3=tuple(interface["target_upper_m3_m3"]),
        safety_lower_m3_m3=tuple(interface["safety_lower_m3_m3"]),
        safety_upper_m3_m3=tuple(interface["safety_upper_m3_m3"]),
        deficit_weights=tuple(interface["deficit_weights"]),
        excess_weights=tuple(interface["excess_weights"]),
        hard_safety_penalty=float(interface["hard_safety_penalty"]),
        dry_weight=candidate["dry_weight"], wet_weight=candidate["wet_weight"],
        water_weight=candidate["water_weight"], movement_weight=candidate["movement_weight"],
        terminal_weight=candidate["terminal_weight"],
    ))
    horizon = cfg["time"]["prediction_horizon_steps"]
    action = controller.act((0.19, 0.20), np.zeros(horizon), np.full(horizon, 0.7))
    assert action in interface["action_candidates_mm"]


def test_two_layer_scenario_splits_are_independent_and_deterministic():
    first, first_audit = build_two_layer_control_scenarios(protocol())
    second, second_audit = build_two_layer_control_scenarios(protocol())
    assert first.equals(second)
    assert first_audit == second_audit
    assert first.groupby("split").size().to_dict() == {
        "development": 36, "locked_evaluation": 90,
    }
    assert set(first.loc[first["split"] == "development", "scenario_seed"]).isdisjoint(
        set(first.loc[first["split"] == "locked_evaluation", "scenario_seed"])
    )


def test_candidate_counts_do_not_exceed_frozen_limits():
    cfg = protocol()
    assert len(cfg["development_candidates"]["rule"]) <= cfg["development_design"]["rule_candidate_limit"]
    assert len(cfg["development_candidates"]["mpc"]) <= cfg["development_design"]["mpc_candidate_limit"]
