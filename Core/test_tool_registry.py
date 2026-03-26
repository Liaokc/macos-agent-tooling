# Tests for ToolRegistry (Task-3B)

import asyncio
import os
import pytest
import tempfile
from tool_registry import ToolRegistry, ToolTemplate


@pytest.fixture
def registry():
    tmp = tempfile.mkdtemp()
    r = ToolRegistry(workspace=tmp)
    yield r


class TestToolRegistryInit:
    def test_registers_phase2_tools(self, registry):
        names = registry.list_tools()
        assert "bash" in names
        assert "read_file" in names
        assert "done" in names

    def test_registers_phase3_tools(self, registry):
        names = registry.list_tools()
        assert "web_search" in names
        assert "read_multiple_files" in names
        assert "http_request" in names
        assert "osascript" in names
        assert "task_completion" in names


class TestToolSchemas:
    def test_get_schemas(self, registry):
        schemas = registry.get_schemas()
        assert len(schemas) > 0
        names = {s["name"] for s in schemas}
        assert "bash" in names
        assert "web_search" in names

    def test_web_search_schema(self, registry):
        schema = registry.get_tool("web_search")
        assert schema is not None
        assert schema.name == "web_search"
        assert "query" in schema.input_schema["properties"]

    def test_http_request_schema(self, registry):
        schema = registry.get_tool("http_request")
        assert schema is not None
        assert schema.name == "http_request"
        props = schema.input_schema["properties"]
        assert "url" in props
        assert "method" in props


class TestConfirmationRequired:
    def test_osascript_requires_confirmation(self, registry):
        assert registry.get_confirmation_required("osascript") is True

    def test_bash_requires_confirmation(self, registry):
        assert registry.get_confirmation_required("bash") is True

    def test_web_search_no_confirmation(self, registry):
        assert registry.get_confirmation_required("web_search") is False

    def test_unknown_tool_no_confirmation(self, registry):
        assert registry.get_confirmation_required("nonexistent") is False


class TestCustomTools:
    def test_register_custom_tool(self, registry):
        async def dummy_handler(**kwargs):
            from tool_registry import ToolResult
            return ToolResult("dummy", kwargs, "ok", success=True)

        template = ToolTemplate(
            name="my_tool",
            description="A custom tool",
            input_schema={
                "name": "my_tool",
                "description": "A custom tool",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
            handler=dummy_handler,
            requires_confirmation=False,
        )
        registry.register(template)
        assert registry.get_tool("my_tool") is not None

    def test_unregister_tool(self, registry):
        registry.unregister("web_search")
        assert registry.get_tool("web_search") is None


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_web_search_returns_result(self, registry):
        result = await registry._web_search("test query", max_results=3)
        # May return empty if network is unavailable, but should not error
        assert result.tool == "web_search"

    @pytest.mark.asyncio
    async def test_web_search_timeout(self, registry):
        result = await registry._web_search("test", max_results=1)
        assert result.tool == "web_search"


class TestReadMultipleFiles:
    @pytest.mark.asyncio
    async def test_read_multiple_files(self, registry):
        # Create test files
        path_a = os.path.join(registry.workspace, "a.txt")
        path_b = os.path.join(registry.workspace, "b.txt")
        with open(path_a, "w") as f:
            f.write("content A")
        with open(path_b, "w") as f:
            f.write("content B")

        result = await registry._read_multiple_files(["a.txt", "b.txt"])
        assert result.success
        assert "content A" in result.output
        assert "content B" in result.output

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, registry):
        result = await registry._read_multiple_files(["/etc/passwd"])
        assert "PATH_OUTSIDE_WORKSPACE" in result.output or not result.success


class TestHttpRequest:
    @pytest.mark.asyncio
    async def test_localhost_allowed(self, registry):
        result = await registry._http_request("http://localhost:8080/")
        # May fail if nothing running, but should not be blocked
        assert result.tool == "http_request"

    @pytest.mark.asyncio
    async def test_external_blocked(self, registry):
        result = await registry._http_request("https://example.com")
        assert not result.success
        assert "localhost" in result.error.lower()


class TestTaskCompletion:
    @pytest.mark.asyncio
    async def test_task_completion(self, registry):
        result = await registry._task_completion(summary="Did the thing", result="42")
        assert result.success
        assert "Task Complete" in result.output
        assert "Did the thing" in result.output
