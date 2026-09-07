from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rootzone_mpc.supervision.supervisor import SupervisorSignals


@dataclass(frozen=True)
class RiskScale:
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low < 0.0 or self.high <= self.low:
            raise ValueError("Risk scale must satisfy 0 <= low < high")

    def normalize(self, value: float) -> float:
        return float(np.clip((value - self.low) / (self.high - self.low), 0.0, 1.0))


@dataclass(frozen=True)
class RiskMonitorConfig:
    observation_rate_scale: RiskScale
    model_residual_scale: RiskScale
    execution_error_scale: RiskScale
    residual_ewma_alpha: float
    maximum_observation_age_steps: int
    valid_theta_min: float
    valid_theta_max: float


class OnlineRiskMonitor:
    def __init__(self, config: RiskMonitorConfig):
        self.config = config
        self.previous_observation: float | None = None
        self.residual_ewma = 0.0

    def update(
        self,
        observation: float | None,
        predicted_observation: float,
        observation_age_steps: int,
        irrigation_command_mm: float,
        delivered_feedback_mm: float | None,
        solver_success: bool,
        actuator_available: bool,
        feedback_consistent: bool,
    ) -> SupervisorSignals:
        in_range = observation is not None and (
            self.config.valid_theta_min <= observation <= self.config.valid_theta_max
        )
        observation_valid = bool(
            in_range and observation_age_steps <= self.config.maximum_observation_age_steps
        )
        if observation is None:
            rate = 0.0
            residual = self.residual_ewma
        else:
            rate = (
                0.0
                if self.previous_observation is None
                else abs(observation - self.previous_observation)
            )
            residual = abs(observation - predicted_observation)
            alpha = self.config.residual_ewma_alpha
            self.residual_ewma = alpha * residual + (1.0 - alpha) * self.residual_ewma
            self.previous_observation = observation

        freshness_risk = float(
            np.clip(
                observation_age_steps / max(self.config.maximum_observation_age_steps, 1),
                0.0,
                1.0,
            )
        )
        observation_risk = max(
            freshness_risk,
            self.config.observation_rate_scale.normalize(rate),
            0.0 if in_range else 1.0,
        )
        model_risk = self.config.model_residual_scale.normalize(self.residual_ewma)

        if delivered_feedback_mm is None:
            execution_error = 0.0
            feedback_consistent = False
        else:
            denominator = max(abs(irrigation_command_mm), 1.0)
            execution_error = abs(delivered_feedback_mm - irrigation_command_mm) / denominator
        execution_risk = self.config.execution_error_scale.normalize(execution_error)
        return SupervisorSignals(
            observation_risk=observation_risk,
            model_risk=model_risk,
            execution_risk=execution_risk,
            observation_valid=observation_valid,
            solver_success=solver_success,
            actuator_available=actuator_available,
            feedback_consistent=feedback_consistent,
        )
