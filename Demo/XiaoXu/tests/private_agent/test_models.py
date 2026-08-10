import json
import sqlite3

from langchain_core.messages import AIMessageChunk

from private_agent.config import AppSettings
from private_agent.models import ModelManager, ReasoningPreservingChatOpenAI


def test_default_catalog_groups_deepseek_and_zhipuai_models(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)

    assert manager.vendors() == ["deepseek", "zhipuai"]
    assert manager.get_profile("deepseek.deepseek-v4-flash").supports_thinking is True
    assert manager.get_profile("deepseek.deepseek-v4-pro").supports_thinking is True
    assert manager.get_profile("zhipuai.glm-5.2").supports_thinking is True
    assert manager.get_profile("zhipuai.glm-4-long").supports_thinking is False


def test_model_catalog_is_loaded_from_static_configuration(tmp_path):
    catalog_path = tmp_path / "models.yaml"
    catalog_path.write_text(
        """
vendors:
  test-vendor:
    api_key_env: TEST_API_KEY
    base_url_env: TEST_BASE_URL
    models:
      - model_id: test-model
        display_name: Test Model
        supports_thinking: true
""".strip(),
        encoding="utf-8",
    )
    settings = AppSettings(
        run_dir=tmp_path,
        model_catalog_path=catalog_path,
    )

    manager = ModelManager(settings)

    assert manager.vendors() == ["test-vendor"]
    assert manager.get_profile("test-vendor.test-model").supports_thinking is True


def test_switch_model_persists_state_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://secret.example/v1")
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)

    result = manager.switch_model("deepseek.deepseek-v4-flash")

    database_path = settings.resolve_in_run_dir(settings.sqlite_database_path)
    with sqlite3.connect(database_path) as connection:
        raw_state = connection.execute(
            "SELECT payload FROM model_state WHERE user_id = ?",
            (settings.user_id,),
        ).fetchone()[0]
    assert result.changed is True
    assert result.requires_agent_rebuild is True
    assert "deepseek.deepseek-v4-flash" in raw_state
    assert "secret-key" not in raw_state
    assert "secret.example" not in raw_state


def test_build_chat_model_reads_secret_values_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.example")
    captured = {}

    def fake_init_openai_compatible_chat_model(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        "private_agent.models.manager.init_openai_compatible_chat_model",
        fake_init_openai_compatible_chat_model,
    )
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")
    manager.set_thinking_enabled(True)
    manager.set_reasoning_effort("high")

    model = manager.build_chat_model()

    assert model == captured
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["api_key"] == "deepseek-key"
    assert captured["base_url"] == "https://api.deepseek.example"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["reasoning_effort"] == "high"


def test_build_chat_model_reads_secret_values_from_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=dotenv-key\n"
        "DEEPSEEK_BASE_URL=https://dotenv.deepseek.example\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_init_openai_compatible_chat_model(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        "private_agent.models.manager.init_openai_compatible_chat_model",
        fake_init_openai_compatible_chat_model,
    )
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")

    model = manager.build_chat_model()

    assert model == captured
    assert captured["api_key"] == "dotenv-key"
    assert captured["base_url"] == "https://dotenv.deepseek.example"


def test_reasoning_preserving_chat_model_keeps_reasoning_content_from_stream_chunk():
    model = ReasoningPreservingChatOpenAI(
        model="deepseek-v4-pro",
        api_key="test-key",
        base_url="https://api.deepseek.example",
    )
    generation_chunk = model._convert_chunk_to_generation_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "先分析",
                    }
                }
            ]
        },
        AIMessageChunk,
        None,
    )

    assert generation_chunk is not None
    assert generation_chunk.message.additional_kwargs["reasoning_content"] == "先分析"


def test_unsupported_thinking_model_reports_no_support(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("zhipuai.glm-4-long")

    result = manager.set_thinking_enabled(True)

    assert result.changed is False
    assert result.requires_agent_rebuild is False
    assert "模型 glm-4-long 不支持思考模式" in result.message


def test_thinking_state_is_persisted_per_model(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)

    manager.switch_model("zhipuai.glm-5.2")
    manager.set_thinking_enabled(False)
    manager.set_reasoning_effort("high")

    database_path = settings.resolve_in_run_dir(settings.sqlite_database_path)
    with sqlite3.connect(database_path) as connection:
        raw_state = connection.execute(
            "SELECT payload FROM model_state WHERE user_id = ?",
            (settings.user_id,),
        ).fetchone()[0]
    state = json.loads(raw_state)
    assert state["active_model"] == "zhipuai.glm-5.2"
    assert state["thinking"]["zhipuai.glm-5.2"] == {
        "enabled": False,
        "reasoning_effort": "high",
    }


def test_model_view_formatters_do_not_show_api_key_or_url_information(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://secret.example/v1")
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")

    output = "\n".join(
        [
            manager.format_current(),
            manager.format_profile("deepseek.deepseek-v4-flash"),
            manager.format_config_status(),
        ]
    )

    assert "secret-key" not in output
    assert "secret.example" not in output
    assert "API Key" not in output
    assert "URL" not in output
    assert "DEEPSEEK_API_KEY" not in output
    assert "DEEPSEEK_BASE_URL" not in output
