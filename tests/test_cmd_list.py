"""
列表和历史命令模块测试

测试覆盖：
- list_environments / list_projects
- show_history / show_history_stats
"""

from pathlib import Path

from jenkins_config.cmd_list import (
    list_environments,
    list_projects,
    show_history,
    show_history_stats,
)


# ============================================================================
# 辅助函数
# ============================================================================


def _write_yaml_config(tmp_path: Path, content: str) -> Path:
    """写入 YAML 配置并返回路径"""
    f = tmp_path / "jenkins-config.yaml"
    f.write_text(content, encoding="utf-8")
    return f


def _write_history(tmp_path: Path, records: list[dict]) -> Path:
    """写入历史记录并返回路径"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    import json
    (data_dir / "build_history.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False)
    )
    return data_dir / "build_history.json"


# ============================================================================
# list_environments
# ============================================================================


def test_list_environments_with_description(tmp_path, capsys):
    """列出带描述的环境"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments:\n"
        "  dev:\n"
        "    description: dev env\n"
        "    projects:\n"
        "      - name: app\n"
        "  prod:\n"
        "    description: prod env\n"
        "    projects:\n"
        "      - name: app\n",
    )

    list_environments(config_file)
    captured = capsys.readouterr()
    assert "dev" in captured.out
    assert "dev env" in captured.out
    assert "prod env" in captured.out


def test_list_environments_without_description(tmp_path, capsys):
    """列出无描述的环境"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments:\n"
        "  dev:\n"
        "    projects:\n"
        "      - name: app\n",
    )

    list_environments(config_file)
    captured = capsys.readouterr()
    assert "  - dev" in captured.out
    assert "(" not in captured.out


# ============================================================================
# list_projects
# ============================================================================


def test_list_projects_all(tmp_path, capsys):
    """列出所有环境的项目"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments:\n"
        "  dev:\n"
        "    projects:\n"
        "      - name: frontend\n"
        "  prod:\n"
        "    projects:\n"
        "      - name: backend\n",
    )

    list_projects(config_file, env=None)
    captured = capsys.readouterr()
    assert "[dev]" in captured.out
    assert "[prod]" in captured.out
    assert "frontend" in captured.out
    assert "backend" in captured.out


def test_list_projects_filtered(tmp_path, capsys):
    """按环境过滤项目"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments:\n"
        "  dev:\n"
        "    projects:\n"
        "      - name: frontend\n"
        "  prod:\n"
        "    projects:\n"
        "      - name: backend\n"
        "      - name: frontend\n",
    )

    list_projects(config_file, env="prod")
    captured = capsys.readouterr()
    # print_header 输出到 stderr，包含环境名称
    assert "prod" in captured.err
    # 项目列表输出到 stdout
    assert "backend" in captured.out
    assert "frontend" in captured.out


# ============================================================================
# show_history
# ============================================================================


def test_show_history_empty(tmp_path, capsys):
    """空历史记录"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\nenvironments: {}\n",
    )
    _write_history(tmp_path, [])

    show_history(config_file, env=None)
    captured = capsys.readouterr()
    assert "暂无记录" in captured.out


def test_show_history_with_records(tmp_path, capsys):
    """有历史记录"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\nenvironments: {}\n",
    )
    _write_history(
        tmp_path,
        [
            {
                "timestamp": "2026-06-09T10:00:00",
                "env": "dev",
                "job_key": "dev_app",
                "build_num": 42,
                "status": "SUCCESS",
                "duration": 65,
                "log_file": "",
            }
        ],
    )

    show_history(config_file, env=None)
    captured = capsys.readouterr()
    assert "dev_app" in captured.out
    assert "#42" in captured.out
    assert "SUCCESS" in captured.out
    assert "1分5秒" in captured.out


# ============================================================================
# show_history_stats
# ============================================================================


def test_show_history_stats(tmp_path, capsys):
    """历史统计"""
    config_file = _write_yaml_config(
        tmp_path,
        "server:\n  url: http://localhost:8080\n  token: t\nenvironments: {}\n",
    )
    _write_history(
        tmp_path,
        [
            {"timestamp": "", "env": "", "job_key": "", "build_num": 1,
             "status": "SUCCESS", "duration": 10, "log_file": ""},
            {"timestamp": "", "env": "", "job_key": "", "build_num": 2,
             "status": "SUCCESS", "duration": 20, "log_file": ""},
            {"timestamp": "", "env": "", "job_key": "", "build_num": 3,
             "status": "FAILURE", "duration": 5, "log_file": ""},
        ],
    )

    show_history_stats(config_file)
    captured = capsys.readouterr()
    assert "3" in captured.out
    assert "66.7" in captured.out or "66.67" in captured.out
