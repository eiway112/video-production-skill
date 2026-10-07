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

用法：
    python render_env_sentinel.py [--target 0.8.140] [--json <绝对路径>]
退出码：0=PASS，1=FAIL，2=UNTESTED（不得读成通过）。

覆盖面局限（登记，不预先分叉代码路径）：本哨兵按"环境变量指向的二进制 → 系统 Chrome 候选表"
取探测对象，未把渲染器解析链里的"托管浏览器缓存"那一档纳入探测。后果是单向的：若某机器缓存里
有 `--version` 秒回的 chrome-headless-shell，渲染器可能跑得通而本哨兵仍按系统 Chrome 判 FAIL——
即误差方向是偏严（拦下一次本可成功的升级），不是偏松（放过一次真会失败的升级）。当前两仓盘上
该缓存目录均不存在（实测 0 个），故不为无对象可验的分叉写代码；出现该形态时改探测链即可，
判据本身不动。

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
                 "chrome_version_probe_timeout_seconds")

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
    return {
        "pinned_version": str(rules["pinned_version"]),
        "probe_gate_since_version": str(rules["probe_gate_since_version"]),
        "timeout_seconds": float(timeout),
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


def installed_hyperframes_version():
    """读全局 npm 安装面的版本号。npm 不可达/读不回一律 (None, 归因) 而非猜一个。"""
    try:
        r = subprocess.run(["npm.cmd", "root", "-g"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
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


def evaluate(rules=None, target=None):
    rules = rules or load_sentinel_rules()
    gate = parse_version(rules["probe_gate_since_version"])
    outcomes = []

    exe, exe_source = resolve_probe_target()
    probe = probe_chrome_version(exe, rules["timeout_seconds"])
    probe["name"] = "chrome_version_probe"
    probe["target_binary"] = str(exe) if exe else None
    probe["binary_source"] = exe_source
    outcomes.append(probe)

    installed, installed_source = installed_hyperframes_version()
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

    if target is not None:
        target_v = parse_version(target)
        upgrade = {"name": "upgrade_target_allowed"}
        if target_v is None:
            upgrade["status"] = gs.UNTESTED
            upgrade["reason"] = f"--target 无法解析为版本号：{target!r}"
        elif target_v < gate:
            upgrade["status"] = gs.PASS
            upgrade["reason"] = f"目标 {target} 无探测腿，不受本判据约束"
        else:
            upgrade["status"] = probe["status"]
            upgrade["reason"] = f"目标 {target} 含探测腿，裁定随探测"
        outcomes.append(upgrade)

    violations = [o for o in outcomes if o["status"] == gs.FAIL]
    untested = [o for o in outcomes if o["status"] == gs.UNTESTED]
    return {
        "verdict": gs.verdict(violations, untested),
        "rules": rules,
        "outcomes": outcomes,
        "counts": gs.count_statuses(outcomes),
    }


def print_report(result, stream=None):
    # stream 不在签名默认值里绑 sys.stdout：def 期求值会冻结原始流，
    # 调用方（含回归用例）redirect_stdout 之后仍写到旧 fd 上，等于报告落不到捕获面。
    stream = stream or sys.stdout
    print(f"[SENTINEL] 渲染器升级哨兵  阈值源={RULES_PATH.name}→{RULES_SECTION}", file=stream)
    print(f"  pinned={result['rules']['pinned_version']} "
          f"gate_since={result['rules']['probe_gate_since_version']} "
          f"probe_timeout={result['rules']['timeout_seconds']}s", file=stream)
    for o in result["outcomes"]:
        print(f"  [{o['status']:<14}] {o['name']}: {o['reason']}", file=stream)
    c = result["counts"]
    print(f"  counts: PASS={c[gs.PASS]} FAIL={c[gs.FAIL]} UNTESTED={c[gs.UNTESTED]} "
          f"N/A={c[gs.NOT_APPLICABLE]}", file=stream)
    print(f"[SENTINEL] verdict={result['verdict']}", file=stream)
    if result["verdict"] == gs.FAIL:
        print(f"  处置：本机不得升级到 "
              f">={result['rules']['probe_gate_since_version']}；"
              f"留在已实证版本 → npm install -g hyperframes@"
              f"{result['rules']['pinned_version']}", file=stream)
    elif result["verdict"] == gs.UNTESTED:
        print("  未测不得读成通过：先修取数腿（Chrome 路径 / npm root -g）再复跑。", file=stream)


EXIT_BY_VERDICT = {gs.PASS: 0, gs.FAIL: 1, gs.UNTESTED: 2}


def main(argv=None):
    ap = argparse.ArgumentParser(description="渲染器升级哨兵（四态裁定）")
    ap.add_argument("--target", help="拟升级到的版本号，给出则追加 upgrade_target_allowed 项")
    ap.add_argument("--json", dest="json_out", help="结论落盘绝对路径（取证用）")
    args = ap.parse_args(argv)
    try:
        rules = load_sentinel_rules()
    except (RuntimeError, ValueError, OSError) as e:
        print(f"[SENTINEL] verdict={gs.UNTESTED}\n  阈值权威源不可用：{e}", file=sys.stderr)
        return 2
    result = evaluate(rules=rules, target=args.target)
    print_report(result)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return EXIT_BY_VERDICT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
