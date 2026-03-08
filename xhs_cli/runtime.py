from __future__ import annotations

import base64
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path(os.getenv("XHS_CLI_STATE_DIR", str(Path.home() / ".xhs-cli")))
STATE_DIR = DEFAULT_STATE_DIR
STATE_FILE = STATE_DIR / "state.json"
SERVICE_URL_FILE = STATE_DIR / "service_url"
MCP_PORT_FILE = STATE_DIR / "mcp_port"


def resolve_service_url() -> str:
    configured = os.getenv("XHS_CLI_SERVICE_URL")
    if configured:
        return configured

    for path in (SERVICE_URL_FILE, MCP_PORT_FILE):
        try:
            if not path.exists():
                continue
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not value:
            continue
        if path == SERVICE_URL_FILE:
            return value
        if value.isdigit():
            return f"http://localhost:{value}/mcp"

    return "http://localhost:18060/mcp"


SERVICE_URL = resolve_service_url()
LAUNCHD_LABEL = os.getenv("XHS_CLI_LAUNCHD_LABEL", "com.codex.xiaohongshu-mcp")
COOKIE_FILE = Path(
    os.getenv(
        "XHS_CLI_COOKIE_FILE",
        str(Path.home() / ".agent-reach" / "xiaohongshu-mcp" / "data" / "cookies.json"),
    )
)
DEFAULT_QR_PATH = STATE_DIR / "login-qrcode.png"
LOGIN_PROBE_KEYWORD = os.getenv("XHS_CLI_LOGIN_PROBE_KEYWORD", "穿搭")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
AUTH_HINTS = (
    "未登录",
    "扫码登录",
    "login",
    "cookie",
    "failed to load cookies",
    "请打开小红书app扫码查看",
)

# --- Timeouts (seconds) ---
SUBPROCESS_TIMEOUT = 45          # Default hard timeout for subprocesses
MCPORTER_TIMEOUT_MS = 30000      # Default mcporter --timeout (milliseconds)
SERVICE_PROBE_TIMEOUT = 3        # HTTP health-check timeout
SERVICE_START_RETRIES = 5        # Attempts to wait for service startup
SERVICE_START_INTERVAL = 1.0     # Sleep between service startup checks
IMAGE_DOWNLOAD_TIMEOUT = 30      # HTTP timeout for image downloads
IMAGE_MAX_BYTES = 50 * 1024 * 1024  # 50 MB max image download size
LOGIN_POLL_INTERVAL = 3.0        # Sleep between login poll attempts
SEARCH_DOWNLOAD_DELAY = 0.2     # Sleep between consecutive image downloads


class CLIError(Exception):
    def __init__(self, message: str, exit_code: int, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.payload = payload or {"status": "error", "message": message}


class AuthRequiredError(CLIError):
    def __init__(self, message: str = "Login required"):
        super().__init__(message, exit_code=10, payload={"status": "requires_login"})


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, Any]:
    ensure_state_dir()
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt state file %s, returning empty state", STATE_FILE)
        return {}


def save_state(state: dict[str, Any]) -> None:
    ensure_state_dir()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state_pending_login() -> None:
    state = load_state()
    for key in ("last_qr_path", "last_qr_expires_at", "last_qr_created_at"):
        state.pop(key, None)
    save_state(state)


def looks_like_auth_error(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in AUTH_HINTS)


def json_dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def run_command(
    args: list[str],
    timeout: int = 45,
    *,
    failure_message: str = "Command failed",
    exit_code: int = 21,
    status: str = "error",
) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CLIError(
            f"{failure_message}: timed out after {timeout}s",
            exit_code=exit_code,
            payload={
                "status": status,
                "message": failure_message,
                "command": args,
                "timeout_seconds": timeout,
                "stdout": ((exc.stdout or "") if isinstance(exc.stdout, str) else "").strip(),
                "stderr": ((exc.stderr or "") if isinstance(exc.stderr, str) else "").strip(),
            },
        ) from exc
    except OSError as exc:
        raise CLIError(
            f"{failure_message}: {exc}",
            exit_code=exit_code,
            payload={
                "status": status,
                "message": failure_message,
                "command": args,
                "error": str(exc),
            },
        ) from exc
    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def service_alive() -> bool:
    request = Request(SERVICE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=SERVICE_PROBE_TIMEOUT):
            return True
    except HTTPError as exc:
        # 400/405 mean the server is running but rejected our probe method/payload
        return exc.code in {400, 405}
    except (URLError, OSError):
        return False


def ensure_service() -> None:
    logger.debug("Checking service availability at %s", SERVICE_URL)
    if shutil.which("mcporter") is None:
        raise CLIError(
            "mcporter is not installed or not in PATH",
            exit_code=20,
            payload={"status": "service_unavailable", "message": "mcporter not found"},
        )
    if service_alive():
        return

    uid = os.getuid()
    label = f"gui/{uid}/{LAUNCHD_LABEL}"
    logger.info("Service not alive, restarting via launchctl: %s", label)
    run_command(
        ["launchctl", "kickstart", "-k", label],
        timeout=15,
        failure_message="Failed to restart xiaohongshu-mcp service",
        exit_code=20,
        status="service_unavailable",
    )

    for _ in range(SERVICE_START_RETRIES):
        if service_alive():
            return
        time.sleep(SERVICE_START_INTERVAL)

    raise CLIError(
        "xiaohongshu-mcp service is not reachable",
        exit_code=20,
        payload={
            "status": "service_unavailable",
            "message": "Unable to reach local xiaohongshu-mcp service",
            "service_url": SERVICE_URL,
        },
    )


def mcporter_call(expr: str, *, timeout_ms: int = MCPORTER_TIMEOUT_MS, output: str | None = None, hard_timeout: int = SUBPROCESS_TIMEOUT) -> CommandResult:
    args = ["mcporter", "call", expr, "--timeout", str(timeout_ms)]
    if output:
        args.extend(["--output", output])
    return run_command(
        args,
        timeout=hard_timeout,
        failure_message="mcporter call failed",
        exit_code=21,
        status="error",
    )


def extract_json_blob(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty output")
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(stripped[start : end + 1])


def parse_qr_response(text: str) -> tuple[str, str, str]:
    text_match = re.search(r"text: '([^']+)'", text, re.S)
    data_match = re.search(r"data: '([^']+)'", text, re.S)
    if not text_match or not data_match:
        raise ValueError("Unable to parse QR response")
    login_text = text_match.group(1)
    image_b64 = data_match.group(1)
    expiry_match = re.search(r"在 ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}) 前扫码登录", login_text)
    expires_at = expiry_match.group(1) if expiry_match else ""
    return login_text, expires_at, image_b64


def quote_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_note_url(feed_id: str, token: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{quote(feed_id)}?xsec_token={quote(token, safe='')}&xsec_source=pc_search"


def sanitize_url(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def infer_extension(content_type: str | None, url: str) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    lowered = url.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"):
        if ext in lowered:
            return ".jpg" if ext == ".jpeg" else ext
    return ".img"


def download_image(url: str, dest_base: Path, *, max_bytes: int = IMAGE_MAX_BYTES) -> Path:
    request = Request(
        sanitize_url(url),
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.xiaohongshu.com/",
        },
    )
    with urlopen(request, timeout=IMAGE_DOWNLOAD_TIMEOUT) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"Image too large: {content_length} bytes (limit {max_bytes})")
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError(f"Image exceeds size limit of {max_bytes} bytes")
        dest = dest_base.with_suffix(infer_extension(response.headers.get("Content-Type"), url))
    dest.write_bytes(content)
    return dest


def pending_login_state() -> tuple[bool, str, Path]:
    state = load_state()
    expires_at = state.get("last_qr_expires_at", "")
    qr_path = Path(state.get("last_qr_path", DEFAULT_QR_PATH))
    if not expires_at:
        return False, "", qr_path
    try:
        expiry = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False, "", qr_path
    if datetime.now() < expiry and qr_path.exists():
        return True, expires_at, qr_path
    return False, expires_at, qr_path


def probe_logged_in() -> str:
    logger.debug("Probing login status with keyword: %s", LOGIN_PROBE_KEYWORD)
    ensure_service()
    result = mcporter_call(
        f"xiaohongshu.search_feeds(keyword: {quote_literal(LOGIN_PROBE_KEYWORD)})",
        timeout_ms=20000,
        hard_timeout=25,
    )
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode == 0:
        try:
            payload = extract_json_blob(result.stdout)
        except ValueError:
            if looks_like_auth_error(text):
                return "logged_out"
            return "unknown"
        if isinstance(payload.get("feeds"), list):
            return "logged_in"
        return "unknown"
    if looks_like_auth_error(text):
        return "logged_out"
    return "unknown"


def pending_login_payload(expires_at: str, qr_path: Path) -> dict[str, Any]:
    return {
        "status": "pending_login",
        "qr_image_path": str(qr_path),
        "expires_at": expires_at,
    }


def login_status_payload() -> dict[str, Any]:
    pending, expires_at, qr_path = pending_login_state()
    if COOKIE_FILE.exists():
        if pending:
            clear_state_pending_login()
        return {
            "status": "logged_in",
            "cookie_path": str(COOKIE_FILE),
            "auth_validation": "not_run",
        }
    if pending:
        return pending_login_payload(expires_at, qr_path)
    return {"status": "logged_out"}


def delete_file_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def reset_login() -> dict[str, Any]:
    ensure_service()
    result = mcporter_call("xiaohongshu.delete_cookies()", timeout_ms=15000, hard_timeout=20)
    if result.returncode != 0 and not looks_like_auth_error(result.stdout + result.stderr):
        raise CLIError(
            "Failed to reset XiaoHongShu cookies",
            exit_code=21,
            payload={
                "status": "error",
                "message": "Failed to reset XiaoHongShu cookies",
                "stderr": result.stderr.strip(),
            },
        )
    delete_file_if_exists(DEFAULT_QR_PATH)
    clear_state_pending_login()
    return {"status": "reset_done"}


def start_login(*, force: bool = False, qr_output: Path | None = None, wait: bool = False, timeout_seconds: int = 120) -> dict[str, Any]:
    ensure_service()
    if force:
        reset_login()
    else:
        pending, expires_at, qr_path = pending_login_state()
        if pending:
            return pending_login_payload(expires_at, qr_path)
        if COOKIE_FILE.exists():
            auth_status = probe_logged_in()
            if auth_status == "logged_in":
                clear_state_pending_login()
                return {"status": "logged_in", "cookie_path": str(COOKIE_FILE), "auth_validation": "ok"}
            if auth_status == "unknown":
                return {
                    "status": "logged_in",
                    "cookie_path": str(COOKIE_FILE),
                    "auth_validation": "unknown",
                    "message": "Local cookie exists but current auth was not validated. Use --force to regenerate login QR.",
                }
            reset_login()

    result = mcporter_call("xiaohongshu.get_login_qrcode()", timeout_ms=20000, output="json", hard_timeout=25)
    if result.returncode != 0:
        raise CLIError(
            "Failed to generate login QR code",
            exit_code=21,
            payload={
                "status": "error",
                "message": "Failed to generate login QR code",
                "stderr": result.stderr.strip(),
            },
        )

    text, expires_at, image_b64 = parse_qr_response(result.stdout)
    target = qr_output or DEFAULT_QR_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(image_b64))

    state = load_state()
    state["last_qr_path"] = str(target)
    state["last_qr_expires_at"] = expires_at
    state["last_qr_created_at"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)

    payload = {
        "status": "pending_login",
        "message": text,
        "qr_image_path": str(target),
        "expires_at": expires_at,
    }
    if not wait:
        return payload

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = login_status_payload()
        if status["status"] == "logged_in":
            return status
        time.sleep(LOGIN_POLL_INTERVAL)

    raise CLIError(
        "Login wait timed out",
        exit_code=11,
        payload={**payload, "status": "pending_login", "message": "Login wait timed out"},
    )


def _search_expr(keyword: str) -> str:
    return f"xiaohongshu.search_feeds(keyword: {quote_literal(keyword)})"


def _detail_expr(feed_id: str, token: str) -> str:
    return (
        "xiaohongshu.get_feed_detail("
        f"feed_id: {quote_literal(feed_id)}, "
        f"xsec_token: {quote_literal(token)})"
    )


def _search_raw(keyword: str) -> tuple[dict[str, Any], str]:
    result = mcporter_call(_search_expr(keyword), timeout_ms=30000, hard_timeout=45)
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    if looks_like_auth_error(text):
        raise AuthRequiredError()
    if result.returncode != 0:
        raise CLIError(
            "Search call failed",
            exit_code=21,
            payload={
                "status": "error",
                "message": "Search call failed",
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            },
        )
    try:
        payload = extract_json_blob(result.stdout)
    except ValueError as exc:
        raise CLIError(
            f"Failed to parse search response: {exc}",
            exit_code=21,
            payload={
                "status": "error",
                "message": "Failed to parse search response",
                "stdout": result.stdout.strip(),
            },
        ) from exc
    return payload, result.stdout


def _filter_note_results(feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [feed for feed in feeds if feed.get("modelType") == "note"]


def _paginate(items: list[dict[str, Any]], page: int, page_size: int) -> list[dict[str, Any]]:
    if page < 1:
        raise CLIError("page must be >= 1", exit_code=30)
    if page_size < 1:
        raise CLIError("page-size must be >= 1", exit_code=30)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]


def _build_output_dir(image_dir: Path) -> Path:
    image_dir = image_dir.expanduser()
    image_dir.mkdir(parents=True, exist_ok=True)
    out_dir = image_dir / f"xhs_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    return out_dir


def _fetch_detail(feed_id: str, token: str) -> tuple[dict[str, Any] | None, str]:
    result = mcporter_call(_detail_expr(feed_id, token), timeout_ms=30000, hard_timeout=50)
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        return None, text.strip()
    try:
        return extract_json_blob(result.stdout), ""
    except ValueError:
        return None, text.strip()


def _cover_url_from_card(card: dict[str, Any]) -> str:
    cover = card.get("cover") or {}
    return sanitize_url(cover.get("urlDefault") or cover.get("urlPre") or "")


def _result_from_feed(rank: int, feed: dict[str, Any]) -> dict[str, Any]:
    card = feed.get("noteCard") or {}
    user = card.get("user") or {}
    token = feed.get("xsecToken", "")
    feed_id = feed.get("id", "")
    return {
        "rank": rank,
        "feed_id": feed_id,
        "xsec_token": token,
        "title": (card.get("displayTitle") or "").strip(),
        "author": user.get("nickname") or user.get("nickName") or "",
        "user_id": user.get("userId") or "",
        "note_type": card.get("type") or "",
        "detail_status": "",
        "detail_error": "",
        "note_url": build_note_url(feed_id, token),
        "image_urls": [],
        "image_paths": [],
    }


def _apply_detail_mode(result: dict[str, Any], feed: dict[str, Any], *, fallback_to_cover: bool) -> dict[str, Any]:
    detail, error_text = _fetch_detail(result["feed_id"], result["xsec_token"])
    if detail is not None:
        note = ((detail.get("data") or {}).get("note")) or {}
        result["title"] = (note.get("title") or result["title"]).strip()
        result["note_type"] = note.get("type") or result["note_type"]
        note_user = note.get("user") or {}
        result["author"] = note_user.get("nickname") or result["author"]
        image_list = note.get("imageList") or []
        urls = [sanitize_url(item.get("urlDefault") or item.get("urlPre") or "") for item in image_list]
        urls = [url for url in urls if url]
        if urls:
            result["image_urls"] = urls
            result["detail_status"] = "ok"
            return result
        result["detail_error"] = "detail returned no imageList"
    else:
        result["detail_error"] = error_text

    if fallback_to_cover:
        cover_url = _cover_url_from_card(feed.get("noteCard") or {})
        if cover_url:
            result["image_urls"] = [cover_url]
            result["detail_status"] = "fallback_cover_only"
            return result
    result["detail_status"] = "failed"
    return result


def _apply_cover_mode(result: dict[str, Any], feed: dict[str, Any]) -> dict[str, Any]:
    cover_url = _cover_url_from_card(feed.get("noteCard") or {})
    if cover_url:
        result["image_urls"] = [cover_url]
        result["detail_status"] = "cover_only"
    else:
        result["detail_status"] = "failed"
        result["detail_error"] = "search result had no cover image"
    return result


def _write_outputs(out_dir: Path, payload: dict[str, Any], search_raw: str, errors: list[dict[str, Any]]) -> None:
    (out_dir / "search_raw.json").write_text(search_raw, encoding="utf-8")
    (out_dir / "results.json").write_text(json_dump(payload), encoding="utf-8")
    (out_dir / "errors.json").write_text(json_dump(errors), encoding="utf-8")

    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "feed_id",
                "title",
                "author",
                "note_type",
                "detail_status",
                "image_count",
                "note_url",
                "detail_error",
            ],
        )
        writer.writeheader()
        for item in payload["items"]:
            writer.writerow(
                {
                    "rank": item["rank"],
                    "feed_id": item["feed_id"],
                    "title": item["title"],
                    "author": item["author"],
                    "note_type": item["note_type"],
                    "detail_status": item["detail_status"],
                    "image_count": len(item["image_paths"]),
                    "note_url": item["note_url"],
                    "detail_error": item["detail_error"],
                }
            )

    summary_lines = [
        f"Query: {payload['keyword']}",
        f"Generated at: {payload['generated_at']}",
        f"Selected results: {payload['returned']}",
        f"Downloaded images: {payload['image_count']}",
        f"Pagination mode: {payload['pagination_mode']}",
        "",
    ]
    for item in payload["items"]:
        summary_lines.append(
            f"{item['rank']:02d}. {item['title']} | {item['author']} | "
            f"{item['detail_status']} | images={len(item['image_paths'])}"
        )
    (out_dir / "summary.txt").write_text("\n".join(summary_lines), encoding="utf-8")


def _handle_login_required(policy: str, timeout_seconds: int) -> None:
    if policy == "fail":
        raise AuthRequiredError()
    if policy == "return":
        login_payload = start_login(force=True, wait=False, timeout_seconds=timeout_seconds)
        login_payload["status"] = "requires_login"
        raise CLIError("Login required", exit_code=10, payload=login_payload)
    if policy == "wait":
        start_login(force=True, wait=True, timeout_seconds=timeout_seconds)
        return
    raise CLIError(f"Unsupported login policy: {policy}", exit_code=30)


def search_images(
    *,
    keyword: str,
    image_dir: Path,
    page: int,
    page_size: int,
    image_mode: str,
    fallback_to_cover: bool,
    login_policy: str,
    login_timeout: int,
    retry_after_login: bool = True,
) -> dict[str, Any]:
    logger.info("search_images keyword=%r image_mode=%s page=%d page_size=%d", keyword, image_mode, page, page_size)
    ensure_service()
    try:
        search_payload, search_raw = _search_raw(keyword)
    except AuthRequiredError:
        _handle_login_required(login_policy, login_timeout)
        if retry_after_login and login_policy == "wait":
            return search_images(
                keyword=keyword,
                image_dir=image_dir,
                page=page,
                page_size=page_size,
                image_mode=image_mode,
                fallback_to_cover=fallback_to_cover,
                login_policy="fail",
                login_timeout=login_timeout,
                retry_after_login=False,
            )
        raise

    feeds = search_payload.get("feeds") or []
    notes = _filter_note_results(feeds)
    selected = _paginate(notes, page, page_size)

    out_dir = _build_output_dir(image_dir)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for rank, feed in enumerate(selected, start=1):
        result = _result_from_feed(rank, feed)
        if image_mode == "cover":
            result = _apply_cover_mode(result, feed)
        elif image_mode == "detail":
            result = _apply_detail_mode(result, feed, fallback_to_cover=fallback_to_cover)
        else:
            raise CLIError("image-mode must be one of: cover, detail", exit_code=30)

        for index, image_url in enumerate(result["image_urls"], start=1):
            try:
                dest = download_image(image_url, out_dir / "images" / f"{rank:02d}_{result['feed_id']}_{index:02d}")
                result["image_paths"].append(str(dest.relative_to(out_dir)))
            except (HTTPError, URLError, OSError, ValueError) as exc:
                message = f"download failed: {image_url} ({exc})"
                logger.warning("Image %s: %s", image_url, message)
                errors.append({"feed_id": result["feed_id"], "error": message})
                result["detail_error"] = f"{result['detail_error']}; {message}".strip("; ")

        if result["detail_status"] == "failed":
            errors.append({"feed_id": result["feed_id"], "error": result["detail_error"] or "image fetch failed"})

        results.append(result)
        time.sleep(SEARCH_DOWNLOAD_DELAY)

    payload = {
        "status": "partial" if errors else "ok",
        "keyword": keyword,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "page": page,
        "page_size": page_size,
        "pagination_mode": "batch",
        "total_in_batch": len(notes),
        "returned": len(results),
        "download_dir": str(out_dir),
        "image_count": sum(len(item["image_paths"]) for item in results),
        "items": results,
        "errors": errors,
    }
    _write_outputs(out_dir, payload, search_raw, errors)
    return payload
