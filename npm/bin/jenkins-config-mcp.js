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
 * 在 PATH 中查找可执行文件（Windows 下按 PATHEXT 补全后缀）。
 *
 * 不走 shell，避免把用户参数交给命令行解析器。
 *
 * @param {string} name 可执行文件名
 * @returns {string|null} 命中的绝对路径，未命中返回 null
 */
function which(name) {
  if (name.includes(path.sep) || name.includes('/')) {
    return fs.existsSync(name) ? name : null;
  }
  const exts =
    process.platform === 'win32'
      ? (process.env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean)
      : [''];
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext);
      try {
        if (fs.statSync(candidate).isFile()) return candidate;
      } catch {
        // 不存在或不可读，继续尝试下一个候选
      }
    }
  }
  return null;
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

  if (process.env.JENKINS_MCP_LAUNCHER_DRYRUN) {
    process.stdout.write(
      JSON.stringify({ source: resolved.source, command: resolved.command, args }) + '\n'
    );
    return;
  }

  const child = spawn(resolved.command, args, {
    stdio: 'inherit',
    shell: false,
    windowsHide: true,
  });

  child.on('error', (err) => {
    log(`启动失败 (${resolved.command}): ${err.message}`);
    process.exit(1);
  });

  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      if (!child.killed) child.kill(sig);
    });
  }

  child.on('exit', (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 0);
  });
}

main();



