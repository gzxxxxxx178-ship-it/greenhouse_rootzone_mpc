from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np


@dataclass(frozen=True)
class InternalModel:
    root_depth_mm: float
    irrigation_efficiency: float
    drainage_coefficient: float
    field_capacity: float

    def predict_step(self, theta: float, irrigation_mm: float, et_mm: float) -> float:
        linear_drainage = self.drainage_coefficient * max(theta - self.field_capacity, 0.0)
        delta_mm = self.irrigation_efficiency * irrigation_mm - et_mm - linear_drainage
        return float(theta + delta_mm / self.root_depth_mm)


@dataclass(frozen=True)
class ZoneMPCParameters:
    lower: float
    upper: float
    safety_lower: float
    safety_upper: float
    prediction_horizon: int
    control_horizon: int
    irrigation_candidates_mm: tuple[float, ...]
    max_step_irrigation_mm: float
    dry_weight: float
    wet_weight: float
    water_weight: float
    movement_weight: float
    terminal_weight: float


class ZoneMPC:
    """Small deterministic zone MPC using enumerated short control sequences."""

    def __init__(self, model: InternalModel, parameters: ZoneMPCParameters):
        self.model = model
        self.p = parameters
        self.previous_action = 0.0

    def _sequence_cost(
        self, theta: float, sequence: tuple[float, ...], et_forecast_mm: list[float]
    ) -> float:
        predicted = theta
        previous = self.previous_action
        cost = 0.0
        last_action = sequence[-1]
        for step in range(self.p.prediction_horizon):
            action = sequence[step] if step < len(sequence) else last_action
            predicted = self.model.predict_step(predicted, action, et_forecast_mm[step])
            dry = max(self.p.lower - predicted, 0.0)
            wet = max(predicted - self.p.upper, 0.0)
            hard = max(self.p.safety_lower - predicted, 0.0) + max(
                predicted - self.p.safety_upper, 0.0
            )
            cost += self.p.dry_weight * dry**2 + self.p.wet_weight * wet**2
            cost += self.p.water_weight * action**2
            cost += self.p.movement_weight * (action - previous) ** 2
            cost += 1e8 * hard**2
            previous = action
        terminal_dry = max(self.p.lower - predicted, 0.0)
        terminal_wet = max(predicted - self.p.upper, 0.0)
        return float(cost + self.p.terminal_weight * (terminal_dry**2 + terminal_wet**2))

    def act(self, theta: float, et_forecast_mm: list[float]) -> float:
        if len(et_forecast_mm) < self.p.prediction_horizon:
            raise ValueError("ET forecast shorter than prediction horizon")
        candidates = tuple(
            value
            for value in self.p.irrigation_candidates_mm
            if 0.0 <= value <= self.p.max_step_irrigation_mm
        )
        if not candidates:
            raise ValueError("No feasible irrigation candidate")
        sequences = product(candidates, repeat=self.p.control_horizon)
        best = min(sequences, key=lambda seq: self._sequence_cost(theta, seq, et_forecast_mm))
        action = float(best[0])
        self.previous_action = action
        return action
