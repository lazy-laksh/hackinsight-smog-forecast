"""A small, polite HTTP client shared by all fetchers.

It does three things for us:
1. Waits between requests so we stay under the API's speed limit.
2. If the API says "too many requests" (HTTP 429), it waits and tries again.
3. If the server has a temporary problem (5xx or a dropped connection),
   it retries a few times, waiting a bit longer each time.

Some APIs (like NASA FIRMS) put the secret key inside the URL. Any value
passed in `secrets` is replaced with *** in every URL and error message the
client gives back, so a key can never end up in manifest.csv or in a log.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlencode

import requests


class ApiError(RuntimeError):
    """The API answered with an error we should not retry.

    `status` is the HTTP status code when there was one (e.g. 500), else None.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status

    @property
    def is_server_error(self) -> bool:
        return self.status is not None and self.status >= 500


@dataclass
class ApiResponse:
    url: str            # full URL that was called, with any secret replaced by ***
    content: bytes      # the response body exactly as received, for data/raw
    data: Any           # the body parsed as JSON, or None for CSV/text responses


class ApiClient:
    def __init__(
        self,
        base_url: str,
        headers: Optional[dict] = None,
        min_interval_s: float = 1.05,     # 60 requests per minute -> 1 per second
        max_retries: int = 5,
        timeout_s: float = 60,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        secrets: Iterable[str] = (),
    ):
        self.secrets = [s for s in secrets if s]
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self._last_call = -1e9
        self.calls = 0   # how many requests we actually sent (useful for the optimization log)

    def _throttle(self) -> None:
        wait = self.min_interval_s - (self.clock() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        self._last_call = self.clock()

    @staticmethod
    def _reset_seconds(headers) -> float:
        """How long the API asks us to wait. Falls back to 60 seconds."""
        try:
            return max(1.0, min(float(headers.get("x-ratelimit-reset", 60)), 3600.0))
        except (TypeError, ValueError):
            return 60.0

    def hide(self, text: str) -> str:
        """Replace every secret in a piece of text with ***."""
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    def get(self, path: str, params: Optional[dict] = None, as_json: bool = True,
            max_retries: Optional[int] = None) -> ApiResponse:
        """`max_retries` overrides the client's default for this one request
        (use a small number when the caller has its own fallback plan)."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        full_url = self.hide(f"{url}?{urlencode(params)}" if params else url)
        backoff = 2.0
        retries = max_retries or self.max_retries

        for attempt in range(1, retries + 1):
            self._throttle()
            self.calls += 1
            try:
                resp = self.session.get(url, params=params, headers=self.headers, timeout=self.timeout_s)
            except requests.RequestException as error:
                if attempt == retries:
                    raise ApiError(f"network error for {full_url}: {self.hide(str(error))}") from None
                self.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 200:
                # Proactively pause if we have used up this period's allowance.
                if str(resp.headers.get("x-ratelimit-remaining", "")) == "0":
                    self.sleep(self._reset_seconds(resp.headers))
                return ApiResponse(full_url, resp.content, resp.json() if as_json else None)

            if resp.status_code == 429:
                wait = self._reset_seconds(resp.headers)
                print(f"    rate limit reached, waiting {wait:.0f}s ...")
                self.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                raise ApiError(
                    f"HTTP {resp.status_code} for {full_url}: the API key was refused. "
                    "Check the key in your .env file.",
                    status=resp.status_code,
                )

            if resp.status_code >= 500 and attempt < retries:
                self.sleep(backoff)
                backoff *= 2
                continue

            raise ApiError(f"HTTP {resp.status_code} for {full_url}: {self.hide(resp.text[:300])}",
                           status=resp.status_code)

        raise ApiError(f"gave up on {full_url} after {retries} attempts")
