# tests/test_config.py
import pytest
from jenkins_config.config import Config, Job


def test_load_config(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "test-token"},
      "build": {"mode": "parallel", "poll_interval": 5},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "projects": [{"name": "test-project", "branch": "feature"}]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    assert config.server.url == "http://localhost:8080"
    assert config.server.token == "test-token"
    assert config.build.mode == "parallel"
    assert config.build.poll_interval == 5


def test_get_jobs(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "params": "skip_tests=false",
          "projects": [
            {"name": "project-a", "branch": "feature"},
            {"name": "project-b"}
          ]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 2
    assert jobs[0].key == "dev_project_a"
    assert jobs[0].branch == "feature"
    assert jobs[1].branch == "develop"  # 使用环境默认分支


def test_get_jobs_with_filter(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {"projects": [{"name": "project-a"}]},
        "test": {"projects": [{"name": "project-b"}]}
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(jobs=["dev:project-a"])

    assert len(jobs) == 1
    assert jobs[0].env == "dev"


def test_git_param_default(tmp_path):
    """未设置 git_param 时，默认使用 'branch' 作为参数名"""
    config_file = tmp_path / "test-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "projects": [{"name": "project-a", "branch": "feature"}]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 1
    assert jobs[0].git_param == "branch"
    assert "branch" in jobs[0].params
    assert jobs[0].params["branch"] == "feature"


def test_git_param_environment_level(tmp_path):
    """环境级别设置 git_param，所有项目继承"""
    config_file = tmp_path / "test-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "git_param": "GIT_BRANCH",
          "projects": [
            {"name": "project-a", "branch": "feature"},
            {"name": "project-b"}
          ]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 2
    # 两个项目都使用环境级别的 GIT_BRANCH
    assert jobs[0].git_param == "GIT_BRANCH"
    assert jobs[0].params["GIT_BRANCH"] == "feature"
    assert jobs[1].git_param == "GIT_BRANCH"
    assert jobs[1].params["GIT_BRANCH"] == "develop"


def test_git_param_project_level(tmp_path):
    """项目级别 git_param 覆盖环境级别设置"""
    config_file = tmp_path / "test-config.json"
    config_file.write_text("""
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "git_param": "GIT_BRANCH",
          "projects": [
            {"name": "project-a", "branch": "feature", "git_param": "MY_BRANCH"},
            {"name": "project-b", "branch": "fix"}
          ]
        }
      }
    }
    """)

    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")

    assert len(jobs) == 2
    # project-a 使用自己的 MY_BRANCH
    assert jobs[0].git_param == "MY_BRANCH"
    assert "MY_BRANCH" in jobs[0].params
    assert jobs[0].params["MY_BRANCH"] == "feature"
    # project-b 使用环境的 GIT_BRANCH
    assert jobs[1].git_param == "GIT_BRANCH"
    assert jobs[1].params["GIT_BRANCH"] == "fix"
