# Tests for ConfigManager (Task-3C)

import os
import pytest
import tempfile
from config_manager import AgentConfig, ConfigManager


TEST_CONFIG = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
TEST_CONFIG_PATH = TEST_CONFIG.name
TEST_CONFIG.close()


def cleanup(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        os.unlink(path + ".tmp")
    except FileNotFoundError:
        pass


@pytest.fixture
def cm():
    path = tempfile.mktemp(suffix=".json")
    mgr = ConfigManager(path=path)
    yield mgr
    cleanup(path)


class TestAgentConfig:
    def test_default_config(self):
        cfg = AgentConfig()
        assert cfg.model == "llama3"
        assert cfg.max_iterations == 10
        assert cfg.temperature == 0.7
        assert cfg.memory_semantic_enabled is True
        assert cfg.memory_episodic_enabled is True
        assert cfg.memory_prune_days == 30
        assert cfg.system_prompt is None
        assert cfg.show_thinking is True
        assert cfg.tool_confirmation is True
        assert cfg.sandbox_workspace == "~/.macos-agent-workspace"

    def test_to_dict_roundtrip(self):
        cfg = AgentConfig(model="mistral", max_iterations=5, temperature=0.9)
        d = cfg.to_dict()
        restored = AgentConfig.from_dict(d)
        assert restored.model == "mistral"
        assert restored.max_iterations == 5
        assert restored.temperature == 0.9

    def test_from_dict_partial(self):
        d = {"model": "llama3.2"}
        cfg = AgentConfig.from_dict(d)
        assert cfg.model == "llama3.2"
        assert cfg.max_iterations == 10  # default


class TestConfigManager:
    def test_default_on_new_path(self, cm):
        cfg = cm.get()
        assert cfg.model == "llama3"
        assert cfg.max_iterations == 10

    def test_update_partial(self, cm):
        cm.update(model="mistral", temperature=0.5)
        cfg = cm.get()
        assert cfg.model == "mistral"
        assert cfg.temperature == 0.5
        assert cfg.max_iterations == 10  # unchanged

    def test_update_unknown_key_ignored(self, cm):
        cm.update(unknown_key="ignored", model="gpt4")
        cfg = cm.get()
        assert cfg.model == "gpt4"
        assert not hasattr(cfg, "unknown_key")

    def test_save_and_reload(self, cm):
        cm.update(model="codellama", max_iterations=20)
        path = cm.path
        # New instance should load saved config
        cm2 = ConfigManager(path=path)
        assert cm2.get().model == "codellama"
        assert cm2.get().max_iterations == 20

    def test_corrupted_json_fallback(self):
        path = tempfile.mktemp(suffix=".json")
        with open(path, "w") as f:
            f.write("{ invalid json }")
        cm = ConfigManager(path=path)
        cfg = cm.get()
        assert cfg.model == "llama3"  # fallback defaults
        cleanup(path)

    def test_empty_file_fallback(self):
        path = tempfile.mktemp(suffix=".json")
        with open(path, "w") as f:
            f.write("")
        cm = ConfigManager(path=path)
        cfg = cm.get()
        assert cfg.model == "llama3"
        cleanup(path)

    def test_reset(self, cm):
        cm.update(model="deepseek", max_iterations=50)
        cm.reset()
        cfg = cm.get()
        assert cfg.model == "llama3"
        assert cfg.max_iterations == 10
