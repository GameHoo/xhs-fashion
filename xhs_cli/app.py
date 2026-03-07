from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import Any

import typer

from . import __version__
from .runtime import CLIError, login_status_payload, reset_login, search_images, start_login


class ImageMode(str, Enum):
    cover = "cover"
    detail = "detail"


class LoginPolicy(str, Enum):
    return_ = "return"
    wait = "wait"
    fail = "fail"


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(payload.get("status", "ok"))
    for key in ("message", "download_dir", "qr_image_path", "expires_at"):
        if payload.get(key):
            typer.echo(f"{key}: {payload[key]}")


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help=(
        "AI-friendly XiaoHongShu CLI.\n\n"
        "Runtime: xhs-cli -> mcporter -> xiaohongshu-mcp\n\n"
        "Design rules:\n"
        "- stdout prefers JSON for agents\n"
        "- login can return QR payload instead of blocking forever\n"
        "- search downloads images to a real directory and writes metadata files\n"
        "- page/page-size is batch pagination over the current search batch\n\n"
        "Machine-readable conventions:\n"
        "- success path returns status=ok or partial\n"
        "- auth path returns status=requires_login plus qr_image_path when available\n"
        "- service/runtime failures use non-zero exit code and status=error/service_unavailable\n"
        "- search output always includes download_dir so callers can locate files on disk\n\n"
        "Auth rules:\n"
        "- `login status` reports local persisted state without forcing a live search\n"
        "- `search images` is the authoritative auth check for actual data access\n\n"
        "Common flows:\n"
        "  xhs login start --json\n"
        "  xhs login status --json\n"
        "  xhs search images --keyword \"男士 宽松 穿搭\" --image-dir ~/Downloads/xhs --json\n"
    ),
)
login_app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode=None,
    help=(
        "Login management commands.\n\n"
        "Use `login start` to generate a QR code, `login status` to inspect the locally\n"
        "persisted login state, and `login reset` to clear the local XiaoHongShu\n"
        "cookie state before re-login.\n\n"
        "Returned status values:\n"
        "- logged_in\n"
        "- logged_out\n"
        "- pending_login\n\n"
        "Auth validation details, when present, are reported in the `auth_validation` field."
    ),
)
search_app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode=None,
    help=(
        "Search and image download commands.\n\n"
        "Use `search images` to search notes by keyword and download either search-card\n"
        "cover images or full detail images.\n\n"
        "Important: current pagination is batch pagination over the result set returned by\n"
        "the underlying search call. It is not XiaoHongShu's real cursor pagination.\n\n"
        "Primary output fields:\n"
        "- status\n"
        "- keyword\n"
        "- download_dir\n"
        "- total_in_batch\n"
        "- returned\n"
        "- image_count\n"
        "- items[].feed_id\n"
        "- items[].title\n"
        "- items[].image_paths"
    ),
)
app.add_typer(login_app, name="login")
app.add_typer(search_app, name="search")


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print the CLI version and exit.",
    ),
) -> None:
    """Top-level callback used only for shared options."""
    if version:
        typer.echo(__version__)
        raise typer.Exit(0)


@login_app.command("start")
def login_start(
    force: bool = typer.Option(
        False,
        "--force",
        help="Clear existing XiaoHongShu cookies before generating a new login QR code.",
    ),
    qr_output: Path | None = typer.Option(
        None,
        "--qr-output",
        help="Optional PNG file path for the login QR code. Defaults to ~/.xhs-cli/login-qrcode.png.",
    ),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Wait until the QR code is scanned successfully instead of returning immediately.",
    ),
    timeout: int = typer.Option(
        120,
        "--timeout",
        min=1,
        help="Maximum number of seconds to wait when --wait is enabled.",
    ),
    json_output: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Emit structured JSON to stdout. Recommended for AI/agent callers.",
    ),
) -> None:
    """Generate a XiaoHongShu login QR code.

    Behavior:
    - If a valid local cookie is confirmed and --force is not set, returns `logged_in`.
    - If a local cookie exists but cannot be validated right now, returns `logged_in` with
      `auth_validation=unknown` and does not destroy the existing cookie.
    - Otherwise calls the underlying MCP tool to generate a QR code.
    - When --wait is used, this command polls until login succeeds or times out.

    Output JSON fields:
    - status: logged_in | pending_login
    - auth_validation: ok | unknown | not_run, when relevant
    - qr_image_path: local PNG path when a QR code is generated
    - expires_at: QR expiry time when available

    Examples:
      xhs login start --json
      xhs login start --force --qr-output /tmp/xhs-login.png --json
      xhs login start --wait --timeout 180 --json
    """
    try:
        payload = start_login(force=force, qr_output=qr_output, wait=wait, timeout_seconds=timeout)
        emit(payload, json_output)
    except CLIError as exc:
        emit(exc.payload, json_output)
        raise typer.Exit(exc.exit_code) from exc


@login_app.command("status")
def login_status(
    json_output: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Emit structured JSON to stdout. Recommended for AI/agent callers.",
    ),
) -> None:
    """Report the locally persisted XiaoHongShu login state.

    Status resolution:
    - `logged_out`: local cookie file missing and no pending QR exists
    - `pending_login`: a fresh QR code exists and has not expired yet
    - `logged_in`: local cookie file exists

    Notes:
    - This command does not force a live search probe, because that path is less stable than
      the real search command and can produce false negatives.
    - Treat `search images` as the authoritative auth check for actual data access.

    Output JSON fields:
    - status
    - cookie_path: present for logged_in
    - auth_validation: currently `not_run` for status checks
    - qr_image_path: only present for pending_login
    - expires_at: only present for pending_login
    """
    try:
        payload = login_status_payload()
        emit(payload, json_output)
    except CLIError as exc:
        emit(exc.payload, json_output)
        raise typer.Exit(exc.exit_code) from exc


@login_app.command("reset")
def login_reset(
    json_output: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Emit structured JSON to stdout. Recommended for AI/agent callers.",
    ),
) -> None:
    """Delete the current local XiaoHongShu cookie state.

    This is the safe way to trigger a re-login flow before calling `xhs login start`
    or any search command with `--login-policy wait`.

    Output JSON fields:
    - status: reset_done
    """
    try:
        payload = reset_login()
        emit(payload, json_output)
    except CLIError as exc:
        emit(exc.payload, json_output)
        raise typer.Exit(exc.exit_code) from exc


@search_app.command("images")
def search_images_command(
    keyword: str = typer.Option(
        ...,
        "--keyword",
        help="Search keyword. Example: '男士 宽松 穿搭'.",
    ),
    image_dir: Path = typer.Option(
        ...,
        "--image-dir",
        help="Root directory for downloaded images and metadata files.",
    ),
    page: int = typer.Option(
        1,
        "--page",
        min=1,
        help="1-based page number over the current search batch.",
    ),
    page_size: int = typer.Option(
        20,
        "--page-size",
        min=1,
        help="How many note results to return from the current search batch.",
    ),
    image_mode: ImageMode = typer.Option(
        ImageMode.detail,
        "--image-mode",
        help=(
            "Image extraction mode. 'cover' downloads only search-card cover images. "
            "'detail' opens each note and tries to download the full image set."
        ),
    ),
    fallback_to_cover: bool = typer.Option(
        True,
        "--fallback-to-cover/--no-fallback-to-cover",
        help="When detail mode fails for a note, fall back to downloading the cover image.",
    ),
    login_policy: LoginPolicy = typer.Option(
        LoginPolicy.return_,
        "--login-policy",
        help=(
            "How to behave when search requires login. "
            "'return' emits a requires_login payload with QR path. "
            "'wait' blocks for login and retries once. "
            "'fail' exits immediately."
        ),
    ),
    login_timeout: int = typer.Option(
        120,
        "--login-timeout",
        min=1,
        help="Maximum seconds to wait for login when --login-policy wait is used.",
    ),
    json_output: bool = typer.Option(
        True,
        "--json/--no-json",
        help="Emit structured JSON to stdout. Recommended for AI/agent callers.",
    ),
) -> None:
    """Search XiaoHongShu and download images for each returned note.

    Implementation summary:
    - Reuses the local `xiaohongshu-mcp` service through `mcporter call`.
    - Filters out non-note search artifacts such as `hot_query`.
    - `page/page-size` slices the returned batch; it is not real upstream cursor paging.
    - In `detail` mode, some note pages may be inaccessible; when fallback is enabled the
      command downloads the cover image instead of failing the whole run.
    - If the search call itself reports auth failure, the command can force a fresh login
      flow instead of trusting any stale local cookie state.

    Files written under --image-dir:
    - results.json
    - results.csv
    - summary.txt
    - search_raw.json
    - errors.json
    - images/*

    Output JSON fields:
    - status: ok | partial | requires_login | error
    - download_dir: actual timestamped directory containing files
    - total_in_batch: number of note-type results in the fetched batch
    - returned: number of items selected by page/page-size
    - image_count: number of successfully downloaded image files
    - items[].detail_status: ok | cover_only | fallback_cover_only | failed
    """
    try:
        payload = search_images(
            keyword=keyword,
            image_dir=image_dir,
            page=page,
            page_size=page_size,
            image_mode=image_mode.value,
            fallback_to_cover=fallback_to_cover,
            login_policy=login_policy.value,
            login_timeout=login_timeout,
        )
        emit(payload, json_output)
    except CLIError as exc:
        emit(exc.payload, json_output)
        raise typer.Exit(exc.exit_code) from exc
