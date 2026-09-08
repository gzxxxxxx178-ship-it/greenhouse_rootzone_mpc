from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BoundedFit:
    coefficients: np.ndarray
    iterations: int
    converged: bool


def bounded_least_squares(
    design: np.ndarray,
    target: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    max_iterations: int,
    tolerance: float,
) -> BoundedFit:
    design = np.asarray(design, dtype=float)
    target = np.asarray(target, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    if design.ndim != 2 or target.shape != (design.shape[0],):
        raise ValueError("design and target shapes are incompatible")
    if lower.shape != (design.shape[1],) or upper.shape != lower.shape:
        raise ValueError("bounds do not match feature count")
    if np.any(lower > upper):
        raise ValueError("lower bounds must not exceed upper bounds")
    if not np.isfinite(design).all() or not np.isfinite(target).all():
        raise ValueError("design and target must be finite")

    unconstrained, *_ = np.linalg.lstsq(design, target, rcond=None)
    coefficients = np.clip(unconstrained, lower, upper)
    residual = target - design @ coefficients
    column_energy = np.sum(design * design, axis=0)
    if np.any(column_energy == 0):
        raise ValueError("design contains a zero-energy feature")

    converged = False
    iteration = 0
    for iteration in range(1, max_iterations + 1):
        maximum_change = 0.0
        for index in range(design.shape[1]):
            column = design[:, index]
            old_value = coefficients[index]
            partial_target = residual + column * old_value
            new_value = float(np.clip(
                np.dot(column, partial_target) / column_energy[index],
                lower[index],
                upper[index],
            ))
            coefficients[index] = new_value
            residual += column * (old_value - new_value)
            maximum_change = max(maximum_change, abs(new_value - old_value))
        if maximum_change <= tolerance:
            converged = True
            break
    return BoundedFit(coefficients, iteration, converged)


@dataclass(frozen=True)
class TwoLayerGreyBox:
    shallow_delta_coefficients: np.ndarray
    deep_delta_coefficients: np.ndarray

    def predict_next(
        self,
        theta_shallow: float,
        theta_deep: float,
        irrigation_delivered_mm: float,
        solar_w_m2: float,
        vpd_kpa: float,
    ) -> tuple[float, float]:
        features = np.array([
            theta_shallow - theta_deep,
            irrigation_delivered_mm,
            solar_w_m2 / 1000.0,
            vpd_kpa,
            1.0,
        ])
        return (
            float(theta_shallow + features @ self.shallow_delta_coefficients),
            float(theta_deep + features @ self.deep_delta_coefficients),
        )


def vapor_pressure_deficit_kpa(temperature_c: np.ndarray, relative_humidity_pct: np.ndarray) -> np.ndarray:
    temperature_c = np.asarray(temperature_c, dtype=float)
    relative_humidity_pct = np.asarray(relative_humidity_pct, dtype=float)
    saturation_pressure = 0.6108 * np.exp(17.27 * temperature_c / (temperature_c + 237.3))
    return saturation_pressure * (1.0 - relative_humidity_pct / 100.0)
