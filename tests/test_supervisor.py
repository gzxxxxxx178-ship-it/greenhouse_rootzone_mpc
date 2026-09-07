import pytest

from rootzone_mpc.supervision import (
    ControlMode,
    SupervisorConfig,
    SupervisorSignals,
    TrustworthySupervisor,
    TrustworthySupervisorV2,
)


def config() -> SupervisorConfig:
    return SupervisorConfig(0.7, 0.4, minimum_dwell_steps=2, recovery_confirmation_steps=2)


def signals(**changes) -> SupervisorSignals:
    values = {
        "observation_risk": 0.1,
        "model_risk": 0.1,
        "execution_risk": 0.1,
        "observation_valid": True,
        "solver_success": True,
        "actuator_available": True,
        "feedback_consistent": True,
    }
    values.update(changes)
    return SupervisorSignals(**values)


def test_critical_execution_has_priority_over_rule_fallback():
    supervisor = TrustworthySupervisor(config())
    decision = supervisor.update(
        signals(solver_success=False, feedback_consistent=False)
    )
    assert decision.mode == ControlMode.SAFE_PAUSE
    assert decision.reason == "critical_observation_or_execution"


def test_solver_failure_falls_back_and_requires_confirmed_recovery():
    supervisor = TrustworthySupervisor(config())
    assert supervisor.update(signals(solver_success=False)).mode == ControlMode.RULE_FALLBACK
    assert supervisor.update(signals()).mode == ControlMode.RULE_FALLBACK
    assert supervisor.update(signals()).mode == ControlMode.MPC


def test_safe_pause_cannot_recover_directly_to_mpc():
    supervisor = TrustworthySupervisor(config())
    assert supervisor.update(signals(observation_valid=False)).mode == ControlMode.SAFE_PAUSE
    assert supervisor.update(signals()).mode == ControlMode.SAFE_PAUSE
    decision = supervisor.update(signals())
    assert decision.mode == ControlMode.RULE_FALLBACK
    assert decision.reason == "pause_recovery_confirmed"


def test_critical_signal_resets_partial_pause_recovery():
    supervisor = TrustworthySupervisor(config())
    supervisor.update(signals(observation_valid=False))
    supervisor.update(signals())
    supervisor.update(signals(feedback_consistent=False))
    assert supervisor.update(signals()).mode == ControlMode.SAFE_PAUSE
    assert supervisor.update(signals()).mode == ControlMode.RULE_FALLBACK


def test_threshold_hysteresis_is_validated():
    with pytest.raises(ValueError):
        SupervisorConfig(0.4, 0.7, 2, 2)


def test_v2_routes_bad_observation_to_estimated_fallback():
    supervisor = TrustworthySupervisorV2(config())
    decision = supervisor.update(signals(observation_valid=False))
    assert decision.mode == ControlMode.ESTIMATED_FALLBACK
    assert decision.reason == "estimate_driven_fallback_required"


def test_v2_reserves_safe_pause_for_unavailable_actuator():
    supervisor = TrustworthySupervisorV2(config())
    assert supervisor.update(signals(actuator_available=False)).mode == ControlMode.SAFE_PAUSE
    assert supervisor.update(signals()).mode == ControlMode.ESTIMATED_FALLBACK
