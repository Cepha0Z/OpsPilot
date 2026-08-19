import time
import re
from typing import Any
from urllib.parse import quote

import requests

from ...config.defaults import (
    GRAPH_CONNECT_TIMEOUT_SECONDS,
    GRAPH_MAX_COLLECTION_ITEMS,
    GRAPH_MAX_GET_RETRIES,
    GRAPH_READ_TIMEOUT_SECONDS,
)
from .auth import get_access_token


GRAPH_URL = "https://graph.microsoft.com/v1.0"
SAFE_METHODS = {"GET"}


class GraphError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int | None = None, provider_code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.provider_code = provider_code
        self.retryable = retryable


def odata_string_literal(value: str) -> str:
    """Escape a user-supplied value inserted into an OData string literal."""
    return "'" + value.replace("'", "''") + "'"


def path_segment(value: str) -> str:
    return quote(str(value), safe="@._-")


def safe_graph_error(response) -> tuple[str | None, str]:
    """Return only safe top-level Graph diagnostics; never retain raw payloads."""
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        return None, "Microsoft Graph rejected the request."

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return None, "Microsoft Graph rejected the request."

    provider_code = error.get("code")
    provider_code = str(provider_code)[:100] if provider_code else None
    message = str(error.get("message") or "Microsoft Graph rejected the request.")[:500]
    if re.search(r"(?i)(?:password|token|secret|authorization)\s*[:=]\s*\S+", message):
        message = "Microsoft Graph rejected a sensitive credential value."
    return provider_code, message


class GraphClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.timeout = (GRAPH_CONNECT_TIMEOUT_SECONDS, GRAPH_READ_TIMEOUT_SECONDS)

    def _url(self, endpoint: str) -> str:
        return endpoint if endpoint.startswith("https://") else GRAPH_URL + endpoint

    def _headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        value = {"Authorization": f"Bearer {get_access_token()}"}
        if headers:
            value.update(headers)
        return value

    def request(self, method: str, endpoint: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        method = method.upper()
        attempts = GRAPH_MAX_GET_RETRIES + 1 if method in SAFE_METHODS else 1

        for attempt in range(attempts):
            try:
                response = self.session.request(method, self._url(endpoint), headers=self._headers(headers), params=params, json=body, timeout=self.timeout)
            except requests.Timeout as exc:
                if method in SAFE_METHODS and attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise GraphError("timeout", "Microsoft Graph timed out.", retryable=method in SAFE_METHODS) from exc
            except requests.RequestException as exc:
                raise GraphError("network_error", "Microsoft Graph could not be reached.", retryable=method in SAFE_METHODS) from exc

            if response.status_code == 429:
                if method in SAFE_METHODS and attempt + 1 < attempts:
                    try:
                        delay = max(0, min(int(response.headers.get("Retry-After", "1")), 30))
                    except ValueError:
                        delay = 1
                    time.sleep(delay)
                    continue
                raise GraphError("rate_limited", "Microsoft Graph rate limited the request.", status_code=429, retryable=method in SAFE_METHODS)

            if 500 <= response.status_code < 600 and method in SAFE_METHODS and attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue

            if not response.ok:
                provider_code, message = safe_graph_error(response)
                raise GraphError(
                    "http_error",
                    message,
                    status_code=response.status_code,
                    provider_code=provider_code,
                    retryable=method in SAFE_METHODS and response.status_code >= 500,
                )

            if not response.text:
                return {"success": True}
            try:
                return response.json()
            except ValueError as exc:
                raise GraphError("malformed_response", "Microsoft Graph returned malformed JSON.") from exc

        raise AssertionError("Unreachable Graph retry state")

    def get_collection(self, endpoint: str, *, params: dict[str, Any] | None = None, limit: int | None = None, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
        limit = GRAPH_MAX_COLLECTION_ITEMS if limit is None else min(limit, GRAPH_MAX_COLLECTION_ITEMS)
        values: list[dict[str, Any]] = []
        next_endpoint = endpoint
        next_params = params

        while next_endpoint and len(values) < limit:
            page = self.request("GET", next_endpoint, params=next_params, headers=headers)
            page_values = page.get("value")
            if not isinstance(page_values, list):
                raise GraphError("malformed_response", "Microsoft Graph collection response has no value array.")
            values.extend(page_values[: limit - len(values)])
            next_endpoint = page.get("@odata.nextLink")
            next_params = None
        return values


graph_client = GraphClient()


def graph_get(endpoint: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return graph_client.request("GET", endpoint, params=params, headers=headers)


def graph_get_collection(endpoint: str, *, params: dict[str, Any] | None = None, limit: int | None = None, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    return graph_client.get_collection(endpoint, params=params, limit=limit, headers=headers)


def graph_post(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
    return graph_client.request("POST", endpoint, body=body, headers={"Content-Type": "application/json"})


def graph_patch(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
    return graph_client.request("PATCH", endpoint, body=body, headers={"Content-Type": "application/json"})


def graph_delete(endpoint: str) -> dict[str, Any]:
    return graph_client.request("DELETE", endpoint)
