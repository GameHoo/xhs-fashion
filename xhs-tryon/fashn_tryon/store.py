from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


MANIFEST_VERSION = 1


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_run_dir(output_dir: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    for attempt in range(1000):
        suffix = "" if attempt == 0 else f"_{attempt:02d}"
        run_dir = output_dir / f"tryon_{timestamp}{suffix}"
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            return run_dir
        except FileExistsError:
            continue
    raise RuntimeError(f"Unable to allocate unique run directory under {output_dir}")


def load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    atomic_write_json(run_dir / "manifest.json", manifest)


def write_results_bundle(run_dir: Path, manifest: dict[str, Any]) -> None:
    results_payload, errors_payload = build_results_payload(manifest)
    atomic_write_json(run_dir / "results.json", results_payload)
    atomic_write_json(run_dir / "errors.json", errors_payload)
    (run_dir / "summary.txt").write_text(build_summary_text(results_payload), encoding="utf-8")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def build_results_payload(manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    jobs = manifest["jobs"]
    completed = [job for job in jobs if job["status"] == "completed"]
    failed = [job for job in jobs if job["status"] == "failed"]

    if completed and not failed:
        status = "ok"
    elif completed and failed:
        status = "partial_success"
    elif failed and not completed:
        status = "failed"
    else:
        status = "in_progress"

    payload = {
        "status": status,
        "run_dir": str(Path(manifest["run_dir"]).resolve()),
        "model_name": manifest["model_name"],
        "user_image": manifest["user_image"]["source_path"],
        "submitted": len(jobs),
        "completed": len(completed),
        "failed": len(failed),
        "created_at": manifest["created_at"],
        "updated_at": manifest["updated_at"],
        "jobs": [
            {
                "job_id": job["job_id"],
                "source_image": job["source_image"],
                "prediction_id": job.get("prediction_id"),
                "category": job["category"],
                "status": job["status"],
                "retry_count": job["retry_count"],
                "output_paths": job["output_paths"],
                "error": job.get("error"),
                "credits_used": job.get("credits_used"),
            }
            for job in jobs
        ],
        "errors": [
            {
                "job_id": job["job_id"],
                "prediction_id": job.get("prediction_id"),
                "error_name": (job.get("error") or {}).get("name"),
                "message": (job.get("error") or {}).get("message"),
                "retry_count": job["retry_count"],
            }
            for job in failed
        ],
    }
    return payload, {"errors": payload["errors"]}


def build_summary_text(results_payload: dict[str, Any]) -> str:
    lines = [
        f"status: {results_payload['status']}",
        f"run_dir: {results_payload['run_dir']}",
        f"model_name: {results_payload['model_name']}",
        f"user_image: {results_payload['user_image']}",
        f"submitted: {results_payload['submitted']}",
        f"completed: {results_payload['completed']}",
        f"failed: {results_payload['failed']}",
        "",
        "jobs:",
    ]
    for job in results_payload["jobs"]:
        line = f"- {job['job_id']} [{job['status']}]"
        if job.get("prediction_id"):
            line += f" prediction_id={job['prediction_id']}"
        if job["output_paths"]:
            line += f" outputs={len(job['output_paths'])}"
        if job.get("error"):
            line += f" error={job['error'].get('name')}: {job['error'].get('message')}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def slugify_filename(value: str) -> str:
    stem = Path(value).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem)
    stem = stem.strip("-")
    return stem or "image"
