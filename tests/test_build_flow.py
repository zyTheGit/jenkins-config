"""构建流程接缝测试 - job 选择与参数解析"""

import pytest

from jenkins_config.build_flow import Selection, parse_params, select_jobs
from jenkins_config.config import Config
from jenkins_config.config_types import (
    BuildConfig,
    Environment,
    Project,
    ServerConfig,
)


def _config() -> Config:
    return Config(
        server=ServerConfig(url="http://jenkins:8080", token="token"),
        build=BuildConfig(curl_timeout=17),
        branch_field="branch",
        environments={
            "dev": Environment(
                name="dev",
                params={"branch": "develop", "ENV_ONLY": "env"},
                projects=[
                    Project(name="app", params={"APP": "1"}),
                    Project(name="api", path="folder/api"),
                ],
            ),
            "test": Environment(
                name="test",
                branch_field="BRANCH",
                params={"BRANCH": "main"},
                projects=[Project(name="app")],
            ),
        },
    )


# ============================================================================
# parse_params
# ============================================================================


def test_parse_params_json():
    parsed, skipped = parse_params('{"BRANCH": "develop", "SKIP_TESTS": "true"}')
    assert parsed == {"BRANCH": "develop", "SKIP_TESTS": "true"}
    assert skipped == []


def test_parse_params_url_encoded():
    parsed, skipped = parse_params("BRANCH=develop&SKIP_TESTS=true")
    assert parsed == {"BRANCH": "develop", "SKIP_TESTS": "true"}
    assert skipped == []


def test_parse_params_empty():
    assert parse_params("") == ({}, [])
    assert parse_params("  ") == ({}, [])


def test_parse_params_invalid_json_raises():
    with pytest.raises(ValueError, match="不是合法的 JSON"):
        parse_params("{invalid json}")


def test_parse_params_json_object_required():
    with pytest.raises(ValueError, match="不是合法的 JSON"):
        parse_params('{"a": 1')


def test_parse_params_json_non_string_values():
    parsed, skipped = parse_params(
        '{"SKIP_TESTS": true, "RETRY": false, "COUNT": 3}'
    )
    assert parsed == {"SKIP_TESTS": "true", "RETRY": "false", "COUNT": "3"}
    assert skipped == []


def test_parse_params_skips_null_and_non_scalar():
    parsed, skipped = parse_params(
        '{"EMPTY": null, "LIST": [1, 2], "DICT": {"x": 1}, "OK": "ok", "NUM": 5}'
    )
    assert parsed == {"OK": "ok", "NUM": "5"}
    assert sorted(skipped) == ["DICT", "EMPTY", "LIST"]


# ============================================================================
# select_jobs
# ============================================================================


def test_select_jobs_merges_env_and_project_params():
    selection = select_jobs(_config(), env="dev")

    assert isinstance(selection, Selection)
    assert [job.key for job in selection.jobs] == ["dev_app", "dev_api"]
    assert selection.jobs[0].params == {
        "branch": "develop",
        "ENV_ONLY": "env",
        "APP": "1",
    }
    assert selection.jobs[0].branch == "develop"
    assert selection.jobs[1].path == "folder/api"
    assert selection.skipped_params == []


def test_select_jobs_filters_projects():
    selection = select_jobs(_config(), env="dev", filters=["dev:app"])

    assert [job.key for job in selection.jobs] == ["dev_app"]


def test_select_jobs_applies_branch_with_env_specific_field():
    selection = select_jobs(_config(), branch="hotfix")

    dev_job = next(job for job in selection.jobs if job.key == "dev_app")
    test_job = next(job for job in selection.jobs if job.key == "test_app")

    assert dev_job.params["branch"] == "hotfix"
    assert dev_job.branch == "hotfix"
    assert test_job.params["BRANCH"] == "hotfix"
    assert test_job.branch == "hotfix"


def test_select_jobs_params_override_branch_override():
    selection = select_jobs(_config(), env="dev", branch="hotfix", params_text="branch=release")

    assert selection.jobs[0].params["branch"] == "release"


def test_select_jobs_applies_url_encoded_params():
    selection = select_jobs(
        _config(), env="dev", params_text="APP_VERSION=2.0&SKIP_TESTS=true"
    )

    assert selection.jobs[0].params["APP_VERSION"] == "2.0"
    assert selection.jobs[0].params["SKIP_TESTS"] == "true"


def test_select_jobs_reports_skipped_json_params():
    selection = select_jobs(
        _config(), env="dev", params_text='{"OK": "yes", "LIST": [1, 2]}'
    )

    assert selection.jobs[0].params["OK"] == "yes"
    assert selection.skipped_params == ["LIST"]


def test_select_jobs_invalid_json_raises():
    with pytest.raises(ValueError, match="不是合法的 JSON"):
        select_jobs(_config(), env="dev", params_text="{invalid json}")


def test_select_jobs_empty_result_skips_params_parsing():
    selection = select_jobs(_config(), env="missing", params_text="{invalid json}")

    assert selection.jobs == []
    assert selection.skipped_params == []
