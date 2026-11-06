"""转场重叠（双曝）描述面：场与场之间「两画面同时可见」的强度与时长。

**存在理由（2026-11-06 立项，用户实测反馈）**：`_修订02` 的场间转场被看到"两个画面
在推进转场之间有重叠现象"。成因是**写法算术**而不是渲染器：入场淡入锚在边界前 0.22s、
出场淡出锚在边界后 0.02s，两条各 0.30s ⇒ 边界瞬间两层都不透明。这条可机算、可判对错，
但它此前只有一种写法在取数面内（`#sceneN` ＋ `T.sK` 双 `tl.to`），实测仅 6 支命中。
**"扫不到"不等于"没有"**（A06 同族：登记面 ＝ 真实读取路径）：同一批项目里大量转场写成
`tl.fromTo("#sceneN", …)` 的整屏推移、字面时刻锚（`, 2.6)`）、或 `tl.set` 硬切，
旧取数面对它们逐条看不见。本件把取数面扩到四种写法，并让"没覆盖到"逐份点名。

**场景根判定走 DOM**：解析 HTML 取带 `data-scene-id` 的元素为场景根（权威标记）；
无该属性的旧项目按仓内既有先例（`choreography_profile._scan_anchors`）回落
`id="sceneN"`。语句里的选择器必须**解析到某个场景根元素**才算转场语句——
不再按 `#scene(\\d+)` 字面猜，故 `#sN` 与 `#sceneN` 两种命名同一条路走通；
带后代的大选择器（`#scene1 .card-item`）与元素级淡出（`#s1-gf2 .person`）不计入，
单独计数 `element_level_statements`——它们不是转场，但必须可见地"被排除"而不是消失。

**读数口径（先说清算什么）**：
  α(τ)      场景根元素的不透明度：初值取 CSS/内联声明，按语句时间链求值；
            缓动按 GSAP 的 powerN.in/out/inOut 建模（全仓场景根语句只用到
            power1/2/3 与 none 五种串），未识别的缓动按线性并在 `ease_unknown` 计数。
  面积      场景根按 `.scene{position:absolute; inset:0}` 视为整屏矩形，
            按 x/y 位移平移到视口坐标（尺寸取 root `data-width/data-height`）；
            **不建模 scale/clip-path**（全仓场景根语句未见），按面积 1 处理。
  α和峰     max over τ of (α_out + α_in)——旧口径，保留以与立项读数可比。
            另出 `alpha_sum_overlap`：只在两矩形**相交**时取 α 和。整屏推移时两场
            α 恒为 1、α 和恒 2.0 却互不重叠，故"α 和 ≈2"必须与交叠同看才读得对。
  叠印强度  max over τ of min(w_out, w_in) × 面积交叠比：
            w_out ＝ α_out × (1 − α_in)（下层能透出的份额），w_in ＝ α_in（上层自身份额）。
            **入场场在上层**由层序契约保证（preflight check 7e L3：非封面场景 z-index＝场序）。
            口径取自合成关系而非"两层 α 相乘"：直接相乘会把"入场层已不透光、把下层
            完全盖住"的硬切读成满分叠印（首版实测 3 支虚报 1.00／0.35s）。
            参考水位：互补叠化（α_out+α_in ≡ 1）时 min(w_out, w_in) 在 α_in≈0.382 处取
            上界 0.382＝正常溶接的理论天花板（解 (1−a)²＝a）；入场提前 0.24s 起步、
            两层同时全不透明的 `_修订02` 读 0.49。整屏推移分两种写法：反向对开时两矩形
            平铺（交叠 0），同向平移时中段几乎完全重合；后者两种 α 恒为 1，靠"上层是否
            实色底"分开——实色底盖住下层（合成口径 0），透明底则内容真的叠着（见边界⑤）。
  叠印秒    叠印强度过线的时长：w_out > 0.35 ∧ w_in > 0.35 ∧ 面积交叠 > 0（`ghost_s`）。
  共现秒    旧口径保留（`both_alpha_s`）：α_out > 0.35 ∧ α_in > 0.35 ∧ 面积交叠 > 0。
            立项读数（`_修订02` 0.33s）与简报验收数走这条，但它**不区分**真叠化与
            "上层已不透光的弹出/推移"，只作参照，不作定性。

**刻意不裁定**：不进 issues、不进退出码、不判 PASS/FAIL（2026-10-10 常设裁定：
不为观感问题新增代码门禁）。读数只进完工报告 `data_sources.transition_overlap` 与交付说明。
升格为 FAIL 的翻转前置**唯一一条**：先取得"该读数与用户逐支观感裁定一致"的 ≥3 支样本，
并同时回答四态给法（取数断链算 UNTESTED 还是 N/A）与"会错杀哪些既有交付"。

**四条取数边界（不得读成"没有缺陷"）**：
  ① 相对锚（`'<'`、`'+=0.5'`）、标签锚、变量锚解析不出 → 计入 `unparsed_anchors` 并随行；
     它们口径上不入读数，故"该边界无重叠"不得由未解析语句反推。
  ② 无 `data-width/height` → 面积口径无从裁定，该边界只出 α 读数并计 `geometry_unmeasured`。
  ③ 初始不透明度按**层叠近似**取：内联 > `#id` 规则 > class 规则，规则只认**主体复合
     选择器**（最后一段），且只扫 `<style>` 块。仍**不建模层叠与 z-index**，故
     `initial_visible_roots`（初值读为可见的非封面根）**偏多报**——被上层盖住的场也算在内。
  ④ 缓动未识别一律按线性并计数；线性对 ease-out 入场是**低报**叠印的方向。
  ⑤ **背景色不参与叠印公式**：w_out 的推导假定上层元素自身不透光。场景根透明底时
     （`.scene{background:transparent}`）下层内容照样透出，叠印强度**低报**——此时
     `both_alpha_s`（两场 α 皆过线且矩形相交）更接近可见重叠。本面另出每边界
     `upper_transparent` 与聚合 `transparent_scene_roots` 让这一前提显形；
     渐变/`var(--x)` 背景不下结论（`background_unknown_roots`）。
  ⑥ **采样窗为边界 ±1.6s**（防异常锚拉爆循环）；读数顶到窗边缘时置
     `both_saturated`/`ghost_saturated`——那是"≥ 窗口值"，不得当上限读。全仓现读
     有 1 个边界撞上（长驻同显形态，实测远超 1.6s）。

只读、零渲染：本件不写 HTML、不改时间轴。
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from _script_env import HTML_BASE  # noqa: E402  根目录单源
from choreography_profile import _CALL, _match_call, _split_args  # noqa: E402  JS 调用切分单源

STEP = 0.01                      # 采样步长（秒）
DEFAULT_DURATION = 0.5           # GSAP 未声明 duration 时的引擎缺省值，非本件自定
BOTH_MIN = 0.35                  # "肉眼可辨在场"阈（沿用旧口径），两层各自过线才算共现
MAX_SCAN_HALF = 1.6              # 单边界采样窗半宽上限（秒），防止异常锚拉爆循环
SLIDE_FRACTION = 0.5             # 位移达到视口该轴的一半即视为"整屏推移"

CALIBER = {
    "scene_root": '带 data-scene-id 的元素；无该属性回落 id="sceneN"（仓内既有先例）',
    "forms_covered": ["tl.to", "tl.fromTo", "tl.from", "tl.set"],
    "anchors": ["T.sK ± 偏移", "命名数值常量（var T1 = …）± 偏移", "字面秒数"],
    "easing": "GSAP power1/power2/power3 的 in/out/inOut 与 none；其余按线性并计数",
    "geometry": "场景根＝整屏矩形，按 x/y 平移；scale/clip-path 不建模",
    "initial_opacity": "内联 > #id 规则 > class 规则，规则只认主体复合选择器且只扫 <style>；"
                       "不建模层叠，initial_visible_roots 偏多报",
    "background": "同层叠近似读 scene root 背景；透明底时叠印强度低报，"
                  "看 upper_transparent 与 both_alpha_s",
    "window": "",
    "adjudicates": False,
}
CALIBER["window"] = (f"边界 ±{MAX_SCAN_HALF}s 采样；顶到窗边缘的读数置 *_saturated"
                     "（截断，不是上限）")

_TBLOCK = re.compile(r'(?:var|const|let)\s+T\s*=\s*\{([^}]*)\}', re.S)
_TVAL = re.compile(r'([A-Za-z_$][\w$]*)\s*:\s*([0-9.]+)')
_SCENE_ID = re.compile(r'scene(\d+)$')
_NUM = r'(-?[0-9.]+)'
_OPACITY = re.compile(r'(?:^|[,{\s])opacity\s*:\s*' + _NUM)
_DURATION = re.compile(r'(?:^|[,{\s])duration\s*:\s*' + _NUM)
_XV = re.compile(r'(?:^|[,{\s])x\s*:\s*' + _NUM)
_YV = re.compile(r'(?:^|[,{\s])y\s*:\s*' + _NUM)
_STR_ARG = re.compile(r'\s*(["\'])(.*?)\1\s*', re.S)
_ROOT_TAG = re.compile(r'<[a-zA-Z][^>]*\bdata-width\s*=\s*["\']([0-9.]+)["\'][^>]*>', re.S)
_HEIGHT = re.compile(r'data-height\s*=\s*["\']([0-9.]+)["\']')
_EASE = re.compile(r'(?:^|[,{\s])ease\s*:\s*["\']([^"\']+)["\']')
_POWER = re.compile(r'^power([1-4])\.(in|out|inOut)$')


# ── 缓动（只建模全仓场景根语句实际用到的族）──────────────────────────────
def _power_ease(power, kind):
    d = power + 1                       # GSAP powerN ＝ N+1 次幂（power1＝quad）
    if kind == "in":
        return lambda p: p ** d
    if kind == "out":
        return lambda p: 1 - (1 - p) ** d

    def inout(p):
        return (2 ** (d - 1)) * (p ** d) if p < 0.5 else 1 - ((-2 * p + 2) ** d) / 2
    return inout


def _resolve_ease(name):
    """返回 (fn, recognized)。未识别一律线性并让调用方计数——不得静默当已知。"""
    if not name:
        return (lambda p: p), True
    nm = name.strip()
    if nm in ("none", "linear"):
        return (lambda p: p), True
    m = _POWER.match(nm)
    if m:
        return _power_ease(int(m.group(1)), m.group(2)), True
    return (lambda p: p), False


# ── DOM 面：场景根与初始不透明度 ─────────────────────────────────────────
class _RootScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.roots = []                # 文档顺序

    @staticmethod
    def _root_of(d):
        sid = (d.get("data-scene-id") or "").strip()
        if sid:
            return sid, "data-scene-id"
        ident = (d.get("id") or "").strip()
        m = _SCENE_ID.match(ident)
        if m:
            return f"s{m.group(1)}", "id-literal"
        return None, None

    def _visit(self, d):
        sid, basis = self._root_of(d)
        if not sid:
            return
        ident = (d.get("id") or "").strip()
        if any(r["id"] == ident for r in self.roots):
            return
        self.roots.append({"id": ident, "sid": sid, "basis": basis,
                           "classes": (d.get("class") or "").split(),
                           "inline": d.get("style") or "",
                           "cover": (d.get("data-scene-entry") or "").strip().lower() == "cover"})

    def handle_starttag(self, tag, attrs):
        self._visit(dict(attrs))

    def handle_startendtag(self, tag, attrs):
        self._visit(dict(attrs))


def _css_rules(txt):
    """极简规则表：返回 [(选择器, 声明体)]，文档顺序。**只扫 `<style>` 块**。

    不能扫全文：`tl.fromTo("#scene1", {x:1920, opacity:0.25}, …)` 在全文正则里长得
    就像一条规则（选择器里含 `#scene1`、声明体里有 `opacity:0.25`），实测把
    `prefab-agent-openatom` 的 15 个场景根初值全读成 0.25、凭空造出 0.25 的叠印。
    不做层叠求解，只用于"初值是否隐藏"这一问；`@media` 的内层规则照常匹配。
    """
    out = []
    for block in re.finditer(r'<style[^>]*>(.*?)</style>', txt, re.S):
        body = re.sub(r'/\*.*?\*/', '', block.group(1), flags=re.S)
        out += [(m.group(1).strip(), m.group(2))
                for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', body)]
    return out


def _declared_alpha(root, rules):
    """初始不透明度。优先级：内联 style > `#id` 规则 > class 规则；同权重取文中后者。

    存在理由（首版实测）：`build-expert-promo` 等片同时有 `.scene{opacity:0}` 与
    `#scene0{opacity:1}`——旧写法"任一命中 opacity:0 即视为隐藏"把**封面**判成隐藏，
    于是封面的淡出语句从链上被算成"0→0 无变化"，整条出场事件消失、边界读成 none。
    """
    def _parse(body):
        m = re.search(r'opacity\s*:\s*([0-9.]+)', body)
        v = float(m.group(1)) if m else None
        if v is None and re.search(r'visibility\s*:\s*hidden', body):
            return 0.0
        return v

    if root["inline"]:
        v = _parse(root["inline"])
        if v is not None:
            return v, "inline"
    best_v, best_score = None, -1
    for sel, bdy in rules:
        score = 0
        # 只看**主体复合选择器**（最后一段）：`#scene1 .card{opacity:.25}` 是子元素的规则，
        # 不是场景根的初值。首版按子串匹配把这类规则读成根的 opacity，实测
        # `prefab-agent-openatom` 的 s1 初值被读成 0.25、凭空多出 0.25 的"常驻叠印"。
        for part in sel.split(","):
            subj = re.split(r'[\s>+~]+', part.strip())[-1]
            if root["id"] and re.search(rf'#{re.escape(root["id"])}(?![\w-])', subj):
                score = max(score, 2)
            elif any(re.search(rf'\.{re.escape(cls)}(?![\w-])', subj) for cls in root["classes"]):
                score = max(score, 1)
        if score and score >= best_score:
            v = _parse(bdy)
            if v is not None:
                best_v, best_score = v, score
    if best_v is not None:
        return best_v, "css"
    return 1.0, "default"


def _bg_kind(body):
    """从一段声明体读背景种类：transparent / opaque / unknown；无声明返回 None。"""
    m = re.search(r'background(?:-color)?\s*:\s*([^;]+)', body)
    if not m:
        return None
    v = m.group(1).strip().lower()
    if "gradient" in v or v.startswith("var("):
        return "unknown"            # 渐变/自定义属性可能半透，不替它下结论
    if v in ("transparent", "none", "initial", "unset", "inherit"):
        return "transparent"
    am = re.search(r'rgba\([^)]*,\s*([0-9.]+)\s*\)', v)
    if am:
        return "transparent" if float(am.group(1)) <= 0.01 else "opaque"
    return "opaque"


def _declared_background(root, rules):
    """场景根背景：同一套层叠近似（内联 > #id > class），取主体复合选择器。

    存在理由（2026-11-06 扩面批实测）：叠印公式 `w_out = α_out × (1 − α_in)` 的前提是
    **上层元素自身不透光**。有的片把场景根写成 `.scene{background:transparent}`，打破
    这个前提——它 8 个边界各 0.37s 两层都不透明，但入场层是透明底，下层内容照样透出；
    而多数推移项目的 `#sceneN{background:#f5f6f8}` 是实色底，同样是两层不透明却真的被
    盖住。不读背景就分不开这两类，读同一条读数会给出相反定性。
    """
    if root["inline"]:
        k = _bg_kind(root["inline"])
        if k:
            return k
    best_k, best_score = None, -1
    for sel, bdy in rules:
        score = 0
        for part in sel.split(","):
            subj = re.split(r'[\s>+~]+', part.strip())[-1]
            if root["id"] and re.search(rf'#{re.escape(root["id"])}(?![\w-])', subj):
                score = max(score, 2)
            elif any(re.search(rf'\.{re.escape(cls)}(?![\w-])', subj) for cls in root["classes"]):
                score = max(score, 1)
        if score and score >= best_score:
            k = _bg_kind(bdy)
            if k:
                best_k, best_score = k, score
    return best_k or "none"         # 无声明＝元素默认透明


def _viewport(txt):
    m = _ROOT_TAG.search(txt)
    if m:
        h = _HEIGHT.search(m.group(0))
        if h:
            return float(m.group(1)), float(h.group(1))
    return None, None


# ── 语句 → 事件 ─────────────────────────────────────────────────────────
def _vars_of(seg):
    def _get(rx):
        m = rx.search(seg)
        return float(m.group(1)) if m else None
    return {"opacity": _get(_OPACITY), "duration": _get(_DURATION),
            "x": _get(_XV), "y": _get(_YV)}


def _anchor_time(anchor, T, consts=None):
    """解析定位参数 → (绝对秒, 是否解析成功)。

    认三种：`T.sK ± off`（T 块）、命名数值常量（`T1`/`Tend ± off`，等价于字面时刻的
    变量写法）、字面秒数。其余（相对锚/标签锚/表达式锚）一律不猜，交调用方点名。
    """
    a = (anchor or "").strip()
    m = re.fullmatch(r'T\.(\w+)\s*(?:([+-])\s*([0-9.]+))?', a)
    if m:
        if m.group(1) not in T:
            return None, False
        base = T[m.group(1)]
        if m.group(2):
            base += float(m.group(3)) * (1 if m.group(2) == "+" else -1)
        return base, True
    m = re.fullmatch(r'([A-Za-z_$][\w$]*)\s*(?:([+-])\s*([0-9.]+))?', a)
    if m and consts and m.group(1) in consts:
        base = consts[m.group(1)]
        if m.group(2):
            base += float(m.group(3)) * (1 if m.group(2) == "+" else -1)
        return base, True
    m = re.fullmatch(r'([0-9.]+)', a)
    if m:
        return float(m.group(1)), True
    return None, False       # 相对锚/标签锚/表达式——不猜


def _numeric_consts(txt):
    """JS 里的数值常量（`var T1=3, T2=24.5, …` 与 `var T1end = T1 + 21.4;`）。

    手写项目把边界写成命名常量，等价于字面时刻；不解析它们会把整片判成"锚不可用"。
    只认"数字"与"已知常量 ± 数字"两种右值，三轮内传递；其余表达式不猜。
    """
    decls = []
    for m in re.finditer(r'(?:var|const|let)\s+([^;{}]+);', txt):
        for part in m.group(1).split(","):
            if "=" not in part:
                continue
            name, expr = part.split("=", 1)
            name, expr = name.strip(), expr.strip()
            if re.fullmatch(r'[A-Za-z_$][\w$]*', name) and name not in ("T", "S"):
                decls.append((name, expr))
    out = {}
    for _ in range(3):
        for name, expr in decls:
            if name in out:
                continue
            m = re.fullmatch(r'(-?[0-9.]+)', expr)
            if m:
                out[name] = float(m.group(1))
                continue
            m = re.fullmatch(r'([A-Za-z_$][\w$]*)\s*([+-])\s*([0-9.]+)', expr)
            if m and m.group(1) in out:
                off = float(m.group(3)) * (1 if m.group(2) == "+" else -1)
                out[name] = out[m.group(1)] + off
    return out


def scan_statements(txt, roots, T, consts=None):
    """遍历 tl 语句，返回 (events, counters)。"""
    by_id = {r["id"]: r for r in roots if r["id"]}
    by_sid = {r["sid"]: r for r in roots}
    events, counters = [], {"statements_total": 0, "scene_root_statements": 0,
                            "element_level_statements": 0, "variable_selector_statements": 0,
                            "unparsed_anchors": 0,
                            "unrecognized_methods": 0, "ease_unknown": 0,
                            "visibility_show_statements": 0,
                            "visibility_hide_statements": 0, "no_supported_var": 0}
    order = 0
    for mo in _CALL.finditer(txt):
        method = mo.group(1).lower()
        if method not in ("to", "from", "fromto", "set"):
            counters["unrecognized_methods"] += 1
            continue
        seg = _match_call(txt, mo.end() - 1)
        if seg is None:
            counters["unparsed_anchors"] += 1
            continue
        args = _split_args(seg[1:-1])
        if not args:
            continue
        counters["statements_total"] += 1
        sm = _STR_ARG.fullmatch(args[0])
        if not sm:
            # 选择器是变量（如 `sceneTransition()` helper 里的 prevSel/nextSel）——
            # 静态解析到不了位；与"元素级选择器"分开点名，两类都不得读成"无转场"。
            counters["variable_selector_statements"] += 1
            continue
        sel = sm.group(2).strip()
        if not sel.startswith("#") or " " in sel or ">" in sel:
            counters["element_level_statements"] += 1
            continue
        root = by_id.get(sel[1:]) or by_sid.get(sel[1:])
        if root is None:
            counters["element_level_statements"] += 1
            continue
        counters["scene_root_statements"] += 1

        if method == "fromto":
            from_vars = _vars_of(args[1]) if len(args) > 1 and args[1].startswith("{") else {}
            to_vars = _vars_of(args[2]) if len(args) > 2 and args[2].startswith("{") else {}
            anchor = args[3] if len(args) > 3 else ""
            ease_src = args[2] if len(args) > 2 else ""
        else:
            from_vars = _vars_of(args[1]) if len(args) > 1 and args[1].startswith("{") else {}
            to_vars = from_vars
            anchor = args[2] if len(args) > 2 else ""
            ease_src = args[1] if len(args) > 1 else ""

        t0, ok = _anchor_time(anchor, T, consts)
        if not ok:
            counters["unparsed_anchors"] += 1
            continue

        dur = 0.0 if method == "set" else to_vars.get("duration", from_vars.get("duration"))
        if dur is None:
            dur = DEFAULT_DURATION
        ease_m = _EASE.search(ease_src)
        ease, ease_ok = _resolve_ease(ease_m.group(1) if ease_m else None)
        if not ease_ok:
            counters["ease_unknown"] += 1

        # α / 位移的 from-to：`from` 语句的终点是"自然值"（α=1、位移=0），由时间链补
        a = x = y = None
        if method == "to":
            if to_vars.get("opacity") is not None:
                a = (None, to_vars["opacity"])
            if to_vars.get("x") is not None:
                x = (None, to_vars["x"])
            if to_vars.get("y") is not None:
                y = (None, to_vars["y"])
        elif method == "from":
            if from_vars.get("opacity") is not None:
                a = (from_vars["opacity"], None)
            if from_vars.get("x") is not None:
                x = (from_vars["x"], None)
            if from_vars.get("y") is not None:
                y = (from_vars["y"], None)
        elif method == "fromto":
            if from_vars.get("opacity") is not None or to_vars.get("opacity") is not None:
                a = (from_vars.get("opacity"), to_vars.get("opacity"))
            if from_vars.get("x") is not None or to_vars.get("x") is not None:
                x = (from_vars.get("x"), to_vars.get("x"))
            if from_vars.get("y") is not None or to_vars.get("y") is not None:
                y = (from_vars.get("y"), to_vars.get("y"))
        else:                                            # set：瞬时跳变
            if from_vars.get("opacity") is not None:
                a = (None, from_vars["opacity"])
            if from_vars.get("x") is not None:
                x = (None, from_vars["x"])
            if from_vars.get("y") is not None:
                y = (None, from_vars["y"])

        if a is None and x is None and y is None:
            # 只动 visibility/zIndex 的语句：`visible` 是**转场**（切显入场），`hidden` 多为
            # 推移结束后的配套隐藏。两者分开计数——前者的边界不在本面取数面内，必须可见。
            vis_m = re.search(r'(?:^|[,{\s])visibility\s*:\s*["\']?(visible|hidden)',
                              args[1] if len(args) > 1 else "")
            if vis_m:
                counters["visibility_show_statements" if vis_m.group(1) == "visible"
                         else "visibility_hide_statements"] += 1
            else:
                counters["no_supported_var"] += 1
            continue
        events.append({"root": root, "method": method, "t0": t0, "dur": dur,
                       "a": a, "x": x, "y": y, "ease": ease, "order": order})
        order += 1
    return events, counters


# ── 时间链求值 ──────────────────────────────────────────────────────────
def _chain(events, key, initial):
    """同场景某属性的事件按时间排序并补全 from 端，返回 `(t0 之前的值, [(t0, dur, from, to, ease)])`。

    **`from`/`fromTo` 的起点在 t0 之前就已生效**：GSAP 对这两类默认 `immediateRender:true`，
    建时间轴时即把 from 值渲染出来。实测后果：`acoustic-handbook-*` 等片的入场写成
    `fromTo("#scene2", {x:1920, opacity:1}, …)`，若把 t0 之前当成"在本位"，上层场景会
    被算成从 0 时刻起就压在旧场上方（0.8s 滑入期间凭空读出一段"整屏重叠"）。
    端点解析结果同时写回事件 `e["<key>v"]`——方向判定必须用链上的值。
    """
    evs = [e for e in events if e[key] is not None]
    evs.sort(key=lambda e: (e["t0"], e["order"]))
    pre = initial
    if evs:
        first = evs[0]
        if first["method"] in ("fromto", "from") and first[key][0] is not None:
            pre = first[key][0]
    out, cur, natural = [], pre, initial
    for e in evs:
        f, t = e[key]
        v_from = cur if f is None else f
        if e["method"] == "from":
            # `from` 的终点是"元素自然值"：不能用 cur——cur 已被本条的 from 值顶成
            # immediateRender 后的渲染值（首版据此把 `from{x:1920}` 算成 1920→1920 的无变化，
            # 整条入场事件消失、用例 B3 当场转红）。
            v_to = natural
        else:
            v_to = v_from if t is None else t
        e[key + "v"] = (v_from, v_to)
        out.append((e["t0"], e["dur"], v_from, v_to, e["ease"]))
        cur = v_to
        natural = v_to
    return pre, out


def _value_at(chain, tau):
    pre, evs = chain
    val = pre
    for t0, dur, v_from, v_to, ease in evs:
        if tau < t0:
            break
        if dur <= 0:
            val = v_to
            continue
        p = min(1.0, (tau - t0) / dur)
        val = v_from + (v_to - v_from) * ease(p)
    return val


def _inter_frac(ax, ay, bx, by, W, H):
    ix = max(0.0, min(ax + W, bx + W) - max(ax, bx))
    iy = max(0.0, min(ay + H, by + H) - max(ay, by))
    return (ix / W) * (iy / H)



def _slide_of(ev, W, H):
    """该事件是否属于"整屏推移"：单轴位移达到视口该轴的一半以上（用链上端点）。"""
    if ev is None:
        return False
    for key, dim in (("x", W), ("y", H)):
        if dim is None:
            continue
        v = ev.get(key + "v") or ev[key]
        if v is None:
            continue
        f = 0.0 if v[0] is None else v[0]
        t = f if v[1] is None else v[1]
        if abs(t - f) >= SLIDE_FRACTION * dim:
            return True
    return False


def _event_t1(ev):
    return ev["t0"] + ev["dur"]


def _is_exit(ev, W, H):
    """出场类语句：α 下降，或把本场**推到视口外**。

    两条腿都用链上端点（`e["av"]`/`e["xv"]`/`e["yv"]`）：推移方向不能只看位移差的符号
    ——`fromTo{ x:1920 → 0 }` 的差也是负的，但它正是**入场**（首版据此把入场判成出场，
    实测 `file-governance` 出现 at_s 全错、偏移 +18s 的行）。
    """
    av = ev.get("av")
    if av is not None and av[1] < av[0] - 0.05:
        return True
    for key, dim in (("x", W), ("y", H)):
        if dim is None or ev.get(key + "v") is None:
            continue
        f, t = ev[key + "v"]
        if abs(t) >= SLIDE_FRACTION * dim and abs(t) > abs(f):
            return True
    return False


def _is_entry(ev, W, H):
    """入场类语句：α 上升，或从视口外推回本位。"""
    av = ev.get("av")
    if av is not None and av[1] > av[0] + 0.05:
        return True
    for key, dim in (("x", W), ("y", H)):
        if dim is None or ev.get(key + "v") is None:
            continue
        f, t = ev[key + "v"]
        if abs(f) >= SLIDE_FRACTION * dim and abs(t) < abs(f):
            return True
    return False


def _classify(exit_ev, entry_ev, W, H):
    if exit_ev is None and entry_ev is None:
        return "none"
    if exit_ev is None or entry_ev is None:
        return "single"
    if exit_ev["dur"] <= 0 and entry_ev["dur"] <= 0:
        return "cut"
    so, si = _slide_of(exit_ev, W, H), _slide_of(entry_ev, W, H)
    if so and si:
        return "push"
    if so or si:
        return "slide-mixed"
    return "fade"


def measure(html_path):
    """单支项目的转场重叠读数（描述面，**不裁定**）。永不抛异常。"""
    try:
        p = Path(html_path)
        if not p.is_file():
            return {"untested": f"HTML 不在位（{p}）——转场面无对象可解析"}
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:                              # noqa: BLE001
        return {"untested": f"HTML 读取失败（{type(e).__name__}）"}

    parser = _RootScan()
    try:
        parser.feed(txt)
    except BaseException as e:                          # noqa: BLE001
        return {"untested": f"DOM 解析异常（{type(e).__name__}）：场景根无从判定"}
    roots = parser.roots
    if not roots:
        return {"untested": "未识别到场景根（既无 data-scene-id 也无 id=\"sceneN\"）——"
                            "转场语句无从归属，不得读成「该片无重叠」"}

    tb = _TBLOCK.search(txt)
    T = {k: float(v) for k, v in _TVAL.findall(tb.group(1))} if tb else {}
    # 命名数值常量：手写项目常把边界写成 `var T1 = 9.5; … }, T1)`，与字面时刻等价。
    consts = _numeric_consts(txt)
    W, H = _viewport(txt)
    events, counters = scan_statements(txt, roots, T, consts)

    rules = _css_rules(txt)
    initial = {}
    backgrounds = {}
    initial_visible = 0
    for r in roots:
        alpha, _basis = _declared_alpha(r, rules)
        initial[r["sid"]] = alpha
        backgrounds[r["sid"]] = _declared_background(r, rules)
        if alpha > 0.05 and not r["cover"]:
            initial_visible += 1
    transparent_roots = sum(1 for r in roots if not r["cover"]
                            and backgrounds[r["sid"]] in ("none", "transparent"))
    bg_unknown = sum(1 for r in roots if backgrounds[r["sid"]] == "unknown")

    dom_idx = {id(r): i for i, r in enumerate(roots)}
    if len(T) >= 2:
        order = sorted(roots, key=lambda r: (T.get(r["sid"], -1.0 if r["cover"] else 1e9),
                                             dom_idx[id(r)]))
    else:
        order = list(roots)

    for r in roots:
        own = [e for e in events if e["root"] is r]
        r["a_chain"] = _chain(own, "a", initial[r["sid"]])
        r["x_chain"] = _chain(own, "x", 0.0)
        r["y_chain"] = _chain(own, "y", 0.0)

    rows = []
    forms = {}
    geometry_unmeasured = 0
    for a_root, b_root in zip(order, order[1:]):
        ref = T.get(b_root["sid"])
        a_evs = [e for e in events if e["root"] is a_root]
        b_evs = [e for e in events if e["root"] is b_root]
        if ref is None and not a_evs and not b_evs:
            continue
        exits = [e for e in a_evs if _is_exit(e, W, H)]
        entries = [e for e in b_evs if _is_entry(e, W, H)]

        if ref is not None:
            exit_ev = min(exits, key=lambda e: abs(e["t0"] - ref)) if exits else None
            entry_ev = min(entries, key=lambda e: abs(e["t0"] - ref)) if entries else None
            # T 块与语句对不上（锚在别处）时退回就近配对：否则偏移会印出 +18s 这种
            # 与画面无关的数（实测 file-governance、无 T 块项目首版即此形态）。
            if exits and entries:
                far = max(abs(exit_ev["t0"] - ref), abs(entry_ev["t0"] - ref))
                if far > 2.5:
                    exit_ev, entry_ev = min(
                        ((x, y) for x in exits for y in entries),
                        key=lambda p: abs(p[0]["t0"] - p[1]["t0"]))
                    ref = min(exit_ev["t0"], entry_ev["t0"])
        elif exits and entries:
            exit_ev, entry_ev = min(((x, y) for x in exits for y in entries),
                                    key=lambda p: abs(p[0]["t0"] - p[1]["t0"]))
            ref = min(exit_ev["t0"], entry_ev["t0"])
        else:
            exit_ev = exits[0] if exits else None
            entry_ev = entries[0] if entries else None
            starts = [e["t0"] for e in (exit_ev, entry_ev) if e]
            if not starts:
                continue
            ref = min(starts)

        starts = [e["t0"] for e in (exit_ev, entry_ev) if e]
        ends = [_event_t1(e) for e in (exit_ev, entry_ev) if e]
        lo = max(ref - MAX_SCAN_HALF, min(starts + [ref]) - 0.05)
        hi = min(ref + MAX_SCAN_HALF, max(ends + [ref]) + 0.05)

        measured_geom = W is not None and H is not None
        if not measured_geom:
            geometry_unmeasured += 1
        peak_alpha = peak_alpha_ov = peak_ghost = ghost_s = both_alpha_s = 0.0
        both_first = both_last = ghost_first = ghost_last = None
        n = int(round((hi - lo) / STEP)) + 1
        for i in range(n):
            tau = lo + i * STEP
            a_out = _value_at(a_root["a_chain"], tau)
            a_in = _value_at(b_root["a_chain"], tau)
            peak_alpha = max(peak_alpha, a_out + a_in)
            if measured_geom:
                frac = _inter_frac(_value_at(a_root["x_chain"], tau),
                                   _value_at(a_root["y_chain"], tau),
                                   _value_at(b_root["x_chain"], tau),
                                   _value_at(b_root["y_chain"], tau), W, H)
            else:
                frac = 0.0 if _slide_of(exit_ev, W, H) or _slide_of(entry_ev, W, H) else 1.0
            if frac > 0:
                peak_alpha_ov = max(peak_alpha_ov, a_out + a_in)
            # 旧口径（立项读数与简报验收数用这条）：两场 α 皆 >0.35。
            # 必须**加面积条件**：整屏推移时两场 α 恒为 1，不加面积会把全场推移
            # 的 0.8~1.0s 全记成"双可见"（实测 55 支被这样污染）。
            if a_out > BOTH_MIN and a_in > BOTH_MIN and frac > 0:
                both_alpha_s += STEP
                if both_first is None:
                    both_first = tau
                both_last = tau
            ao, ai = max(0.0, a_out), max(0.0, a_in)
            # 合成关系：入场场在上层（层序由 preflight check 7e L3 约束），故
            #   下层能透出的份额 w_out ＝ α_out × (1 − α_in)，入场层自身份额 w_in ＝ α_in。
            # 两者的**较小值**＝"两层同时可辨"的强度：互补叠化时上界 0.382（正常溶接），
            # 入场层已不透明时它 →0（上层把下层完全盖住，看不到叠印）。
            # 直接乘 α_out×α_in 会把"上层已不透光的硬切"读成满分叠印（首版实测
            # 3 支因此虚报 1.00／0.35s，实际是"弹出即遮住"）。
            w_out, w_in = ao * (1.0 - ai), ai
            peak_ghost = max(peak_ghost, min(w_out, w_in) * frac)
            if w_out > BOTH_MIN and w_in > BOTH_MIN and frac > 0:
                ghost_s += STEP
                if ghost_first is None:
                    ghost_first = tau
                ghost_last = tau

        def _edge_hit(first, last):
            """读数是否顶到采样窗边缘——顶到即"被窗口截断"，不是真实上限。"""
            return (first is not None
                    and (first <= lo + STEP / 2 or last >= hi - STEP / 2))

        both_sat = _edge_hit(both_first, both_last)
        ghost_sat = _edge_hit(ghost_first, ghost_last)

        form = _classify(exit_ev, entry_ev, W, H)
        forms[form] = forms.get(form, 0) + 1
        if exit_ev is None and entry_ev is None:
            continue
        rows.append({
            "boundary": f'{a_root["sid"]}→{b_root["sid"]}',
            "at_s": round(ref, 2),
            "form": form,
            "exit_offset_s": round(exit_ev["t0"] - ref, 2) if exit_ev else None,
            "entry_offset_s": round(entry_ev["t0"] - ref, 2) if entry_ev else None,
            "exit_duration_s": round(exit_ev["dur"], 2) if exit_ev else None,
            "entry_duration_s": round(entry_ev["dur"], 2) if entry_ev else None,
            "alpha_sum_peak": round(peak_alpha, 2),
            "alpha_sum_overlap": round(peak_alpha_ov, 2),
            "ghost_peak": round(peak_ghost, 2),
            "ghost_s": round(ghost_s, 2),
            "both_alpha_s": round(both_alpha_s, 2),
            # 读数顶到采样窗边缘＝被窗口截断（真实时长 ≥ 该值），不得当上限读。
            "both_saturated": both_sat,
            "ghost_saturated": ghost_sat,
            "geometry_measured": measured_geom,
            # 上层（入场场，层序契约）场景根是否透明底：透明底时叠印公式的
            # "上层把下层盖住"前提不成立，叠印强度低估、双可见秒更接近可见重叠。
            "upper_transparent": backgrounds.get(b_root["sid"]) in ("none", "transparent"),
            # 单侧边界（另一侧没有转场语句）的读数**不计入峰值**：该场若初值即可见，
            # 画面是"上层淡出揭示下层"（合法的溶接），α 乘积恒为 1 与叠印无关；
            # 若初值隐藏且无入场语句，那是"内容没揭示"，归 check_element_reveal／
            # presence_check 裁，不归本面。两种都不该被读成"两画面重叠"。
            "counted": form != "single",
        })

    uncovered = None
    if not counters["scene_root_statements"]:
        uncovered = ("无字面选择器的场景根淡入淡出/位移语句（元素级 %d 条、"
                     "变量选择器 %d 条不计入转场面）——该片转场写法不在本面取数面内"
                     % (counters["element_level_statements"],
                        counters["variable_selector_statements"]))
    elif not rows:
        if counters["visibility_show_statements"] and not counters["unparsed_anchors"]:
            uncovered = ("场景转场由 visibility 切显实现（%d 条），本面量 α 与位移、"
                         "不含 visibility——该写法不在取数面内"
                         % counters["visibility_show_statements"])
        elif counters["unparsed_anchors"]:
            uncovered = ("%d 条场景根语句的定位参数解析不出（相对锚/表达式锚），"
                         "无可配对边界" % counters["unparsed_anchors"])
        else:
            uncovered = ("场景根语句 %d 条但无可配对边界（场景顺序不可用）"
                         % counters["scene_root_statements"])
    elif counters["visibility_show_statements"]:
        # 有读数、但另有边界由 visibility 切显承担：**部分覆盖**也得说出来
        # （否则"1 个边界、0 叠印"会被读成"整片转场都干净"）。
        uncovered = ("另有 %d 个边界由 visibility 切显实现，不在本面取数面内"
                     % counters["visibility_show_statements"])

    out = {
        "caliber": dict(CALIBER),
        "scenes": len(roots),
        "scene_root_basis": {b: sum(1 for r in roots if r["basis"] == b)
                             for b in ("data-scene-id", "id-literal")},
        "t_block_keys": len(T),
        "viewport": [W, H],
        "initial_visible_roots": initial_visible,
        "transparent_scene_roots": transparent_roots,
        "background_unknown_roots": bg_unknown,
        "boundaries_measured": len(rows),
        "single_side_boundaries": sum(1 for r in rows if not r["counted"]),
        "geometry_unmeasured": geometry_unmeasured,
        "saturated_boundaries": sum(1 for r in rows if r["counted"]
                                    and (r["both_saturated"] or r["ghost_saturated"])),
        "forms": forms,
        "max_alpha_sum_peak": max((r["alpha_sum_peak"] for r in rows if r["counted"]),
                                  default=0.0),
        "max_alpha_sum_overlap": max((r["alpha_sum_overlap"] for r in rows if r["counted"]),
                                     default=0.0),
        "max_ghost_peak": max((r["ghost_peak"] for r in rows if r["counted"]),
                              default=0.0),
        "max_ghost_s": max((r["ghost_s"] for r in rows if r["counted"]),
                           default=0.0),
        "max_both_alpha_s": max((r["both_alpha_s"] for r in rows if r["counted"]),
                                default=0.0),
        "boundaries": rows,
    }
    out.update(counters)
    if uncovered:
        out["uncovered_reason"] = uncovered
    return out


def format_line(m):
    """人类可读那一行的**唯一生成点**（报告侧不得另抄一句描述）。"""
    if not isinstance(m, dict) or "untested" in m:
        why = m.get("untested") if isinstance(m, dict) else None
        return f"[TRANSITION] UNTESTED — {why or '未附归因（取数面未点名原因）'}"
    if "max_ghost_peak" not in m:
        return (f"[TRANSITION] UNTESTED — 读数键缺失（现有键 {sorted(m)[:6]}…），"
                "不得按空读数印成已测")
    forms = m.get("forms") or {}
    form_txt = "／".join(f"{k} {v}" for k, v in sorted(forms.items())) or "无"
    tail = ""
    if m.get("uncovered_reason"):
        tail += f"｜未覆盖：{m['uncovered_reason']}"
    if m.get("unparsed_anchors"):
        tail += f"｜锚解析不出 {m['unparsed_anchors']} 条（不入读数，不得读成无重叠）"
    if m.get("geometry_unmeasured"):
        tail += f"｜面积口径不可测 {m['geometry_unmeasured']} 个边界（只出 α 读数）"
    if m.get("single_side_boundaries"):
        tail += (f"｜{m['single_side_boundaries']} 个边界只有单侧转场语句"
                 "（淡出揭示／未揭示形态，不计入峰值）")
    if m.get("initial_visible_roots"):
        tail += f"｜{m['initial_visible_roots']} 个非封面场景根初值即可见（z-index 遮挡未建模＝偏多报）"
    if m.get("ease_unknown"):
        tail += f"｜未识别缓动 {m['ease_unknown']} 条按线性（ease-out 入场下为低报）"
    if m.get("transparent_scene_roots"):
        tail += (f"｜{m['transparent_scene_roots']} 个非封面场景根透明底：叠印强度按合成口径"
                 "低报（下层内容照样透出），此时双可见秒更接近可见重叠")
    if m.get("background_unknown_roots"):
        tail += f"｜{m['background_unknown_roots']} 个场景根背景为渐变/自定义属性（未下结论）"
    if m.get("saturated_boundaries"):
        tail += (f"｜{m['saturated_boundaries']} 个边界读数顶到采样窗边缘（±{MAX_SCAN_HALF}s）"
                 "＝被窗口截断，真实时长 ≥ 该值")
    return (f"[TRANSITION] 描述面（不裁定）：叠印强度峰值 {m['max_ghost_peak']} "
            f"（弱层同时在场的份额，0＝无叠印；互补叠化理论上界 0.38）"
            f"｜旧口径双可见最长 {m['max_both_alpha_s']}s（两场 α 皆 >0.35）"
            f"｜重叠处α和峰 {m['max_alpha_sum_overlap']}"
            f"｜{m['boundaries_measured']} 个边界（{form_txt}）"
            f"｜场景根 {m['scenes']}（data-scene-id {m['scene_root_basis'].get('data-scene-id', 0)}"
            f"／id 回落 {m['scene_root_basis'].get('id-literal', 0)}）"
            f"，T 块键 {m['t_block_keys']}，元素级语句 {m['element_level_statements']} 条"
            f"／变量选择器 {m.get('variable_selector_statements', 0)} 条不计入"
            f"｜口径 静态解析＋缓动建模，零渲染" + tail)


def _merge_forms(rows):
    total = {}
    for v in rows:
        for k, n in v["forms"].items():
            total[k] = total.get(k, 0) + n
    return "/".join(f"{k} {n}" for k, n in sorted(total.items()))


def main():
    import argparse

    ap = argparse.ArgumentParser(description="转场重叠（双曝）描述面——只出数，不裁定")
    ap.add_argument("project", nargs="?", help="只量单个项目目录名；缺省量全仓")
    ap.add_argument("--json", help="把读数写成 JSON（全仓时为 {项目: 读数}）")
    args = ap.parse_args()

    if args.project:
        targets = [HTML_BASE / args.project / "index.html"]
    else:
        targets = sorted(HTML_BASE.glob("*/index.html"))

    results = {}
    for h in targets:
        results[h.parent.name] = measure(h)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    readable = [(k, v) for k, v in results.items()
                if "untested" not in v and v.get("boundaries_measured")]
    rows = sorted(readable, key=lambda kv: -kv[1]["max_ghost_peak"])
    print("%-36s %-4s %-6s %-8s %-8s %s"
          % ("project", "边界", "叠印峰", "双可见s", "α和峰", "形态"))
    for name, m in rows:
        print("%-36s %-4d %-6.2f %-8.2f %-8.2f %s%s"
              % (name, m["boundaries_measured"], m["max_ghost_peak"],
                 m["max_both_alpha_s"], m["max_alpha_sum_overlap"],
                 "/".join(f"{k}{v}" for k, v in sorted(m["forms"].items())),
                 "｜部分未覆盖" if m.get("uncovered_reason") else ""))

    uncovered = [(k, v.get("untested") or v.get("uncovered_reason"))
                 for k, v in results.items() if "untested" in v or v.get("uncovered_reason")]
    measured_names = {k for k, _ in readable}
    partial = [(k, why) for k, why in uncovered if k in measured_names]
    blind = [(k, why) for k, why in uncovered if k not in measured_names]
    print("\n有边界读数的项目 %d 支（其中 %d 支另有部分写法不在取数面内）："
          % (len(rows), len(partial)))
    for k, why in partial:
        print("  - %-34s %s" % (k, why))
    print("完全不在取数面内 %d 支（**不得读成\"无重叠\"**）：" % len(blind))
    for k, why in blind:
        print("  - %-34s %s" % (k, why))

    with_rows = [v for _, v in rows]
    for thr in (0.5, 0.35, 0.25):
        print("  叠印强度峰值 >= %.2f：%2d 支" % (thr, sum(1 for v in with_rows
                                                        if v["max_ghost_peak"] >= thr)))
    print("  双可见(旧口径) >= 0.20s：%2d 支" % sum(1 for v in with_rows
                                            if v["max_both_alpha_s"] >= 0.20))
    if with_rows:
        print("  覆盖边界合计 %d 个；形态分布 %s"
              % (sum(v["boundaries_measured"] for v in with_rows), _merge_forms(with_rows)))


if __name__ == "__main__":
    main()
