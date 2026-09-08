import numpy as np

from rootzone_mpc.models.two_layer_greybox import (
    TwoLayerGreyBox,
    bounded_least_squares,
    vapor_pressure_deficit_kpa,
)


def test_bounded_least_squares_recovers_feasible_coefficients():
    design = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0]])
    target = design @ np.array([0.25, -0.4])
    fit = bounded_least_squares(
        design, target, np.array([0.0, -1.0]), np.array([1.0, 0.0]),
        max_iterations=1000, tolerance=1e-12,
    )
    assert fit.converged
    np.testing.assert_allclose(fit.coefficients, [0.25, -0.4], atol=1e-9)


def test_bounded_least_squares_clips_active_constraint():
    design = np.array([[1.0], [2.0], [3.0]])
    target = np.array([-1.0, -2.0, -3.0])
    fit = bounded_least_squares(
        design, target, np.array([0.0]), np.array([2.0]),
        max_iterations=10, tolerance=1e-12,
    )
    assert fit.converged
    assert fit.coefficients[0] == 0.0


def test_model_applies_irrigation_with_nonnegative_response():
    model = TwoLayerGreyBox(
        shallow_delta_coefficients=np.array([-0.1, 0.02, -0.01, -0.01, 0.0]),
        deep_delta_coefficients=np.array([0.1, 0.01, -0.005, -0.005, 0.0]),
    )
    dry = model.predict_next(0.22, 0.21, 0.0, 300.0, 1.0)
    irrigated = model.predict_next(0.22, 0.21, 2.0, 300.0, 1.0)
    assert irrigated[0] >= dry[0]
    assert irrigated[1] >= dry[1]


def test_vapor_pressure_deficit_increases_as_humidity_falls():
    temperature = np.array([25.0, 25.0])
    values = vapor_pressure_deficit_kpa(temperature, np.array([80.0, 40.0]))
    assert values[1] > values[0] > 0
