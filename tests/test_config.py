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
