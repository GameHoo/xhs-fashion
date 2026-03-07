from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .fashn_client import FashnApiError, FashnClient
from .runner import CliRuntimeError, TryonRunner, resolve_model_images, resolve_user_image


logger = logging.getLogger(__name__)


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


ROOT_DESCRIPTION = """\
AI-friendly local CLI for FASHN virtual try-on.

Current command set:
  fashn-tryon run     Submit 1 user image + N reference model images to FASHN.
  fashn-tryon resume  Continue an interrupted run from manifest.json.

Design rules:
  - Use --json when an agent needs machine-readable stdout.
  - Logs and progress always go to stderr.
  - The CLI writes a resumable run directory containing manifest/results/errors/outputs.
"""


RUN_DESCRIPTION = """\
Submit a batch try-on run.

Input contract:
  - Exactly 1 user image.
  - One or more reference model images from --model-image and/or --model-image-dir.
  - The CLI preprocesses every image to JPEG <= 2000px longest edge before upload.

Network contract:
  - Reads FASHN_API_KEY from the environment.
  - POSTs to https://api.fashn.ai/v1/run with model_name=tryon-v1.6.
  - Polls https://api.fashn.ai/v1/status/{id} until completed or failed.

Output contract:
  - Creates <output-dir>/tryon_YYYYMMDD_HHMMSS/
  - Writes manifest.json, results.json, errors.json, summary.txt
  - Writes prepared inputs under prepared/
  - Writes final outputs under results/<job_id>/

Exit behavior:
  - Exit 0 when status is ok or partial_success.
  - Exit 1 when status is failed.
  - Exit 2 on local usage/config/API-request errors before a run completes.
"""


RUN_EPILOG = """\
Examples:
  fashn-tryon run
    --user-image /abs/user.jpg
    --model-image /abs/look-01.jpg
    --model-image /abs/look-02.jpg
    --output-dir /abs/out
    --json

  fashn-tryon run
    --user-image /abs/user.jpg
    --model-image-dir /abs/looks
    --category tops
    --garment-photo-type model
    --mode quality
    --num-samples 2
    --concurrency 2
    --json

JSON stdout statuses:
  ok              All jobs completed successfully.
  partial_success At least one job completed and at least one failed.
  failed          No jobs completed successfully.

Important parameter guidance:
  --category auto
    Recommended default. For on-model full-body images it may swap the whole outfit.
    Use tops or bottoms explicitly when you only want one garment class transferred.

  --garment-photo-type model
    Correct default for this workflow because the reference clothing is worn by a person.
    Use flat-lay only for flat-lay / ghost mannequin product shots.

  --num-samples
    Requests multiple outputs from one submission. Credits are charged per output.
"""


RESUME_DESCRIPTION = """\
Resume an existing run directory.

What resume does:
  - Reads manifest.json from --run-dir
  - Continues polling jobs that were already submitted
  - Retries retryable failed jobs up to --max-retries
  - Never duplicates completed jobs

Use this when the CLI process was interrupted, the terminal closed, or you want to
continue a run after a temporary API/network failure.
"""


RESUME_EPILOG = """\
Example:
  fashn-tryon resume
    --run-dir /abs/out/tryon_20260307_180000
    --json

Expected files in --run-dir:
  manifest.json  Source of truth for job state.
  prepared/      Preprocessed input images.
  results/       Generated output images written during completed jobs.
"""


def add_common_runtime_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="Seconds between GET /status polls for each active prediction.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=900.0,
        help="Maximum seconds to wait for a single prediction before marking it failed.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries for retryable API/network/runtime failures.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="How many predictions to process in parallel. FASHN's default concurrency limit is 6.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON to stdout. Recommended for AI callers.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Increase stderr logging.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fashn-tryon",
        description=ROOT_DESCRIPTION,
        formatter_class=HelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"fashn-tryon {__version__}")

    root_subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = root_subparsers.add_parser(
        "run",
        description=RUN_DESCRIPTION,
        epilog=RUN_EPILOG,
        formatter_class=HelpFormatter,
        help="Submit a new try-on batch run",
    )
    run_parser.add_argument("--user-image", required=True, help="Path to the single user image.")
    run_parser.add_argument(
        "--model-image",
        action="append",
        default=[],
        help="Path to a reference model image. May be repeated.",
    )
    run_parser.add_argument(
        "--model-image-dir",
        help="Directory of reference model images. Supported files are loaded in filename order.",
    )
    run_parser.add_argument("--output-dir", required=True, help="Root directory that will contain a new run folder.")
    run_parser.add_argument(
        "--category",
        choices=["auto", "tops", "bottoms", "one-pieces"],
        default="auto",
        help="FASHN garment category. Use a specific value when auto would be ambiguous.",
    )
    run_parser.add_argument(
        "--garment-photo-type",
        choices=["auto", "model", "flat-lay"],
        default="model",
        help="Tell FASHN whether the clothing reference is worn by a model or shown flat.",
    )
    run_parser.add_argument(
        "--mode",
        choices=["performance", "balanced", "quality"],
        default="balanced",
        help="FASHN speed/quality tradeoff.",
    )
    run_parser.add_argument(
        "--num-samples",
        type=int,
        default=1,
        help="Number of output images requested from each prediction. Valid range: 1-4.",
    )
    run_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="FASHN seed used for deterministic reproducibility.",
    )
    run_parser.add_argument(
        "--output-format",
        choices=["png", "jpeg"],
        default="png",
        help="Format requested from FASHN for final outputs.",
    )
    run_parser.add_argument(
        "--moderation-level",
        choices=["conservative", "permissive", "none"],
        default="permissive",
        help="FASHN garment moderation setting.",
    )
    run_parser.add_argument(
        "--segmentation-free",
        dest="segmentation_free",
        action="store_true",
        default=True,
        help="Keep FASHN segmentation_free=true. Good default for bulkier garments.",
    )
    run_parser.add_argument(
        "--no-segmentation-free",
        dest="segmentation_free",
        action="store_false",
        help="Set FASHN segmentation_free=false if the original clothes are not removed cleanly.",
    )
    add_common_runtime_flags(run_parser)
    run_parser.set_defaults(func=handle_tryon_run)

    resume_parser = root_subparsers.add_parser(
        "resume",
        description=RESUME_DESCRIPTION,
        epilog=RESUME_EPILOG,
        formatter_class=HelpFormatter,
        help="Resume a previous try-on run directory",
    )
    resume_parser.add_argument(
        "--run-dir",
        required=True,
        help="Existing run directory containing manifest.json.",
    )
    add_common_runtime_flags(resume_parser)
    resume_parser.set_defaults(func=handle_tryon_resume)

    return parser


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)


def handle_tryon_run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_common_args(args)
    user_image = resolve_user_image(args.user_image)
    model_images = resolve_model_images(args.model_image, args.model_image_dir)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    runner = _build_runner(args)
    result = runner.create_run(
        user_image=user_image,
        model_images=model_images,
        output_dir=output_dir,
        options={
            "category": args.category,
            "garment_photo_type": args.garment_photo_type,
            "mode": args.mode,
            "num_samples": args.num_samples,
            "seed": args.seed,
            "output_format": args.output_format,
            "segmentation_free": args.segmentation_free,
            "moderation_level": args.moderation_level,
        },
    )
    return result


def handle_tryon_resume(args: argparse.Namespace) -> dict[str, Any]:
    _validate_common_args(args)
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise CliRuntimeError(f"Run directory does not exist: {run_dir}")
    if not (run_dir / "manifest.json").exists():
        raise CliRuntimeError(f"manifest.json not found in run directory: {run_dir}")
    runner = _build_runner(args)
    return runner.resume_run(run_dir)


def _validate_common_args(args: argparse.Namespace) -> None:
    if args.max_retries < 0:
        raise CliRuntimeError("--max-retries must be >= 0")
    if args.concurrency < 1:
        raise CliRuntimeError("--concurrency must be >= 1")
    if getattr(args, "num_samples", 1) < 1 or getattr(args, "num_samples", 1) > 4:
        raise CliRuntimeError("--num-samples must be between 1 and 4")
    if getattr(args, "seed", 0) < 0:
        raise CliRuntimeError("--seed must be >= 0")
    if args.poll_interval <= 0:
        raise CliRuntimeError("--poll-interval must be > 0")
    if args.poll_timeout <= 0:
        raise CliRuntimeError("--poll-timeout must be > 0")


def _build_runner(args: argparse.Namespace) -> TryonRunner:
    api_key = os.getenv("FASHN_API_KEY")
    if not api_key:
        raise CliRuntimeError("FASHN_API_KEY is not set")
    client = FashnClient(api_key=api_key)
    return TryonRunner(
        client=client,
        model_name="tryon-v1.6",
        poll_interval=args.poll_interval,
        poll_timeout=args.poll_timeout,
        max_retries=args.max_retries,
        request_concurrency=args.concurrency,
        verbose=args.verbose,
    )


def emit_result(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    print(f"status: {payload['status']}")
    print(f"run_dir: {payload['run_dir']}")
    print(f"submitted: {payload['submitted']}")
    print(f"completed: {payload['completed']}")
    print(f"failed: {payload['failed']}")
    for job in payload["jobs"]:
        outputs = ", ".join(job["output_paths"]) if job["output_paths"] else "-"
        print(f"{job['job_id']} [{job['status']}] outputs={outputs}")


def emit_error(message: str, *, as_json: bool, details: dict[str, Any] | None = None) -> None:
    payload = {
        "status": "error",
        "message": message,
    }
    if details:
        payload["details"] = details
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(getattr(args, "verbose", False))

    try:
        payload = args.func(args)
    except FashnApiError as exc:
        emit_error(
            f"FASHN API request failed: HTTP {exc.status_code} {exc.error_code} {exc.message}",
            as_json=getattr(args, "json", False),
            details=exc.to_dict(),
        )
        return 2
    except CliRuntimeError as exc:
        emit_error(str(exc), as_json=getattr(args, "json", False))
        return 2
    except KeyboardInterrupt:
        emit_error("Interrupted by user", as_json=getattr(args, "json", False))
        return 130

    emit_result(payload, as_json=getattr(args, "json", False))
    return 0 if payload["status"] in {"ok", "partial_success", "in_progress"} else 1
