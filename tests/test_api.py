"""Tests for the Nature Remo API adapter."""

import asyncio
import time
from typing import Any

import pytest
from aiohttp import ClientError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.nature_remo import NatureRemoAPI


class FakeResponse:
    """Minimal aiohttp response test double."""

    def __init__(
        self,
        payload: Any,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self.headers = headers or {}
        self.raise_for_status_called = False

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True

    async def json(self) -> Any:
        return self._payload


class FakeSession:
    """Minimal aiohttp session test double."""

    def __init__(
        self,
        *payloads: Any,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payloads = list(payloads)
        self._status = status
        self._headers = headers
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.responses: list[FakeResponse] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        response = FakeResponse(self._payloads.pop(0), self._status, self._headers)
        self.responses.append(response)
        return response


class FailingSession:
    """Session test double that raises an aiohttp client error."""

    def request(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        raise ClientError("boom")


def test_get_fetches_appliances_and_devices() -> None:
    session = FakeSession(
        [{"id": "appliance-1"}],
        [{"id": "device-1"}],
    )
    api = NatureRemoAPI("token", session)  # type: ignore[arg-type]

    data = asyncio.run(api.get())

    assert data == {
        "appliances": {"appliance-1": {"id": "appliance-1"}},
        "devices": {"device-1": {"id": "device-1"}},
    }
    assert session.calls == [
        (
            "GET",
            "https://api.nature.global/1/appliances",
            {"headers": {"Authorization": "Bearer token"}},
        ),
        (
            "GET",
            "https://api.nature.global/1/devices",
            {"headers": {"Authorization": "Bearer token"}},
        ),
    ]
    assert all(response.raise_for_status_called for response in session.responses)


def test_post_returns_json_payload() -> None:
    session = FakeSession({"ok": True})
    api = NatureRemoAPI("token", session)  # type: ignore[arg-type]

    data = asyncio.run(api.post("/appliances/id/aircon_settings", {"button": "power-off"}))

    assert data == {"ok": True}
    assert session.calls == [
        (
            "POST",
            "https://api.nature.global/1/appliances/id/aircon_settings",
            {
                "data": {"button": "power-off"},
                "headers": {"Authorization": "Bearer token"},
            },
        )
    ]


def test_get_wraps_client_errors_as_update_failed() -> None:
    api = NatureRemoAPI("token", FailingSession())  # type: ignore[arg-type]

    with pytest.raises(UpdateFailed):
        asyncio.run(api.get())


def test_rate_limited_requests_raise_a_clear_error() -> None:
    session = FakeSession(
        {},
        status=429,
        headers={"X-Rate-Limit-Reset": str(int(time.time()) + 90)},
    )
    api = NatureRemoAPI("token", session)  # type: ignore[arg-type]

    with pytest.raises(HomeAssistantError) as excinfo:
        asyncio.run(api.post("/appliances/id/light", {"button": "on"}))

    message = str(excinfo.value)
    assert "rate limited" in message
    assert "30 requests per 5 minutes" in message
    assert "retry in" in message
    # A 429 must not be reported as a generic failure by raise_for_status.
    assert session.responses[0].raise_for_status_called is False


def test_rate_limited_poll_becomes_update_failed() -> None:
    session = FakeSession([], [], status=429)
    api = NatureRemoAPI("token", session)  # type: ignore[arg-type]

    with pytest.raises(UpdateFailed):
        asyncio.run(api.get())


def test_get_issues_both_requests_concurrently() -> None:
    session = FakeSession([{"id": "appliance-1"}], [{"id": "device-1"}])
    api = NatureRemoAPI("token", session)  # type: ignore[arg-type]

    asyncio.run(api.get())

    assert [method for method, _url, _kwargs in session.calls] == ["GET", "GET"]
    assert [url.rsplit("/", 1)[-1] for _method, url, _kwargs in session.calls] == [
        "appliances",
        "devices",
    ]
