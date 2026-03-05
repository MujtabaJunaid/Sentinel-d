"""Tests for the SRE Agent three-way router."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from router import route_classification, _route_active, _route_dormant, _route_deferred


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def active_classification() -> dict:
    return {
        "event_id": "550e8400-e29b-41d4-a716-446655440001",
        "status": "ACTIVE",
        "call_count_30d": 150,
        "last_called": "2026-03-04T10:00:00Z",
        "blast_radius": "HIGH",
        "kql_query_used": "traces | where message contains 'log4j'",
        "confidence": 0.95,
    }


@pytest.fixture
def dormant_classification() -> dict:
    return {
        "event_id": "550e8400-e29b-41d4-a716-446655440002",
        "status": "DORMANT",
        "call_count_30d": 0,
        "last_called": None,
        "blast_radius": "HIGH",
        "kql_query_used": "traces | where message contains 'log4j'",
        "confidence": 0.70,
    }


@pytest.fixture
def deferred_classification() -> dict:
    return {
        "event_id": "550e8400-e29b-41d4-a716-446655440003",
        "status": "DEFERRED",
        "call_count_30d": 0,
        "last_called": None,
        "blast_radius": "LOW",
        "kql_query_used": "traces | where message contains 'some-lib'",
        "confidence": 0.70,
    }


@pytest.fixture
def webhook_event() -> dict:
    return {
        "event_id": "550e8400-e29b-41d4-a716-446655440001",
        "cve_id": "CVE-2021-44228",
        "severity": "CRITICAL",
        "affected_package": "org.apache.logging.log4j:log4j-core",
        "current_version": "2.14.0",
        "fix_version_range": ">=2.15.0",
        "file_path": "pom.xml",
        "line_range": [42, 42],
        "repo": "test-org/test-repo",
        "timestamp": "2026-03-05T12:00:00Z",
    }


@pytest.fixture
def historical_match() -> dict:
    return {
        "lookup_status": "NO_MATCH",
        "matched_cve_id": None,
        "match_confidence": 0,
        "recommended_strategy": None,
        "previous_outcome": None,
        "replay_eligible": False,
    }


# ── ACTIVE routing tests ────────────────────────────────────────────────────

class TestRouteActive:
    """Tests for ACTIVE → Service Bus topic routing."""

    @pytest.mark.asyncio
    async def test_active_publishes_to_topic(
        self, active_classification: dict, webhook_event: dict
    ) -> None:
        """ACTIVE events should be published to the nlp-pipeline-input topic."""
        mock_sender = AsyncMock()
        mock_sender.__aenter__ = AsyncMock(return_value=mock_sender)
        mock_sender.__aexit__ = AsyncMock(return_value=None)

        mock_client = AsyncMock()
        mock_client.get_topic_sender = MagicMock(return_value=mock_sender)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_credential = AsyncMock()

        with patch("router.DefaultAzureCredential", return_value=mock_credential), \
             patch("router.ServiceBusClient", return_value=mock_client), \
             patch("router.SB_NAMESPACE", "test-namespace"):

            result = await _route_active(active_classification, webhook_event)

        assert result["status"] == "ACTIVE"
        assert result["destination"].startswith("topic:")
        assert "nlp-pipeline-input" in result["destination"]
        mock_sender.send_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_active_fails_without_namespace(
        self, active_classification: dict, webhook_event: dict
    ) -> None:
        """ACTIVE routing should raise if SERVICE_BUS_NAMESPACE is missing."""
        with patch("router.SB_NAMESPACE", ""):
            with pytest.raises(EnvironmentError):
                await _route_active(active_classification, webhook_event)

    @pytest.mark.asyncio
    async def test_active_message_contains_event_id(
        self, active_classification: dict, webhook_event: dict
    ) -> None:
        """The published message body should contain the event_id."""
        mock_sender = AsyncMock()
        mock_sender.__aenter__ = AsyncMock(return_value=mock_sender)
        mock_sender.__aexit__ = AsyncMock(return_value=None)

        mock_client = AsyncMock()
        mock_client.get_topic_sender = MagicMock(return_value=mock_sender)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_credential = AsyncMock()

        with patch("router.DefaultAzureCredential", return_value=mock_credential), \
             patch("router.ServiceBusClient", return_value=mock_client), \
             patch("router.SB_NAMESPACE", "test-namespace"):

            await _route_active(active_classification, webhook_event)

        call_args = mock_sender.send_messages.call_args
        sent_msg = call_args[0][0]
        # ServiceBusMessage stores the body passed to constructor
        # Access via the _raw_amqp_message or check constructor call
        # Since we're using the real ServiceBusMessage class, extract body from constructor
        body_str = b"".join(sent_msg.body).decode("utf-8")
        body = json.loads(body_str)
        assert body["event_id"] == webhook_event["event_id"]
        assert body["classification"]["status"] == "ACTIVE"


# ── DORMANT routing tests ───────────────────────────────────────────────────

class TestRouteDormant:
    """Tests for DORMANT → GitHub Issue routing."""

    @pytest.mark.asyncio
    async def test_dormant_calls_subprocess(
        self, dormant_classification: dict, webhook_event: dict, historical_match: dict
    ) -> None:
        """DORMANT should invoke Node.js create-decision-issue via subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"issueNumber": 42, "issueUrl": "https://github.com/issues/42"})
        mock_result.stderr = ""

        with patch("router.subprocess.run", return_value=mock_result):
            result = await _route_dormant(dormant_classification, webhook_event, historical_match)

        assert result["status"] == "DORMANT"
        assert result["destination"] == "github-issue"
        assert "#42" in result["detail"]

    @pytest.mark.asyncio
    async def test_dormant_handles_subprocess_failure(
        self, dormant_classification: dict, webhook_event: dict
    ) -> None:
        """Failed subprocess should return error detail, not raise."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "GITHUB_TOKEN missing"
        mock_result.stdout = ""

        with patch("router.subprocess.run", return_value=mock_result):
            result = await _route_dormant(dormant_classification, webhook_event)

        assert result["status"] == "DORMANT"
        assert "failed" in result["detail"].lower()

    @pytest.mark.asyncio
    async def test_dormant_handles_timeout(
        self, dormant_classification: dict, webhook_event: dict
    ) -> None:
        """Subprocess timeout should return timeout detail."""
        import subprocess as sp

        with patch("router.subprocess.run", side_effect=sp.TimeoutExpired("node", 30)):
            result = await _route_dormant(dormant_classification, webhook_event)

        assert result["status"] == "DORMANT"
        assert "timed out" in result["detail"].lower()


# ── DEFERRED routing tests ──────────────────────────────────────────────────

class TestRouteDeferred:
    """Tests for DEFERRED → backlog table storage routing."""

    @pytest.mark.asyncio
    async def test_deferred_calls_backlog_writer(
        self, deferred_classification: dict, webhook_event: dict
    ) -> None:
        """DEFERRED should write to backlog via subprocess."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"success": True})
        mock_result.stderr = ""

        with patch("router.subprocess.run", return_value=mock_result):
            result = await _route_deferred(deferred_classification, webhook_event)

        assert result["status"] == "DEFERRED"
        assert result["destination"] == "backlog"
        assert "Deferred until" in result["detail"]

    @pytest.mark.asyncio
    async def test_deferred_handles_failure(
        self, deferred_classification: dict, webhook_event: dict
    ) -> None:
        """Failed backlog write should return error detail."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Table Storage connection failed"
        mock_result.stdout = ""

        with patch("router.subprocess.run", return_value=mock_result):
            result = await _route_deferred(deferred_classification, webhook_event)

        assert result["status"] == "DEFERRED"
        assert "failed" in result["detail"].lower()


# ── Integration: route_classification dispatcher ─────────────────────────────

class TestRouteClassification:
    """Tests for the main route_classification dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatches_active(
        self, active_classification: dict, webhook_event: dict
    ) -> None:
        """ACTIVE status should dispatch to _route_active."""
        with patch("router._route_active", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "ACTIVE", "destination": "topic:nlp-pipeline-input", "detail": "ok"}
            result = await route_classification(active_classification, webhook_event)
        assert result["status"] == "ACTIVE"
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_dormant(
        self, dormant_classification: dict, webhook_event: dict
    ) -> None:
        """DORMANT status should dispatch to _route_dormant."""
        with patch("router._route_dormant", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "DORMANT", "destination": "github-issue", "detail": "ok"}
            result = await route_classification(dormant_classification, webhook_event)
        assert result["status"] == "DORMANT"
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_deferred(
        self, deferred_classification: dict, webhook_event: dict
    ) -> None:
        """DEFERRED status should dispatch to _route_deferred."""
        with patch("router._route_deferred", new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "DEFERRED", "destination": "backlog", "detail": "ok"}
            result = await route_classification(deferred_classification, webhook_event)
        assert result["status"] == "DEFERRED"
        mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_status(self, webhook_event: dict) -> None:
        """Unknown status should return UNKNOWN destination."""
        unknown = {"status": "INVALID", "event_id": "test"}
        result = await route_classification(unknown, webhook_event)
        assert result["destination"] == "UNKNOWN"
