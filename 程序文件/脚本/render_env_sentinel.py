# -*- coding: utf-8 -*-
"""渲染器升级哨兵：裁定"这台机器能不能跑 >= probe_gate_since_version 的 hyperframes"。

存在理由（2026-10-07，消费端反馈实测）：hyperframes 0.8.x 起，`render` 入口无条件跑
`runEnvironmentChecks({includeBrowser:true})`，其中 `chromeLaunchOutcome()` 以 5 秒字面量
超时执行 `<browser> --version` 取 versionMajor；探测失败即返回 `Chrome cannot start` 并抛错。
该探测不可跳过（无环境变量、无命令行开关），且 `HYPERFRAMES_BROWSER_PATH` 显式给出时照样跑。
在 Chrome 进程创建病态的机器上（本机 2026-10-07 实测 `chrome --version` 三次各 60s 未返回），
按"安装最新"指引装 0.8.x 等于必然渲染失败。

本哨兵回答的是"能否安全升级"，不是"当前能否渲染"：当前安装版本低于探测引入界时该项判
NOT_APPLICABLE——不得计成升级安全，也不得读成阻断。

给出 `--target` 时被问的是"这次安装/升级动作可否做"，裁定只由 `upgrade_target_allowed` 给，
探测腿与安装腿降为 advisory（照常点名、不进裁定）。理由见 evaluate() 内 2026-10-07 A 批注记。

用法：
    python render_env_sentinel.py [--target 0.8.140] [--json <绝对路径>]
    python render_env_sentinel.py --check-render-env   # 问"现在这份副本能不能渲染"
退出码：0=PASS 或 NOT_APPLICABLE（判据对本次动作不适用，不阻断），1=FAIL，2=UNTESTED
（不得读成通过）。

两个问题各有模式，不得互相顶替：默认模式与 `--target` 问的是"这台机器能否安全升级到
>= probe_gate_since_version"（升级面）；`--check-render-env` 问的是"渲染命令解析到的那份
副本现在能不能用"（渲染面），由三条副本腿裁定，Chrome 探测腿与升级腿降为 advisory。
2026-10-09 现读实证两者方向不同：本机默认模式判 FAIL（Chrome `--version` 3s 不返回＝别升
0.8.x），而同一时刻副本入口可加载、版本＝钉住、sharp 可 require（渲染面全绿、10-07 刚出过片）。
把默认模式的 FAIL 当渲染门禁接进流水线，等于用升级判据拦掉正常交付。

覆盖面局限（登记，不预先分叉代码路径）：本哨兵按"环境变量指向的二进制 → 系统 Chrome 候选表"
取探测对象，未把渲染器解析链里的"托管浏览器缓存"那一档纳入探测。后果是单向的：若某机器缓存里
有 `--version` 秒回的 chrome-headless-shell，渲染器可能跑得通而本哨兵仍按系统 Chrome 判 FAIL——
即误差方向是偏严（拦下一次本可成功的升级），不是偏松（放过一次真会失败的升级）。
2026-10-09 现读更正：`hyperframes doctor` 报本机已有该缓存
（`~/.cache/hyperframes/chrome/chrome-headless-shell/win64-152.0.7928.2/…`，10-07 那轮成功渲染
日志亦印 `headlessShell=true`），本段初版"两仓盘上实测 0 个"的前提交件已不成立；偏严方向的结论
不变（该缓存里的 headless-shell 并非本哨兵探测链的取数对象，`--version` 腿仍打系统 Chrome），
但"无对象可验"这句不得再沿用——把托管缓存纳入探测链属另案，须先在真实渲染轮次上核它对
探测结果的映射，不在本文件里凭推断接。

副本腿的取数对象（同 A06"登记面＝真实读取路径"族）：腿 A 走 `npx` 同款解析链，问的就是渲染
会用的那份——为此它必须用**渲染将来用的那个 cwd**（流水线侧传 `self.source_dir`，命令行侧
`--render-cwd`），npx 的解析结果随 cwd 变（项目内 `node_modules` 优先），不传就是问 A 处答 B 处；
腿 C/D 只能取 `npm root -g` 那份（npx 不回传路径）。两者版本不一致时腿 C/D 的读数
属"描述另一份副本"，报告行点名该失配，不得当成对渲染那份的裁定。
腿 A 带 `--no-install`：2026-10-09 云端 gate 首跑实证，未装 hyperframes 的机器上裸 `npx
hyperframes --version` 会去 registry 把最新版装上再执行（runner 解析到 0.8.143、耗满 60s 超时）
——一个自称只读的探测不该亲手装上本判据要防的危险版本；未安装即 rc≠0，归因照实打印。

阈值权威源：config/quality/render_rules.json → upgrade_sentinel（本文件内不写死任何阈值）。
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import _script_env  # noqa: F401  (GBK 控制台修复)
from _script_env import QUALITY_DIR, SCRIPT_DIR

sys.path.insert(0, str(SCRIPT_DIR))
import _gate_status as gs  # noqa: E402

RULES_PATH = QUALITY_DIR / "render_rules.json"
RULES_SECTION = "upgrade_sentinel"
REQUIRED_KEYS = ("pinned_version", "probe_gate_since_version",
                 "chrome_version_probe_timeout_seconds",
                 "render_copy_probe_timeout_seconds")

# 平台原生包"应有二进制"的后缀集合：只用于 advisory 腿点名空壳，不进任何裁定
# （致命/非致命分档的理由见本文件头与 render_rules.json → upgrade_sentinel.render_copy_description）。
NATIVE_BINARY_SUFFIXES = (".exe", ".dll", ".node", ".dylib", ".so")
# 副本探测的子命令：与渲染命令同款解析链（npx）走最廉价的一次入口加载，实测 0.87s。
RENDER_COPY_ARGS = ("--version",)
# `--no-install` 不是优化而是必需：2026-10-09 云端 gate 首跑实证，本机没装 hyperframes 时
# 裸 `npx hyperframes --version` 会**去 registry 把最新版装上再执行**（runner 上解析到 0.8.143
# 并耗满 60s 超时）——即一个"只读探测"自己把本批要防的那个危险版本装了进来。加此位后
# 未安装即 rc≠0（"could not determine executable to run"，实测 3.4s、零落地安装），归因清楚。
NPX_NO_INSTALL = "--no-install"
# --check-render-env 模式下真正裁定的腿；其余一律 advisory（照常点名，不进裁定）。
RENDER_ENV_ADJUDICATING = ("render_copy_entry_loadable",
                           "render_copy_version_within_gate",
                           "render_copy_native_deps")
# 本批新增的四条副本腿。默认与 --target 模式下**一律 advisory**：那两种模式答的是升级面，
# 既有裁定面（探测腿＋安装版本腿）不得被本批静默改向——否则 §6/README 登记的契约会变，
# 而"契约静默改向"正是本仓 A 批点名过的形态（给出 --target 时退出码含义与处置指令相反）。
COPY_LEG_NAMES = RENDER_ENV_ADJUDICATING + ("render_copy_native_binaries",)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def load_sentinel_rules(path: Path = RULES_PATH) -> dict:
    """fail-closed：缺文件/缺节/缺键/类型不符一律报错，不得退回内置默认。

    理由与 A06 同族——回退默认会让"配置断链"读成"阈值恰好等于旧值"，本机 3.0 与渲染器
    自带的 5.0 一旦漂移，静默回退会把判据放宽 66%。
    """
    if not path.exists():
        raise RuntimeError(f"阈值权威源缺失：{path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rules = doc.get(RULES_SECTION)
    if not isinstance(rules, dict):
        raise RuntimeError(f"{path.name} 缺 {RULES_SECTION} 节")
    missing = [k for k in REQUIRED_KEYS if k not in rules]
    if missing:
        raise RuntimeError(f"{RULES_SECTION} 缺键：{missing}")
    timeout = rules["chrome_version_probe_timeout_seconds"]
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise RuntimeError(f"chrome_version_probe_timeout_seconds 非法：{timeout!r}")
    copy_timeout = rules["render_copy_probe_timeout_seconds"]
    if not isinstance(copy_timeout, (int, float)) or copy_timeout <= 0:
        raise RuntimeError(f"render_copy_probe_timeout_seconds 非法：{copy_timeout!r}")
    return {
        "pinned_version": str(rules["pinned_version"]),
        "probe_gate_since_version": str(rules["probe_gate_since_version"]),
        "timeout_seconds": float(timeout),
        "copy_timeout_seconds": float(copy_timeout),
    }


def parse_version(text):
    m = VERSION_RE.search(str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def resolve_probe_target():
    """取渲染器实际会探测的那个二进制：HYPERFRAMES_BROWSER_PATH 优先（与 0.8.x 的
    checkChrome 显式路径分支同源），否则回退系统 Chrome 候选表。"""
    import os
    env_path = os.environ.get("HYPERFRAMES_BROWSER_PATH", "")
    if env_path:
        return Path(env_path), "HYPERFRAMES_BROWSER_PATH"
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return Path(p), "system-candidates"
    return None, "not-found"


def probe_chrome_version(exe, timeout_seconds):
    """三态：秒回并解析出 major=PASS；跑不起来/超时/非零退出=FAIL；取不到版本串=UNTESTED。

    解析不到 major 判 UNTESTED 而非 FAIL：渲染器只在进程调用抛错时报 Chrome cannot start，
    版本串解析失败不触发该抛错——判成 FAIL 会把渲染器不会拦的形态拦下来。
    """
    if exe is None:
        return {"status": gs.UNTESTED, "reason": "未找到 Chrome 可执行文件，无从探测"}
    t0 = time.monotonic()
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"status": gs.FAIL,
                "reason": f"--version 在 {timeout_seconds}s 内未返回（渲染器硬上限 5s）",
                "elapsed_seconds": round(time.monotonic() - t0, 2)}
    except OSError as e:
        return {"status": gs.FAIL, "reason": f"无法执行 {exe}: {e}"}
    elapsed = round(time.monotonic() - t0, 2)
    if r.returncode != 0:
        return {"status": gs.FAIL,
                "reason": f"--version 退出码 {r.returncode}",
                "elapsed_seconds": elapsed}
    major = parse_version((r.stdout or "") + (r.stderr or ""))
    if not major:
        return {"status": gs.UNTESTED,
                "reason": f"--version 输出解析不到版本号：{(r.stdout or '').strip()[:60]!r}",
                "elapsed_seconds": elapsed}
    return {"status": gs.PASS, "elapsed_seconds": elapsed, "version": ".".join(map(str, major)),
            "reason": f"{elapsed}s 返回，在 {timeout_seconds}s 阈值内"}


def installed_hyperframes_version(timeout_seconds):
    """读全局 npm 安装面的版本号。npm 不可达/读不回一律 (None, 归因) 而非猜一个。

    取数上限由调用方传入（住 render_rules.json → upgrade_sentinel.
    render_copy_probe_timeout_seconds），本函数不留内置默认——旧形态写死 60 时，E5 型"判据值不住
    源码"的机算检查会被这个数直接逮住，而"换个别的数当默认"只是把同一个断链藏得更好：配置读不回
    与配置里写着别的值，在读数面上就分不清了（A06/A10 同族）。
    """
    try:
        r = subprocess.run(["npm.cmd", "root", "-g"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"npm root -g 不可用：{e}"
    root = (r.stdout or "").strip()
    if r.returncode != 0 or not root:
        return None, f"npm root -g 退出码 {r.returncode}，stdout={root!r}"
    pkg = Path(root) / "hyperframes" / "package.json"
    if not pkg.exists():
        return None, f"未在该 root 下找到 hyperframes：{pkg}"
    try:
        return json.loads(pkg.read_text(encoding="utf-8")).get("version"), str(pkg)
    except (ValueError, OSError) as e:
        return None, f"{pkg} 不可解析：{e}"


def platform_tags():
    """当前平台的 os/cpu 标签，供"这份副本自带哪些本平台原生包"的推导使用。

    候选一律由包自己的 `optionalDependencies` 键形态（同时含 os 与 cpu 标签）推出，
    脚本内不写死任何包名——写死即把"当前已知会坏的那两个包"当成判据，下一个原生依赖
    坏了照样看不见（与 A12"检查项登记名不得手抄"同族）。
    """
    import platform as _platform
    os_tag = {"win32": "win32", "darwin": "darwin", "linux": "linux"}.get(sys.platform, sys.platform)
    machine = (_platform.machine() or "").lower()
    cpu = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return os_tag, cpu


def render_copy_entry(timeout_seconds, runner=None, cwd=None):
    """腿A：以渲染命令同款解析链（npx）跑一次最廉价的入口加载。

    存在的理由（2026-10-09，消费侧两份致命日志）：宿主重置托管 Node 目录后，npx 解析到的那份
    副本可能整包缺原生依赖——`dist/cli.js` 静态 import esbuild，import 期即崩，而版本号照印。
    本腿不问版本对不对，只问"这份副本能不能启动"：rc≠0 即 FAIL，取 stderr/stdout 首行归因。
    超时或 npx 不可用判 UNTESTED（不得读成可用）。

    `cwd` 必须是渲染命令将来用的那个目录（`pipeline_runner` 传 `self.source_dir`）：npx 的解析
    结果随 cwd 变（项目内 `node_modules` 优先），不传就等于问 A 处答 B 处（A06"登记面＝真实
    读取路径"同族）。2026-10-09 云端 gate 首跑另证：未装 hyperframes 时裸 npx 会去 registry
    装最新版再执行，故命令带 `NPX_NO_INSTALL`——探测不得自己把危险版本装进机器。
    """
    run = runner or subprocess.run
    # runner 不写进签名默认值：def 期求值会冻结当时的 subprocess.run，回归注入替身即静默失效
    # （与用例76"行宽不绑死签名默认值"、用例78"print_report(stream=sys.stdout)" 同因）。
    try:
        r = run(["npx.cmd", NPX_NO_INSTALL, "hyperframes", *RENDER_COPY_ARGS],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_seconds, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": gs.UNTESTED, "version": None,
                "reason": f"渲染副本探测未能跑成（{type(e).__name__}: {e}）——未测不得读成可用"}
    text = f"{r.stdout or ''}{r.stderr or ''}"
    ver = parse_version(text)
    version = ".".join(map(str, ver)) if ver else None
    if r.returncode != 0:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        return {"status": gs.FAIL, "version": version,
                "reason": f"渲染入口加载失败（rc={r.returncode}）：{first[:180]}"}
    if not version:
        return {"status": gs.UNTESTED, "version": None,
                "reason": f"入口返回 0 但读不到版本号：{text.strip()[:60]!r}"}
    return {"status": gs.PASS, "version": version,
            "reason": f"入口可加载，解析到 {version}（{timeout_seconds}s 上限内）"}


def native_dep_candidates(pkg_dir):
    """从副本自己的 package.json 出发，找"直接依赖里声明了本平台原生包"的那些依赖。

    返回 (candidates, 归因)。整包缺失的依赖在此跳过——那种形态由腿A 的 import 崩溃裁定，
    不在本腿重复计一次（同一缺陷两处裁定＝双源，且会让 counts 虚高）。
    """
    os_tag, cpu = platform_tags()
    try:
        doc = json.loads((Path(pkg_dir) / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"副本 package.json 不可解析：{e}"
    nm = Path(pkg_dir) / "node_modules"
    out = []
    for dep in sorted(list(doc.get("dependencies", {})) + list(doc.get("optionalDependencies", {}))):
        dp = nm / dep / "package.json"
        if not dp.exists():
            continue
        try:
            ddoc = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        opts = ddoc.get("optionalDependencies") or {}
        plat = sorted(k for k in opts if os_tag in k and cpu in k)
        if plat:
            out.append({"dependency": dep, "platform_packages": plat})
    return out, f"{os_tag}/{cpu}"


def load_native_deps(candidates, pkg_dir, timeout_seconds, runner=None):
    """腿C：候选依赖逐个 require 一次（单进程内），能接上才算实装。

    抓的是"目录在但加载即抛"这一档（消费侧 10-09 第二份日志：sharp win32-x64 runtime）。
    esbuild 那种"JS 包装器在、原生二进制空壳"在本腿 require 得到——本仓实证 render 不调它，
    故本腿判 PASS 是对的，空壳由 advisory 腿点名。
    """
    if not candidates:
        return {"status": gs.NOT_APPLICABLE,
                "reason": "该副本未声明任何带本平台原生包的直接依赖（无从 require）"}
    run = runner or subprocess.run
    names = [c["dependency"] for c in candidates]
    # resolve() 而非直接 as_uri()：相对路径调 as_uri() 直接抛
    # "relative paths can't be expressed as file URIs"——取数腿自己炸不等于副本坏，
    # 该按未测处理，但更没必要让一个调用方传相对路径就崩的判据存在。
    anchor = (Path(pkg_dir).resolve() / "package.json").as_uri()
    script = (
        "const {createRequire}=require('module');"
        f"const req=createRequire({json.dumps(anchor)});"
        f"for (const n of {json.dumps(names, ensure_ascii=False)}) {{"
        "try{req(n);console.log(n+'=OK');}"
        "catch(e){console.log(n+'=FAIL:'+String((e&&e.message)||e).split('\\n')[0].slice(0,180));}"
        "}"
    )
    try:
        r = run(["node", "-e", script], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": gs.UNTESTED,
                "reason": f"node require 探测未能跑成（{type(e).__name__}: {e}）——未测不得读成可用"}
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if "=" in ln]
    if r.returncode != 0 and not lines:
        return {"status": gs.FAIL,
                "reason": f"require 探测进程非零退出（rc={r.returncode}）："
                          f"{((r.stderr or '') + (r.stdout or '')).strip()[:180]}"}
    missing = [n for n in names if not any(ln.startswith(n + "=") for ln in lines)]
    if missing:
        # 未被打印＝该包的 require 把进程带崩了，取数面断链不得读成"没失败就是通过"
        return {"status": gs.UNTESTED,
                "reason": f"以下依赖在 require 探测里没有读数（进程可能中途退出）：{missing}"}
    bad = [ln for ln in lines if "=FAIL" in ln]
    if bad:
        return {"status": gs.FAIL, "failed": bad,
                "reason": f"平台原生依赖加载失败：{'; '.join(bad)[:220]}"}
    return {"status": gs.PASS,
            "reason": f"{len(names)} 个平台原生依赖逐个 require 通过：{', '.join(names)}"}


def native_binary_presence(candidates, pkg_dir):
    """advisory 腿：点名候选平台包里"只有 README＋package.json"的空壳形态。

    永不进裁定——本仓全局 0.7.52 的 @esbuild/win32-x64 正是空壳，而同一副本 10-07 22:03
    完成了发布仓全量渲染出片（当轮日志 esbuild 命中 0）。写成门禁等于把自家已实证能出片的
    基线判成不合格（2026-10-09 消费侧建议判据在本机反证，见收件箱裁定件）。
    """
    hollow = []
    for c in candidates:
        for plat_pkg in c["platform_packages"]:
            d = Path(pkg_dir) / "node_modules" / plat_pkg
            if not d.exists():
                hollow.append(f"{plat_pkg}(未安装)")
                continue
            files = [p for p in d.rglob("*") if p.is_file()]
            if not any(p.suffix.lower() in NATIVE_BINARY_SUFFIXES for p in files):
                hollow.append(f"{plat_pkg}(仅 {len(files)} 个非二进制文件)")
    return hollow


def evaluate(rules=None, target=None, check_render_env=False, copy_cwd=None):
    rules = rules or load_sentinel_rules()
    gate = parse_version(rules["probe_gate_since_version"])
    outcomes = []

    exe, exe_source = resolve_probe_target()
    probe = probe_chrome_version(exe, rules["timeout_seconds"])
    probe["name"] = "chrome_version_probe"
    probe["target_binary"] = str(exe) if exe else None
    probe["binary_source"] = exe_source
    outcomes.append(probe)

    installed, installed_source = installed_hyperframes_version(rules["copy_timeout_seconds"])
    installed_v = parse_version(installed)
    current = {"name": "installed_version_within_gate"}
    if installed_v is None:
        current["status"] = gs.UNTESTED
        current["reason"] = f"安装版本读不回：{installed_source}"
    elif installed_v >= gate:
        current.update({"status": probe["status"], "installed": installed,
                        "reason": f"已装 {installed} ≥ {rules['probe_gate_since_version']}，"
                                  f"渲染链含 --version 探测腿，裁定随探测"})
    else:
        current["status"] = gs.NOT_APPLICABLE
        current["reason"] = (f"已装 {installed} < {rules['probe_gate_since_version']}，"
                             f"该版本无 --version 探测腿（当前可用属侥幸，不构成升级安全证据）")
    outcomes.append(current)

    # ── 渲染副本实测腿（2026-10-09 增，据消费侧 10-09 两份致命日志）──
    # 版本面读不到"入口 import 期即崩"这一档，故本批三条腿各证一件事；致命/非致命分档
    # 与本机反证（空壳 esbuild 不影响 render）见文件头与 render_rules.json→upgrade_sentinel。
    entry = render_copy_entry(rules["copy_timeout_seconds"], cwd=copy_cwd)
    entry["name"] = "render_copy_entry_loadable"
    entry["probe_cwd"] = str(copy_cwd) if copy_cwd else None
    outcomes.append(entry)

    copy_v = parse_version(entry.get("version"))
    pinned_v = parse_version(rules["pinned_version"])
    ver_leg = {"name": "render_copy_version_within_gate"}
    if entry["status"] != gs.PASS or copy_v is None:
        ver_leg["status"] = gs.UNTESTED
        ver_leg["reason"] = f"入口腿判 {entry['status']}，版本面无从裁定（不得由『读不到』反推『没问题』）"
    elif copy_v == pinned_v:
        ver_leg["status"] = gs.PASS
        ver_leg["reason"] = f"解析到 {entry['version']}＝pinned_version，即清单 §6 钉住的那一版"
    elif copy_v >= gate:
        ver_leg["status"] = probe["status"]
        ver_leg["reason"] = (f"解析到 {entry['version']} ≥ 探测引入界 "
                             f"{rules['probe_gate_since_version']} 且非钉住版，裁定随 Chrome 探测腿")
    else:
        ver_leg["status"] = gs.UNTESTED
        ver_leg["reason"] = (f"解析到 {entry['version']}，既非钉住 {rules['pinned_version']} 又低于"
                             f"探测引入界，本判据对该版本无实证数据——不阻断，但不得读成已验证")
    outcomes.append(ver_leg)

    pkg_dir = Path(installed_source).parent if installed_v is not None else None
    cands, plat_note = (None, None)
    if pkg_dir is not None:
        cands, plat_note = native_dep_candidates(pkg_dir)
    native = {"name": "render_copy_native_deps"}
    if cands is None:
        native["status"] = gs.UNTESTED
        native["reason"] = (f"副本位置取不回，原生依赖腿无从推导：{installed_source}"
                            if pkg_dir is None else f"取数面断链：{plat_note}")
    else:
        native.update(load_native_deps(cands, pkg_dir, rules["copy_timeout_seconds"]))
        native["native_platform"] = plat_note
        if (entry["status"] == gs.PASS and entry.get("version")
                and parse_version(entry["version"]) != installed_v):
            # 腿C 只能取 npm root -g 那份（npx 不回传路径），与腿A 解析到的那份不一致时，
            # 本腿描述的是"另一副本"，读数不得当成对渲染那份的裁定（A06 登记面＝真实读取路径）。
            native["copy_source_mismatch"] = True
            native["reason"] += (f"（注：本腿取 npm root -g 的 {installed}，渲染解析到 "
                                 f"{entry['version']}，两非同一副本）")
    outcomes.append(native)

    hollow = native_binary_presence(cands or [], pkg_dir) if pkg_dir is not None else []
    shells = {"name": "render_copy_native_binaries", "hollow": hollow}
    if cands is None or pkg_dir is None:
        shells["status"] = gs.UNTESTED
        shells["reason"] = "候选平台包未能推导，空壳面无从点名"
    elif hollow:
        shells["status"] = gs.FAIL
        shells["reason"] = (f"平台原生包空壳/未装：{', '.join(hollow)}——不影响 render 的 import 面"
                            f"（本仓 10-07 反证），影响调用它的子命令（如 beats/transcribe）")
    else:
        shells["status"] = gs.PASS
        shells["reason"] = "候选平台原生包均含二进制文件"
    outcomes.append(shells)

    # 模式分岔（2026-10-09）：--check-render-env 问"现在能不能渲染"，由三条副本腿裁定，
    # Chrome 探测腿/安装版本腿/空壳腿降 advisory；默认与 --target 模式下副本腿一律 advisory
    # ——既有裁定面零变化，否则 §6 与 README 登记的契约会静默改向。
    for o in outcomes:
        if o["name"] in COPY_LEG_NAMES:
            o["adjudicates"] = bool(check_render_env
                                    and o["name"] in RENDER_ENV_ADJUDICATING)
        elif check_render_env:
            o["adjudicates"] = False

    if target is not None:
        target_v = parse_version(target)
        upgrade = {"name": "upgrade_target_allowed"}
        if target_v is None:
            upgrade["status"] = gs.UNTESTED
            upgrade["reason"] = f"--target 无法解析为版本号：{target!r}"
        elif target_v < gate:
            upgrade["status"] = gs.NOT_APPLICABLE
            upgrade["reason"] = f"目标 {target} 无探测腿，不受本判据约束"
        else:
            upgrade["status"] = probe["status"]
            upgrade["reason"] = f"目标 {target} 含探测腿，裁定随探测"
        outcomes.append(upgrade)

        # 裁定面收口到被问的那个问题（2026-10-07 A 批）。旧实现把三条腿一并喂给 verdict()，
        # 于是 `--target <钉住版本>` 在探测失败的机器上整体判 FAIL、退出码 1——而 FAIL 说的是
        # "本机不能升到 0.8.x"，调用方只看退出码会读成"连钉住版本也不许装"，与本报告给出的
        # 处置指令正好相反。探测腿与安装腿属机器事实，给出 --target 时降为 advisory：照常点名，
        # 不进裁定。
        for o in outcomes:
            if o["name"] != "upgrade_target_allowed":
                o["adjudicates"] = False

    deciding = [o for o in outcomes if o.get("adjudicates", True)]
    violations = [o for o in deciding if o["status"] == gs.FAIL]
    untested = [o for o in deciding if o["status"] == gs.UNTESTED]
    verdict = gs.verdict(violations, untested)
    if verdict == gs.PASS and deciding and all(
            o["status"] == gs.NOT_APPLICABLE for o in deciding):
        # 全部裁定腿都不适用时不得印成 PASS（四态纪律：N/A 不计通过）
        verdict = gs.NOT_APPLICABLE
    return {
        "verdict": verdict,
        "mode": "check_render_env" if check_render_env else "upgrade",
        "rules": rules,
        "outcomes": outcomes,
        "counts": gs.count_statuses(outcomes),
    }


def print_report(result, stream=None):
    # stream 不在签名默认值里绑 sys.stdout：def 期求值会冻结原始流，
    # 调用方（含回归用例）redirect_stdout 之后仍写到旧 fd 上，等于报告落不到捕获面。
    stream = stream or sys.stdout
    mode = result.get("mode", "upgrade")
    title = ("渲染环境哨兵（渲染副本实测裁定）" if mode == "check_render_env"
             else "渲染器升级哨兵（升级安全裁定）")
    print(f"[SENTINEL] {title}  阈值源={RULES_PATH.name}→{RULES_SECTION}", file=stream)
    print(f"  pinned={result['rules']['pinned_version']} "
          f"gate_since={result['rules']['probe_gate_since_version']} "
          f"probe_timeout={result['rules']['timeout_seconds']}s "
          f"copy_probe_timeout={result['rules'].get('copy_timeout_seconds')}s", file=stream)
    for o in result["outcomes"]:
        role = "" if o.get("adjudicates", True) else " (advisory，不进裁定)"
        print(f"  [{o['status']:<14}] {o['name']}: {o['reason']}{role}", file=stream)
    c = result["counts"]
    print(f"  counts: PASS={c[gs.PASS]} FAIL={c[gs.FAIL]} UNTESTED={c[gs.UNTESTED]} "
          f"N/A={c[gs.NOT_APPLICABLE]}", file=stream)
    print(f"[SENTINEL] verdict={result['verdict']}", file=stream)
    if mode == "check_render_env":
        # 本模式答的是"现在这份副本能不能渲染"，处置指令必须落回副本本身，
        # 不得沿用升级面那句"别升到 >= 引入界"（那是另一个问题的答案）。
        if result["verdict"] == gs.FAIL:
            print(f"  处置：渲染副本不可用。在 `npm root -g` 指向的那份上重装钉住版 → "
                  f"npm install -g hyperframes@{result['rules']['pinned_version']}；"
                  f"崩的是 import 期（ERR_MODULE_NOT_FOUND／原生模块抛错）时，"
                  f"先确认宿主托管 Node 目录未被重置，再重装。", file=stream)
            print("  确需在明知副本有问题的情况下继续 → 流水线侧追加 --accept-render-env（决策留痕）",
                  file=stream)
        elif result["verdict"] == gs.UNTESTED:
            print("  未测不得读成可用：先修取数腿（npx 解析／npm root -g／node），"
                  "本模式默认不阻断渲染。", file=stream)
        return
    if result["verdict"] == gs.FAIL:
        print(f"  处置：本机不得升级到 "
              f">={result['rules']['probe_gate_since_version']}；"
              f"留在已实证版本 → npm install -g hyperframes@"
              f"{result['rules']['pinned_version']}", file=stream)
    elif result["verdict"] == gs.UNTESTED:
        print("  未测不得读成通过：先修取数腿（Chrome 路径 / npm root -g）再复跑。", file=stream)
    elif result["verdict"] == gs.NOT_APPLICABLE:
        gate = result["rules"]["probe_gate_since_version"]
        print(f"  本次动作不在本判据射程内（目标低于探测引入界 {gate}）——不阻断。"
              f"上方 advisory 行的探测读数属机器事实，只供'要不要升到 {gate} 及以上'"
              f"决策参考，不得读成本次动作被拒。", file=stream)


# NOT_APPLICABLE 与 PASS 同为 0：本判据对本次动作不适用＝不阻断，不是拒绝。
# 报告面已分列打印（verdict=N/A 与 verdict=PASS 的措辞不同），调用方要区分结论就读 stdout，
# 只按退出码分支时不得把 0 读成"探测干净"。
EXIT_BY_VERDICT = {gs.PASS: 0, gs.NOT_APPLICABLE: 0, gs.FAIL: 1, gs.UNTESTED: 2}


def main(argv=None):
    ap = argparse.ArgumentParser(description="渲染器升级哨兵／渲染副本实测（四态裁定）")
    ap.add_argument("--target", help="拟升级到的版本号，给出则追加 upgrade_target_allowed 项")
    ap.add_argument("--check-render-env", dest="check_render_env", action="store_true",
                    help="改问『渲染命令解析到的那份副本现在能不能用』，由三条副本腿裁定")
    ap.add_argument("--render-cwd", dest="render_cwd",
                    help="渲染命令将来用的目录（＝HTML 项目目录）。npx 解析随 cwd 变，"
                         "不给就只答『当前目录下解析到的那份』，不等于渲染将用的那份")
    ap.add_argument("--json", dest="json_out", help="结论落盘绝对路径（取证用）")
    args = ap.parse_args(argv)
    if args.check_render_env and args.target is not None:
        # 两问各自要有答案（与 §8.5/--drift 两层判据分立的同一纪律）：--target 问"这次升级可否做"，
        # --check-render-env 问"现在这份副本可否渲染"。合并裁定会让退出码说不出它答的是哪个问题。
        print(f"[SENTINEL] verdict={gs.UNTESTED}\n"
              f"  --check-render-env 与 --target 不得同时给出（两个问题各自裁定）。"
              f"升级面用 --target V，渲染面用 --check-render-env。", file=sys.stderr)
        return 2
    try:
        rules = load_sentinel_rules()
    except (RuntimeError, ValueError, OSError) as e:
        print(f"[SENTINEL] verdict={gs.UNTESTED}\n  阈值权威源不可用：{e}", file=sys.stderr)
        return 2
    result = evaluate(rules=rules, target=args.target,
                      check_render_env=args.check_render_env,
                      copy_cwd=args.render_cwd)
    print_report(result)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return EXIT_BY_VERDICT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
