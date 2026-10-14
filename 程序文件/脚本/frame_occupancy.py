# -*- coding: utf-8 -*-
"""画面占用度量（描述面，2026-10-13 复盘评估批）。

**存在理由不是"再加一条门禁"，而是把只能靠肉眼发现的形态写成纸上的数。**
2026-10-13 的实测读数：`_修订02` 有 6 块内容永久不可见，而终检 20 项、审计 6 维、
92 件回归全绿 —— 更早三轮观感问题（嗡嗡背景音、字幕灰盒、竖排塌陷）也全部由用户
肉眼抓出，用例 0 抓出。"全绿但难看"是本仓反复发生的形态，缺的不是判据，是**观测面**。

**刻意不裁定**：本模块只出数，不判 PASS/FAIL，不进任何退出码路径。理由是同日跨 9 支
已交付成片的实测把它证伪成了阈值不可定 ——

  · 同一口径的绝对占用在横竖屏之间不可比：竖屏病灶项目稳态中位 0.366，是全部样本
    最高；用户验收通过的横屏浅色件只有 0.096（差 3.8 倍，方向
    与"健康/病灶"相反）。
  · 纵向几何下限会误杀既有交付：把主体区三等分后，某浅色交付件 **13 场
    全部** 下带占用为 0.0（用户验收通过件照样"下部空"），同批另一支 10 场
    同形 —— 任何"L3/L1 < k"的门禁当场错杀 6/9 已交付项目。
  · 口径稳健性住在项目级而非最矮场：宽窄两窗口的项目稳态中位秩相关 0.950，而"最矮场"
    在两口径下差到 10 倍（某内部推广件 0.037 vs 0.003）—— 想拿来当阈值的恰是后者。

所以这里的产物是**逐场分布的实测描述**，交给人在看片之前先读事实；判它的人仍是人。
翻转前置（何时才该考虑定档）：取到 ≥3 支**同一画布向**、且用户逐支点过"观感合格"的项目
同口径分布，并同时回答四态给法、FAIL 还是 UNTESTED、会错杀哪些既有交付。

口径（与 `_取证/20261013` 两份探针逐字一致，改动即须重标分布）：
  8×8 块灰度标准差 >6 记为有内容；等比缩放至 H=384（不强制比例——强制会把横屏压成
  竖屏形状，块的灰度标准差随之改变，等于换了判据还自称同口径）；主体区纵向比例
  [0.10, 0.796)，上界避顶部 chrome、下界取字幕安全线（1080→y860、1920→y1529 同比例）；
  1fps 抽帧。
"""
TILE = 8
STD_MIN = 6.0
SCALE_H = 384
BODY_TOP = 0.10
SUBTITLE_LINE_RATIO = 860 / 1080.0      # 字幕安全线（横竖同比例，本仓按实测画布高推导）
LOW_SHARE_OF_BLANK = 0.05               # "低占用秒"的门槛，仅用于描述，不作裁定

CALIBER = {"tile": TILE, "std_min": STD_MIN, "scale_h": SCALE_H,
           "body_ratio": [BODY_TOP, round(SUBTITLE_LINE_RATIO, 4)],
           "fps": 1, "low_blank_gate": LOW_SHARE_OF_BLANK}


def measure(video_path, scene_windows=None):
    """返回 {…实测值…} 或 {untested: 归因}。永不抛异常——取不到就点名为什么取不到。

    scene_windows: [(id, start, end)]，可选。给了就附逐场稳态读数，不给只报全片分布。
    """
    try:
        import numpy as np
    except Exception as e:                              # noqa: BLE001
        return {"untested": f"numpy 不可用（{type(e).__name__}）——本项按缺依赖处理，"
                            f"不得读成成片有缺陷"}
    try:
        meta = _ffprobe_size(video_path)
        if not meta:
            return {"untested": "ffprobe 取不到分辨率/时长"}
        w0, h0, dur = meta
        W = max(TILE, int(round(w0 * SCALE_H / h0 / TILE) * TILE))
        raw = _extract(video_path, W)
        if raw is None:
            return {"untested": "ffmpeg 抽帧失败"}
        n = len(raw) // (W * SCALE_H * 3)
        if n < 2:
            return {"untested": f"抽到 {n} 帧（<2），无从给分布"}
        arr = np.frombuffer(raw[:n * W * SCALE_H * 3], dtype=np.uint8)
        arr = arr.reshape(n, SCALE_H, W, 3).astype(int)
        g = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
        occ = _occupancy(np, g)
        out = {"caliber": dict(CALIBER), "frames": int(n),
               "canvas": [w0, h0], "duration_s": round(float(dur), 2),
               "median": round(float(np.median(occ)), 3),
               "p10": round(float(np.percentile(occ, 10)), 3),
               "min": round(float(occ.min()), 3),
               "low_blank_seconds": int((occ < LOW_SHARE_OF_BLANK).sum()),
               "max_low_blank_run_seconds": int(_longest_run(occ < LOW_SHARE_OF_BLANK))}
        if scene_windows:
            per = []
            for sid, a, b in scene_windows:
                lo, hi = int(a) + 1, int(b) - 1          # 稳态：避开场淡入与收尾淡出
                if hi <= lo or hi > n:
                    per.append({"scene": sid, "untested": f"窗口 [{a},{b}) 落在帧面外"})
                    continue
                v = occ[lo:hi]
                per.append({"scene": sid, "occupancy": round(float(v.mean()), 3),
                            "low_blank_seconds": int((v < LOW_SHARE_OF_BLANK).sum())})
            out["per_scene"] = per
            vals = [p["occupancy"] for p in per if "occupancy" in p]
            out["scenes_measured"] = len(vals)
            if vals:
                out["steady_median"] = round(float(np.median(vals)), 3)
                out["steady_min_scene"] = min(per, key=lambda p: p.get("occupancy", 9))
        return out
    except Exception as e:                              # noqa: BLE001
        return {"untested": f"度量异常（{type(e).__name__}: {str(e)[:80]}）"}


def _occupancy(np, gray):
    n, H = gray.shape[0], gray.shape[1]
    y0, y1 = int(BODY_TOP * H), int(SUBTITLE_LINE_RATIO * H)
    y1 = max(y1, y0 + TILE)
    body = gray[:, y0:y1, :]
    hh = (body.shape[1] // TILE) * TILE
    ww = (body.shape[2] // TILE) * TILE
    blocks = body[:, :hh, :ww].reshape(n, hh // TILE, TILE, ww // TILE, TILE)
    return (blocks.std(axis=(2, 4)) > STD_MIN).mean(axis=(1, 2))


def _longest_run(mask):
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def _ffprobe_size(video_path):
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "stream=width,height,duration",
                       "-of", "csv=s=x:p=0", str(video_path)],
                      capture_output=True, text=True)
    if r.returncode != 0:
        return None
    parts = r.stdout.strip().split("x")
    try:
        w, h = int(parts[0]), int(parts[1])
        dur = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    except (ValueError, IndexError):
        return None
    if not dur:                                          # 某些容器不在 stream 上给 duration
        import re as _re
        rr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of", "csv=p=0", str(video_path)],
                            capture_output=True, text=True)
        m = _re.search(r"\d+(?:\.\d+)?", rr.stdout or "")
        dur = float(m.group(0)) if m else 0.0
    return (w, h, dur) if w > 0 and h > 0 else None


def _extract(video_path, W):
    import subprocess
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path),
                       "-vf", f"fps=1,scale={W}:{SCALE_H}", "-f", "rawvideo",
                       "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    return r.stdout if r.returncode == 0 and r.stdout else None


def format_line(m):
    """人类可读那一行的**唯一生成点**（文案住这里，报告侧不得另抄一句描述）。

    刻意不含"合格/不合格"字样：本面不裁定（理由见模块 docstring）。
    """
    # 判"键在不在"而非"值真假"：{"untested": None}（归因没填上）也必须走 UNTESTED 分支，
    # 否则会掉进下面的取数分支并 KeyError——这条由用例93 B3 抓到并当场修。
    if not m or "untested" in m:
        return f"[FRAME-OCCUPANCY] UNTESTED — {m.get('untested') or '未附归因（取数面未点名原因）'}"
    return (f"[FRAME-OCCUPANCY] 描述面（不裁定）：主体区占用 中位 {m['median']} "
            f"p10 {m['p10']} 最低 {m['min']}｜低占用(<{LOW_SHARE_OF_BLANK}) "
            f"{m['low_blank_seconds']}s／{m['frames']}s｜最长连续低占用 "
            f"{m['max_low_blank_run_seconds']}s｜口径 {m['caliber']['tile']}×"
            f"{m['caliber']['tile']} 块 std>{m['caliber']['std_min']}，"
            f"主体区纵向比例 {m['caliber']['body_ratio']}")
