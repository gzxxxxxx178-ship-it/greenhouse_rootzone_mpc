from rootzone_mpc.controllers import InternalModel
from rootzone_mpc.supervision import GuardedStateEstimator, StateEstimatorConfig


def estimator() -> GuardedStateEstimator:
    return GuardedStateEstimator(
        InternalModel(300.0, 0.85, 15.0, 0.28),
        StateEstimatorConfig(0.5, 0.002, 0.25, 0.01, 0.08, 0.45),
        0.20,
    )


def test_untrusted_observation_cannot_move_estimate():
    state = estimator()
    assert state.assimilate(0.40, trusted=False) == 0.20


def test_conservative_state_drops_as_open_loop_uncertainty_grows():
    state = estimator()
    state.advance(0.0, 0.6)
    assert state.conservative_theta < state.theta
    previous_uncertainty = state.uncertainty
    state.assimilate(0.20, trusted=True)
    assert state.uncertainty < previous_uncertainty
