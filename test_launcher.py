"""
Unit and Integration Tests for NodeCore Orchestrator Interactive Launcher
========================================================================
Tests configuration persistence, endpoint probing, queue streaming,
GUI widget instantiation, and CLI fallback functionality.
"""
import os
import sys
import json
import queue
import urllib.error
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "NodeCore") not in sys.path:
    sys.path.insert(0, str(_ROOT / "NodeCore"))

from launcher_gui import (
    load_config,
    save_config,
    probe_endpoint_health,
    QueueStream,
    NodeCoreLauncherApp,
    DEFAULT_CONFIG,
    CONFIG_FILENAME,
    get_config_path
)


class TestConfigManager:
    """Tests for orchestrator_config.json persistence."""

    def test_load_default_config(self, tmp_path, monkeypatch):
        fake_cfg_file = tmp_path / "test_config.json"
        monkeypatch.setattr("launcher_gui.get_config_path", lambda: fake_cfg_file)

        cfg = load_config()
        assert cfg["model"] == "qwen2.5-coder:32b"
        assert "tunnel_url" in cfg
        assert "workspace_root" in cfg
        assert "task_prompt" in cfg

    def test_save_and_reload_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TUNNEL_URL", raising=False)
        fake_cfg_file = tmp_path / "test_config.json"
        monkeypatch.setattr("launcher_gui.get_config_path", lambda: fake_cfg_file)

        test_data = {
            "tunnel_url": "https://custom-tunnel.trycloudflare.com",
            "workspace_root": str(tmp_path / "custom_ws"),
            "model": "custom-model:latest",
            "max_rounds": 10,
            "task_prompt": "Custom test task prompt"
        }

        assert save_config(test_data) is True
        loaded = load_config()
        assert loaded["tunnel_url"] == test_data["tunnel_url"]
        assert loaded["workspace_root"] == test_data["workspace_root"]
        assert loaded["model"] == "custom-model:latest"
        assert loaded["max_rounds"] == 10
        assert loaded["task_prompt"] == "Custom test task prompt"


class TestEndpointProber:
    """Tests for probe_endpoint_health."""

    def test_invalid_url(self):
        ok, msg = probe_endpoint_health("ftp://invalid.com")
        assert ok is False
        assert "Must begin with http://" in msg

    @patch("urllib.request.urlopen")
    def test_successful_probe(self, mock_urlopen):
        # Mock 200 response for both models and chat/completions
        mock_res = MagicMock()
        mock_res.status = 200
        mock_res.__enter__.return_value = mock_res
        mock_urlopen.return_value = mock_res

        ok, msg = probe_endpoint_health("https://valid-tunnel.trycloudflare.com", model="test-model")
        assert ok is True
        assert "Online & Ready" in msg

    @patch("urllib.request.urlopen")
    def test_tokenizer_error_diagnosis(self, mock_urlopen):
        # Mock models succeeding (200), but completions failing with HTTP 500 containing 'tokenizer'
        mock_models = MagicMock()
        mock_models.status = 200
        mock_models.__enter__.return_value = mock_models

        http_err = urllib.error.HTTPError(
            url="https://tunnel/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=MagicMock(read=lambda: b'{"error": "name tokenizer is not defined"}')
        )

        mock_urlopen.side_effect = [mock_models, http_err]

        ok, msg = probe_endpoint_health("https://valid-tunnel.trycloudflare.com")
        assert ok is False
        assert "tokenizer" in msg
        assert "Diagnosis" in msg

    @patch("urllib.request.urlopen")
    def test_connection_refused(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        ok, msg = probe_endpoint_health("https://dead-tunnel.trycloudflare.com")
        assert ok is False
        assert "Unreachable" in msg


class TestQueueStream:
    """Tests for QueueStream multi-plexing."""

    def test_stream_writes_to_queue_and_stdout(self):
        q = queue.Queue()
        mock_stream = MagicMock()
        qs = QueueStream(q, mock_stream, stream_name="stdout")

        qs.write("Hello world!\n")
        assert mock_stream.write.called
        assert not q.empty()

        item = q.get()
        assert item == ("stream", "stdout", "Hello world!\n")


class TestTkinterApp:
    """Tests for NodeCoreLauncherApp GUI components."""

    def test_gui_lifecycle_and_widgets(self):
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            pytest.skip(f"Native Tk unavailable in this runtime: {exc}")
        root.withdraw()  # Hide window during test

        test_cfg = {
            "tunnel_url": "https://test-tunnel.trycloudflare.com",
            "workspace_root": r"C:\Test\Workspace",
            "model": "qwen-test",
            "max_rounds": 15,
            "task_prompt": "Test Prompt Content"
        }

        try:
            app = NodeCoreLauncherApp(root, initial_config=test_cfg)

            # Check pre-filled variables
            assert app.url_var.get() == "https://test-tunnel.trycloudflare.com"
            assert app.ws_var.get() == r"C:\Test\Workspace"
            assert app.model_var.get() == "qwen-test"
            assert app.rounds_var.get() == 15
            assert "Test Prompt Content" in app.prompt_text.get("1.0", tk.END)

            # Check health badge initial state
            assert "Untested" in app.health_badge.cget("text")

            # Test console text clearing and writing
            app._append_log_text("[+] Test Success Log\n", "tag_success")
            content = app.console_text.get("1.0", tk.END)
            assert "Test Success Log" in content

            app._on_clear_log()
            assert app.console_text.get("1.0", tk.END).strip() == ""

            # Test probe queue processing
            app.log_queue.put(("probe_result", True, "HTTP 200 OK"))
            app._process_log_queue()
            assert "Reachable" in app.health_badge.cget("text")

            app.log_queue.put(("probe_result", False, "Connection refused"))
            app._process_log_queue()
            assert "Unreachable" in app.health_badge.cget("text")

            # Test log tag detection
            assert app._detect_tag("[+] Created file") == "tag_success"
            assert app._detect_tag("[!] Warning alert") == "tag_warning"
            assert app._detect_tag("[*] Info message") == "tag_info"
            assert app._detect_tag("CoderAgent (to UserProxyRunner):") == "tag_agent"
            assert app._detect_tag("[NodeForge] Wrote 10 bytes") == "tag_tool"

        finally:
            root.destroy()
