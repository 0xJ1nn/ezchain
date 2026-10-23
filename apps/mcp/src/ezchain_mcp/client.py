"""Small typed client for the ezCHAIN HTTP API."""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(RuntimeError):
    """A non-2xx API response, reduced to a stable code and message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code, "message": self.message}


class EzchainClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EzchainClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, "API_UNREACHABLE", str(exc)) from exc
        if response.is_success:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        try:
            detail = response.json().get("detail", {})
        except ValueError:
            detail = {}
        if isinstance(detail, dict):
            code = str(detail.get("code", "API_ERROR"))
            message = str(detail.get("message", response.text[:300]))
        elif isinstance(detail, list):
            code = "VALIDATION_ERROR"
            message = str(detail)[:500]
        else:
            code = "API_ERROR"
            message = str(detail or response.text)[:500]
        raise ApiError(response.status_code, code, message)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json)

    def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)


__all__ = ["ApiError", "EzchainClient"]
