from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from rootzone_mpc.controllers import InternalModel
from rootzone_mpc.experiments.confirmation import _bootstrap_median_interval
from rootzone_mpc.experiments.development import (
    _controller,
    _plant_from_row,
    build_et_series,
)
from rootzone_mpc.supervision import (
    ControlMode,
    OnlineRiskMonitor,
    RiskMonitorConfig,
    RiskScale,
    SupervisorConfig,
    TrustworthySupervisor,
)


def _active(step: int, anomaly: pd.Series) -> bool:
    return int(anomaly["start_step"]) <= step < int(anomaly["end_step"])


def _risk_monitor(frozen_supervision: dict) -> OnlineRiskMonitor:
    scales = frozen_supervision["risk_scales"]
    monitor = frozen_supervision["monitor"]
    return OnlineRiskMonitor(
        RiskMonitorConfig(
            observation_rate_scale=RiskScale(
                scales["observation_rate"]["low"], scales["observation_rate"]["high"]
            ),
            model_residual_scale=RiskScale(
                scales["model_residual_ewma"]["low"],
                scales["model_residual_ewma"]["high"],
            ),
            execution_error_scale=RiskScale(
                scales["execution_relative_error"]["low"],
                scales["execution_relative_error"]["high"],
            ),
            residual_ewma_alpha=float(monitor["residual_ewma_alpha"]),
            maximum_observation_age_steps=int(monitor["maximum_observation_age_steps"]),
            valid_theta_min=float(monitor["valid_theta_min"]),
            valid_theta_max=float(monitor["valid_theta_max"]),
        )
    )


def _supervisor(frozen_supervision: dict) -> TrustworthySupervisor:
    return TrustworthySupervisor(SupervisorConfig(**frozen_supervision["supervisor"]))


def _observed_value(
    raw: float,
    step: int,
    anomaly: pd.Series,
    magnitudes: dict,
) -> float | None:
    if not _active(step, anomaly):
        return raw
    kind = anomaly["anomaly_type"]
    start = int(anomaly["start_step"])
    end = int(anomaly["end_step"])
    if kind == "sensor_missing":
        return None
    if kind == "sensor_bias":
        return raw + float(magnitudes["sensor_bias"])
    if kind == "sensor_drift":
        progress = (step - start + 1) / max(end - start, 1)
        return raw + progress * float(magnitudes["sensor_drift_final"])
    if kind == "sensor_spike":
        return raw + float(magnitudes["sensor_spike"])
    return raw


def _recovery_delay_steps(trace: pd.DataFrame, anomaly: pd.Series) -> int | None:
    """Count steps from anomaly end until MPC is available again."""
    if anomaly["anomaly_type"] == "normal":
        return None
    end = int(anomaly["end_step"])
    post_event = trace.loc[trace["step"] >= end]
    recovered = post_event.loc[post_event["mode"] == ControlMode.MPC.value, "step"]
    if not recovered.empty:
        return int(recovered.iloc[0]) - end
    return max(int(len(trace)) - end, 0)


def _run_method(
    scenario: pd.Series,
    anomaly: pd.Series,
    method: str,
    tuning: dict,
    frozen_controller: dict,
    frozen_supervision: dict,
    anomaly_cfg: dict,
    confirm_cfg: dict,
) -> tuple[dict, pd.DataFrame]:
    plant = _plant_from_row(scenario)
    mpc = _controller("mpc", frozen_controller["mpc"]["parameters"], tuning)
    rule = _controller("rule", frozen_controller["rule"]["parameters"], tuning)
    internal = InternalModel(**frozen_controller["internal_model"])
    monitor = _risk_monitor(frozen_supervision)
    supervisor = _supervisor(frozen_supervision)
    nominal_plant_parameters = plant.p
    hours = int(scenario["horizon_hours"])
    horizon = int(frozen_controller["mpc"]["parameters"]["prediction_horizon"])
    actual_et = build_et_series(scenario, hours + horizon)
    forecast_rng = np.random.default_rng(int(scenario["scenario_seed"]) + 303)
    flow_rng = np.random.default_rng(int(scenario["scenario_seed"]) + 505)
    forecast_noise = forecast_rng.normal(
        0.0,
        float(scenario["forecast_noise_std_fraction"]),
        size=(hours, horizon),
    )
    flow_noise = flow_rng.normal(
        0.0,
        float(anomaly_cfg["risk_calibration"]["simulated_flow_feedback_noise_std_fraction"]),
        size=hours,
    )
    magnitudes = anomaly_cfg["magnitudes"]
    raw_observation = plant.measure()
    observation = _observed_value(raw_observation, 0, anomaly, magnitudes)
    last_valid_observation = raw_observation if observation is None else observation
    predicted_observation = last_valid_observation
    observation_age = 0 if observation is not None else 1
    previous_command = 0.0
    previous_delivered_feedback = 0.0
    previous_feedback_consistent = True
    last_mpc_command = 0.0
    transition_count = 0
    first_non_mpc_step: int | None = None
    rows = []

    for step in range(hours):
        active = _active(step, anomaly)
        kind = anomaly["anomaly_type"]
        if active and kind == "root_depth_shift" and step == int(anomaly["start_step"]):
            plant.p = replace(
                plant.p,
                root_depth_mm=plant.p.root_depth_mm
                * float(magnitudes["root_depth_multiplier"]),
            )
        if active and kind == "drainage_shift" and step == int(anomaly["start_step"]):
            plant.p = replace(
                plant.p,
                drainage_coefficient=plant.p.drainage_coefficient
                * float(magnitudes["drainage_multiplier"]),
            )
        if step == int(anomaly["end_step"]) and kind in {
            "root_depth_shift",
            "drainage_shift",
        }:
            plant.p = nominal_plant_parameters

        forecast_bias = float(scenario["forecast_bias_fraction"])
        if active and kind == "forecast_underprediction":
            forecast_bias += float(magnitudes["forecast_underprediction_extra_bias"])
        forecast = np.maximum(
            actual_et[step : step + horizon]
            * (1.0 + forecast_bias)
            * (1.0 + forecast_noise[step]),
            0.0,
        )
        solver_success = not (active and kind == "solver_failure")
        feedback_consistent = not (active and kind == "feedback_conflict")
        signals = monitor.update(
            observation,
            predicted_observation,
            observation_age,
            previous_command,
            previous_delivered_feedback,
            solver_success,
            actuator_available=True,
            feedback_consistent=feedback_consistent and previous_feedback_consistent,
        )

        effective_observation = last_valid_observation if observation is None else float(observation)
        if method == "base_mpc":
            mode = ControlMode.MPC
            reason = "base_mpc"
            transitioned = False
        elif method == "fixed_fallback":
            if not signals.observation_valid or not feedback_consistent:
                mode = ControlMode.SAFE_PAUSE
                reason = "fixed_critical_flag"
            elif not solver_success:
                mode = ControlMode.RULE_FALLBACK
                reason = "fixed_solver_fallback"
            else:
                mode = ControlMode.MPC
                reason = "fixed_mpc"
            transitioned = bool(rows and rows[-1]["mode"] != mode.value)
        elif method == "trustworthy_supervision":
            decision = supervisor.update(signals)
            mode = decision.mode
            reason = decision.reason
            transitioned = decision.transitioned
        else:
            raise ValueError(f"Unknown method: {method}")

        if mode == ControlMode.MPC:
            if rows and rows[-1]["mode"] != ControlMode.MPC.value:
                mpc.previous_action = previous_command
            mpc_candidate = (
                mpc.act(effective_observation, forecast.tolist())
                if solver_success
                else last_mpc_command
            )
            if solver_success:
                last_mpc_command = mpc_candidate
            command = mpc_candidate
        elif mode == ControlMode.RULE_FALLBACK:
            command = rule.act(effective_observation)
        else:
            command = 0.0

        if transitioned:
            transition_count += 1
        if mode != ControlMode.MPC and first_non_mpc_step is None and step >= int(anomaly["start_step"]):
            first_non_mpc_step = step

        delivery_multiplier = 1.0
        if active and kind == "flow_decay":
            delivery_multiplier = float(magnitudes["flow_delivery_multiplier"])
        elif active and kind == "valve_stuck":
            delivery_multiplier = float(magnitudes["valve_stuck_delivery_multiplier"])
        delivered_command = command * delivery_multiplier
        delivered_feedback = max(delivered_command * (1.0 + flow_noise[step]), 0.0)
        result = plant.step(delivered_command, float(actual_et[step]))
        predicted_next = internal.predict_step(effective_observation, command, float(forecast[0]))
        next_raw = result.theta_measured
        next_observation = _observed_value(next_raw, step + 1, anomaly, magnitudes)
        if next_observation is None:
            observation_age += 1
        else:
            observation_age = 0
            last_valid_observation = float(next_observation)

        rows.append(
            {
                "anomaly_scenario_id": anomaly["anomaly_scenario_id"],
                "base_scenario_id": scenario["scenario_id"],
                "anomaly_type": kind,
                "method": method,
                "step": step,
                "theta_true": result.theta_true,
                "observation": np.nan if observation is None else observation,
                "command_mm": command,
                "delivered_command_mm": delivered_command,
                "mode": mode.value,
                "reason": reason,
                "observation_risk": signals.observation_risk,
                "model_risk": signals.model_risk,
                "execution_risk": signals.execution_risk,
                "solver_success": solver_success,
                "feedback_consistent": feedback_consistent,
                "anomaly_active": active,
            }
        )
        observation = next_observation
        raw_observation = next_raw
        predicted_observation = predicted_next
        previous_command = command
        previous_delivered_feedback = delivered_feedback
        previous_feedback_consistent = feedback_consistent

    trace = pd.DataFrame(rows)
    zone = tuning["tuning"]["zone"]
    deficit = np.maximum(float(zone["lower"]) - trace["theta_true"].to_numpy(), 0.0)
    excess = np.maximum(trace["theta_true"].to_numpy() - float(zone["upper"]), 0.0)
    safety = (trace["theta_true"] < float(zone["safety_lower"])) | (
        trace["theta_true"] > float(zone["safety_upper"])
    )
    start = int(anomaly["start_step"])
    end = min(
        int(anomaly["end_step"]) + int(confirm_cfg["confirmation"]["event_followup_steps"]),
        hours,
    )
    event_mask = (trace["step"] >= start) & (trace["step"] < end)
    mode_fraction = trace["mode"].value_counts(normalize=True)
    detection_delay = (
        None
        if kind == "normal" or first_non_mpc_step is None
        else max(first_non_mpc_step - start, 0)
    )
    metrics = {
        "anomaly_scenario_id": anomaly["anomaly_scenario_id"],
        "base_scenario_id": scenario["scenario_id"],
        "anomaly_type": kind,
        "method": method,
        "deficit_integral": float(deficit.sum()),
        "event_deficit_integral": float(deficit[event_mask].sum()),
        "excess_integral": float(excess.sum()),
        "irrigation_command_total_mm": float(trace["command_mm"].sum()),
        "delivered_command_total_mm": float(trace["delivered_command_mm"].sum()),
        "action_change_count": int(np.count_nonzero(np.diff(trace["command_mm"]) != 0.0)),
        "safety_violation_steps": int(safety.sum()),
        "mpc_fraction": float(mode_fraction.get(ControlMode.MPC.value, 0.0)),
        "rule_fallback_fraction": float(
            mode_fraction.get(ControlMode.RULE_FALLBACK.value, 0.0)
        ),
        "safe_pause_fraction": float(mode_fraction.get(ControlMode.SAFE_PAUSE.value, 0.0)),
        "non_mpc_fraction": float(1.0 - mode_fraction.get(ControlMode.MPC.value, 0.0)),
        "transition_count": transition_count,
        "detection_delay_steps": detection_delay,
        "recovery_delay_steps": _recovery_delay_steps(trace, anomaly),
    }
    return metrics, trace


def _pair(metrics: pd.DataFrame, candidate: str, baseline: str) -> pd.DataFrame:
    keys = ["anomaly_scenario_id", "base_scenario_id", "anomaly_type"]
    paired = metrics[metrics["method"] == baseline][keys].copy().set_index(
        "anomaly_scenario_id"
    )
    numerical = [
        "deficit_integral",
        "event_deficit_integral",
        "excess_integral",
        "irrigation_command_total_mm",
        "delivered_command_total_mm",
        "action_change_count",
        "safety_violation_steps",
        "non_mpc_fraction",
        "transition_count",
    ]
    for metric in numerical:
        wide = metrics.pivot(index="anomaly_scenario_id", columns="method", values=metric)
        paired[f"delta_{metric}"] = wide[candidate] - wide[baseline]
        paired[f"{baseline}_{metric}"] = wide[baseline]
        paired[f"{candidate}_{metric}"] = wide[candidate]
    return paired.reset_index()


def _classification(pairs: pd.DataFrame, metrics: pd.DataFrame, config: dict) -> tuple[str, dict]:
    gate = config["confirmation"]["success_gate"]
    tolerance = float(config["confirmation"]["tie_tolerance_event_deficit"])
    by_type = pairs.groupby("anomaly_type")["delta_event_deficit_integral"].median()
    non_normal = by_type.drop(index="normal", errors="ignore")
    improved_types = int((non_normal < -tolerance).sum())
    adverse_types = int((non_normal > tolerance).sum())
    trust = metrics[metrics["method"] == "trustworthy_supervision"]
    base = metrics[metrics["method"] == "base_mpc"]
    trust_safety = int((trust["safety_violation_steps"] > 0).sum())
    base_safety = int((base["safety_violation_steps"] > 0).sum())
    normal_non_mpc = float(
        trust.loc[trust["anomaly_type"] == "normal", "non_mpc_fraction"].median()
    )
    no_extra_safety = trust_safety <= base_safety
    passed = (
        improved_types >= int(gate["minimum_improved_anomaly_types"])
        and adverse_types <= int(gate["maximum_adverse_anomaly_types"])
        and normal_non_mpc <= float(gate["normal_non_mpc_fraction_max"])
        and (no_extra_safety or not gate["require_no_extra_safety_violation"])
    )
    if passed:
        label = "RISK_SUPERVISION_MECHANISM_SUPPORTED"
    elif improved_types > 0 and no_extra_safety:
        label = "PARTIAL_MECHANISM_VALUE_WITH_BOUNDARIES"
    else:
        label = "SUPERVISION_VALUE_NOT_SUPPORTED"
    return label, {
        "improved_anomaly_types": improved_types,
        "adverse_anomaly_types": adverse_types,
        "normal_non_mpc_fraction_median": normal_non_mpc,
        "trustworthy_safety_violation_scenarios": trust_safety,
        "base_mpc_safety_violation_scenarios": base_safety,
        "no_extra_safety_violation": no_extra_safety,
        "success_gate_passed": passed,
    }


def run_supervision_confirmation(project_root: Path) -> tuple[Path, ...]:
    confirm_path = project_root / "configs" / "supervision_confirmation_v1.yaml"
    anomaly_config_path = project_root / "configs" / "anomaly_design_v1.yaml"
    scenario_path = project_root / "data" / "processed" / "scenario_manifest_v1.csv"
    anomaly_manifest_path = project_root / "data" / "processed" / "anomaly_manifest_v1.csv"
    tuning_path = project_root / "configs" / "tuning_design_v1.yaml"
    controller_path = project_root / "configs" / "frozen_controller_v1.yaml"
    supervision_path = project_root / "configs" / "frozen_supervision_v1.yaml"
    confirm = yaml.safe_load(confirm_path.read_text(encoding="utf-8"))
    anomaly_cfg = yaml.safe_load(anomaly_config_path.read_text(encoding="utf-8"))
    tuning = yaml.safe_load(tuning_path.read_text(encoding="utf-8"))
    controller = yaml.safe_load(controller_path.read_text(encoding="utf-8"))
    supervision = yaml.safe_load(supervision_path.read_text(encoding="utf-8"))
    scenarios = pd.read_csv(scenario_path).set_index("scenario_id")
    anomaly_manifest = pd.read_csv(anomaly_manifest_path)
    locked = anomaly_manifest[
        anomaly_manifest["split"] == confirm["confirmation"]["split"]
    ]
    if len(locked) != int(confirm["confirmation"]["expected_scenario_count"]):
        raise ValueError("Locked anomaly scenario count differs from confirmation contract")
    if int(supervision["locked_evaluation_scenarios_used"]) != 0:
        raise ValueError("Supervision parameters indicate prior locked evaluation access")

    metric_rows = []
    traces = []
    for _, anomaly in locked.iterrows():
        scenario = scenarios.loc[anomaly["base_scenario_id"]].copy()
        scenario["scenario_id"] = anomaly["base_scenario_id"]
        for method in confirm["confirmation"]["methods"]:
            metric, trace = _run_method(
                scenario,
                anomaly,
                method,
                tuning,
                controller,
                supervision,
                anomaly_cfg,
                confirm,
            )
            metric_rows.append(metric)
            traces.append(trace)
    metrics = pd.DataFrame(metric_rows)
    trace_frame = pd.concat(traces, ignore_index=True)
    trust_vs_base = _pair(metrics, "trustworthy_supervision", "base_mpc")
    trust_vs_fixed = _pair(metrics, "trustworthy_supervision", "fixed_fallback")
    classification, diagnostics = _classification(trust_vs_base, metrics, confirm)

    resamples = int(confirm["confirmation"]["bootstrap_resamples"])
    seed = int(confirm["confirmation"]["bootstrap_seed"])
    overall = {}
    for index, metric in enumerate(
        ["event_deficit_integral", "deficit_integral", "irrigation_command_total_mm"]
    ):
        values = trust_vs_base[f"delta_{metric}"].to_numpy(float)
        lower, upper = _bootstrap_median_interval(values, resamples, seed + index)
        overall[metric] = {
            "paired_median": float(np.median(values)),
            "paired_p90": float(np.quantile(values, 0.90)),
            "ci95_lower": lower,
            "ci95_upper": upper,
            "improved_ratio": float(np.mean(values < 0.0)),
            "equal_ratio": float(np.mean(values == 0.0)),
            "adverse_ratio": float(np.mean(values > 0.0)),
        }

    subgroup = trust_vs_base.groupby("anomaly_type").agg(
        scenario_count=("anomaly_scenario_id", "count"),
        median_delta_event_deficit=("delta_event_deficit_integral", "median"),
        p90_delta_event_deficit=("delta_event_deficit_integral", lambda x: x.quantile(0.90)),
        median_delta_total_deficit=("delta_deficit_integral", "median"),
        median_delta_irrigation_mm=("delta_irrigation_command_total_mm", "median"),
        median_delta_action_changes=("delta_action_change_count", "median"),
    ).reset_index()
    trustworthy = metrics[metrics["method"] == "trustworthy_supervision"]
    operational = trustworthy.groupby("anomaly_type", dropna=False).agg(
        scenario_count=("anomaly_scenario_id", "count"),
        median_detection_delay_steps=("detection_delay_steps", "median"),
        median_recovery_delay_steps=("recovery_delay_steps", "median"),
        median_non_mpc_fraction=("non_mpc_fraction", "median"),
        median_rule_fallback_fraction=("rule_fallback_fraction", "median"),
        median_safe_pause_fraction=("safe_pause_fraction", "median"),
        median_transition_count=("transition_count", "median"),
    ).reset_index()

    run_dir = project_root / "outputs" / "runs"
    table_dir = project_root / "outputs" / "tables"
    result_dir = project_root / "data" / "processed"
    raw_path = run_dir / "supervision_confirmation_metrics.csv"
    trace_path = run_dir / "supervision_confirmation_traces.csv"
    base_pair_path = table_dir / "trustworthy_vs_base_pairs.csv"
    fixed_pair_path = table_dir / "trustworthy_vs_fixed_pairs.csv"
    subgroup_path = table_dir / "supervision_confirmation_subgroups.csv"
    operational_path = table_dir / "supervision_operational_subgroups.csv"
    result_path = result_dir / "supervision_confirmation_v1.json"
    metrics.to_csv(raw_path, index=False)
    trace_frame.to_csv(trace_path, index=False)
    trust_vs_base.to_csv(base_pair_path, index=False)
    trust_vs_fixed.to_csv(fixed_pair_path, index=False)
    subgroup.to_csv(subgroup_path, index=False)
    operational.to_csv(operational_path, index=False)
    result = {
        "confirmation_name": confirm["confirmation"]["name"],
        "classification": classification,
        "scenario_count": int(len(locked)),
        "method_runs": int(len(metrics)),
        "overall_trustworthy_minus_base": overall,
        "diagnostics": diagnostics,
        "operational_by_anomaly_type": [
            {
                key: None if pd.isna(value) else value
                for key, value in row.items()
            }
            for row in operational.to_dict(orient="records")
        ],
        "evidence_boundary": confirm["confirmation"]["evidence_boundary"],
        "hashes": {
            "confirmation_config": hashlib.sha256(confirm_path.read_bytes()).hexdigest(),
            "anomaly_config": hashlib.sha256(anomaly_config_path.read_bytes()).hexdigest(),
            "anomaly_manifest": hashlib.sha256(anomaly_manifest_path.read_bytes()).hexdigest(),
            "frozen_controller": hashlib.sha256(controller_path.read_bytes()).hexdigest(),
            "frozen_supervision": hashlib.sha256(supervision_path.read_bytes()).hexdigest(),
            "trustworthy_vs_base_pairs": hashlib.sha256(base_pair_path.read_bytes()).hexdigest(),
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return (
        raw_path,
        trace_path,
        base_pair_path,
        fixed_pair_path,
        subgroup_path,
        operational_path,
        result_path,
    )
