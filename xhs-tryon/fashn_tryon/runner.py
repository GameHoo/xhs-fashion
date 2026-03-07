from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import requests

from .fashn_client import FashnApiError, FashnClient
from .image_prep import (
    encode_data_uri,
    guess_extension_from_mime,
    is_supported_image,
    preprocess_image,
    write_data_uri,
)
from .store import (
    MANIFEST_VERSION,
    build_results_payload,
    create_run_dir,
    load_manifest,
    now_iso,
    slugify_filename,
    write_manifest,
    write_results_bundle,
)

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
RETRYABLE_RUNTIME_ERRORS = {"PipelineError", "UnavailableError", "ThirdPartyError"}
NON_RETRYABLE_RUNTIME_ERRORS = {
    "ImageLoadError",
    "InputValidationError",
    "ContentModerationError",
    "PoseError",
}
IN_PROGRESS_STATUSES = {"submitted", "starting", "in_queue", "processing"}
TERMINAL_STATUSES = {"completed", "failed"}


class CliRuntimeError(RuntimeError):
    """Raised for local validation or processing failures."""


class TryonRunner:
    def __init__(
        self,
        *,
        client: FashnClient,
        model_name: str,
        poll_interval: float,
        poll_timeout: float,
        max_retries: int,
        request_concurrency: int,
        verbose: bool = False,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.max_retries = max_retries
        self.request_concurrency = request_concurrency
        self.verbose = verbose
        self._lock = threading.Lock()

    def create_run(
        self,
        *,
        user_image: Path,
        model_images: list[Path],
        output_dir: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        run_dir = create_run_dir(output_dir)
        prepared_dir = run_dir / "prepared"
        prepared_user_path = prepared_dir / "user.jpg"
        user_meta = preprocess_image(user_image, prepared_user_path)

        jobs: list[dict[str, Any]] = []
        seen_slugs: dict[str, int] = {}
        for index, image_path in enumerate(model_images, start=1):
            base_slug = slugify_filename(image_path.name)
            seen_slugs[base_slug] = seen_slugs.get(base_slug, 0) + 1
            suffix = seen_slugs[base_slug]
            job_slug = base_slug if suffix == 1 else f"{base_slug}-{suffix}"
            job_id = f"look_{index:04d}_{job_slug}"
            prepared_path = prepared_dir / f"{job_id}.jpg"
            prep_meta = preprocess_image(image_path, prepared_path)
            jobs.append(
                {
                    "job_id": job_id,
                    "source_image": str(image_path.resolve()),
                    "prepared_image": prep_meta["prepared_path"],
                    "prepared_metadata": prep_meta,
                    "category": options["category"],
                    "garment_photo_type": options["garment_photo_type"],
                    "mode": options["mode"],
                    "num_samples": options["num_samples"],
                    "seed": options["seed"],
                    "output_format": options["output_format"],
                    "segmentation_free": options["segmentation_free"],
                    "moderation_level": options["moderation_level"],
                    "status": "created",
                    "prediction_id": None,
                    "retry_count": 0,
                    "output_paths": [],
                    "remote_output": [],
                    "credits_used": None,
                    "error": None,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "run_dir": str(run_dir.resolve()),
            "model_name": self.model_name,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "user_image": user_meta,
            "options": {
                "category": options["category"],
                "garment_photo_type": options["garment_photo_type"],
                "mode": options["mode"],
                "num_samples": options["num_samples"],
                "seed": options["seed"],
                "output_format": options["output_format"],
                "segmentation_free": options["segmentation_free"],
                "moderation_level": options["moderation_level"],
                "poll_interval": self.poll_interval,
                "poll_timeout": self.poll_timeout,
                "max_retries": self.max_retries,
                "request_concurrency": self.request_concurrency,
            },
            "jobs": jobs,
        }
        write_manifest(run_dir, manifest)
        self._process_manifest(manifest)
        return build_results_payload(manifest)[0]

    def resume_run(self, run_dir: Path) -> dict[str, Any]:
        manifest = load_manifest(run_dir)
        self._process_manifest(manifest)
        return build_results_payload(manifest)[0]

    def _process_manifest(self, manifest: dict[str, Any]) -> None:
        runnable_jobs = [
            job["job_id"]
            for job in manifest["jobs"]
            if self._job_needs_work(job)
        ]
        if runnable_jobs:
            with ThreadPoolExecutor(max_workers=self.request_concurrency) as executor:
                list(executor.map(lambda job_id: self._process_job(manifest, job_id), runnable_jobs))
        manifest["updated_at"] = now_iso()
        write_manifest(Path(manifest["run_dir"]), manifest)
        write_results_bundle(Path(manifest["run_dir"]), manifest)

    def _process_job(self, manifest: dict[str, Any], job_id: str) -> None:
        while True:
            job = self._get_job(manifest, job_id)
            if job["status"] == "completed":
                return
            if job["status"] == "failed" and not self._can_retry_runtime(job):
                return

            try:
                if not job.get("prediction_id") or job["status"] in {"created", "failed"}:
                    self._submit_job(manifest, job)

                status_payload, headers = self._poll_prediction(manifest, job)
                status = str(status_payload.get("status") or "failed")
                error = status_payload.get("error")
                if status == "completed":
                    self._save_outputs(manifest, job, status_payload, headers)
                    return

                if status == "failed":
                    if self._handle_remote_failure(manifest, job, error):
                        continue
                    return

                # This should not happen because _poll_prediction only exits on terminal states.
                self._update_job(
                    manifest,
                    job["job_id"],
                    status=status,
                    error={
                        "name": "UnexpectedStatus",
                        "message": f"Unexpected terminal status returned: {status}",
                    },
                )
                return
            except Exception as exc:  # requests/network/runtime local failures
                if self._handle_local_failure(manifest, job, exc):
                    continue
                return

    def _submit_job(self, manifest: dict[str, Any], job: dict[str, Any]) -> None:
        payload = {
            "model_name": self.model_name,
            "inputs": {
                "model_image": encode_data_uri(Path(manifest["user_image"]["prepared_path"])),
                "garment_image": encode_data_uri(Path(job["prepared_image"])),
                "category": job["category"],
                "garment_photo_type": job["garment_photo_type"],
                "mode": job["mode"],
                "num_samples": job["num_samples"],
                "seed": job["seed"],
                "output_format": job["output_format"],
                "segmentation_free": job["segmentation_free"],
                "moderation_level": job["moderation_level"],
                "return_base64": True,
            },
        }

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.run_prediction(payload)
                prediction_id = str(response.get("id") or "")
                if not prediction_id:
                    raise CliRuntimeError("FASHN API returned no prediction id")
                self._update_job(
                    manifest,
                    job["job_id"],
                    status="submitted",
                    prediction_id=prediction_id,
                    error=None,
                )
                return
            except (FashnApiError, requests.RequestException, CliRuntimeError) as exc:
                if attempt >= self.max_retries or not self._is_retryable_submit_error(exc):
                    raise
                sleep_seconds = 2 ** attempt
                logger.warning(
                    "submit retry job_id=%s attempt=%s sleep=%ss reason=%s",
                    job["job_id"],
                    attempt + 1,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)

    def _poll_prediction(
        self,
        manifest: dict[str, Any],
        job: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        started = time.monotonic()
        attempts = 0
        prediction_id = str(job["prediction_id"])
        while True:
            if time.monotonic() - started > self.poll_timeout:
                raise CliRuntimeError(
                    f"Polling timed out after {int(self.poll_timeout)}s for prediction {prediction_id}"
                )
            try:
                status_payload, headers = self.client.get_status(prediction_id)
            except (FashnApiError, requests.RequestException) as exc:
                attempts += 1
                if attempts > self.max_retries or not self._is_retryable_submit_error(exc):
                    raise
                sleep_seconds = min(2 ** (attempts - 1), 8)
                logger.warning(
                    "poll retry job_id=%s prediction_id=%s attempt=%s sleep=%ss reason=%s",
                    job["job_id"],
                    prediction_id,
                    attempts,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)
                continue

            status = str(status_payload.get("status") or "")
            credits_used = headers.get("x-fashn-credits-used")
            self._update_job(
                manifest,
                job["job_id"],
                status=status,
                credits_used=credits_used,
                last_polled_at=now_iso(),
            )
            if status in TERMINAL_STATUSES:
                return status_payload, headers
            if status not in IN_PROGRESS_STATUSES:
                raise CliRuntimeError(f"Unexpected status from FASHN API: {status!r}")
            time.sleep(self.poll_interval)

    def _save_outputs(
        self,
        manifest: dict[str, Any],
        job: dict[str, Any],
        status_payload: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        normalized_outputs = self._normalize_outputs(status_payload.get("output"))
        if not normalized_outputs:
            raise CliRuntimeError(
                f"Prediction {job.get('prediction_id') or job['job_id']} completed without any output images"
            )
        result_dir = Path(manifest["run_dir"]) / "results" / job["job_id"]
        output_paths: list[str] = []
        remote_output: list[str] = []

        for index, item in enumerate(normalized_outputs, start=1):
            if isinstance(item, str) and item.startswith("data:image/"):
                extension = guess_extension_from_mime(
                    item.split(";", 1)[0].replace("data:", ""),
                    default=f".{job['output_format']}",
                )
                target_path = result_dir / f"output_{index:02d}{extension}"
                write_data_uri(item, target_path)
                output_paths.append(str(target_path.resolve()))
                continue

            if isinstance(item, str) and item.startswith("http"):
                content, content_type = self.client.download_file(item)
                extension = guess_extension_from_mime(content_type, default=f".{job['output_format']}")
                target_path = result_dir / f"output_{index:02d}{extension}"
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(content)
                output_paths.append(str(target_path.resolve()))
                remote_output.append(item)
                continue

            raise CliRuntimeError(f"Unsupported output item returned by FASHN API: {item!r}")

        if not output_paths:
            raise CliRuntimeError(
                f"Prediction {job.get('prediction_id') or job['job_id']} completed without saved output files"
            )

        self._update_job(
            manifest,
            job["job_id"],
            status="completed",
            output_paths=output_paths,
            remote_output=remote_output,
            credits_used=headers.get("x-fashn-credits-used"),
            error=None,
        )

    def _normalize_outputs(self, output: Any) -> list[str]:
        if output is None:
            return []
        if isinstance(output, str):
            return [output]
        if isinstance(output, list):
            flat: list[str] = []
            for item in output:
                flat.extend(self._normalize_outputs(item))
            return flat
        if isinstance(output, dict):
            if "images" in output:
                return self._normalize_outputs(output["images"])
            if "output" in output:
                return self._normalize_outputs(output["output"])
            if "url" in output:
                return [str(output["url"])]
            if "base64" in output:
                return [str(output["base64"])]
        raise CliRuntimeError(f"Unsupported output payload returned by FASHN API: {output!r}")

    def _handle_remote_failure(
        self,
        manifest: dict[str, Any],
        job: dict[str, Any],
        error: Any,
    ) -> bool:
        error = error or {"name": "UnknownRuntimeError", "message": "Prediction failed without error payload"}
        error_name = str(error.get("name") or "UnknownRuntimeError")
        self._update_job(
            manifest,
            job["job_id"],
            status="failed",
            error={"name": error_name, "message": str(error.get("message") or "")},
        )
        if error_name in RETRYABLE_RUNTIME_ERRORS and self._can_retry_runtime(job):
            self._prepare_retry(manifest, job, reason=error_name)
            return True
        return False

    def _handle_local_failure(
        self,
        manifest: dict[str, Any],
        job: dict[str, Any],
        exc: Exception,
    ) -> bool:
        error_name = type(exc).__name__
        message = str(exc)
        if self._is_retryable_submit_error(exc) and self._can_retry_runtime(job):
            self._prepare_retry(manifest, job, reason=error_name)
            return True
        self._update_job(
            manifest,
            job["job_id"],
            status="failed",
            error={"name": error_name, "message": message},
        )
        return False

    def _prepare_retry(self, manifest: dict[str, Any], job: dict[str, Any], *, reason: str) -> None:
        next_retry = int(job["retry_count"]) + 1
        logger.warning("retrying job_id=%s retry_count=%s reason=%s", job["job_id"], next_retry, reason)
        self._update_job(
            manifest,
            job["job_id"],
            status="created",
            retry_count=next_retry,
            prediction_id=None,
            output_paths=[],
            remote_output=[],
            error={"name": reason, "message": f"Retrying after {reason}"},
        )

    def _job_needs_work(self, job: dict[str, Any]) -> bool:
        if job["status"] in {"created", "submitted", "starting", "in_queue", "processing"}:
            return True
        if job["status"] == "failed" and self._can_retry_runtime(job):
            return True
        return False

    def _can_retry_runtime(self, job: dict[str, Any]) -> bool:
        if int(job.get("retry_count") or 0) >= self.max_retries:
            return False
        error = job.get("error") or {}
        error_name = error.get("name")
        if job["status"] != "failed":
            return True
        if not error_name:
            return True
        if error_name in NON_RETRYABLE_RUNTIME_ERRORS:
            return False
        return error_name in RETRYABLE_RUNTIME_ERRORS or error_name not in NON_RETRYABLE_RUNTIME_ERRORS

    def _is_retryable_submit_error(self, exc: Exception) -> bool:
        if isinstance(exc, requests.RequestException):
            return True
        if isinstance(exc, FashnApiError):
            return exc.status_code in RETRYABLE_HTTP_CODES
        if isinstance(exc, CliRuntimeError):
            return False
        return False

    def _get_job(self, manifest: dict[str, Any], job_id: str) -> dict[str, Any]:
        with self._lock:
            for job in manifest["jobs"]:
                if job["job_id"] == job_id:
                    return job
        raise KeyError(f"Unknown job_id: {job_id}")

    def _update_job(self, manifest: dict[str, Any], job_id: str, **changes: Any) -> None:
        with self._lock:
            for job in manifest["jobs"]:
                if job["job_id"] != job_id:
                    continue
                job.update(changes)
                job["updated_at"] = now_iso()
                manifest["updated_at"] = now_iso()
                write_manifest(Path(manifest["run_dir"]), manifest)
                return
        raise KeyError(f"Unknown job_id: {job_id}")


def resolve_model_images(paths: Iterable[str], directory: str | None) -> list[Path]:
    images: list[Path] = []
    for value in paths:
        path = Path(value).expanduser().resolve()
        if not is_supported_image(path):
            raise CliRuntimeError(f"Unsupported model image: {path}")
        images.append(path)

    if directory:
        directory_path = Path(directory).expanduser().resolve()
        if not directory_path.exists():
            raise CliRuntimeError(f"Model image directory does not exist: {directory_path}")
        if not directory_path.is_dir():
            raise CliRuntimeError(f"Model image directory is not a directory: {directory_path}")
        dir_images = sorted(path for path in directory_path.iterdir() if is_supported_image(path))
        images.extend(dir_images)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in images:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    if not unique:
        raise CliRuntimeError("No model images provided. Use --model-image or --model-image-dir.")
    return unique


def resolve_user_image(path: str) -> Path:
    user_path = Path(path).expanduser().resolve()
    if not is_supported_image(user_path):
        raise CliRuntimeError(f"Unsupported user image: {user_path}")
    return user_path
