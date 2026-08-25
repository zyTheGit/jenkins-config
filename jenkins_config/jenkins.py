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
from types import TracebackType
from typing import Any
from urllib.parse import quote

import requests

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import log_debug, log_warn, is_debug_mode

# Git Parameter 插件的 _class 标识
_GIT_PARAM_CLASS = "GitParameterDefinition"

# Git Parameter 插件要求的远程仓库名前缀
_REMOTE_PREFIX = "origin/"


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
    UNKNOWN = "UNKNOWN"


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
    result: str | None
    duration: int  # 秒


# ============================================================================
# 日志脱敏
# ============================================================================

# 调试日志中需要脱敏的响应头（Set-Cookie 里的 JSESSIONID 等同于可复用的会话凭据）
SENSITIVE_HEADERS = frozenset({"set-cookie", "authorization", "www-authenticate"})


def _redact_headers(headers: Any) -> dict[str, str]:
    """脱敏 HTTP 响应头，供调试日志使用

    Args:
        headers: 响应头映射（requests 的 CaseInsensitiveDict 或普通 dict）

    Returns:
        敏感字段值替换为 "***" 的普通字典

    Example:
        >>> _redact_headers({"Set-Cookie": "JSESSIONID=abc", "Location": "/q/1"})
        {'Set-Cookie': '***', 'Location': '/q/1'}
    """
    return {
        key: ("***" if key.lower() in SENSITIVE_HEADERS else value)
        for key, value in dict(headers).items()
    }


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
        >>> queue_url, _ = client.trigger_build("my-project", {"branch": "main"})
        >>> build_num = client.get_build_number(queue_url)
        >>> status = client.get_build_status("my-project", build_num)
    """

    def __init__(
        self, url: str, token: str, username: str = "admin", timeout: int = 6
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

        # 配置 HTTP 重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],  # 仅 GET 重试，POST 不重试避免重复触发
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # 设置认证：Jenkins 使用 HTTP Basic Auth
        # 用户名和密码使用 API Token
        self.session.auth = (username, token)

        # 移除末尾的斜杠，方便后续拼接 URL
        self.base_url = url.rstrip("/")

        # 设置默认超时
        self.timeout = timeout

        # 缓存 Git Parameter 参数名查询结果 {job_path: set[str]}
        self._git_param_cache: dict[str, set[str]] = {}

        # 缓存 CSRF Crumb
        self._crumb_cache: tuple[str, str] | None = None
        self._crumb_time: float = 0

    # ========================================================================
    # 资源管理：显式关闭底层 Session
    # ========================================================================

    def close(self) -> None:
        """
        关闭底层 requests.Session，释放连接池

        长驻进程（如 MCP Server）中每次调用都新建客户端时，
        必须显式关闭，否则连接释放只能依赖 GC。

        Example:
            >>> client = JenkinsClient("http://jenkins", "token")
            >>> client.close()
        """
        try:
            self.session.close()
        except Exception:
            # 关闭失败不应影响主流程
            pass

    def __enter__(self) -> JenkinsClient:
        """
        进入上下文，返回自身

        Returns:
            当前 JenkinsClient 实例

        Example:
            >>> with JenkinsClient("http://jenkins", "token") as client:
            ...     client.health_check()  # doctest: +SKIP
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:

        """
        退出上下文时关闭 Session

        Args:
            exc_type: 异常类型
            exc_value: 异常实例
            traceback: 异常回溯

        Returns:
            始终为 False，不吞掉上下文中的异常
        """
        self.close()
        return False

    # ========================================================================
    # 公共方法：查询 Git Parameter 参数名
    # ========================================================================


    def get_git_parameter_names(self, job_path: str) -> set[str]:
        """
        查询 Job 中 GitParameterDefinition 类型的参数名

        Git Parameter 插件会验证分支值是否存在于远程仓库，
        裸名（如 prod）不在分支列表中，需要 origin/ 前缀（如 origin/prod）。
        此方法查询 Job 的参数定义，返回所有 GitParameterDefinition 参数名，
        供 trigger_build 自动添加 origin/ 前缀。

        结果会缓存，同一 Job 只查询一次（仅缓存成功结果，失败时下次重试）。

        Args:
            job_path: Jenkins Job 路径

        Returns:
            GitParameterDefinition 参数名集合，查询失败返回空集合
        """
        # 检查缓存
        if job_path in self._git_param_cache:
            return self._git_param_cache[job_path]

        git_params: set[str] = set()
        query_success = False

        try:
            encoded_path = quote(job_path, safe="-_.~")
            url = (
                f"{self.base_url}/job/{encoded_path}"
                f"/api/json?tree=property[parameterDefinitions[name,_class]]"
            )
            resp = self.session.get(url, timeout=self.timeout)
            if resp.ok:
                data = resp.json()
                for prop in data.get("property", []):
                    for param in prop.get("parameterDefinitions", []):
                        param_class = param.get("_class", "")
                        # 匹配 GitParameterDefinition（含完整包名或短名）
                        if _GIT_PARAM_CLASS in param_class:
                            git_params.add(param["name"])
                query_success = True
                log_debug(
                    f"Job '{job_path}' Git Parameter 参数: "
                    f"{git_params or '无'}"
                )
            else:
                log_debug(
                    f"查询 Job '{job_path}' 参数定义失败: "
                    f"HTTP {resp.status_code}"
                )
        except Exception as e:
            log_debug(f"查询 Job '{job_path}' 参数定义异常: {e}")

        # 仅缓存成功查询的结果（包括空集合），失败不缓存以便下次重试
        if query_success:
            self._git_param_cache[job_path] = git_params
        return git_params

    # ========================================================================
    # 私有方法：CSRF Token 处理
    # ========================================================================

    def _get_crumb(self) -> tuple[str, str] | None:
        """
        获取 CSRF Token（Crumb）

        Jenkins 默认启用 CSRF 保护，POST 请求需要携带 Crumb。
        Crumb 是一种简单的 CSRF 防护机制：
        1. 客户端先 GET /crumbIssuer/api/json 获取 crumb
        2. 然后在 POST 请求头中携带 crumb

        结果会缓存 30 分钟，避免频繁请求 crumb 接口。

        Returns:
            元组 (字段名, crumb值)，如 ("Jenkins-Crumb", "abc123")
            如果获取失败返回 None

        Note:
            有些 Jenkins 实例可能禁用了 CSRF 保护，此时返回 None
            调用方应该处理 None 的情况
        """
        # 检查缓存是否有效（30 分钟 TTL）
        if self._crumb_cache is not None and time.time() - self._crumb_time < 1800:
            return self._crumb_cache

        try:
            resp = self.session.get(
                f"{self.base_url}/crumbIssuer/api/json", timeout=self.timeout
            )
            if resp.ok:
                data = resp.json()
                crumb_value = data.get("crumb")
                if crumb_value:
                    result = (
                        data.get("crumbRequestField", "Jenkins-Crumb"),
                        crumb_value,
                    )
                    self._crumb_cache = result
                    self._crumb_time = time.time()
                    return result
        except Exception:
            # 忽略错误，让调用方处理 None 的情况
            pass
        return None

    # ========================================================================
    # 公共方法：触发构建
    # ========================================================================

    def trigger_build(
        self, job_path: str, params: dict
    ) -> tuple[str | None, str]:
        """
        触发 Jenkins 构建

        发送构建请求到 Jenkins，返回队列 URL 用于后续查询。
        对 GitParameterDefinition 类型的参数值自动添加 origin/ 前缀
        （Git Parameter 插件要求分支值带远程仓库名前缀）。

        Args:
            job_path: Jenkins Job 路径，如 "my-project" 或 "folder/my-project"
            params: 构建参数字典，如 {"BRANCH": "prod", "skip_tests": "true"}

        Returns:
            元组 (queue_url, diagnostic):
              - queue_url: 队列项 URL，如 "http://jenkins/queue/item/123/"，失败时为 None
              - diagnostic: 诊断信息（请求 URL、状态码、响应内容），成功时为空字符串

        Note:
            - 返回的 URL 用于查询构建编号（get_build_number）
            - Jenkins 会先排队，然后分配构建编号
            - HTTP 201 表示请求成功，队列项 URL 在 Location 头中
            - Git Parameter 参数值如无 origin/ 前缀会自动添加

        Example:
            >>> url, diag = client.trigger_build("my-project", {"BRANCH": "prod"})
            >>> # BRANCH="prod" 自动变为 BRANCH="origin/prod"
            >>> print(url)
            http://jenkins.example.com/queue/item/456/
        """
        # URL 编码 Job 路径（处理特殊字符和中文）
        # safe="-_.~" 保留 RFC 3986 未保留字符，Jenkins Job 名称中常见
        encoded_path = quote(job_path, safe="-_.~")

        # 构建完整 URL
        url = f"{self.base_url}/job/{encoded_path}/buildWithParameters"

        # 对 Git Parameter 参数值自动添加 origin/ 前缀
        git_param_names = self.get_git_parameter_names(job_path)
        effective_params = dict(params)  # 不修改原始参数
        for name in git_param_names:
            if name in effective_params:
                value = str(effective_params[name])
                if value and not value.startswith(_REMOTE_PREFIX):
                    effective_params[name] = f"{_REMOTE_PREFIX}{value}"
                    log_debug(
                        f"Git Parameter '{name}': "
                        f"'{value}' -> 'origin/{value}'"
                    )

        log_debug(f"触发构建: {url}")
        log_debug(f"原始参数: {params}")
        log_debug(f"发送参数: {effective_params}")

        # 准备请求头
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        # 获取并添加 CSRF Token
        crumb = self._get_crumb()
        if crumb:
            headers[crumb[0]] = crumb[1]
            # 只记录 crumb 字段名，值本身是可复用的 CSRF 凭据，不入日志
            log_debug(f"CSRF Token: {crumb[0]}=***")


        try:
            # 发送 POST 请求
            # allow_redirects=False 防止自动跟随重定向
            # Jenkins 返回 201 + Location 头，不是 302 重定向
            resp = self.session.post(
                url,
                data=effective_params,  # 参数作为 form data 发送（含自动前缀）
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )

            log_debug(f"响应状态码: {resp.status_code}")
            log_debug(f"响应头: {_redact_headers(resp.headers)}")


            # 201 Created 表示构建请求成功入队
            if resp.status_code == 201:
                # 队列项 URL 在 Location 头中
                queue_url = resp.headers.get("Location")
                log_debug(f"队列 URL: {queue_url}")
                return queue_url, ""
            elif resp.status_code == 403:
                # 403 可能是 crumb 过期，清除缓存并重试一次
                log_debug("收到 403，清除 crumb 缓存并重试")
                # 先移除旧的 crumb header，防止新 crumb 为 None 时残留
                if self._crumb_cache is not None:
                    headers.pop(self._crumb_cache[0], None)
                self._crumb_cache = None
                crumb = self._get_crumb()
                if crumb:
                    headers[crumb[0]] = crumb[1]
                else:
                    log_warn("403 重试: 无法获取新的 CSRF crumb，将尝试无 crumb 请求")
                try:
                    resp = self.session.post(
                        url,
                        data=effective_params,
                        headers=headers,
                        timeout=self.timeout,
                        allow_redirects=False,
                    )
                    if resp.status_code == 201:
                        queue_url = resp.headers.get("Location")
                        log_debug(f"重试成功，队列 URL: {queue_url}")
                        return queue_url, ""
                except Exception as retry_e:
                    log_debug(f"重试异常: {retry_e}")
                    # 重试异常时，诊断信息应包含重试异常而非原始 403
                    diagnostic = (
                        f"请求URL: {url}\n"
                        f"重试异常: {retry_e}"
                    )
                    return None, diagnostic
                log_debug(f"重试仍失败，响应内容: {resp.text[:500]}")
                diagnostic = (
                    f"请求URL: {url}\n"
                    f"状态码: {resp.status_code}\n"
                    f"响应内容: {resp.text[:500]}"
                )
                return None, diagnostic
            else:
                log_debug(f"触发失败，响应内容: {resp.text[:500]}")
                diagnostic = (
                    f"请求URL: {url}\n"
                    f"状态码: {resp.status_code}\n"
                    f"响应内容: {resp.text[:500]}"
                )
                return None, diagnostic
        except requests.exceptions.RetryError as e:
            log_debug(f"触发异常（重试耗尽）: {e}")
            diagnostic = f"请求URL: {url}\n重试耗尽: {e}"
            return None, diagnostic
        except Exception as e:
            log_debug(f"触发异常: {e}")
            diagnostic = f"请求URL: {url}\n异常: {e}"
            return None, diagnostic

    # ========================================================================
    # 公共方法：获取构建编号
    # ========================================================================

    def get_build_number(self, queue_url: str, timeout: int = 30) -> int | None:
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
            - 单次请求超时与轮询间隔都收敛到剩余预算之内，
              使 timeout 成为整体墙钟上限（调用方据此保证快速返回）

        Example:
            >>> build_num = client.get_build_number("http://jenkins/queue/item/456/")
            >>> print(build_num)
            789
        """
        # 基于 elapsed 时间轮询，每次间隔最多 3 秒
        deadline = time.monotonic() + timeout
        poll_count = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            poll_count += 1
            try:
                api_url = f"{queue_url.rstrip('/')}/api/json"
                log_debug(f"查询队列: {api_url} (第 {poll_count} 次)")
                # 单次请求不得超出剩余预算，否则底层超时与重试会把总耗时放大
                resp = self.session.get(
                    api_url, timeout=min(self.timeout, remaining)
                )
                if resp.ok:
                    data = resp.json()
                    log_debug(f"队列响应: {data}")
                    if data.get("cancelled"):
                        log_debug("构建已被取消")
                        return None
                    executable = data.get("executable")
                    if executable and executable.get("number"):
                        build_num = executable["number"]
                        log_debug(f"已分配构建编号: #{build_num}")
                        return build_num
            except requests.exceptions.RetryError as e:
                log_debug(f"查询队列异常（重试耗尽）: {e}")
            except Exception as e:
                log_debug(f"查询队列异常: {e}")
            # 轮询间隔同样受剩余预算约束，避免在超时后仍多睡 3 秒
            sleep_for = min(3.0, deadline - time.monotonic())
            if sleep_for <= 0:
                break
            time.sleep(sleep_for)

        log_debug("获取构建编号超时")
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
        encoded_path = quote(job_path, safe="-_.~")
        url = f"{self.base_url}/job/{encoded_path}/{build_num}/api/json"

        log_debug(f"查询构建状态: {url}")

        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.ok:
                data = resp.json()
                result = data.get("result")  # SUCCESS, FAILURE, ABORTED, null
                duration_ms = data.get("duration", 0)  # 毫秒

                log_debug(f"构建结果: {result}, 耗时: {duration_ms}ms")

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
            else:
                log_debug(f"查询状态失败: HTTP {resp.status_code}")
        except requests.exceptions.RetryError as e:
            log_debug(f"查询状态异常（重试耗尽）: {e}")
        except Exception as e:
            log_debug(f"查询状态异常: {e}")

        # 请求失败时返回 UNKNOWN 状态（而非 BUILDING）
        return BuildInfo(
            number=build_num, status=BuildStatus.UNKNOWN, result=None, duration=0
        )

    # ========================================================================
    # 公共方法：获取构建日志
    # ========================================================================

    def get_build_log(
        self, job_path: str, build_num: int, max_bytes: int | None = None
    ) -> str:
        """
        获取构建日志

        获取指定构建的控制台输出日志。

        Args:
            job_path: Jenkins Job 路径
            build_num: 构建编号
            max_bytes: 最多保留的字节数，None 表示不限制；
                指定时以流式方式读取并只保留日志尾部，避免超大日志占满内存

        Returns:
            日志文本，失败时返回空字符串；被截断时开头带一行截断说明

        Note:
            - consoleText 返回纯文本格式
            - console 返回 HTML 格式（包含 ANSI 颜色码）

        Example:
            >>> log = client.get_build_log("my-project", 123)
            >>> print(log[:100])  # 打印前 100 个字符
            Started by user admin...
            >>> tail = client.get_build_log("my-project", 123, max_bytes=50 * 1024)
        """
        encoded_path = quote(job_path, safe="-_.~")
        url = f"{self.base_url}/job/{encoded_path}/{build_num}/consoleText"

        log_debug(f"获取构建日志: {url}")

        try:
            if max_bytes is None or max_bytes <= 0:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.ok:
                    log_content = resp.text
                    log_debug(f"日志长度: {len(log_content)} 字符")
                    return log_content
                log_debug(f"获取日志失败: HTTP {resp.status_code}")
                return ""

            return self._get_build_log_tail(url, max_bytes)
        except requests.exceptions.RetryError as e:
            log_debug(f"获取日志异常（重试耗尽）: {e}")
        except Exception as e:
            log_debug(f"获取日志异常: {e}")

        return ""

    def _get_build_log_tail(self, url: str, max_bytes: int) -> str:
        """
        只取日志尾部 max_bytes 字节

        Args:
            url: consoleText 接口地址
            max_bytes: 保留的最大字节数

        Returns:
            日志尾部文本；发生截断时开头附加一行说明，请求失败返回空字符串

        Note:
            - 优先用 HTTP 尾部 Range（``bytes=-N``）请求，服务端支持时只传输尾部，
              不必把几百 MB 的完整日志拉过网络；且不增加额外往返
            - 服务端忽略 Range（返回 200）时退回流式读取 + 滚动丢弃，
              内存占用仍恒定在 max_bytes + 单块大小以内
        """
        headers = {"Range": f"bytes=-{max_bytes}"}
        with self.session.get(
            url, timeout=self.timeout, stream=True, headers=headers
        ) as resp:
            if not resp.ok:
                log_debug(f"获取日志失败: HTTP {resp.status_code}")
                return ""

            partial = resp.status_code == 206
            buffer = bytearray()
            total = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    # 只保留尾部，内存占用恒定在 max_bytes + 单块大小以内
                    del buffer[: len(buffer) - max_bytes]

            if partial:
                # 206 时只收到尾部，原始长度需从 Content-Range 头解析
                total = _parse_content_range_total(
                    resp.headers.get("Content-Range", "")
                ) or total

        text = buffer.decode("utf-8", errors="replace")
        log_debug(
            f"日志长度: {total} 字节（保留尾部 {len(buffer)} 字节，"
            f"{'Range 命中' if partial else '流式截断'}）"
        )
        if total > len(buffer):
            return (
                f"...（日志已截断，原始长度 {total} 字节，"
                f"仅保留尾部 {len(buffer)} 字节）\n{text}"
            )
        return text


    # ========================================================================

    # 公共方法：Jenkins 连接预检
    # ========================================================================

    def health_check(self) -> bool:
        """
        检查 Jenkins 服务器是否可达

        Returns:
            True 如果 Jenkins 可达

        Raises:
            ConnectionError: 如果 Jenkins 不可达
        """
        try:
            resp = self.session.get(
                f"{self.base_url}/api/json", timeout=self.timeout
            )
            if resp.ok:
                return True
            raise ConnectionError(
                f"Jenkins 服务器返回异常状态: HTTP {resp.status_code}"
            )
        except requests.exceptions.RetryError as e:
            raise ConnectionError(
                f"Jenkins 服务器多次重试后仍不可达: {self.base_url}"
            ) from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"无法连接到 Jenkins 服务器: {self.base_url}"
            ) from e
        except requests.exceptions.Timeout as e:
            raise ConnectionError(
                f"连接 Jenkins 服务器超时: {self.base_url}"
            ) from e


def _parse_content_range_total(value: str) -> int:
    """
    从 Content-Range 头解析资源总长度

    Args:
        value: 形如 ``bytes 900-999/1000`` 的头部值

    Returns:
        总字节数；无法解析时返回 0

    Example:
        >>> _parse_content_range_total("bytes 900-999/1000")
        1000
        >>> _parse_content_range_total("bytes 0-1/*")
        0
    """
    total = value.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else 0
