"""Stage vocabulary, allowlisted params, and command previews."""

from __future__ import annotations

from typing import Any

PIPELINE_STAGES: list[str] = [
    "ingest",
    "build-case",
    "run-before-after",
    "analyze-case",
    "localize",
    "repair",
    "validate",
    "explain",
    "metrics",
    "report",
]

CASE_RUNNABLE_STAGES: list[str] = [
    "build-case",
    "run-before-after",
    "analyze-case",
    "localize",
    "repair",
    "validate",
    "explain",
    "metrics",
    "report",
]

REPAIR_MODES: set[str] = {
    "full_thesis",
    "raw_error",
    "context_rich",
    "iterative_agentic",
}

PATCH_STRATEGIES: set[str] = {"single_diff", "chain_by_file"}
PROVIDERS: set[str] = {"anthropic", "vertex"}
TARGETS: set[str] = {"shared", "android", "ios", "jvm"}
REPORT_FORMATS: set[str] = {"csv", "json", "markdown", "all"}


def _unknown_keys(params: dict[str, Any], allowed: set[str]) -> list[str]:
    return sorted(k for k in params.keys() if k not in allowed)


def _normalize_targets(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("targets debe ser una lista")
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("targets debe contener strings")
        target = item.strip().lower()
        if target not in TARGETS:
            raise ValueError(f"target no permitido: {target}")
        if target not in cleaned:
            cleaned.append(target)
    return cleaned


def _normalize_provider(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("provider debe ser string")
    provider = raw.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"provider no permitido: {provider}")
    return provider


def _normalize_mode(raw: Any) -> str:
    if raw is None:
        return "full_thesis"
    if not isinstance(raw, str):
        raise ValueError("mode debe ser string")
    mode = raw.strip()
    if mode not in REPAIR_MODES:
        raise ValueError(f"mode no permitido: {mode}")
    return mode


def sanitize_stage_params(stage: str, raw_params: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize params using a strict allowlist per stage."""
    if stage not in CASE_RUNNABLE_STAGES:
        raise ValueError(f"Etapa no ejecutable: {stage}")

    params = raw_params or {}
    if not isinstance(params, dict):
        raise ValueError("params debe ser objeto JSON")

    if stage == "build-case":
        allowed = {"artifact_base", "work_dir", "overwrite"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en build-case: {', '.join(unknown)}")
        artifact_base = params.get("artifact_base", "data/artifacts")
        work_dir = params.get("work_dir")
        overwrite = bool(params.get("overwrite", False))
        if not isinstance(artifact_base, str):
            raise ValueError("artifact_base debe ser string")
        if work_dir is not None and not isinstance(work_dir, str):
            raise ValueError("work_dir debe ser string")
        return {
            "artifact_base": artifact_base,
            "work_dir": work_dir,
            "overwrite": overwrite,
        }

    if stage == "run-before-after":
        allowed = {"artifact_base", "targets", "timeout_s"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en run-before-after: {', '.join(unknown)}")
        timeout_s = int(params.get("timeout_s", 600))
        if timeout_s < 30 or timeout_s > 7200:
            raise ValueError("timeout_s debe estar entre 30 y 7200")
        artifact_base = params.get("artifact_base", "data/artifacts")
        if not isinstance(artifact_base, str):
            raise ValueError("artifact_base debe ser string")
        return {
            "artifact_base": artifact_base,
            "targets": _normalize_targets(params.get("targets")),
            "timeout_s": timeout_s,
        }

    if stage == "analyze-case":
        unknown = _unknown_keys(params, set())
        if unknown:
            raise ValueError(f"Parámetros no permitidos en analyze-case: {', '.join(unknown)}")
        return {}

    if stage == "localize":
        allowed = {"no_agent", "top_k", "provider", "model", "artifact_base"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en localize: {', '.join(unknown)}")
        top_k = int(params.get("top_k", 10))
        if top_k < 1 or top_k > 50:
            raise ValueError("top_k debe estar entre 1 y 50")
        model = params.get("model")
        if model is not None and not isinstance(model, str):
            raise ValueError("model debe ser string")
        artifact_base = params.get("artifact_base", "data/artifacts")
        if not isinstance(artifact_base, str):
            raise ValueError("artifact_base debe ser string")
        return {
            "no_agent": bool(params.get("no_agent", False)),
            "top_k": top_k,
            "provider": _normalize_provider(params.get("provider")),
            "model": model,
            "artifact_base": artifact_base,
        }

    if stage == "repair":
        allowed = {
            "mode",
            "top_k",
            "provider",
            "model",
            "patch_strategy",
            "force_patch_attempt",
            "artifact_base",
        }
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en repair: {', '.join(unknown)}")
        top_k = int(params.get("top_k", 5))
        if top_k < 1 or top_k > 20:
            raise ValueError("top_k debe estar entre 1 y 20")
        patch_strategy = params.get("patch_strategy", "single_diff")
        if patch_strategy not in PATCH_STRATEGIES:
            raise ValueError(f"patch_strategy no permitido: {patch_strategy}")
        model = params.get("model")
        if model is not None and not isinstance(model, str):
            raise ValueError("model debe ser string")
        artifact_base = params.get("artifact_base", "data/artifacts")
        if not isinstance(artifact_base, str):
            raise ValueError("artifact_base debe ser string")
        return {
            "mode": _normalize_mode(params.get("mode")),
            "top_k": top_k,
            "provider": _normalize_provider(params.get("provider")),
            "model": model,
            "patch_strategy": patch_strategy,
            "force_patch_attempt": bool(params.get("force_patch_attempt", True)),
            "artifact_base": artifact_base,
        }

    if stage == "validate":
        allowed = {"attempt_id", "targets", "timeout_s", "artifact_base"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en validate: {', '.join(unknown)}")
        timeout_s = int(params.get("timeout_s", 600))
        if timeout_s < 30 or timeout_s > 7200:
            raise ValueError("timeout_s debe estar entre 30 y 7200")
        attempt_id = params.get("attempt_id")
        if attempt_id is not None and not isinstance(attempt_id, str):
            raise ValueError("attempt_id debe ser string")
        artifact_base = params.get("artifact_base", "data/artifacts")
        if not isinstance(artifact_base, str):
            raise ValueError("artifact_base debe ser string")
        return {
            "attempt_id": attempt_id,
            "targets": _normalize_targets(params.get("targets")),
            "timeout_s": timeout_s,
            "artifact_base": artifact_base,
        }

    if stage == "explain":
        allowed = {"provider", "model", "artifact_base"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en explain: {', '.join(unknown)}")
        model = params.get("model")
        if model is not None and not isinstance(model, str):
            raise ValueError("model debe ser string")
        artifact_base = params.get("artifact_base", "data/artifacts")
        if not isinstance(artifact_base, str):
            raise ValueError("artifact_base debe ser string")
        return {
            "provider": _normalize_provider(params.get("provider")),
            "model": model,
            "artifact_base": artifact_base,
        }

    if stage == "metrics":
        allowed = {"ground_truth"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en metrics: {', '.join(unknown)}")
        ground_truth = params.get("ground_truth")
        if ground_truth is not None and not isinstance(ground_truth, dict):
            raise ValueError("ground_truth debe ser objeto JSON")
        return {"ground_truth": ground_truth}

    if stage == "report":
        allowed = {"output_dir", "format", "modes", "cases"}
        unknown = _unknown_keys(params, allowed)
        if unknown:
            raise ValueError(f"Parámetros no permitidos en report: {', '.join(unknown)}")
        fmt = params.get("format", "all")
        if not isinstance(fmt, str) or fmt not in REPORT_FORMATS:
            raise ValueError(f"format no permitido: {fmt}")
        output_dir = params.get("output_dir", "data/reports")
        if not isinstance(output_dir, str):
            raise ValueError("output_dir debe ser string")
        modes = params.get("modes")
        if modes is not None:
            if not isinstance(modes, list):
                raise ValueError("modes debe ser lista")
            bad = [m for m in modes if not isinstance(m, str) or m not in REPAIR_MODES]
            if bad:
                raise ValueError(f"modes inválidos: {', '.join(str(v) for v in bad)}")
        cases = params.get("cases")
        if cases is not None:
            if not isinstance(cases, list) or not all(isinstance(v, str) for v in cases):
                raise ValueError("cases debe ser lista de strings")
        return {
            "output_dir": output_dir,
            "format": fmt,
            "modes": modes,
            "cases": cases,
        }

    raise ValueError(f"Etapa no soportada: {stage}")


def sanitize_pipeline_request(
    start_from_stage: str | None,
    params_by_stage: dict[str, dict[str, Any]] | None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Validate pipeline run request and return normalized stage params map."""
    start = start_from_stage or "build-case"
    if start not in CASE_RUNNABLE_STAGES:
        raise ValueError(f"start_from_stage inválida: {start}")

    source = params_by_stage or {}
    if not isinstance(source, dict):
        raise ValueError("params_by_stage debe ser objeto JSON")

    unknown_stage_names = [k for k in source.keys() if k not in CASE_RUNNABLE_STAGES]
    if unknown_stage_names:
        raise ValueError(f"Etapas no permitidas en params_by_stage: {', '.join(sorted(unknown_stage_names))}")

    start_index = CASE_RUNNABLE_STAGES.index(start)
    planned = CASE_RUNNABLE_STAGES[start_index:]

    normalized: dict[str, dict[str, Any]] = {}
    for stage in planned:
        stage_raw = source.get(stage, {})
        normalized[stage] = sanitize_stage_params(stage, stage_raw)

    return start, normalized


def command_for_stage(case_id: str, stage: str, params: dict[str, Any]) -> str:
    """Build a human-readable equivalent CLI command from effective params."""
    if stage == "build-case":
        cmd = ["kmp-repair", "build-case", case_id, "--artifact-base", params["artifact_base"]]
        if params.get("work_dir"):
            cmd.extend(["--work-dir", params["work_dir"]])
        if params.get("overwrite"):
            cmd.append("--overwrite")
        return " ".join(cmd)

    if stage == "run-before-after":
        cmd = ["kmp-repair", "run-before-after", case_id, "--artifact-base", params["artifact_base"], "--timeout", str(params["timeout_s"])]
        for t in params.get("targets") or []:
            cmd.extend(["--target", t])
        return " ".join(cmd)

    if stage == "analyze-case":
        return f"kmp-repair analyze-case {case_id}"

    if stage == "localize":
        cmd = ["kmp-repair", "localize", case_id, "--top-k", str(params["top_k"])]
        if params.get("no_agent"):
            cmd.append("--no-agent")
        if params.get("provider"):
            cmd.extend(["--provider", params["provider"]])
        if params.get("model"):
            cmd.extend(["--model", params["model"]])
        return " ".join(cmd)

    if stage == "repair":
        cmd = [
            "kmp-repair",
            "repair",
            case_id,
            "--mode",
            params["mode"],
            "--top-k",
            str(params["top_k"]),
            "--patch-strategy",
            params["patch_strategy"],
        ]
        if params.get("provider"):
            cmd.extend(["--provider", params["provider"]])
        if params.get("model"):
            cmd.extend(["--model", params["model"]])
        if params.get("force_patch_attempt", True):
            cmd.append("--force-patch-attempt")
        else:
            cmd.append("--no-force-patch-attempt")
        return " ".join(cmd)

    if stage == "validate":
        cmd = ["kmp-repair", "validate", case_id, "--artifact-base", params["artifact_base"], "--timeout", str(params["timeout_s"])]
        if params.get("attempt_id"):
            cmd.extend(["--attempt-id", params["attempt_id"]])
        if params.get("targets"):
            cmd.extend(["--targets", ",".join(params["targets"])])
        return " ".join(cmd)

    if stage == "explain":
        cmd = ["kmp-repair", "explain", case_id, "--artifact-base", params["artifact_base"]]
        if params.get("provider"):
            cmd.extend(["--provider", params["provider"]])
        if params.get("model"):
            cmd.extend(["--model", params["model"]])
        return " ".join(cmd)

    if stage == "metrics":
        return f"kmp-repair metrics {case_id}"

    if stage == "report":
        cmd = ["kmp-repair", "report", "--output-dir", params["output_dir"], "--format", params["format"]]
        if params.get("modes"):
            cmd.extend(["--modes", ",".join(params["modes"])])
        if params.get("cases"):
            cmd.extend(["--cases", ",".join(params["cases"])])
        return " ".join(cmd)

    raise ValueError(f"Etapa no soportada: {stage}")


def command_for_pipeline(case_id: str, start_from_stage: str, params_by_stage: dict[str, dict[str, Any]]) -> str:
    start_index = CASE_RUNNABLE_STAGES.index(start_from_stage)
    commands = [
        command_for_stage(case_id, stage, params_by_stage[stage])
        for stage in CASE_RUNNABLE_STAGES[start_index:]
    ]
    return " && ".join(commands)
