#!/usr/bin/env node
/**
 * jenkins-config MCP Server 的 npx 启动器。
 *
 * MCP Server 本体是 Python 实现，但发布物是 PyInstaller 打出的单文件二进制
 * （自带 Python 运行时），因此使用方机器上只需要 Node，不需要 Python。
 *
 * 首次运行时从 GitHub Release 下载当前平台的二进制并缓存，之后直接复用。
 * 所有日志一律写 stderr —— stdout 是 MCP 的 JSON-RPC 通道，不能污染。
 *
 * 解析优先级：
 *   1. JENKINS_MCP_BINARY   直接指定二进制路径
 *   2. JENKINS_MCP_PYTHON   指定解释器，运行 -m jenkins_config.mcp.server（开发用）
 *   3. 缓存中已下载的二进制
 *   4. 从 Release 下载二进制（校验 sha256）
 *   5. 兜底：PATH 上的 jenkins-config-mcp / uvx / python
 */

'use strict';

const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { version } = require('../package.json');

const REPO = 'zyTheGit/jenkins-config';
const CHECKSUMS = 'checksums.txt';
const MODULE_ARGS = ['-m', 'jenkins_config.mcp.server'];
const DEFAULT_PACKAGE =
  'jenkins-config[mcp] @ git+https://github.com/zyTheGit/jenkins-config.git';

// cmd.exe 需要转义的元字符。三处转义共用一份，避免改一处漏两处
const CMD_META = /([()\][%!^"`<>&|;, *?])/g;

// 主动关停时记录信号：既用于去重（避免重复杀树），也用于区分关停与真失败
let shutdownSignal = null;

// 平台 → Release 资产名，与 .github/workflows/build.yml 的产物命名保持一致
const ASSETS = {
  'win32-x64': 'jenkins-config-mcp-win-x64.exe',
  'darwin-x64': 'jenkins-config-mcp-macos-x64',
  'darwin-arm64': 'jenkins-config-mcp-macos-arm64',
  'linux-x64': 'jenkins-config-mcp-linux-x64',
  'linux-arm64': 'jenkins-config-mcp-linux-arm64',
};

/**
 * 输出一行日志到 stderr。
 *
 * @param {string} message 日志内容
 */
function log(message) {
  process.stderr.write(`[jenkins-config-mcp] ${message}\n`);
}

/**
 * 判断路径是否指向一个存在的普通文件。
 *
 * @param {string} candidate 待检查路径
 * @returns {boolean} 是文件则 true
 */
function isExistingFile(candidate) {
  try {
    return fs.statSync(candidate).isFile();
  } catch {
    // 不存在或不可读
    return false;
  }
}

/**
 * 在 PATH 中查找可执行文件（Windows 下按 PATHEXT 补全后缀）。
 *
 * 不走 shell，避免把用户参数交给命令行解析器。
 *
 * @param {string} name 可执行文件名
 * @returns {string|null} 命中的绝对路径，未命中返回 null
 */
function which(name) {
  if (name.includes(path.sep) || name.includes('/')) {
    return isExistingFile(name) ? name : null;
  }
  let exts = [''];
  if (process.platform === 'win32') {
    exts = (process.env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean);
    // 名字已自带 PATHEXT 后缀时（jenkins-config-mcp.cmd）要先按原名找，
    // 否则只会去试 xxx.cmd.EXE 这类不存在的组合，明明在 PATH 上却找不到
    if (exts.some((ext) => name.toLowerCase().endsWith(ext.toLowerCase()))) {
      exts = [''].concat(exts);
    }
  }
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext);
      if (isExistingFile(candidate)) return candidate;
    }
  }
  return null;
}

/**
 * 判断命令是否是 Windows 批处理脚本（.cmd / .bat）。
 *
 * Node 18.20 / 20.12 起（CVE-2024-27980 加固）不再允许在 shell: false 下
 * 直接 spawn 批处理文件，会同步抛出 EINVAL。而 pip / npm 在 Windows 上
 * 生成的 console script shim 经常就是 .cmd，兜底分支正好会命中它。
 *
 * @param {string} command 待执行的命令路径
 * @returns {boolean} 是批处理脚本则 true
 */
function isBatchScript(command) {
  return process.platform === 'win32' && /\.(cmd|bat)$/i.test(command);
}

/**
 * 转义交给 cmd.exe 的命令路径。
 *
 * 走 cmd.exe 是被迫的（批处理只能由它解释），因此必须自己把元字符挡掉，
 * 不能让路径里的 `&` `^` 之类被当成命令分隔符。
 *
 * @param {string} value 命令路径
 * @returns {string} 转义后的命令
 */
function escapeCmdCommand(value) {
  return String(value).replace(CMD_META, '^$1');
}

/**
 * 转义交给 cmd.exe 的单个参数。
 *
 * 先按 Windows 的 argv 规则处理反斜杠与引号，再包引号，最后转义 cmd 元字符；
 * 元字符转义做两遍 —— 批处理里的 `%*` 会被 cmd 二次解析，只转一遍在 shim
 * 展开参数时又会被吃掉一层。
 *
 * @param {string} value 参数原文
 * @returns {string} 转义后的参数
 */
function escapeCmdArgument(value) {
  let arg = String(value);
  arg = arg.replace(/(?=(\\+?)?)\1"/g, '$1$1\\"');
  arg = arg.replace(/(?=(\\+?)?)\1$/, '$1$1');
  arg = `"${arg}"`;
  return arg.replace(CMD_META, '^$1').replace(CMD_META, '^$1');
}

/**
 * 把批处理命令包装成一次 cmd.exe 调用。
 *
 * 不用 spawn 的 shell: true —— Node 24 起那条路径会打印 DEP0190 弃用警告，
 * 且由 Node 拼接命令行时不会转义参数。这里自己拼好并置
 * windowsVerbatimArguments，告诉 Node 命令行已经转义完毕。
 *
 * @param {string} command 批处理脚本路径
 * @param {string[]} args 传给脚本的参数
 * @returns {{command: string, args: string[]}} cmd.exe 调用形式
 */
function wrapForCmd(command, args) {
  const line = [escapeCmdCommand(command)].concat(args.map(escapeCmdArgument)).join(' ');
  return { command: process.env.COMSPEC || 'cmd.exe', args: ['/d', '/s', '/c', `"${line}"`] };
}

/**
 * 终止子进程（经 cmd.exe 转发时连同整棵进程树）。
 *
 * 走 cmd.exe 时 child 只是 cmd.exe 本身，真正的 MCP Server 是它的子进程。
 * Windows 没有进程组信号，单杀 cmd.exe 会留下孤儿进程继续持有继承来的
 * stdin/stdout，客户端会一直等一个没人回应的管道，只能用 taskkill /T 杀树。
 *
 * 只执行一次：taskkill 路径不会置位 child.killed，重复信号会拿着可能已被
 * 复用的 PID 再杀一遍。
 *
 * @param {import('node:child_process').ChildProcess} child 子进程
 * @param {string} sig 收到的信号名
 * @param {boolean} viaCmd 是否经 cmd.exe 转发
 */
function terminate(child, sig, viaCmd) {
  if (shutdownSignal) return;
  shutdownSignal = sig;

  if (viaCmd && child.pid) {
    try {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
      // taskkill 不在 PATH 上（精简镜像）时退回单进程 kill，至少别静默什么都不做
      killer.on('error', () => child.kill(sig));
      // taskkill 起来了却没干成（拒绝访问、被安全软件拦截）同样要退回，
      // 否则进程树存活而这里既无日志也无动作 —— child.kill 对已退出进程是空操作
      killer.on('exit', (code) => {
        if (code !== 0 && !child.killed) {
          log(`taskkill 失败（退出码 ${code}），退回单进程 kill`);
          child.kill(sig);
        }
      });
      return;
    } catch {
      // spawn 同步抛错，同样退回单进程 kill
    }
  }
  child.kill(sig);
}

/**
 * 当前平台对应的 Release 资产名。
 *
 * @returns {string} 资产文件名
 * @throws {Error} 平台/架构不在支持列表中
 */
function assetName() {
  const key = `${process.platform}-${process.arch}`;
  const name = ASSETS[key];
  if (!name) {
    throw new Error(
      `暂无 ${key} 的预编译二进制，可设置 JENKINS_MCP_PYTHON 指向本地 Python 解释器`
    );
  }
  return name;
}

/**
 * 二进制缓存目录：<缓存根>/jenkins-config-mcp/<version>。
 *
 * @returns {string} 目录绝对路径
 */
function cacheDir() {
  const override = process.env.JENKINS_MCP_CACHE_DIR;
  const base =
    override ||
    (process.platform === 'win32'
      ? process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
      : path.join(os.homedir(), '.cache'));
  return path.join(base, 'jenkins-config-mcp', releaseTag());
}

/**
 * 要下载的 Release tag，默认与 npm 包版本一致。
 *
 * @returns {string} 形如 v1.0.0
 */
function releaseTag() {
  return process.env.JENKINS_MCP_VERSION || `v${version}`;
}

/**
 * Release 资产下载地址前缀。
 *
 * @returns {string} 不含结尾斜杠的 URL 前缀
 */
function releaseBase() {
  const base =
    process.env.JENKINS_MCP_RELEASE_BASE || `https://github.com/${REPO}/releases/download`;
  return `${base.replace(/\/+$/, '')}/${releaseTag()}`;
}

/**
 * 下载 URL 内容到内存。
 *
 * @param {string} url 目标地址
 * @returns {Promise<Buffer>} 响应体
 * @throws {Error} 非 2xx 响应
 */
async function fetchBuffer(url) {
  const response = await fetch(url, { redirect: 'follow' });
  if (!response.ok) {
    throw new Error(`下载失败 ${response.status} ${response.statusText}: ${url}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

/**
 * 从 Release 的 checksums.txt 中取出指定资产的 sha256。
 *
 * @param {string} name 资产文件名
 * @returns {Promise<string>} 小写 sha256
 * @throws {Error} 清单缺失或清单中没有该资产
 */
async function expectedSha256(name) {
  const text = (await fetchBuffer(`${releaseBase()}/${CHECKSUMS}`)).toString('utf8');
  for (const line of text.split(/\r?\n/)) {
    const [hash, file] = line.trim().split(/\s+/);
    if (file === name && hash) return hash.toLowerCase();
  }
  throw new Error(`${CHECKSUMS} 中缺少 ${name} 的记录`);
}

/**
 * 确保当前平台的二进制已就绪，必要时下载并校验。
 *
 * @returns {Promise<string>} 二进制绝对路径
 */
async function ensureBinary() {
  const name = assetName();
  const dir = cacheDir();
  const target = path.join(dir, name);
  if (fs.existsSync(target)) return target;

  const url = `${releaseBase()}/${name}`;
  log(`首次运行，正在下载 ${releaseTag()} 的 ${name} ...`);
  const payload = await fetchBuffer(url);

  if (process.env.JENKINS_MCP_SKIP_CHECKSUM !== '1') {
    const expected = await expectedSha256(name);
    const actual = crypto.createHash('sha256').update(payload).digest('hex');
    if (actual !== expected) {
      throw new Error(`sha256 校验失败：期望 ${expected}，实际 ${actual}`);
    }
  }

  fs.mkdirSync(dir, { recursive: true });
  // 先写临时文件再 rename，避免并发启动时读到半个文件
  const tmp = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, payload, { mode: 0o755 });
  fs.renameSync(tmp, target);
  log(`已缓存到 ${target}`);
  return target;
}

/**
 * 判断 PATH 上命中的 jenkins-config-mcp 是否就是本 npm 包生成的 shim。
 *
 * @param {string} candidate which() 命中的路径
 * @returns {boolean} 是自身则 true
 */
function isSelf(candidate) {
  try {
    const dir = path.dirname(fs.realpathSync(candidate));
    return dir === path.dirname(fs.realpathSync(__filename));
  } catch {
    return false;
  }
}

/**
 * 下载不可用时的兜底解析（面向已有 Python 环境的开发者）。
 *
 * @returns {{command: string, args: string[], source: string}|null}
 */
function resolveFallback() {
  const installed = which('jenkins-config-mcp');
  if (installed && !isSelf(installed)) {
    return { command: installed, args: [], source: 'console-script' };
  }

  const uvx = which('uvx');
  if (uvx) {
    const spec = process.env.JENKINS_MCP_PACKAGE || DEFAULT_PACKAGE;
    return { command: uvx, args: ['--from', spec, 'jenkins-config-mcp'], source: 'uvx' };
  }

  for (const py of ['python3', 'python']) {
    const found = which(py);
    if (found) return { command: found, args: MODULE_ARGS, source: py };
  }

  return null;
}

/**
 * 解析最终要执行的命令。
 *
 * @returns {Promise<{command: string, args: string[], source: string}>}
 * @throws {Error} 二进制下载失败且无任何兜底方案
 */
async function resolveCommand() {
  const explicitBinary = process.env.JENKINS_MCP_BINARY;
  if (explicitBinary) {
    return { command: explicitBinary, args: [], source: 'JENKINS_MCP_BINARY' };
  }

  const explicitPython = process.env.JENKINS_MCP_PYTHON;
  if (explicitPython) {
    return { command: explicitPython, args: MODULE_ARGS, source: 'JENKINS_MCP_PYTHON' };
  }

  try {
    return { command: await ensureBinary(), args: [], source: 'release-binary' };
  } catch (err) {
    log(`预编译二进制不可用：${err.message}`);
    const fallback = resolveFallback();
    if (fallback) {
      log(`回退到本地 Python 环境（${fallback.source}）`);
      return fallback;
    }
    throw new Error(
      [
        '无法启动 MCP Server。可任选一种方式：',
        `  1) 确认能访问 ${releaseBase()}，或用 JENKINS_MCP_RELEASE_BASE 指定镜像地址`,
        '  2) 用 JENKINS_MCP_BINARY 指向手动下载的二进制',
        '  3) 安装 uv 或 pip install "jenkins-config[mcp]" 后重试',
      ].join('\n')
    );
  }
}

async function main() {
  let resolved;
  try {
    resolved = await resolveCommand();
  } catch (err) {
    log(err.message);
    process.exit(1);
  }

  const args = resolved.args.concat(process.argv.slice(2));
  // 只有批处理 shim 才绕 cmd.exe —— 二进制与 python.exe 继续直接 spawn，
  // 不让用户参数经过命令行解析器
  const viaCmd = isBatchScript(resolved.command);
  const launch = viaCmd ? wrapForCmd(resolved.command, args) : { command: resolved.command, args };

  if (process.env.JENKINS_MCP_LAUNCHER_DRYRUN) {
    process.stdout.write(
      JSON.stringify({
        source: resolved.source,
        command: resolved.command,
        args,
        via_cmd: viaCmd,
      }) + '\n'
    );
    return;
  }

  // 经 cmd.exe 转发后，脚本不存在只会让 cmd 自己报错并返回 1，spawn 的
  // 'error' 事件不再触发，所以这里先自己判一次，保证仍有一行归因日志。
  // 用 which() 判定而不是直接 existsSync：命令可能是裸名（如
  // JENKINS_MCP_BINARY=foo.cmd），那种情况 cmd.exe 会按 PATH 找，不能按 CWD 否掉
  if (viaCmd && !which(resolved.command)) {
    log(`启动失败 (${resolved.command}): 文件不存在`);
    process.exit(1);
  }

  const child = spawn(launch.command, launch.args, {
    stdio: 'inherit',
    shell: false,
    windowsHide: true,
    windowsVerbatimArguments: viaCmd,
  });

  child.on('error', (err) => {
    // 转发时 spawn 的对象是 cmd.exe，两个路径都打出来才能区分是 COMSPEC 还是 shim 的问题
    log(`启动失败 (${launch.command}${viaCmd ? ` → ${resolved.command}` : ''}): ${err.message}`);
    process.exit(1);
  });

  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      if (!child.killed) terminate(child, sig, viaCmd);
    });
  }

  child.on('exit', (code, signal) => {
    // 主动关停：taskkill /F 会让 cmd.exe 带非零码退出，不能报成失败。
    // 重新抛信号前先摘掉自己的 listener，否则被自己接住，进程反而退不掉
    if (shutdownSignal) {
      process.removeAllListeners(shutdownSignal);
      if (process.platform === 'win32') {
        // Windows 没有真正的信号投递，process.kill 自己等于 TerminateProcess
        process.exit(0);
      }
      process.kill(process.pid, shutdownSignal);
      return;
    }
    if (signal) {
      process.removeAllListeners(signal);
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 0);
  });
}

main();



