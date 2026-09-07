from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PlantParameters:
    root_depth_mm: float
    field_capacity: float
    wilting_point: float
    saturation: float
    irrigation_efficiency: float
    drainage_coefficient: float
    et_stress_start: float
    process_noise_std: float = 0.0
    sensor_noise_std: float = 0.0


@dataclass(frozen=True)
class StepResult:
    theta_true: float
    theta_measured: float
    irrigation_actual_mm: float
    et_actual_mm: float
    drainage_mm: float
    water_balance_residual_mm: float


class RootZonePlant:
    """Nonlinear bucket plant used only as the virtual evaluation object."""

    def __init__(self, parameters: PlantParameters, initial_theta: float, seed: int):
        self.p = parameters
        self.theta = float(initial_theta)
        self.rng = np.random.default_rng(seed)

    def measure(self) -> float:
        value = self.theta + self.rng.normal(0.0, self.p.sensor_noise_std)
        return float(np.clip(value, self.p.wilting_point * 0.7, self.p.saturation))

    def step(self, irrigation_command_mm: float, et_demand_mm: float) -> StepResult:
        theta_before = self.theta
        irrigation_actual = max(0.0, irrigation_command_mm) * self.p.irrigation_efficiency

        if theta_before >= self.p.et_stress_start:
            stress_factor = 1.0
        else:
            denominator = max(self.p.et_stress_start - self.p.wilting_point, 1e-9)
            stress_factor = np.clip(
                (theta_before - self.p.wilting_point) / denominator, 0.0, 1.0
            )
        et_actual = max(0.0, et_demand_mm) * float(stress_factor)

        excess = max(theta_before - self.p.field_capacity, 0.0)
        drainage = self.p.drainage_coefficient * excess**2
        deterministic_delta_mm = irrigation_actual - et_actual - drainage
        process_delta_theta = self.rng.normal(0.0, self.p.process_noise_std)
        theta_unclipped = (
            theta_before
            + deterministic_delta_mm / self.p.root_depth_mm
            + process_delta_theta
        )
        self.theta = float(
            np.clip(theta_unclipped, self.p.wilting_point * 0.5, self.p.saturation)
        )
        actual_storage_delta = (self.theta - theta_before) * self.p.root_depth_mm
        residual = actual_storage_delta - (
            deterministic_delta_mm + process_delta_theta * self.p.root_depth_mm
        )

        return StepResult(
            theta_true=self.theta,
            theta_measured=self.measure(),
            irrigation_actual_mm=float(irrigation_actual),
            et_actual_mm=float(et_actual),
            drainage_mm=float(drainage),
            water_balance_residual_mm=float(residual),
        )
