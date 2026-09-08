from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml


POSITIVE_REQUIREMENT_STATUSES = {"completed", "ready"}


def load_audit_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["thesis_evidence_audit"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(project_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
    ).strip()


def _git_object_exists(project_root: Path, revision: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"], cwd=project_root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return completed.returncode == 0


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_selector(document: Any, selector: str) -> Any:
    value = document
    for component in selector.split("."):
        if isinstance(value, list):
            value = value[int(component)]
        else:
            value = value[component]
    return value


def _evidence_parts(reference: str) -> list[str]:
    return [part.strip() for part in reference.split(" and ") if part.strip()]


def _reference_exists(project_root: Path, reference: str) -> bool:
    if reference.startswith("origin/"):
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/remotes/{reference}"],
            cwd=project_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    return (project_root / reference).exists()


def _status_map(rows: list[dict[str, str]], id_column: str) -> dict[str, str]:
    return {row[id_column]: row["current_status"] for row in rows}


def _claim_register(config: dict, requirement_status: dict[str, str]) -> dict:
    supported = []
    for claim in config["claims"]["supported"]:
        accepted = set(claim.get("accepted_statuses", POSITIVE_REQUIREMENT_STATUSES))
        unavailable = [
            item for item in claim["requirements"]
            if requirement_status.get(item) not in accepted
        ]
        supported.append({
            **claim,
            "audit_status": "supported" if not unavailable else "unsupported",
            "unavailable_requirements": unavailable,
        })
    conditional = []
    for claim in config["claims"]["conditional"]:
        unavailable = [
            item for item in claim["requirements"]
            if requirement_status.get(item) not in POSITIVE_REQUIREMENT_STATUSES
        ]
        conditional.append({
            **claim,
            "audit_status": "conditional" if unavailable else "supported",
            "unavailable_requirements": unavailable,
        })
    prohibited = [dict(claim, audit_status="prohibited") for claim in config["claims"]["prohibited"]]
    return {"supported": supported, "conditional": conditional, "prohibited": prohibited}


def audit_repository(project_root: Path, config_path: Path) -> dict:
    config = load_audit_config(config_path)
    task_path = project_root / config["task_list"]
    matrix_path = project_root / config["evidence_matrix"]
    roadmap_path = project_root / config["execution_roadmap"]
    tasks = _read_csv(task_path)
    requirements = _read_csv(matrix_path)
    roadmap = _read_csv(roadmap_path)

    task_ids = [row["task_id"] for row in tasks]
    expected_task_ids = [f"T{index:03d}" for index in range(1, len(tasks) + 1)]
    task_evidence_missing = []
    for row in tasks:
        for reference in _evidence_parts(row["evidence"]):
            if not _reference_exists(project_root, reference):
                task_evidence_missing.append({"task_id": row["task_id"], "reference": reference})
    task_checks = {
        "task_ids_unique": len(task_ids) == len(set(task_ids)),
        "task_ids_contiguous": task_ids == expected_task_ids,
        "task_statuses_valid": all(
            row["status"] in config["allowed_task_statuses"] for row in tasks
        ),
        "completed_tasks_have_date_and_evidence": all(
            row["status"] != "completed" or (row["completed_at"] and row["evidence"])
            for row in tasks
        ),
        "task_evidence_resolves": not task_evidence_missing,
    }

    requirement_ids = [row["requirement_id"] for row in requirements]
    requirement_status = _status_map(requirements, "requirement_id")
    requirement_evidence_missing = []
    for row in requirements:
        if row["current_status"] == "missing" and row["evidence"] == "none":
            continue
        for reference in _evidence_parts(row["evidence"]):
            if not _reference_exists(project_root, reference):
                requirement_evidence_missing.append({
                    "requirement_id": row["requirement_id"], "reference": reference,
                })
    observed_missing = sorted(
        item for item, status in requirement_status.items() if status == "missing"
    )
    observed_nonpositive = sorted(
        item for item, status in requirement_status.items() if status in {"failed", "stopped"}
    )
    requirement_checks = {
        "requirement_ids_unique": len(requirement_ids) == len(set(requirement_ids)),
        "requirement_statuses_valid": all(
            row["current_status"] in config["allowed_requirement_statuses"]
            for row in requirements
        ),
        "requirement_evidence_resolves": not requirement_evidence_missing,
        "expected_missing_requirements_preserved": observed_missing
        == sorted(config["expected_missing_requirements"]),
        "expected_failed_or_stopped_requirements_preserved": observed_nonpositive
        == sorted(config["expected_nonpositive_requirements"]),
    }

    json_cache: dict[str, dict] = {}
    artifact_load_errors: dict[str, str] = {}

    def document(relative_path: str) -> dict:
        if relative_path not in json_cache:
            try:
                json_cache[relative_path] = json.loads(
                    (project_root / relative_path).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                artifact_load_errors[relative_path] = f"{type(error).__name__}: {error}"
                json_cache[relative_path] = {}
        return json_cache[relative_path]

    result_expectations = []
    for expectation in config["result_expectations"]:
        try:
            actual = resolve_selector(document(expectation["artifact"]), expectation["selector"])
        except (KeyError, IndexError, TypeError, ValueError):
            actual = None
        result_expectations.append({
            **expectation, "actual": actual, "matched": actual == expectation["expected"],
        })

    config_bindings = []
    for binding in config["config_bindings"]:
        recorded = document(binding["artifact"]).get("config_sha256")
        try:
            current = _sha256(project_root / binding["config"])
        except OSError:
            current = None
        config_bindings.append({
            **binding, "recorded_sha256": recorded, "current_sha256": current,
            "matched": recorded == current,
        })

    code_commits = []
    for artifact, contents in json_cache.items():
        if "code_commit" in contents:
            revision = str(contents["code_commit"])
            code_commits.append({
                "artifact": artifact, "code_commit": revision,
                "commit_exists": _git_object_exists(project_root, revision),
            })

    key_metrics = []
    for metric in config["key_metrics"]:
        try:
            actual = resolve_selector(document(metric["artifact"]), metric["selector"])
        except (KeyError, IndexError, TypeError, ValueError):
            actual = None
        expected = metric["expected"]
        tolerance = float(metric["tolerance"])
        matched = actual is not None and (
            abs(float(actual) - float(expected)) <= tolerance
            if isinstance(expected, (int, float)) else actual == expected
        )
        key_metrics.append({**metric, "actual": actual, "matched": bool(matched)})

    claims = _claim_register(config, requirement_status)
    supported_claims_valid = all(
        item["audit_status"] == "supported" for item in claims["supported"]
    )
    conditional_claims_preserved = all(
        item["audit_status"] == "conditional" for item in claims["conditional"]
    )
    readiness = {}
    for name, required in config["readiness_rules"].items():
        unavailable = [
            item for item in required
            if requirement_status.get(item) not in POSITIVE_REQUIREMENT_STATUSES
        ]
        readiness[name] = {
            "status": "ready" if not unavailable else "blocked",
            "unavailable_requirements": unavailable,
        }

    checks = {
        **task_checks,
        **requirement_checks,
        "result_statuses_match_frozen_expectations": all(
            item["matched"] for item in result_expectations
        ),
        "frozen_config_hashes_match": all(item["matched"] for item in config_bindings),
        "recorded_code_commits_exist": all(item["commit_exists"] for item in code_commits),
        "key_metrics_match_frozen_sources": all(item["matched"] for item in key_metrics),
        "core_json_artifacts_load": not artifact_load_errors,
        "supported_claims_have_positive_evidence": supported_claims_valid,
        "conditional_claims_remain_conditional": conditional_claims_preserved,
        "roadmap_is_nonempty": bool(roadmap),
    }
    return {
        "name": config["name"],
        "status": "passed" if all(checks.values()) else "failed",
        "code_commit": _git_commit(project_root),
        "config_sha256": _sha256(config_path),
        "counts": {
            "tasks": len(tasks),
            "requirements": len(requirements),
            "roadmap_items": len(roadmap),
            "result_expectations": len(result_expectations),
            "config_bindings": len(config_bindings),
            "key_metrics": len(key_metrics),
            "supported_claims": len(claims["supported"]),
            "conditional_claims": len(claims["conditional"]),
            "prohibited_claims": len(claims["prohibited"]),
        },
        "checks": checks,
        "task_evidence_missing": task_evidence_missing,
        "requirement_evidence_missing": requirement_evidence_missing,
        "artifact_load_errors": artifact_load_errors,
        "result_expectations": result_expectations,
        "config_bindings": config_bindings,
        "code_commits": code_commits,
        "key_metrics": key_metrics,
        "claim_register": claims,
        "readiness": readiness,
        "evidence_boundary": config["evidence_boundary"],
    }


def write_audit(project_root: Path, config_path: Path, output_path: Path) -> dict:
    result = audit_repository(project_root, config_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
