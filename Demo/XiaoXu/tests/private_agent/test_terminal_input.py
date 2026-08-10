from private_agent.config import AppSettings
from private_agent.terminal_input import USER_PROMPT, build_prompt_toolkit_session


class FakeHistory:
    def __init__(self, path):
        self.path = path


class FakePromptSession:
    kwargs = None

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs


def test_prompt_toolkit_session_keeps_user_prompt_outside_edit_buffer(tmp_path):
    settings = AppSettings(run_dir=tmp_path)

    session = build_prompt_toolkit_session(settings, FakePromptSession, FakeHistory)

    assert isinstance(session, FakePromptSession)
    assert FakePromptSession.kwargs["message"] == USER_PROMPT
    assert "default" not in FakePromptSession.kwargs
    assert FakePromptSession.kwargs["history"].path == str(
        settings.resolve_in_run_dir(settings.command_history_path)
    )
