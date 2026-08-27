# tests/test_mcp/test_history_tools.py
"""
MCP 历史查询工具测试

测试覆盖：
- show_history 返回正确格式
- show_history_stats 统计正确（含 building 占位记录）
- 按环境过滤
- 只读查询不产生副作用文件
"""

from unittest.mock import Mock, patch

import pytest

from jenkins_config.history import BuildRecord


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_records():
    """创建示例构建记录列表"""
    return [
        BuildRecord(
            timestamp="2026-08-24T10:00:00",
            env="dev",
            job_key="dev_project_a",
            build_num=100,
            status="SUCCESS",
            duration=60,
            log_file="/logs/dev_project_a_#100.log",
            branch="develop",
            params={"branch": "develop"},
            project_name="project-a",
        ),
        BuildRecord(
            timestamp="2026-08-24T09:00:00",
            env="test",
            job_key="test_project_b",
            build_num=200,
            status="FAILURE",
            duration=120,
            log_file="/logs/test_project_b_#200.log",
            branch="test",
            params={"branch": "test"},
            project_name="project-b",
        ),
    ]


@pytest.fixture
def mock_manager(sample_records):
    """创建 mock HistoryManager"""
    manager = Mock()
    manager.list.return_value = sample_records
    manager.stats.return_value = {
        "total": 10,
        "success": 8,
        "failure": 2,
        "building": 0,
        "success_rate": 80.0,
    }
    return manager


# ============================================================================
# show_history 测试
# ============================================================================


def test_show_history_returns_correct_format(mock_manager, sample_records):
    """验证 show_history 返回正确的 BuildRecord 字典格式"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history

        result = show_history()

        assert len(result) == 2
        # 验证返回的是字典格式（asdict 转换）
        assert isinstance(result[0], dict)
        assert result[0]["timestamp"] == "2026-08-24T10:00:00"
        assert result[0]["env"] == "dev"
        assert result[0]["job_key"] == "dev_project_a"
        assert result[0]["build_num"] == 100
        assert result[0]["status"] == "SUCCESS"


def test_show_history_with_env_filter(mock_manager):
    """验证 show_history 按环境过滤"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history

        show_history(env="dev")

        # 验证 manager.list 被调用时带 env 参数
        mock_manager.list.assert_called_with(env="dev", limit=20)


def test_show_history_no_env_passes_none(mock_manager):
    """验证 show_history env 为空时传 None"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history

        show_history(env="")

        mock_manager.list.assert_called_with(env=None, limit=20)


def test_show_history_with_limit(mock_manager):
    """验证 show_history 限制返回数量"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history

        show_history(limit=5)

        mock_manager.list.assert_called_with(env=None, limit=5)


def test_show_history_empty_result():
    """验证 show_history 无记录时返回空列表"""
    mock_manager = Mock()
    mock_manager.list.return_value = []

    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history

        result = show_history()
        assert result == []


def test_show_history_error_handling():
    """验证 show_history 异常时返回单元素纯错误载荷"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", side_effect=Exception("文件损坏")):
        from jenkins_config.mcp.tools.history_tools import show_history

        result = show_history()

        assert len(result) == 1
        payload = result[0]
        assert set(payload) == {"error_code", "error", "config_path", "next_steps", "docs"}
        assert "查询历史记录失败" in payload["error"]
        assert payload["next_steps"]


# ============================================================================
# show_history_stats 测试
# ============================================================================


def test_show_history_stats_returns_correct_format(mock_manager):
    """验证 show_history_stats 返回正确的统计格式"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history_stats

        result = show_history_stats()

        assert result["total"] == 10
        assert result["success"] == 8
        assert result["failure"] == 2
        # success_rate 应该格式化为百分比字符串
        assert result["success_rate"] == "80.0%"


def test_show_history_stats_zero_records():
    """验证 show_history_stats 无记录时返回正确统计"""
    mock_manager = Mock()
    mock_manager.stats.return_value = {
        "total": 0,
        "success": 0,
        "failure": 0,
        "success_rate": 0,
    }

    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history_stats

        result = show_history_stats()

        assert result["total"] == 0
        assert result["success_rate"] == "0%"


def test_show_history_stats_error_handling():
    """验证 show_history_stats 异常时顶层合并统一失败载荷"""
    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", side_effect=Exception("统计失败")):
        from jenkins_config.mcp.tools.history_tools import show_history_stats

        result = show_history_stats()

        assert "error" in result
        assert "查询历史统计失败" in result["error"]
        assert result["error_code"]
        assert result["next_steps"]


def test_show_history_stats_high_success_rate(mock_manager):
    """验证 show_history_stats 成功率计算正确"""
    mock_manager.stats.return_value = {
        "total": 100,
        "success": 95,
        "failure": 5,
        "success_rate": 95.0,
    }

    with patch("jenkins_config.mcp.tools.history_tools._get_history_manager", return_value=mock_manager):
        from jenkins_config.mcp.tools.history_tools import show_history_stats

        result = show_history_stats()

        assert result["success_rate"] == "95.0%"
        assert result["total"] == 100


def test_show_history_stats_reports_building(mock_manager):
    """验证占位记录数量单独回传，且不计入成功率分母"""
    mock_manager.stats.return_value = {
        "total": 10,
        "success": 8,
        "failure": 0,
        "building": 2,
        "success_rate": 100.0,
    }

    with patch(
        "jenkins_config.mcp.tools.history_tools._get_history_manager",
        return_value=mock_manager,
    ):
        from jenkins_config.mcp.tools.history_tools import show_history_stats

        result = show_history_stats()

        assert result["building"] == 2
        assert result["success_rate"] == "100.0%"


def test_history_query_does_not_create_files(tmp_path, monkeypatch):
    """验证只读查询不会在任意目录下创建 data/build_history.json"""
    from jenkins_config.mcp import utils
    from jenkins_config.mcp.tools import history_tools

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        utils,
        "resolve_history_path",
        lambda config_path="": tmp_path / "data" / "build_history.json",
    )

    assert history_tools.show_history() == []
    assert not (tmp_path / "data").exists()
