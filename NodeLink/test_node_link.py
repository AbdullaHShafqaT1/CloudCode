"""
test_node_link.py
=================
Standalone unit test suite for the NodeLink cloud/remote execution module.

Coverage
--------
  1. AuthManager – header construction, env-var fallback, token masking,
                   audit-log scrubbing
  2. NodeLink Gateway – connect/disconnect lifecycle, adapter routing
  3. Healthcheck / Ping – 200 OK, 503 busy, bytes-metric tracking
  4. Payload Dispatch – JSON forwarding, RemoteJobHandle, JobResult
                        deserialization, token non-leakage
  5. Network Resilience – 429 (non-retry), 503 back-off, TimeoutError,
                          aiohttp.ClientError exhaustion
  6. Tunnel Handling – initial state, send on dead tunnel, reconnect loop,
                       graceful disconnect, handler dispatch
  7. Kaggle Adapter – kernels/push routing, status parsing, error propagation
  8. SyncManager – SHA-256 determinism, chunked download, missing-file guard
  9. End-to-end smoke – connect → health → submit → poll → disconnect

Run
---
    pytest test_node_link.py -v
    python test_node_link.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import unittest
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Put the project root on sys.path so imports work from any working directory.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from nodelink import (
    NodeLink,
    ConnectionHandle,
    ApiResponse,
    RemoteJobHandle,
    JobResult,
    ConnectionState,
)
from nodelink.auth_manager import AuthManager
from nodelink.adapters.generic_rest import GenericRestConnector
from nodelink.adapters.kaggle_adapter import KaggleAdapter
from nodelink.tunnel_engine import TunnelEngine, TunnelState
from nodelink.nodelog_stub import NodeLog


# ===========================================================================
# Shared constants & factories
# ===========================================================================

TARGET_ID = "test_target_01"
ENDPOINT  = "https://mock-cloud-runtime.example.com"

BEARER_CONFIG: Dict[str, Any] = {
    "endpoint":  ENDPOINT,
    "auth_type": "BEARER_TOKEN",
    "token":     "supersecrettoken_abc123",
    "adapter":   "generic",
}

API_KEY_CONFIG: Dict[str, Any] = {
    "endpoint":       ENDPOINT,
    "auth_type":      "API_KEY",
    "api_key":        "myapikey_xyz789",
    "api_key_header": "x-api-key",
    "adapter":        "generic",
}

BASIC_CONFIG: Dict[str, Any] = {
    "endpoint":  ENDPOINT,
    "auth_type": "BASIC",
    "username":  "alice",
    "password":  "s3cr3t!",
    "adapter":   "generic",
}


def _make_api_response(
    status_code: int = 200,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
) -> ApiResponse:
    return ApiResponse(
        status_code=status_code,
        headers=headers or {"content-type": "application/json"},
        data=data if data is not None else {},
    )


def _make_mock_adapter(
    send_response: Optional[ApiResponse] = None,
    run_response:  Optional[RemoteJobHandle] = None,
    fetch_response: Optional[JobResult] = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.send_request  = AsyncMock(
        return_value=send_response or _make_api_response()
    )
    adapter.run_remote    = AsyncMock(
        return_value=run_response
        or RemoteJobHandle(target_id=TARGET_ID, job_id="job_42", status="SUBMITTED")
    )
    adapter.fetch_results = AsyncMock(
        return_value=fetch_response
        or JobResult(job_id="job_42", status="COMPLETE", result_data={"acc": 0.97})
    )
    return adapter


# ===========================================================================
# Section 1 – AuthManager
# ===========================================================================

class TestAuthHeadersAndConfig(unittest.TestCase):
    """Verifies tokens / auth headers are injected correctly and never leaked."""

    def setUp(self):
        self.auth = AuthManager()

    # 1a. Bearer token
    def test_bearer_token_header_format(self):
        headers = self.auth.get_auth_headers(TARGET_ID, BEARER_CONFIG)
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        self.assertIn("supersecrettoken_abc123", headers["Authorization"])

    # 1b. API key placement
    def test_api_key_placed_in_correct_header(self):
        headers = self.auth.get_auth_headers(TARGET_ID, API_KEY_CONFIG)
        self.assertIn("x-api-key", headers)
        self.assertEqual(headers["x-api-key"], "myapikey_xyz789")
        self.assertNotIn("Authorization", headers)

    # 1c. BASIC auth encoding
    def test_basic_auth_b64_encoding(self):
        headers = self.auth.get_auth_headers(TARGET_ID, BASIC_CONFIG)
        self.assertIn("Authorization", headers)
        raw     = headers["Authorization"].replace("Basic ", "")
        decoded = base64.b64decode(raw).decode()
        self.assertEqual(decoded, "alice:s3cr3t!")

    # 1d. NONE auth → empty dict
    def test_no_auth_type_produces_empty_headers(self):
        headers = self.auth.get_auth_headers(TARGET_ID, {"auth_type": "NONE"})
        self.assertEqual(headers, {})

    # 1e. Environment-variable fallback
    def test_env_variable_credential_resolution(self):
        env_key = f"{TARGET_ID.upper()}_TOKEN"
        with patch.dict(os.environ, {env_key: "env_secret_token"}):
            config = {"endpoint": ENDPOINT, "auth_type": "BEARER_TOKEN"}
            headers = self.auth.get_auth_headers(TARGET_ID, config)
        self.assertIn("Authorization", headers)
        self.assertIn("env_secret_token", headers["Authorization"])

    # 1f. Long token masking
    def test_sanitize_headers_masks_long_token(self):
        raw       = {"Authorization": "Bearer supersecrettoken_abc123"}
        sanitized = self.auth.sanitize_headers(raw)
        self.assertNotIn("supersecrettoken_abc123", sanitized["Authorization"])
        self.assertIn("...", sanitized["Authorization"])

    # 1g. Short token → full asterisk mask
    def test_sanitize_headers_masks_short_token(self):
        sanitized = self.auth.sanitize_headers({"x-api-key": "short"})
        self.assertEqual(sanitized["x-api-key"], "***")

    # 1h. Audit log does NOT contain raw token
    def test_log_auth_event_scrubs_sensitive_data(self):
        raw_token = "mysupersensitivetoken_9999"
        headers   = {"Authorization": f"Bearer {raw_token}"}

        log_records: list[str] = []
        original_audit = NodeLog.audit

        def capturing_audit(event_type, details):
            log_records.append(str(details))
            original_audit(event_type, details)

        with patch.object(NodeLog, "audit", side_effect=capturing_audit):
            self.auth.log_auth_event(TARGET_ID, "CONNECT_INIT", {"headers": headers})

        combined = " ".join(log_records)
        self.assertNotIn(raw_token, combined,
                         "Raw credential token must be scrubbed from audit output")


# ===========================================================================
# Section 2 – NodeLink Gateway Lifecycle
# ===========================================================================

class TestGatewayLifecycle(unittest.TestCase):

    # 2a. connect() returns ConnectionHandle
    def test_connect_returns_connection_handle(self):
        nl     = NodeLink()
        handle = nl.connect(TARGET_ID, BEARER_CONFIG)
        self.assertIsInstance(handle, ConnectionHandle)
        self.assertEqual(handle.target_id, TARGET_ID)
        self.assertEqual(handle.status,    "ESTABLISHED")
        self.assertEqual(handle.endpoint,  ENDPOINT)
        self.assertEqual(handle.auth_type, "BEARER_TOKEN")

    # 2b. Correct adapter selected per config
    def test_generic_adapter_selected_by_default(self):
        nl = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        self.assertIsInstance(nl._adapters[TARGET_ID], GenericRestConnector)

    def test_kaggle_adapter_selected(self):
        nl = NodeLink()
        nl.connect(TARGET_ID, {**BEARER_CONFIG, "adapter": "kaggle"})
        self.assertIsInstance(nl._adapters[TARGET_ID], KaggleAdapter)

    def test_colab_adapter_selected(self):
        from nodelink.adapters.colab_adapter import ColabAdapter
        nl = NodeLink()
        nl.connect(TARGET_ID, {**BEARER_CONFIG, "adapter": "colab"})
        self.assertIsInstance(nl._adapters[TARGET_ID], ColabAdapter)

    # 2c. disconnect() cleans internal state
    def test_disconnect_removes_adapter_and_state(self):
        nl = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        asyncio.run(nl.disconnect(TARGET_ID))
        self.assertNotIn(TARGET_ID, nl._adapters)
        self.assertNotIn(TARGET_ID, nl._connections)

    # 2d. send_request raises for unknown target
    def test_send_request_raises_for_unknown_target(self):
        nl = NodeLink()
        with self.assertRaises(ValueError):
            asyncio.run(nl.send_request("ghost_target", "health", method="GET"))

    # 2e. Global config can supply credentials
    def test_global_config_credentials_injected(self):
        global_cfg = {TARGET_ID: {"token": "global_token_xyz"}}
        nl         = NodeLink(global_config=global_cfg)
        config     = {"endpoint": ENDPOINT, "auth_type": "BEARER_TOKEN", "adapter": "generic"}
        nl.connect(TARGET_ID, config)
        auth_header = nl._adapters[TARGET_ID].headers.get("Authorization", "")
        self.assertIn("global_token_xyz", auth_header)


# ===========================================================================
# Section 3 – Healthcheck / Ping
# ===========================================================================

class TestHealthcheckSuccess(unittest.IsolatedAsyncioTestCase):

    async def test_healthcheck_200_with_gpu_flag(self):
        payload      = {"status": "ok", "gpu": True, "disk_free_gb": 48}
        mock_resp    = _make_api_response(200, payload)
        nl           = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = _make_mock_adapter(send_response=mock_resp)

        res = await nl.send_request(TARGET_ID, "health", method="GET")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "ok")
        self.assertTrue(res.data["gpu"])

    async def test_busy_worker_returns_503(self):
        payload   = {"status": "busy", "queue_depth": 15}
        mock_resp = _make_api_response(503, payload)
        nl        = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = _make_mock_adapter(send_response=mock_resp)

        res = await nl.send_request(TARGET_ID, "health", method="GET")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data["status"], "busy")

    async def test_bytes_tracking_updated_after_request(self):
        mock_resp = _make_api_response(200, {"echo": "hello", "ts": 123})
        nl        = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = _make_mock_adapter(send_response=mock_resp)

        await nl.send_request(TARGET_ID, "echo", method="POST", data={"msg": "hi"})
        state = nl._connections[TARGET_ID]
        self.assertGreater(state.bytes_sent,     0)
        self.assertGreater(state.bytes_received, 0)


# ===========================================================================
# Section 4 – Payload Dispatch & Remote Invocation
# ===========================================================================

class TestPayloadDispatchAndResponse(unittest.IsolatedAsyncioTestCase):

    async def test_send_request_dispatches_json_payload(self):
        mock_adapter = _make_mock_adapter(
            send_response=_make_api_response(200, {"result": "accepted", "job_id": "jb_001"})
        )
        nl = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = mock_adapter

        payload = {"script": "train.py", "epochs": 5, "lr": 0.001}
        res     = await nl.send_request(TARGET_ID, "run", method="POST", data=payload)

        self.assertEqual(res.status_code, 200)
        mock_adapter.send_request.assert_awaited_once_with("run", "POST", payload, None)

    async def test_run_remote_returns_submitted_job_handle(self):
        expected = RemoteJobHandle(TARGET_ID, "fine_tune_99", "SUBMITTED")
        nl       = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = _make_mock_adapter(run_response=expected)

        handle = await nl.run_remote(TARGET_ID, "finetune_bert.py", {"dataset": "my_ds"})
        self.assertIsInstance(handle, RemoteJobHandle)
        self.assertEqual(handle.job_id, "fine_tune_99")
        self.assertEqual(handle.status, "SUBMITTED")

    async def test_fetch_results_deserializes_complete_job(self):
        job_handle = RemoteJobHandle(TARGET_ID, "fine_tune_99", "SUBMITTED")
        expected   = JobResult("fine_tune_99", "COMPLETE", result_data={"f1": 0.88})
        nl         = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = _make_mock_adapter(fetch_response=expected)

        result = await nl.fetch_results(job_handle)
        self.assertEqual(result.status, "COMPLETE")
        self.assertAlmostEqual(result.result_data["f1"], 0.88)
        self.assertIsNone(result.error)

    async def test_run_remote_raises_for_unknown_target(self):
        nl = NodeLink()
        with self.assertRaises(ValueError):
            await nl.run_remote("ghost_target", "script.py")

    async def test_response_data_contains_no_raw_token(self):
        """Mocked response must not accidentally echo back the auth token."""
        token     = BEARER_CONFIG["token"]
        mock_resp = _make_api_response(200, {"status": "ok", "echo": "payload received"})
        nl        = NodeLink()
        nl.connect(TARGET_ID, BEARER_CONFIG)
        nl._adapters[TARGET_ID] = _make_mock_adapter(send_response=mock_resp)

        res = await nl.send_request(TARGET_ID, "echo", method="POST", data={"msg": "hi"})
        self.assertNotIn(token, json.dumps(res.data))


# ===========================================================================
# Section 5 – Network Resilience & Retry Logic
# ===========================================================================

class TestRemoteTimeoutAndRetry(unittest.IsolatedAsyncioTestCase):

    # 5a. 429 is non-retriable (< 500) — returned after a single attempt
    async def test_rate_limit_429_not_retried(self):
        import aiohttp

        mock_resp = MagicMock()
        mock_resp.status       = 429
        mock_resp.content_type = "application/json"
        mock_resp.json         = AsyncMock(return_value={"error": "rate_limited"})
        mock_resp.__aenter__   = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__    = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request   = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            connector = GenericRestConnector(TARGET_ID, ENDPOINT, {})
            with patch("asyncio.sleep", new_callable=AsyncMock):
                res = await connector.send_request("health", method="GET")

        self.assertEqual(res.status_code, 429)
        self.assertEqual(mock_session.request.call_count, 1,
                         "429 must NOT trigger retry loops")

    # 5b. 503 triggers exponential back-off retries
    async def test_503_triggers_exponential_backoff(self):
        import aiohttp

        mock_resp = MagicMock()
        mock_resp.status       = 503
        mock_resp.content_type = "application/json"
        mock_resp.json         = AsyncMock(return_value={"error": "unavailable"})
        mock_resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=503
            )
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__  = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request    = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)

        sleep_calls: list[float] = []

        async def record_sleep(delay):
            sleep_calls.append(delay)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            connector = GenericRestConnector(TARGET_ID, ENDPOINT, {})
            with patch("asyncio.sleep", side_effect=record_sleep):
                res = await connector.send_request("run", method="POST", data={})

        self.assertEqual(res.status_code, 500)
        self.assertEqual(len(sleep_calls), 2,
                         "Two back-off sleeps expected for 3-attempt retry")
        self.assertLess(sleep_calls[0], sleep_calls[1],
                        "Back-off delays must grow exponentially")

    # 5c. asyncio.TimeoutError exhausts retries → 500
    async def test_timeout_error_exhausts_retries_returns_500(self):
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_resp.__aexit__  = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.request    = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)

        sleep_calls: list[float] = []
        async def record_sleep(delay): sleep_calls.append(delay)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            connector = GenericRestConnector(TARGET_ID, ENDPOINT, {})
            with patch("asyncio.sleep", side_effect=record_sleep):
                res = await connector.send_request("run", method="POST", data={})

        self.assertEqual(res.status_code, 500)
        self.assertEqual(len(sleep_calls), 2)
        self.assertNotIn("supersecrettoken", json.dumps(res.data))

    # 5d. aiohttp.ClientConnectorError exhausts retries → 500
    async def test_client_connection_error_exhausts_retries(self):
        import aiohttp

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)
        mock_session.request    = MagicMock(
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(),
                os_error=OSError("Network unreachable"),
            )
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            connector = GenericRestConnector(TARGET_ID, ENDPOINT, {})
            with patch("asyncio.sleep", new_callable=AsyncMock):
                res = await connector.send_request("run", method="POST")

        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.data)


# ===========================================================================
# Section 6 – Disconnected / Expired Tunnel Handling
# ===========================================================================

class TestDisconnectedTunnelHandling(unittest.IsolatedAsyncioTestCase):

    # 6a. Initial state is TERMINATED
    def test_tunnel_initial_state_is_terminated(self):
        engine = TunnelEngine(TARGET_ID, "wss://mock-ws.example.com", {})
        self.assertEqual(engine.state, TunnelState.TERMINATED)
        self.assertIsNone(engine.websocket)

    # 6b. send() on non-established tunnel raises RuntimeError
    async def test_send_on_terminated_tunnel_raises(self):
        engine = TunnelEngine(TARGET_ID, "wss://mock-ws.example.com", {})
        with self.assertRaises(RuntimeError) as ctx:
            await engine.send("hello")
        self.assertIn("tunnel is in state", str(ctx.exception).lower())

    # 6c. Expired WebSocket triggers RECONNECTING state
    async def test_expired_tunnel_enters_reconnecting(self):
        # Import the concrete exception class directly — the `websockets.exceptions`
        # sub-module path varies across installed versions.
        from websockets.exceptions import WebSocketException

        engine     = TunnelEngine(TARGET_ID, "wss://expired.example.com", {})
        state_trace: list[str] = []
        call_count = 0

        async def controlled_sleep(delay):
            nonlocal call_count
            state_trace.append(engine.state)
            call_count += 1
            if call_count >= 1:
                engine._shutdown_event.set()

        # Patch at the module level where TunnelEngine imports websockets.connect
        with patch(
            "nodelink.tunnel_engine.websockets.connect",
            side_effect=WebSocketException("expired tunnel URL"),
        ):
            with patch("asyncio.sleep", side_effect=controlled_sleep):
                with patch.object(NodeLog, "warn"):
                    await engine.connect()

        self.assertIn(TunnelState.RECONNECTING, state_trace,
                      "Tunnel must enter RECONNECTING after failed connection")

    # 6d. disconnect() → TERMINATED, websocket cleared
    async def test_disconnect_terminates_and_clears_socket(self):
        engine      = TunnelEngine(TARGET_ID, "wss://mock-ws.example.com", {})
        mock_ws     = AsyncMock()
        mock_ws.close = AsyncMock()
        engine.websocket = mock_ws
        engine.state     = TunnelState.ESTABLISHED

        await engine.disconnect()

        self.assertEqual(engine.state, TunnelState.TERMINATED)
        self.assertIsNone(engine.websocket)
        mock_ws.close.assert_awaited_once()

    # 6e. Registered handler is called for each incoming message
    async def test_listen_loop_dispatches_to_handlers(self):
        engine   = TunnelEngine(TARGET_ID, "wss://mock-ws.example.com", {})
        received: list[str] = []
        engine.register_handler(lambda msg: received.append(msg))

        # Build a proper async iterator — MagicMock().__aiter__ won't work
        # because Python's `async for` calls __aiter__ on the *object*, then
        # __anext__ on the iterator it returns.
        class _FakeWebSocket:
            def __init__(self):
                self._msgs = ['{"event": "heartbeat", "ts": 9999}']

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._msgs:
                    return self._msgs.pop(0)
                raise StopAsyncIteration

        engine.websocket = _FakeWebSocket()
        engine.state     = TunnelState.ESTABLISHED

        with patch.object(NodeLog, "warn"):
            await engine._listen_loop()

        self.assertEqual(len(received), 1)
        self.assertIn("heartbeat", received[0])

    # 6f. connect(tunnel=True) does not block – spawns asyncio task
    def test_connect_with_tunnel_flag_spawns_task(self):
        nl             = NodeLink()
        created_coros: list = []

        with patch("asyncio.create_task", side_effect=lambda c: created_coros.append(c)) as mock_ct:
            async def run():
                nl.connect(TARGET_ID, {**BEARER_CONFIG, "tunnel": True})
            try:
                asyncio.run(run())
            except RuntimeError:
                pass

        mock_ct.assert_called_once()


# ===========================================================================
# Section 7 – Kaggle Adapter Specifics
# ===========================================================================

class TestKaggleAdapter(unittest.IsolatedAsyncioTestCase):

    async def test_kaggle_run_remote_posts_to_kernels_push(self):
        adapter = KaggleAdapter(TARGET_ID, {"x-api-key": "kaggle_key_123"})
        adapter.send_request = AsyncMock(
            return_value=_make_api_response(200, {"ref": "alice/my-kernel"})
        )

        params = {"kernel_type": "notebook", "language": "python"}
        handle = await adapter.run_remote("meta.json", params)

        self.assertEqual(handle.job_id, "alice/my-kernel")
        self.assertEqual(handle.status, "SUBMITTED")
        adapter.send_request.assert_awaited_once_with(
            "kernels/push", method="POST", data=params
        )

    async def test_kaggle_fetch_results_parses_status_field(self):
        adapter = KaggleAdapter(TARGET_ID, {"x-api-key": "kaggle_key_123"})
        adapter.send_request = AsyncMock(
            return_value=_make_api_response(200, {"status": "complete", "totalVotes": 42})
        )
        job_handle = RemoteJobHandle(TARGET_ID, "alice/my-kernel", "SUBMITTED")
        result     = await adapter.fetch_results(job_handle)

        self.assertEqual(result.status, "complete")
        self.assertIsNone(result.error)

    async def test_kaggle_run_remote_raises_on_non_200(self):
        adapter = KaggleAdapter(TARGET_ID, {"x-api-key": "kaggle_key_123"})
        adapter.send_request = AsyncMock(
            return_value=_make_api_response(403, {"message": "Forbidden"})
        )
        with self.assertRaises(RuntimeError):
            await adapter.run_remote("meta.json", {})

    async def test_kaggle_api_key_not_in_error_message(self):
        api_key = "kaggle_ultra_secret_key"
        adapter = KaggleAdapter(TARGET_ID, {"x-api-key": api_key})
        adapter.send_request = AsyncMock(
            return_value=_make_api_response(401, {"message": "Unauthorized"})
        )
        try:
            await adapter.run_remote("meta.json", {})
        except RuntimeError as exc:
            self.assertNotIn(api_key, str(exc),
                             "API key must not appear in RuntimeError message")


# ===========================================================================
# Section 8 – SyncManager
# ===========================================================================

class TestSyncManager(unittest.IsolatedAsyncioTestCase):

    def test_checksum_is_deterministic_sha256(self):
        import hashlib, tempfile
        from nodelink.sync_manager import SyncManager

        content  = b"hello nodelink deterministic data"
        expected = hashlib.sha256(content).hexdigest()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
            tf.write(content)
            tf_path = tf.name
        try:
            self.assertEqual(SyncManager.calculate_checksum(tf_path), expected)
        finally:
            os.unlink(tf_path)

    async def test_download_file_writes_content_and_returns_true(self):
        import tempfile
        from nodelink.sync_manager import SyncManager

        fake_content = b"binary artifact data"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        async def fake_iter_chunked(size):
            yield fake_content
        mock_resp.content.iter_chunked = fake_iter_chunked
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__  = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get        = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__  = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "sub", "artifact.bin")
            mgr  = SyncManager()
            with patch("aiohttp.ClientSession", return_value=mock_session):
                ok = await mgr.download_file("https://cdn.example.com/artifact.bin", dest)
            self.assertTrue(ok)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), fake_content)

    async def test_upload_returns_false_for_missing_file(self):
        from nodelink.sync_manager import SyncManager
        mgr    = SyncManager()
        result = await mgr.upload_file(
            "https://cdn.example.com/upload", "/nonexistent/file.bin"
        )
        self.assertFalse(result)


# ===========================================================================
# Section 9 – End-to-End Smoke Test
# ===========================================================================

class TestEndToEndSmoke(unittest.IsolatedAsyncioTestCase):
    """Full connect → health → submit → poll → disconnect with zero real I/O."""

    async def test_full_workflow_with_mock_adapter(self):
        health_resp = _make_api_response(200, {"status": "ok", "gpu": True})
        job_handle  = RemoteJobHandle(TARGET_ID, "smoke_job_01", "SUBMITTED")
        job_result  = JobResult("smoke_job_01", "COMPLETE",
                                result_data={"accuracy": 0.95, "epochs": 10})

        nl     = NodeLink()
        handle = nl.connect(TARGET_ID, BEARER_CONFIG)
        self.assertEqual(handle.status, "ESTABLISHED")

        nl._adapters[TARGET_ID] = _make_mock_adapter(
            send_response=health_resp,
            run_response=job_handle,
            fetch_response=job_result,
        )

        # 1 – Healthcheck
        health = await nl.send_request(TARGET_ID, "health", method="GET")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.data["status"], "ok")

        # 2 – Submit job
        jh = await nl.run_remote(TARGET_ID, "finetune.py", {"epochs": 10})
        self.assertEqual(jh.status, "SUBMITTED")

        # 3 – Poll result
        result = await nl.fetch_results(jh)
        self.assertEqual(result.status, "COMPLETE")
        self.assertAlmostEqual(result.result_data["accuracy"], 0.95, places=2)

        # 4 – Disconnect
        ok = await nl.disconnect(TARGET_ID)
        self.assertTrue(ok)
        self.assertNotIn(TARGET_ID, nl._adapters)

    async def test_connection_handle_to_dict_keys(self):
        nl     = NodeLink()
        handle = nl.connect(TARGET_ID, BEARER_CONFIG)
        d      = handle.to_dict()
        required = {
            "target_id", "status", "endpoint", "auth_type",
            "uptime_seconds", "bytes_sent", "bytes_received", "last_heartbeat",
        }
        missing = required - set(d.keys())
        self.assertFalse(missing, f"Missing keys in to_dict(): {missing}")
        self.assertEqual(d["target_id"], TARGET_ID)
        self.assertEqual(d["status"],    "ESTABLISHED")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    logging.disable(logging.WARNING)
    raise SystemExit(pytest.main([__file__, "-v", "--tb=short"]))
