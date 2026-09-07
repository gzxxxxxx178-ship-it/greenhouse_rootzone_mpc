from __future__ import annotations

from dataclasses import dataclass

from rootzone_mpc.controllers import InternalModel


@dataclass(frozen=True)
class StateEstimatorConfig:
    trusted_observation_gain: float
    uncertainty_growth_per_step: float
    uncertainty_reduction_factor: float
    maximum_uncertainty: float
    valid_theta_min: float
    valid_theta_max: float

    def __post_init__(self) -> None:
        if not 0.0 < self.trusted_observation_gain <= 1.0:
            raise ValueError("Observation gain must be in (0, 1]")
        if not 0.0 <= self.uncertainty_reduction_factor <= 1.0:
            raise ValueError("Uncertainty reduction factor must be in [0, 1]")
        if self.uncertainty_growth_per_step < 0 or self.maximum_uncertainty < 0:
            raise ValueError("Uncertainty parameters must be nonnegative")


class GuardedStateEstimator:
    def __init__(self, model: InternalModel, config: StateEstimatorConfig, initial_theta: float):
        self.model = model
        self.config = config
        self.theta = float(initial_theta)
        self.uncertainty = 0.0

    def assimilate(self, observation: float | None, trusted: bool) -> float:
        if observation is not None and trusted:
            gain = self.config.trusted_observation_gain
            self.theta = (1.0 - gain) * self.theta + gain * float(observation)
            self.uncertainty *= self.config.uncertainty_reduction_factor
        return self.theta

    def advance(self, delivered_irrigation_mm: float, et_estimate_mm: float) -> float:
        self.theta = self.model.predict_step(
            self.theta,
            max(float(delivered_irrigation_mm), 0.0),
            max(float(et_estimate_mm), 0.0),
        )
        self.theta = min(max(self.theta, self.config.valid_theta_min), self.config.valid_theta_max)
        self.uncertainty = min(
            self.uncertainty + self.config.uncertainty_growth_per_step,
            self.config.maximum_uncertainty,
        )
        return self.theta

    @property
    def conservative_theta(self) -> float:
        return max(self.theta - self.uncertainty, self.config.valid_theta_min)
