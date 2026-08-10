from private_agent.config import AppSettings
from private_agent.input_history import configure_cli_history, record_cli_history


class FakeReadline:
    def __init__(self):
        self.bindings = []
        self.history_length = None
        self.read_paths = []
        self.write_paths = []
        self.items = []

    def parse_and_bind(self, command):
        self.bindings.append(command)

    def set_history_length(self, length):
        self.history_length = length

    def read_history_file(self, path):
        self.read_paths.append(path)

    def write_history_file(self, path):
        self.write_paths.append(path)

    def get_current_history_length(self):
        return len(self.items)

    def get_history_item(self, index):
        return self.items[index - 1]

    def add_history(self, text):
        self.items.append(text)


def test_configure_cli_history_loads_history_and_binds_arrows(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    fake = FakeReadline()

    configured = configure_cli_history(settings, readline_module=fake, register_atexit=False)

    assert configured is True
    assert fake.history_length == 1000
    assert "set editing-mode emacs" in fake.bindings
    assert "\\e[A: previous-history" in fake.bindings
    assert "\\e[B: next-history" in fake.bindings
    assert fake.read_paths == [str(settings.resolve_in_run_dir(settings.command_history_path))]


def test_configure_cli_history_ignores_missing_history_file(tmp_path):
    settings = AppSettings(run_dir=tmp_path)
    fake = FakeReadline()

    configured = configure_cli_history(settings, readline_module=fake, register_atexit=False)

    assert configured is True


def test_record_cli_history_adds_non_empty_lines_without_consecutive_duplicates():
    fake = FakeReadline()

    record_cli_history("hello", readline_module=fake)
    record_cli_history("hello", readline_module=fake)
    record_cli_history("  ", readline_module=fake)
    record_cli_history("world", readline_module=fake)

    assert fake.items == ["hello", "world"]
