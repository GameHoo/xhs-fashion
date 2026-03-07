from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class FashnApiError(RuntimeError):
    """Represents a failed HTTP interaction with the FASHN API."""

    def __init__(self, status_code: int, error_code: str, message: str, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "error_code": self.error_code,
            "message": self.message,
            "payload": self.payload,
        }


@dataclass(slots=True)
class FashnClient:
    api_key: str
    base_url: str = "https://api.fashn.ai/v1"
    connect_timeout: float = 15.0
    read_timeout: float = 120.0
    user_agent: str = "fashn-tryon/0.1.1"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

    def run_prediction(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/run",
            json=payload,
            headers=self._headers(),
            timeout=(self.connect_timeout, self.read_timeout),
        )
        return self._decode_response(response)

    def get_status(self, prediction_id: str) -> tuple[dict[str, Any], dict[str, str]]:
        response = requests.get(
            f"{self.base_url}/status/{prediction_id}",
            headers=self._headers(),
            timeout=(self.connect_timeout, self.read_timeout),
        )
        payload = self._decode_response(response)
        return payload, dict(response.headers)

    def download_file(self, url: str) -> tuple[bytes, str]:
        response = requests.get(
            url,
            timeout=(self.connect_timeout, self.read_timeout),
            headers={"User-Agent": self.user_agent},
        )
        if not response.ok:
            raise self._build_error(response)
        return response.content, response.headers.get("Content-Type", "")

    def _decode_response(self, response: requests.Response) -> dict[str, Any]:
        if response.ok:
            try:
                return response.json()
            except ValueError as exc:
                raise FashnApiError(
                    status_code=response.status_code,
                    error_code="InvalidJson",
                    message="FASHN API returned invalid JSON",
                    payload=response.text,
                ) from exc
        raise self._build_error(response)

    def _build_error(self, response: requests.Response) -> FashnApiError:
        payload: Any
        error_code = "HttpError"
        message = response.text.strip() or response.reason
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        else:
            if isinstance(payload, dict):
                error_code = str(payload.get("error") or payload.get("code") or error_code)
                message = str(payload.get("message") or payload.get("error_description") or message)
        return FashnApiError(
            status_code=response.status_code,
            error_code=error_code,
            message=message,
            payload=payload,
        )
