from private_agent.config import AppSettings
from private_agent.model_menus import run_model_menu, run_thinking_menu
from private_agent.models import ModelManager


def _ask_from(answers):
    pending = iter(answers)
    prompts = []

    def ask(prompt, choices=None, default=None):
        prompts.append(prompt)
        return next(pending)

    ask.prompts = prompts
    return ask


def test_model_menu_switches_model_through_numbered_options(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)

    result = run_model_menu(manager, _ask_from(["5", "1", "1"]))

    assert result.requires_agent_rebuild is True
    assert manager.active_model_ref() == "deepseek.deepseek-v4-flash"
    assert "已切换模型" in result.message


def test_model_menu_lists_models_without_switching(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)

    result = run_model_menu(manager, _ask_from(["2"]))

    assert result.requires_agent_rebuild is False
    assert "deepseek.deepseek-v4-flash" in result.message
    assert "zhipuai.glm-5.2" in result.message


def test_thinking_menu_turns_on_supported_model(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")

    result = run_thinking_menu(manager, _ask_from(["1"]))

    assert result.requires_agent_rebuild is True
    assert "已开启思考模式" in result.message
    assert manager.current_thinking().enabled is True


def test_thinking_menu_reports_unsupported_model(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("zhipuai.glm-4-long")

    result = run_thinking_menu(manager, _ask_from(["1"]))

    assert result.requires_agent_rebuild is False
    assert "模型 glm-4-long 不支持思考模式" in result.message


def test_thinking_menu_sets_reasoning_effort(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("zhipuai.glm-5.2")

    result = run_thinking_menu(manager, _ask_from(["3", "2"]))

    assert result.requires_agent_rebuild is True
    assert "reasoning_effort 已设置为 high" in result.message
    assert manager.current_thinking().reasoning_effort == "high"


def test_model_menu_prompt_shows_current_status_without_secret_or_url_info(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")
    ask = _ask_from(["0"])

    run_model_menu(manager, ask)

    prompt = ask.prompts[0]
    assert "当前模型：deepseek.deepseek-v4-flash" in prompt
    assert "思考模式：" in prompt
    assert "查看当前模型" not in prompt
    assert "API Key" not in prompt
    assert "URL" not in prompt
    assert "DEEPSEEK_API_KEY" not in prompt
    assert "DEEPSEEK_BASE_URL" not in prompt


def test_thinking_menu_prompt_shows_current_status_without_separate_view_option(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    manager = ModelManager(settings)
    manager.switch_model("deepseek.deepseek-v4-flash")
    ask = _ask_from(["0"])

    run_thinking_menu(manager, ask)

    prompt = ask.prompts[0]
    assert "当前模型：deepseek.deepseek-v4-flash" in prompt
    assert "思考模式：" in prompt
    assert "查看当前状态" not in prompt
