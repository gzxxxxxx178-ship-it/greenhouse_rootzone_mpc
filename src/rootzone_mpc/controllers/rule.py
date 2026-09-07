from dataclasses import dataclass


@dataclass(frozen=True)
class RuleParameters:
    start_threshold: float
    stop_threshold: float
    pulse_mm: float
    minimum_on_steps: int
    minimum_off_steps: int


class RuleController:
    def __init__(self, parameters: RuleParameters):
        self.p = parameters
        self.is_on = False
        self.steps_in_state = 0

    def act(self, theta: float, et_forecast_mm: list[float] | None = None) -> float:
        del et_forecast_mm
        self.steps_in_state += 1
        if self.is_on:
            if theta >= self.p.stop_threshold and self.steps_in_state >= self.p.minimum_on_steps:
                self.is_on = False
                self.steps_in_state = 0
        elif theta <= self.p.start_threshold and self.steps_in_state >= self.p.minimum_off_steps:
            self.is_on = True
            self.steps_in_state = 0
        return self.p.pulse_mm if self.is_on else 0.0
