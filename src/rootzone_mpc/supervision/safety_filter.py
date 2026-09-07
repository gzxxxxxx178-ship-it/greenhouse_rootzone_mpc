from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class IrrigationSafetyFilterConfig:
    wet_guard_threshold: float
    rolling_window_steps: int
    rolling_budget_mm: float

    def __post_init__(self) -> None:
        if self.rolling_window_steps < 1 or self.rolling_budget_mm < 0:
            raise ValueError("Rolling window must be positive and budget nonnegative")


class IrrigationSafetyFilter:
    def __init__(self, config: IrrigationSafetyFilterConfig):
        self.config = config
        self.history: deque[float] = deque(maxlen=max(config.rolling_window_steps - 1, 0))

    def apply(self, requested_mm: float, wetness_indicator: float) -> tuple[float, str]:
        requested = max(float(requested_mm), 0.0)
        if wetness_indicator >= self.config.wet_guard_threshold:
            delivered = 0.0
            reason = "wet_guard"
        else:
            remaining = max(self.config.rolling_budget_mm - sum(self.history), 0.0)
            delivered = min(requested, remaining)
            reason = "rolling_budget" if delivered < requested else "accepted"
        self.history.append(delivered)
        return delivered, reason
