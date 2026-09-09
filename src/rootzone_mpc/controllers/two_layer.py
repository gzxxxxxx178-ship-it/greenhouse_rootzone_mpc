from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np

from rootzone_mpc.models.two_layer_greybox import TwoLayerGreyBox


@dataclass(frozen=True)
class TwoLayerRuleParameters:
    start: tuple[float, float]
    stop: tuple[float, float]
    pulse_mm: float
    minimum_on_steps: int
    minimum_off_steps: int


class TwoLayerRuleController:
    def __init__(self, parameters: TwoLayerRuleParameters):
        self.p = parameters
        self.is_on = False
        self.steps_in_state = 0

    def act(
        self,
        theta: tuple[float, float],
        solar_forecast_w_m2: np.ndarray | None = None,
        vpd_forecast_kpa: np.ndarray | None = None,
    ) -> float:
        del solar_forecast_w_m2, vpd_forecast_kpa
        self.steps_in_state += 1
        if self.is_on:
            can_stop = all(value >= limit for value, limit in zip(theta, self.p.stop))
            if can_stop and self.steps_in_state >= self.p.minimum_on_steps:
                self.is_on = False
                self.steps_in_state = 0
        else:
            should_start = any(value <= limit for value, limit in zip(theta, self.p.start))
            if should_start and self.steps_in_state >= self.p.minimum_off_steps:
                self.is_on = True
                self.steps_in_state = 0
        return float(self.p.pulse_mm if self.is_on else 0.0)


@dataclass(frozen=True)
class TwoLayerMPCParameters:
    prediction_horizon_steps: int
    control_horizon_steps: int
    action_candidates_mm: tuple[float, ...]
    maximum_step_command_mm: float
    nominal_command_to_delivered_efficiency: float
    target_lower_m3_m3: tuple[float, float]
    target_upper_m3_m3: tuple[float, float]
    safety_lower_m3_m3: tuple[float, float]
    safety_upper_m3_m3: tuple[float, float]
    deficit_weights: tuple[float, float]
    excess_weights: tuple[float, float]
    dry_weight: float
    wet_weight: float
    water_weight: float
    movement_weight: float
    terminal_weight: float
    hard_safety_penalty: float


class TwoLayerMPC:
    def __init__(self, model: TwoLayerGreyBox, parameters: TwoLayerMPCParameters):
        self.model = model
        self.p = parameters
        self.previous_action = 0.0

    def _cost(
        self,
        theta: tuple[float, float],
        sequence: tuple[float, ...],
        solar_forecast_w_m2: np.ndarray,
        vpd_forecast_kpa: np.ndarray,
    ) -> float:
        state = np.asarray(theta, dtype=float)
        lower = np.asarray(self.p.target_lower_m3_m3)
        upper = np.asarray(self.p.target_upper_m3_m3)
        safety_lower = np.asarray(self.p.safety_lower_m3_m3)
        safety_upper = np.asarray(self.p.safety_upper_m3_m3)
        deficit_weights = np.asarray(self.p.deficit_weights)
        excess_weights = np.asarray(self.p.excess_weights)
        previous = self.previous_action
        cost = 0.0
        last_action = sequence[-1]
        for step in range(self.p.prediction_horizon_steps):
            action = sequence[step] if step < len(sequence) else last_action
            delivered = action * self.p.nominal_command_to_delivered_efficiency
            state = np.asarray(self.model.predict_next(
                state[0], state[1], delivered,
                float(solar_forecast_w_m2[step]), float(vpd_forecast_kpa[step]),
            ))
            dry = np.maximum(lower - state, 0.0)
            wet = np.maximum(state - upper, 0.0)
            safety = np.maximum(safety_lower - state, 0.0) + np.maximum(
                state - safety_upper, 0.0
            )
            cost += self.p.dry_weight * float(np.sum(deficit_weights * dry**2))
            cost += self.p.wet_weight * float(np.sum(excess_weights * wet**2))
            cost += self.p.water_weight * action**2
            cost += self.p.movement_weight * (action - previous) ** 2
            cost += self.p.hard_safety_penalty * float(np.sum(safety**2))
            previous = action
        terminal = np.maximum(lower - state, 0.0) + np.maximum(state - upper, 0.0)
        return float(cost + self.p.terminal_weight * np.sum(terminal**2))

    def act(
        self,
        theta: tuple[float, float],
        solar_forecast_w_m2: np.ndarray,
        vpd_forecast_kpa: np.ndarray,
    ) -> float:
        horizon = self.p.prediction_horizon_steps
        if len(solar_forecast_w_m2) < horizon or len(vpd_forecast_kpa) < horizon:
            raise ValueError("Weather forecast shorter than prediction horizon")
        candidates = tuple(
            value for value in self.p.action_candidates_mm
            if 0.0 <= value <= self.p.maximum_step_command_mm
        )
        if not candidates:
            raise ValueError("No feasible irrigation candidate")
        sequences = product(candidates, repeat=self.p.control_horizon_steps)
        best = min(
            sequences,
            key=lambda sequence: self._cost(
                theta, sequence, solar_forecast_w_m2, vpd_forecast_kpa
            ),
        )
        action = float(best[0])
        self.previous_action = action
        return action
