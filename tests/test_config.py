# tests/test_config.py
"""
配置模块测试

测试覆盖：
- YAML/JSON 配置加载
- 动态参数合并（项目 params > 环境 params）
- branch_field 派生
- 向后兼容（旧格式字段迁移）
"""

import pytest
from jenkins_config.config import Config, Job


# ============================================================================
# YAML 加载
# ============================================================================


def test_load_yaml(tmp_path):
    """加载 YAML 格式配置文件"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "test-token"\n'
        "build:\n"
        "  mode: parallel\n"
        "  poll_interval: 5\n"
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        '      - name: "test-project"\n'
        "        params:\n"
        '          branch: "feature"\n'
    )

    config = Config.load(str(config_file))
    assert config.server.url == "http://localhost:8080"
    assert config.server.token == "test-token"
    assert config.build.mode == "parallel"
    assert config.build.poll_interval == 5
    assert config.branch_field == "branch"
    assert "dev" in config.environments
    assert len(config.environments["dev"].projects) == 1


def test_load_json_backward_compat(tmp_path):
    """加载 JSON 格式（向后兼容）"""
    config_file = tmp_path / "jenkins-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "test-token"},
      "build": {"mode": "parallel", "poll_interval": 5},
      "environments": {
        "dev": {
          "params": {"branch": "develop"},
          "projects": [{"name": "test-project", "params": {"branch": "feature"}}]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    assert config.server.url == "http://localhost:8080"
    assert config.build.poll_interval == 5


# ============================================================================
# 动态参数合并
# ============================================================================


def test_params_merge(tmp_path):
    """项目 params 覆盖环境 params"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      branch: "develop"\n'
        '      skip_tests: "false"\n'
        "    projects:\n"
        "      - name: project-a\n"
        "        params:\n"
        '          branch: "feature"\n'
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert jobs[0].params["branch"] == "feature"  # 项目覆盖环境
    assert jobs[0].params["skip_tests"] == "false"  # 环境参数保留


def test_params_add_new_key(tmp_path):
    """在 params 中添加新键值对（模拟新增插件参数）"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        "      - name: project-a\n"
        "        params:\n"
        '          BRANCH: "origin/prod"\n'
        '          APP_VERSION: "1.2.3"\n'  # 新插件参数
        '          SKIP_TESTS: "true"\n'     # 新插件参数
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert jobs[0].params["BRANCH"] == "origin/prod"
    assert jobs[0].params["APP_VERSION"] == "1.2.3"
    assert jobs[0].params["SKIP_TESTS"] == "true"


# ============================================================================
# branch_field 派生
# ============================================================================


def test_branch_field_default(tmp_path):
    """默认 branch_field='branch'，Job.branch 从 params 派生"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        '      - name: project-a\n'
        "        params:\n"
        '          branch: "feature"\n'
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert jobs[0].branch == "feature"
    assert jobs[0].params["branch"] == "feature"


def test_branch_field_custom(tmp_path):
    """自定义 branch_field 使用不同的参数名"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "branch_field: BRANCH\n"
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      BRANCH: "develop"\n'
        "    projects:\n"
        '      - name: project-a\n'
        "        params:\n"
        '          BRANCH: "origin/prod"\n'
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert config.branch_field == "BRANCH"
    assert jobs[0].branch == "origin/prod"
    assert jobs[0].params["BRANCH"] == "origin/prod"


def test_branch_field_env_override(tmp_path):
    """环境级别 branch_field 覆盖全局"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "branch_field: branch\n"
        "environments:\n"
        "  dev:\n"
        "    branch_field: GIT_BRANCH\n"
        "    params:\n"
        '      GIT_BRANCH: "develop"\n'
        "    projects:\n"
        '      - name: project-a\n'
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert jobs[0].branch == "develop"
    assert "GIT_BRANCH" in jobs[0].params
    assert "branch" not in jobs[0].params


# ============================================================================
# 向后兼容
# ============================================================================


def test_backward_compat_old_json(tmp_path):
    """旧格式 JSON（branch/git_param/default_branch）自动迁移"""
    config_file = tmp_path / "test.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "git_param": "GIT_BRANCH",
          "params": "skip_tests=false",
          "projects": [
            {"name": "project-a", "branch": "feature", "git_param": "MY_BRANCH"},
            {"name": "project-b"}
          ]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 2

    # project-a: 使用自己的 MY_BRANCH
    assert jobs[0].params["MY_BRANCH"] == "feature"
    assert jobs[0].params["skip_tests"] == "false"
    assert jobs[0].branch == "feature"

    # project-b: 使用环境的 GIT_BRANCH
    assert jobs[1].params["GIT_BRANCH"] == "develop"
    assert jobs[1].params["skip_tests"] == "false"
    assert jobs[1].branch == "develop"


def test_backward_compat_partial(tmp_path):
    """旧格式部分字段也能正确迁移"""
    config_file = tmp_path / "test.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "main",
          "projects": [
            {"name": "project-a", "branch": "feature"}
          ]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert jobs[0].branch == "feature"
    assert jobs[0].params["branch"] == "feature"


# ============================================================================
# 过滤
# ============================================================================


def test_get_jobs_with_filter(tmp_path):
    """按 env:project 格式过滤"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        '      - name: project-a\n'
        "  test:\n"
        "    params:\n"
        '      branch: "test"\n'
        "    projects:\n"
        '      - name: project-b\n'
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(jobs=["dev:project-a"])

    assert len(jobs) == 1
    assert jobs[0].env == "dev"
    assert jobs[0].project_name == "project-a"


def test_get_jobs_with_filter_by_name_only(tmp_path):
    """只按项目名过滤（不指定环境）"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        '      - name: project-a\n'
        "  test:\n"
        "    params:\n"
        '      branch: "test"\n'
        "    projects:\n"
        '      - name: project-a\n'
    )

    config = Config.load(str(config_file))
    jobs = config.get_jobs(jobs=["project-a"])

    # 两个环境都有 project-a
    assert len(jobs) == 2
    assert all(j.project_name == "project-a" for j in jobs)


# ============================================================================
# 参数解析
# ============================================================================


def test_parse_params_dict(tmp_path):
    """params 为字典格式"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        "    params:\n"
        '      key1: "value1"\n'
        '      key2: "value2"\n'
        "    projects:\n"
        '      - name: project-a\n'
    )

    config = Config.load(str(config_file))
    assert config.environments["dev"].params == {"key1": "value1", "key2": "value2"}


def test_parse_params_string(tmp_path):
    """params 为旧格式字符串（向后兼容）"""
    config_file = tmp_path / "test.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "params": "key1=value1&key2=value2",
          "projects": [{"name": "project-a"}]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    assert config.environments["dev"].params == {"key1": "value1", "key2": "value2"}



