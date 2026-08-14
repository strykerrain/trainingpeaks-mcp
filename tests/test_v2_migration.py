"""SDK v2 migration guarantees (PRD PR 3).

Locks in the behaviour the migration must preserve: the structured error
contract, tolerance of omitted arguments, the INVALID_ARGS replacement for
v1's SDK-level validation, wire-level cache metadata, deterministic tool
ordering, and a clean stdio stream (stdout must carry JSON-RPC only).

All tests here are offline - no TrainingPeaks network calls.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tp_mcp.server import TOOLS, call_tool, list_tools, server


class TestErrorContract:
    async def test_unknown_tool_returns_structured_error(self):
        contents = await call_tool("tp_nonexistent", {})
        payload = json.loads(contents[0].text)
        assert payload["isError"] is True
        assert payload["error_code"] == "UNKNOWN_TOOL"

    async def test_omitted_arguments_is_legal_for_no_arg_tools(self):
        # v2 clients may omit "arguments" entirely -> params.arguments is None.
        contents = await call_tool("tp_nonexistent_no_args", None)
        payload = json.loads(contents[0].text)
        assert payload["error_code"] == "UNKNOWN_TOOL"  # not a crash

    async def test_missing_required_args_returns_invalid_args(self):
        # v1's decorator validated against inputSchema; v2 validates nothing.
        # The dispatch-level check must keep the error readable for the model.
        contents = await call_tool("tp_get_workouts", {})
        payload = json.loads(contents[0].text)
        assert payload["isError"] is True
        assert payload["error_code"] == "INVALID_ARGS"
        assert "start_date" in payload["message"] and "end_date" in payload["message"]

    async def test_internal_error_returns_structured_api_error(self, monkeypatch):
        import tp_mcp.server as srv

        async def boom(args):
            raise RuntimeError("synthetic failure")

        monkeypatch.setitem(srv._TOOL_HANDLERS, "tp_auth_status", boom)
        contents = await call_tool("tp_auth_status", {})
        payload = json.loads(contents[0].text)
        assert payload["isError"] is True
        assert payload["error_code"] == "API_ERROR"
        assert "synthetic failure" not in payload["message"]  # no traceback leak


class TestProtocolLayer:
    async def test_in_memory_v2_client_end_to_end(self):
        from mcp import Client

        async with Client(server) as client:
            listed = await client.list_tools()
            names = [t.name for t in listed.tools]
            assert len(names) == len(TOOLS)
            # deterministic order, straight from the static TOOLS constant
            assert names == [t.name for t in TOOLS]
            # cache metadata rides the 2026-07-28 wire (in-memory negotiates latest)
            assert listed.ttl_ms == 3600000
            assert listed.cache_scope == "private"
            # a call that stays offline: structured INVALID_ARGS through the full stack
            result = await client.call_tool("tp_get_workouts", {})
            payload = json.loads(result.content[0].text)
            assert payload["error_code"] == "INVALID_ARGS"

    async def test_distance_swim_payload_through_supported_mcp_client(self):
        """Exercise distance mode through the public MCP client, not a helper call."""
        from unittest.mock import AsyncMock, patch

        from mcp import Client

        from tp_mcp.client.http import APIResponse

        structure = json.dumps(
            {
                "primaryLengthMetric": "distance",
                "primaryIntensityMetric": "rpe",
                "distance_unit": "meter",
                "steps": [
                    {
                        "name": "Warm up",
                        "distance_value": 200,
                        "distance_unit": "meter",
                        "intensity_min": 2,
                        "intensity_max": 3,
                    },
                    {
                        "type": "repetition",
                        "name": "4 x 50",
                        "reps": 4,
                        "steps": [
                            {
                                "name": "Fast",
                                "distance_value": 50,
                                "distance_unit": "meter",
                                "intensity_min": 7,
                                "intensity_max": 8,
                            }
                        ],
                    },
                    {
                        "name": "Cool down",
                        "distance_value": 100,
                        "distance_unit": "meter",
                        "intensity_min": 1,
                        "intensity_max": 2,
                    },
                ],
            }
        )
        created = APIResponse(
            success=True,
            data={"workoutId": 9005, "title": "Distance swim", "workoutDay": "2026-08-14T00:00:00"},
        )

        with patch("tp_mcp.tools.workouts.TPClient") as mock_client:
            instance = AsyncMock()
            instance.ensure_athlete_id = AsyncMock(return_value=123)
            instance.post = AsyncMock(return_value=created)
            mock_client.return_value.__aenter__.return_value = instance
            async with Client(server) as client:
                result = await client.call_tool(
                    "tp_create_workout",
                    {
                        "date": "2026-08-14",
                        "sport": "Swim",
                        "title": "Distance swim",
                        "duration_minutes": 30,
                        "structure": structure,
                    },
                )

        response = json.loads(result.content[0].text)
        assert response["success"] is True
        wire = json.loads(instance.post.call_args.kwargs["json"]["structure"])
        assert wire["primaryLengthMetric"] == "distance"
        assert wire["visualizationDistanceUnit"] == "meter"
        assert "polyline" not in wire
        assert [(block["begin"], block["end"]) for block in wire["structure"]] == [
            (0, 200),
            (200, 400),
            (400, 500),
        ]
        assert wire["structure"][1]["steps"][0]["length"] == {"value": 50, "unit": "meter"}

    async def test_server_reports_package_version(self):
        from tp_mcp import __version__

        assert server.version == __version__

    def test_all_version_reports_derive_from_project_metadata(self):
        """pyproject.toml is authoritative; generated/runtime reports must agree."""
        from importlib.metadata import version

        from tp_mcp import __version__

        root = Path(__file__).resolve().parents[1]
        project_text = (root / "pyproject.toml").read_text(encoding="utf-8")
        lock_text = (root / "uv.lock").read_text(encoding="utf-8")
        project_match = re.search(r'(?ms)^\[project\].*?^version = "([^"]+)"', project_text)
        lock_match = re.search(r'(?ms)^\[\[package\]\]\s+name = "tp-mcp"\s+version = "([^"]+)"', lock_text)

        assert project_match is not None
        assert lock_match is not None
        project_version = project_match.group(1)
        assert lock_match.group(1) == project_version
        assert version("tp-mcp") == project_version
        assert __version__ == project_version
        assert server.version == project_version

    def test_httpx_and_httpx2_coexist(self):
        # tp_mcp's own client uses httpx; SDK v2 brings httpx2. Both must import.
        import httpx
        import httpx2

        assert httpx.__name__ == "httpx" and httpx2.__name__ == "httpx2"

    async def test_list_tools_plain_function_unchanged(self):
        assert await list_tools() is TOOLS


class TestStdioWire:
    """Spawn the real stdio server and drive it as a LEGACY (2025-era) client.

    Doubles as the stdout-hygiene check: with OpenTelemetry on by default in
    SDK v2, nothing may write non-JSON-RPC bytes to stdout or the transport
    corrupts.
    """

    def _read_response(self, proc, want_id, deadline=30):
        """Read stdout lines until the response with ``want_id`` arrives.

        Every line must parse as JSON-RPC - anything else (OTel spans, stray
        logging) is a transport-corrupting leak and fails the test.
        """
        import time

        end = time.monotonic() + deadline
        while time.monotonic() < end:
            ln = proc.stdout.readline()
            if not ln:
                pytest.fail("server closed stdout before responding")
            ln = ln.strip()
            if not ln:
                continue
            try:
                msg = json.loads(ln)
            except json.JSONDecodeError:
                pytest.fail(f"non-JSON-RPC bytes on stdout (OTel/logging leak?): {ln[:200]}")
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        pytest.fail(f"no response for id={want_id} within {deadline}s")

    def test_legacy_initialize_handshake_and_tools_list_over_stdio(self):
        env = dict(os.environ, TP_MCP_SKIP_STARTUP_VALIDATION="1")
        proc = subprocess.Popen(
            [sys.executable, "-m", "tp_mcp", "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        try:
            def send(msg):
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()

            send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18",
                             "capabilities": {},
                             "clientInfo": {"name": "legacy-test", "version": "0"}}})
            init = self._read_response(proc, 1)
            assert "result" in init, f"initialize failed: {init}"
            assert init["result"]["protocolVersion"] == "2025-06-18"

            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = self._read_response(proc, 2)
            assert "result" in tools, f"tools/list failed: {tools}"
            assert len(tools["result"]["tools"]) == len(TOOLS)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
