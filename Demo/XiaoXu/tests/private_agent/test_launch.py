from pathlib import Path
import os
import stat


def test_posix_xiaoxu_script_launches_private_agent_from_project_root():
    script = Path("bin/xiaoxu")

    text = script.read_text(encoding="utf-8")

    assert "/opt/anaconda3/envs/langchain1.2/bin/python" in text
    assert 'cd "$script_dir/.."' in text
    assert 'export PYTHONPATH="$script_dir/../src${PYTHONPATH:+:$PYTHONPATH}"' in text
    assert "-m private_agent.interfaces.cli.app" in text
    if os.name != "nt":
        assert script.stat().st_mode & stat.S_IXUSR


def test_windows_xiaoxu_script_launches_private_agent_from_project_root():
    script = Path("bin/xiaoxu.cmd")

    text = script.read_text(encoding="utf-8")

    assert r"D:\Anaconda3\envs\langchain1.2\python.exe" in text
    assert "%~dp0.." in text
    assert r'set "PYTHONPATH=%PROJECT_ROOT%\src' in text
    assert "-m private_agent.interfaces.cli.app" in text


def test_docker_runtime_dependencies_include_full_cli_and_search():
    requirements = Path("requirements/runtime.txt").read_text(encoding="utf-8")

    assert "typer==0.24.1" in requirements
    assert "rich==14.3.3" in requirements
    assert "prompt-toolkit==3.0.52" in requirements
    assert "langchain-tavily==0.2.17" in requirements


def test_docker_build_copies_dynamic_requirement_file_before_install():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY requirements ./requirements" in dockerfile
    assert "pip install --requirement requirements/runtime.txt" in dockerfile
    assert "private_agent.interfaces.api.app:create_app" in dockerfile
    assert "--factory" in dockerfile
