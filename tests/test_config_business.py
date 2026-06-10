"""
配置高级业务逻辑测试

测试覆盖：
- get_jobs 边界情况（空环境、全部、过滤、key 格式）
- list_environments / list_projects
- create_job_from_record
- Config 类型默认值
"""

from jenkins_config.config import Config


def _setup_config(tmp_path):
    """共享的测试配置"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        '    description: "dev env"\n'
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        "      - name: project-a\n"
        "        params:\n"
        '          branch: "feature"\n'
        "  test:\n"
        "    params:\n"
        '      branch: "test"\n'
        "    projects:\n"
        "      - name: project-b\n",
        encoding="utf-8",
    )
    return config_file


# ============================================================================
# get_jobs 边界
# ============================================================================


def test_get_jobs_empty_env(tmp_path):
    """不存在的环境返回空列表"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="nonexistent")
    assert jobs == []


def test_get_jobs_all_envs(tmp_path):
    """不指定环境返回所有环境的 job"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    jobs = config.get_jobs()
    assert len(jobs) == 2  # project-a + project-b


def test_get_jobs_env_filter(tmp_path):
    """使用 env 过滤"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="test")
    assert len(jobs) == 1
    assert jobs[0].env == "test"


def test_get_jobs_no_match_filter(tmp_path):
    """不匹配的 jobs 过滤返回空列表"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    jobs = config.get_jobs(jobs=["dev:nonexistent"])
    assert jobs == []


def test_get_jobs_key_format(tmp_path):
    """Job key 格式: env_project_name（中划线转下划线）"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")
    assert jobs[0].key == "dev_project_a"
    assert jobs[0].key == f"dev_{jobs[0].project_name.replace('-', '_')}"


# ============================================================================
# list_environments / list_projects
# ============================================================================


def test_list_environments(tmp_path):
    """列出环境名称和描述"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    envs = config.list_environments()
    assert len(envs) == 2
    assert ("dev", "dev env") in envs
    assert ("test", "") in envs


def test_list_projects_all(tmp_path):
    """列出所有项目"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    projects = config.list_projects()
    assert len(projects) == 2
    assert ("dev", "project-a", "project-a") in projects


def test_list_projects_filtered(tmp_path):
    """按环境过滤项目"""
    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))
    projects = config.list_projects(env="test")
    assert len(projects) == 1
    assert projects[0][0] == "test"
    assert projects[0][1] == "project-b"


# ============================================================================
# create_job_from_record
# ============================================================================


def test_create_job_from_record_match(tmp_path):
    """从历史记录重建 Job"""
    from jenkins_config.history import BuildRecord

    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))

    record = BuildRecord(
        timestamp="2026-06-09T10:00:00",
        env="dev",
        job_key="dev_project_a",
        build_num=42,
        status="SUCCESS",
        duration=60,
        log_file="",
        project_name="project-a",
        params={"branch": "hotfix"},
    )

    job = config.create_job_from_record(record)
    assert job is not None
    assert job.key == "dev_project_a"
    assert job.branch == "hotfix"
    assert job.params["branch"] == "hotfix"


def test_create_job_from_record_no_match(tmp_path):
    """历史记录对应环境不存在时返回 None"""
    from jenkins_config.history import BuildRecord

    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))

    record = BuildRecord(
        timestamp="", env="unknown", job_key="unknown_app",
        build_num=1, status="SUCCESS", duration=0, log_file="",
    )

    assert config.create_job_from_record(record) is None


def test_create_job_from_record_no_params(tmp_path):
    """历史记录无 params 时从配置合并"""
    from jenkins_config.history import BuildRecord

    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))

    record = BuildRecord(
        timestamp="", env="dev", job_key="dev_project_a",
        build_num=1, status="SUCCESS", duration=0, log_file="",
        project_name="project-a",
    )

    job = config.create_job_from_record(record)
    assert job is not None
    assert job.params["branch"] == "feature"  # 从配置合并


def test_create_job_from_record_unknown_project(tmp_path):
    """历史记录的项目不在配置中时返回 None"""
    from jenkins_config.history import BuildRecord

    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))

    record = BuildRecord(
        timestamp="", env="dev", job_key="dev_ghost",
        build_num=1, status="SUCCESS", duration=0, log_file="",
        project_name="ghost",
    )

    assert config.create_job_from_record(record) is None


def test_create_job_from_record_extract_name(tmp_path):
    """从 job_key 提取项目名称（project_name 为空时）"""
    from jenkins_config.history import BuildRecord

    config_file = _setup_config(tmp_path)
    config = Config.load(str(config_file))

    record = BuildRecord(
        timestamp="", env="dev", job_key="dev_project_a",
        build_num=1, status="SUCCESS", duration=0, log_file="",
        # project_name 为空，从 job_key 提取
    )

    job = config.create_job_from_record(record)
    assert job is not None
    assert job.project_name == "project-a"


# ============================================================================
# Config 类型默认值
# ============================================================================


def test_server_config_defaults():
    """ServerConfig 默认值"""
    from jenkins_config.config_types import ServerConfig
    s = ServerConfig(url="http://localhost:8080")
    assert s.username == "admin"
    assert s.token == ""


def test_build_config_defaults():
    """BuildConfig 默认值"""
    from jenkins_config.config_types import BuildConfig
    b = BuildConfig()
    assert b.mode == "parallel"
    assert b.poll_interval == 10


def test_config_defaults():
    """Config dataclass 默认值"""
    from jenkins_config.config_types import Config
    c = Config()
    assert c.branch_field == "branch"
    assert c.environments == {}
