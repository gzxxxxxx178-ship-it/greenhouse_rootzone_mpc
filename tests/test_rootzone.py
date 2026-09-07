from rootzone_mpc.models import PlantParameters, RootZonePlant


def parameters() -> PlantParameters:
    return PlantParameters(
        root_depth_mm=300.0,
        field_capacity=0.28,
        wilting_point=0.12,
        saturation=0.40,
        irrigation_efficiency=0.9,
        drainage_coefficient=18.0,
        et_stress_start=0.18,
    )


def test_irrigation_increases_storage_when_et_is_zero():
    plant = RootZonePlant(parameters(), initial_theta=0.20, seed=1)
    result = plant.step(irrigation_command_mm=3.0, et_demand_mm=0.0)
    assert result.theta_true > 0.20
    assert abs(result.water_balance_residual_mm) < 1e-10


def test_dry_soil_limits_actual_et():
    plant = RootZonePlant(parameters(), initial_theta=0.13, seed=1)
    result = plant.step(irrigation_command_mm=0.0, et_demand_mm=1.0)
    assert 0.0 <= result.et_actual_mm < 1.0
