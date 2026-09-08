from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TwoLayerPlantParameters:
    shallow_depth_mm: float
    deep_depth_mm: float
    shallow_field_capacity: float
    deep_field_capacity: float
    shallow_wilting_point: float
    deep_wilting_point: float
    saturation: float
    irrigation_efficiency: float
    bypass_fraction: float
    shallow_et_fraction: float
    exchange_gradient_mm_per_step: float
    shallow_excess_drainage_mm_per_step: float
    deep_drainage_mm_per_step: float
    et_intercept_mm_per_step: float
    et_solar_coefficient_mm_per_w_m2_step: float
    et_vpd_coefficient_mm_per_kpa_step: float
    process_noise_sd_m3_m3: float
    measurement_noise_sd_m3_m3: float
    command_to_delivered_relative_sd: float


@dataclass(frozen=True)
class TwoLayerPlantStep:
    theta_shallow_true: float
    theta_deep_true: float
    theta_shallow_measured: float
    theta_deep_measured: float
    irrigation_command_mm: float
    irrigation_delivered_mm: float
    evapotranspiration_demand_mm: float
    evapotranspiration_shallow_mm: float
    evapotranspiration_deep_mm: float
    interlayer_flux_mm: float
    deep_drainage_mm: float
    water_balance_residual_mm: float


class TwoLayerRootZonePlant:
    """Independent nonlinear two-layer plant for synthetic closed-loop evaluation."""

    def __init__(
        self,
        parameters: TwoLayerPlantParameters,
        initial_theta_shallow: float,
        initial_theta_deep: float,
        seed: int,
    ):
        self.p = parameters
        self.theta_shallow = float(initial_theta_shallow)
        self.theta_deep = float(initial_theta_deep)
        self.rng = np.random.default_rng(seed)

    def weather_et_demand_mm(self, solar_w_m2: float, vpd_kpa: float) -> float:
        return float(max(
            0.0,
            self.p.et_intercept_mm_per_step
            + self.p.et_solar_coefficient_mm_per_w_m2_step * solar_w_m2
            + self.p.et_vpd_coefficient_mm_per_kpa_step * vpd_kpa,
        ))

    @staticmethod
    def _stress(theta: float, wilting: float, stress_start: float) -> float:
        if theta >= stress_start:
            return 1.0
        return float(np.clip((theta - wilting) / max(stress_start - wilting, 1e-12), 0, 1))

    def measure(self) -> tuple[float, float]:
        noise = self.rng.normal(0.0, self.p.measurement_noise_sd_m3_m3, 2)
        lower = min(self.p.shallow_wilting_point, self.p.deep_wilting_point) * 0.5
        return (
            float(np.clip(self.theta_shallow + noise[0], lower, self.p.saturation)),
            float(np.clip(self.theta_deep + noise[1], lower, self.p.saturation)),
        )

    def step(
        self, irrigation_command_mm: float, solar_w_m2: float, vpd_kpa: float
    ) -> TwoLayerPlantStep:
        p = self.p
        before_storage = (
            self.theta_shallow * p.shallow_depth_mm
            + self.theta_deep * p.deep_depth_mm
        )
        execution_factor = max(
            0.0, 1.0 + self.rng.normal(0.0, p.command_to_delivered_relative_sd)
        )
        delivered = max(0.0, irrigation_command_mm) * p.irrigation_efficiency * execution_factor
        shallow_input = delivered * (1.0 - p.bypass_fraction)
        deep_input = delivered * p.bypass_fraction

        et_demand = self.weather_et_demand_mm(solar_w_m2, vpd_kpa)
        shallow_stress = self._stress(
            self.theta_shallow, p.shallow_wilting_point,
            p.shallow_wilting_point + 0.45 * (
                p.shallow_field_capacity - p.shallow_wilting_point
            ),
        )
        deep_stress = self._stress(
            self.theta_deep, p.deep_wilting_point,
            p.deep_wilting_point + 0.45 * (p.deep_field_capacity - p.deep_wilting_point),
        )
        et_shallow = et_demand * p.shallow_et_fraction * shallow_stress
        et_deep = et_demand * (1.0 - p.shallow_et_fraction) * deep_stress

        gradient_flux = p.exchange_gradient_mm_per_step * (
            self.theta_shallow - self.theta_deep
        )
        excess_flux = p.shallow_excess_drainage_mm_per_step * max(
            self.theta_shallow - p.shallow_field_capacity, 0.0
        ) ** 2
        shallow_available = max(
            0.0,
            (self.theta_shallow - 0.5 * p.shallow_wilting_point) * p.shallow_depth_mm
            + shallow_input - et_shallow,
        )
        interlayer_candidate = gradient_flux + excess_flux
        if interlayer_candidate >= 0.0:
            interlayer = min(interlayer_candidate, shallow_available)
        else:
            upward_available = max(
                0.0,
                (self.theta_deep - 0.5 * p.deep_wilting_point) * p.deep_depth_mm
                + deep_input - et_deep,
            )
            interlayer = max(interlayer_candidate, -upward_available)

        deep_excess = max(self.theta_deep - p.deep_field_capacity, 0.0)
        drainage_candidate = p.deep_drainage_mm_per_step * deep_excess**2
        deep_available = max(
            0.0,
            (self.theta_deep - 0.5 * p.deep_wilting_point) * p.deep_depth_mm
            + deep_input + interlayer - et_deep,
        )
        deep_drainage = min(drainage_candidate, deep_available)

        process_noise = self.rng.normal(0.0, p.process_noise_sd_m3_m3, 2)
        shallow_storage = (
            self.theta_shallow * p.shallow_depth_mm
            + shallow_input - et_shallow - interlayer
            + process_noise[0] * p.shallow_depth_mm
        )
        deep_storage = (
            self.theta_deep * p.deep_depth_mm
            + deep_input + interlayer - et_deep - deep_drainage
            + process_noise[1] * p.deep_depth_mm
        )
        lower_shallow = 0.5 * p.shallow_wilting_point
        lower_deep = 0.5 * p.deep_wilting_point
        next_shallow = float(np.clip(
            shallow_storage / p.shallow_depth_mm, lower_shallow, p.saturation
        ))
        next_deep = float(np.clip(
            deep_storage / p.deep_depth_mm, lower_deep, p.saturation
        ))
        after_storage = next_shallow * p.shallow_depth_mm + next_deep * p.deep_depth_mm
        expected_delta = (
            delivered - et_shallow - et_deep - deep_drainage
            + process_noise[0] * p.shallow_depth_mm
            + process_noise[1] * p.deep_depth_mm
        )
        balance_residual = after_storage - before_storage - expected_delta
        self.theta_shallow = next_shallow
        self.theta_deep = next_deep
        measured_shallow, measured_deep = self.measure()
        return TwoLayerPlantStep(
            theta_shallow_true=next_shallow,
            theta_deep_true=next_deep,
            theta_shallow_measured=measured_shallow,
            theta_deep_measured=measured_deep,
            irrigation_command_mm=float(max(0.0, irrigation_command_mm)),
            irrigation_delivered_mm=float(delivered),
            evapotranspiration_demand_mm=et_demand,
            evapotranspiration_shallow_mm=float(et_shallow),
            evapotranspiration_deep_mm=float(et_deep),
            interlayer_flux_mm=float(interlayer),
            deep_drainage_mm=float(deep_drainage),
            water_balance_residual_mm=float(balance_residual),
        )
