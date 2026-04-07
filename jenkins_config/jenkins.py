# jenkins_config/jenkins.py
"""
Jenkins API 客户端模块 - 封装与 Jenkins 服务器的所有 HTTP 交互

这个模块提供了与 Jenkins 服务器通信的核心功能：
1. 触发构建（带参数）
2. 获取构建编号（从队列中）
3. 查询构建状态
4. 获取构建日志

使用 requests 库替代了原来的 curl 命令，实现了纯 Python 实现。

Jenkins API 说明：
- 触发构建：POST /job/{job_path}/buildWithParameters
- 队列查询：GET /queue/item/{id}/api/json
- 构建状态：GET /job/{job_path}/{build_num}/api/json
- 构建日志：GET /job/{job_path}/{build_num}/consoleText
"""

from __future__ import annotations
import time
from enum import Enum
from dataclasses import dataclass
from urllib.parse import quote
from typing import Optional
import requests


# ============================================================================
# 枚举和数据类定义
# ============================================================================


class BuildStatus(Enum):
    """
    构建状态枚举

    使用枚举可以确保状态值的一致性，避免字符串拼写错误

    Attributes:
        SUCCESS: 构建成功
        FAILURE: 构建失败
        ABORTED: 构建被中止（手动取消）
        BUILDING: 正在构建中
        TIMEOUT: 监控超时
        CANCELLED: 在队列中被取消
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    BUILDING = "BUILDING"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


@dataclass
class BuildInfo:
    """
    构建信息

    存储单次构建的基本信息

    Attributes:
        number: 构建编号
        status: 构建状态（BuildStatus 枚举）
        result: 原始结果字符串（来自 Jenkins API）
        duration: 构建耗时（秒）
    """

    number: int
    status: BuildStatus
    result: Optional[str]
    duration: int  # 秒


# ============================================================================
# Jenkins 客户端类
# ============================================================================


class JenkinsClient:
    """
    Jenkins API 客户端

    封装所有与 Jenkins 服务器的 HTTP 交互。
    使用 requests.Session 保持连接，支持认证和超时设置。

    Attributes:
        session: requests Session 对象，用于保持连接
        base_url: Jenkins 服务器基础 URL
        timeout: HTTP 请求超时时间

    Example:
        >>> client = JenkinsClient("http://jenkins.example.com", "api_token")
        >>> queue_url = client.trigger_build("my-project", {"branch": "main"})
        >>> build_num = client.get_build_number(queue_url)
        >>> status = client.get_build_status("my-project", build_num)
    """

    def __init__(
        self, url: str, token: str, username: str = "admin", timeout: int = 30
    ):
        """
        初始化 Jenkins 客户端

        Args:
            url: Jenkins 服务器地址
            username: Jenkins 用户名
            token: API Token（在 Jenkins 用户设置中生成）
            timeout: HTTP 请求超时时间（秒）
        """
        # 创建 Session 以保持连接和 Cookie
        self.session = requests.Session()

        # 设置认证：Jenkins 使用 HTTP Basic Auth
        # 用户名和密码使用 API Token
        self.session.auth = (username, token)

        # 移除末尾的斜杠，方便后续拼接 URL
        self.base_url = url.rstrip("/")

        # 设置默认超时
        self.timeout = timeout

    # ========================================================================
    # 私有方法：CSRF Token 处理
    # ========================================================================

    def _get_crumb(self) -> Optional[tuple[str, str]]:
        """
        获取 CSRF Token（Crumb）

        Jenkins 默认启用 CSRF 保护，POST 请求需要携带 Crumb。
        Crumb 是一种简单的 CSRF 防护机制：
        1. 客户端先 GET /crumbIssuer/api/json 获取 crumb
        2. 然后在 POST 请求头中携带 crumb

        Returns:
            元组 (字段名, crumb值)，如 ("Jenkins-Crumb", "abc123")
            如果获取失败返回 None

        Note:
            有些 Jenkins 实例可能禁用了 CSRF 保护，此时返回 None
            调用方应该处理 None 的情况
        """
        try:
            resp = self.session.get(
                f"{self.base_url}/crumbIssuer/api/json", timeout=self.timeout
            )
            if resp.ok:
                data = resp.json()
                # 返回 (字段名, crumb值)
                # 有些实例可能使用不同的字段名
                return (
                    data.get("crumbRequestField", "Jenkins-Crumb"),
                    data.get("crumb"),
                )
        except Exception:
            # 忽略错误，让调用方处理 None 的情况
            pass
        return None

    # ========================================================================
    # 公共方法：触发构建
    # ========================================================================

    def trigger_build(self, job_path: str, params: dict) -> Optional[str]:
        """
        触发 Jenkins 构建

        发送构建请求到 Jenkins，返回队列 URL 用于后续查询。

        Args:
            job_path: Jenkins Job 路径，如 "my-project" 或 "folder/my-project"
            params: 构建参数字典，如 {"branch": "main", "skip_tests": "true"}

        Returns:
            队列项 URL，如 "http://jenkins/queue/item/123/"
            如果触发失败返回 None

        Note:
            - 返回的 URL 用于查询构建编号（get_build_number）
            - Jenkins 会先排队，然后分配构建编号
            - HTTP 201 表示请求成功，队列项 URL 在 Location 头中

        Example:
            >>> url = client.trigger_build("my-project", {"branch": "develop"})
            >>> print(url)
            http://jenkins.example.com/queue/item/456/
        """
        # URL 编码 Job 路径（处理特殊字符和中文）
        # safe="" 表示所有字符都编码（包括 /）
        encoded_path = quote(job_path, safe="")

        # 构建完整 URL
        url = f"{self.base_url}/job/{encoded_path}/buildWithParameters"

        # 准备请求头
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        # 获取并添加 CSRF Token
        crumb = self._get_crumb()
        if crumb:
            headers[crumb[0]] = crumb[1]

        try:
            # 发送 POST 请求
            # allow_redirects=False 防止自动跟随重定向
            # Jenkins 返回 201 + Location 头，不是 302 重定向
            resp = self.session.post(
                url,
                data=params,  # 参数作为 form data 发送
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )

            # 201 Created 表示构建请求成功入队
            if resp.status_code == 201:
                # 队列项 URL 在 Location 头中
                return resp.headers.get("Location")
        except Exception:
            pass

        return None

    # ========================================================================
    # 公共方法：获取构建编号
    # ========================================================================

    def get_build_number(self, queue_url: str, timeout: int = 30) -> Optional[int]:
        """
        从队列中获取构建编号

        构建触发后，Jenkins 先将其放入队列，然后分配执行器和构建编号。
        这个方法轮询队列 API 直到获取到构建编号。

        Args:
            queue_url: 队列项 URL（trigger_build 的返回值）
            timeout: 超时时间（秒）

        Returns:
            构建编号，如 123
            如果超时或被取消返回 None

        Note:
            - 队列项包含 executable 字段时，表示已分配构建编号
            - cancelled 字段为 true 时，表示构建被取消

        Example:
            >>> build_num = client.get_build_number("http://jenkins/queue/item/456/")
            >>> print(build_num)
            789
        """
        # 轮询 timeout 次，每次间隔 1 秒
        for _ in range(timeout):
            try:
                resp = self.session.get(
                    f"{queue_url.rstrip('/')}/api/json", timeout=self.timeout
                )
                if resp.ok:
                    data = resp.json()

                    # 检查是否被取消
                    if data.get("cancelled"):
                        return None

                    # 检查是否已分配执行器
                    executable = data.get("executable")
                    if executable and executable.get("number"):
                        return executable["number"]

            except Exception:
                pass

            # 等待 1 秒后重试
            time.sleep(1)

        return None

    # ========================================================================
    # 公共方法：查询构建状态
    # ========================================================================

    def get_build_status(self, job_path: str, build_num: int) -> BuildInfo:
        """
        获取构建状态

        查询指定构建的当前状态和详细信息。

        Args:
            job_path: Jenkins Job 路径
            build_num: 构建编号

        Returns:
            BuildInfo 对象，包含编号、状态、结果、耗时

        Note:
            - result 为 null 表示还在构建中
            - duration 单位是毫秒，需要转换为秒

        Example:
            >>> info = client.get_build_status("my-project", 123)
            >>> print(info.status)
            BuildStatus.SUCCESS
            >>> print(info.duration)
            60  # 60秒
        """
        # URL 编码并构建 API URL
        encoded_path = quote(job_path, safe="")
        url = f"{self.base_url}/job/{encoded_path}/{build_num}/api/json"

        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.ok:
                data = resp.json()
                result = data.get("result")  # SUCCESS, FAILURE, ABORTED, null
                duration_ms = data.get("duration", 0)  # 毫秒

                # 将字符串结果转换为枚举
                if result == "SUCCESS":
                    status = BuildStatus.SUCCESS
                elif result == "FAILURE":
                    status = BuildStatus.FAILURE
                elif result == "ABORTED":
                    status = BuildStatus.ABORTED
                else:
                    # result 为 null 表示还在构建中
                    status = BuildStatus.BUILDING

                return BuildInfo(
                    number=build_num,
                    status=status,
                    result=result,
                    duration=duration_ms // 1000,  # 转换为秒
                )
        except Exception:
            pass

        # 请求失败时返回默认状态
        return BuildInfo(
            number=build_num, status=BuildStatus.BUILDING, result=None, duration=0
        )

    # ========================================================================
    # 公共方法：获取构建日志
    # ========================================================================

    def get_build_log(self, job_path: str, build_num: int) -> str:
        """
        获取构建日志

        获取指定构建的控制台输出日志。

        Args:
            job_path: Jenkins Job 路径
            build_num: 构建编号

        Returns:
            日志文本，失败时返回空字符串

        Note:
            - consoleText 返回纯文本格式
            - console 返回 HTML 格式（包含 ANSI 颜色码）

        Example:
            >>> log = client.get_build_log("my-project", 123)
            >>> print(log[:100])  # 打印前 100 个字符
            Started by user admin...
        """
        encoded_path = quote(job_path, safe="")
        url = f"{self.base_url}/job/{encoded_path}/{build_num}/consoleText"

        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.ok:
                return resp.text
        except Exception:
            pass

        return ""
