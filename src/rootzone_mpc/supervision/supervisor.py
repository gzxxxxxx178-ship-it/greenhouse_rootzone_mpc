from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ControlMode(StrEnum):
    MPC = "mpc"
    RULE_FALLBACK = "rule_fallback"
    ESTIMATED_FALLBACK = "estimated_fallback"
    SAFE_PAUSE = "safe_pause"


@dataclass(frozen=True)
class SupervisorConfig:
    risk_high: float
    risk_recovery: float
    minimum_dwell_steps: int
    recovery_confirmation_steps: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.risk_recovery < self.risk_high <= 1.0:
            raise ValueError("Risk thresholds must satisfy 0 <= recovery < high <= 1")
        if self.minimum_dwell_steps < 0 or self.recovery_confirmation_steps < 1:
            raise ValueError("Dwell must be nonnegative and confirmation must be positive")


@dataclass(frozen=True)
class SupervisorSignals:
    observation_risk: float
    model_risk: float
    execution_risk: float
    observation_valid: bool
    solver_success: bool
    actuator_available: bool
    feedback_consistent: bool

    def __post_init__(self) -> None:
        risks = (self.observation_risk, self.model_risk, self.execution_risk)
        if not all(0.0 <= risk <= 1.0 for risk in risks):
            raise ValueError("All normalized risks must be in [0, 1]")


@dataclass(frozen=True)
class SupervisorDecision:
    mode: ControlMode
    previous_mode: ControlMode
    transitioned: bool
    reason: str
    steps_in_mode: int
    recovery_count: int


class TrustworthySupervisor:
    def __init__(self, config: SupervisorConfig):
        self.config = config
        self.mode = ControlMode.MPC
        self.steps_in_mode = 0
        self.recovery_count = 0

    def _transition(self, target: ControlMode, reason: str) -> SupervisorDecision:
        previous = self.mode
        transitioned = target != previous
        if transitioned:
            self.mode = target
            self.steps_in_mode = 0
            self.recovery_count = 0
        return SupervisorDecision(
            mode=self.mode,
            previous_mode=previous,
            transitioned=transitioned,
            reason=reason,
            steps_in_mode=self.steps_in_mode,
            recovery_count=self.recovery_count,
        )

    def update(self, signals: SupervisorSignals) -> SupervisorDecision:
        self.steps_in_mode += 1
        critical = (
            not signals.observation_valid
            or not signals.actuator_available
            or not signals.feedback_consistent
            or signals.execution_risk >= self.config.risk_high
        )
        if critical:
            self.recovery_count = 0
            return self._transition(ControlMode.SAFE_PAUSE, "critical_observation_or_execution")

        high_control_risk = (
            not signals.solver_success
            or signals.observation_risk >= self.config.risk_high
            or signals.model_risk >= self.config.risk_high
        )
        if self.mode == ControlMode.MPC and high_control_risk:
            return self._transition(ControlMode.RULE_FALLBACK, "mpc_unavailable_or_high_risk")

        recovery_ready = (
            signals.solver_success
            and signals.observation_risk <= self.config.risk_recovery
            and signals.model_risk <= self.config.risk_recovery
            and signals.execution_risk <= self.config.risk_recovery
        )
        self.recovery_count = self.recovery_count + 1 if recovery_ready else 0

        if self.mode == ControlMode.SAFE_PAUSE:
            if (
                self.steps_in_mode >= self.config.minimum_dwell_steps
                and self.recovery_count >= self.config.recovery_confirmation_steps
            ):
                return self._transition(ControlMode.RULE_FALLBACK, "pause_recovery_confirmed")
            return self._transition(ControlMode.SAFE_PAUSE, "pause_conditions_not_confirmed")

        if self.mode == ControlMode.RULE_FALLBACK:
            if high_control_risk:
                self.recovery_count = 0
                return self._transition(ControlMode.RULE_FALLBACK, "fallback_risk_remains_high")
            if (
                self.steps_in_mode >= self.config.minimum_dwell_steps
                and self.recovery_count >= self.config.recovery_confirmation_steps
            ):
                return self._transition(ControlMode.MPC, "mpc_recovery_confirmed")
            return self._transition(ControlMode.RULE_FALLBACK, "fallback_recovery_pending")

        return self._transition(ControlMode.MPC, "mpc_conditions_acceptable")


class TrustworthySupervisorV2(TrustworthySupervisor):
    """Route uncertain observations or execution to an estimate-driven fallback."""

    def update(self, signals: SupervisorSignals) -> SupervisorDecision:
        self.steps_in_mode += 1
        if not signals.actuator_available:
            self.recovery_count = 0
            return self._transition(ControlMode.SAFE_PAUSE, "actuator_unavailable")

        fallback_required = (
            not signals.observation_valid
            or not signals.feedback_consistent
            or not signals.solver_success
            or signals.observation_risk >= self.config.risk_high
            or signals.model_risk >= self.config.risk_high
            or signals.execution_risk >= self.config.risk_high
        )
        if fallback_required:
            self.recovery_count = 0
            return self._transition(
                ControlMode.ESTIMATED_FALLBACK, "estimate_driven_fallback_required"
            )

        recovery_ready = (
            signals.observation_risk <= self.config.risk_recovery
            and signals.model_risk <= self.config.risk_recovery
            and signals.execution_risk <= self.config.risk_recovery
        )
        self.recovery_count = self.recovery_count + 1 if recovery_ready else 0
        if self.mode != ControlMode.MPC:
            if (
                self.steps_in_mode >= self.config.minimum_dwell_steps
                and self.recovery_count >= self.config.recovery_confirmation_steps
            ):
                return self._transition(ControlMode.MPC, "mpc_recovery_confirmed")
            return self._transition(
                ControlMode.ESTIMATED_FALLBACK, "fallback_recovery_pending"
            )
        return self._transition(ControlMode.MPC, "mpc_conditions_acceptable")
