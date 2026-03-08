from pathlib import Path

from xhs_cli import runtime


def configure_service_files(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    service_url_file = tmp_path / "service_url"
    mcp_port_file = tmp_path / "mcp_port"
    monkeypatch.setattr(runtime, "SERVICE_URL_FILE", service_url_file)
    monkeypatch.setattr(runtime, "MCP_PORT_FILE", mcp_port_file)
    return service_url_file, mcp_port_file


def test_resolve_service_url_prefers_explicit_env(monkeypatch, tmp_path):
    configure_service_files(monkeypatch, tmp_path)
    monkeypatch.setenv("XHS_CLI_SERVICE_URL", "http://localhost:18061/mcp")

    assert runtime.resolve_service_url() == "http://localhost:18061/mcp"


def test_resolve_service_url_uses_persisted_service_url(monkeypatch, tmp_path):
    service_url_file, _ = configure_service_files(monkeypatch, tmp_path)
    monkeypatch.delenv("XHS_CLI_SERVICE_URL", raising=False)
    service_url_file.write_text("http://localhost:18062/mcp\n", encoding="utf-8")

    assert runtime.resolve_service_url() == "http://localhost:18062/mcp"


def test_resolve_service_url_falls_back_to_persisted_port(monkeypatch, tmp_path):
    _, mcp_port_file = configure_service_files(monkeypatch, tmp_path)
    monkeypatch.delenv("XHS_CLI_SERVICE_URL", raising=False)
    mcp_port_file.write_text("18063\n", encoding="utf-8")

    assert runtime.resolve_service_url() == "http://localhost:18063/mcp"
