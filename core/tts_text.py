from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
FORMULA_POLICY_VERSION = "kgts-formula-speech-v3-sre"
SRE_LATEX_SPEECH_CLI = ROOT_DIR / "scripts" / "latex_speech_cli.cjs"


@dataclass(frozen=True)
class NormalizedTtsText:
    original_text: str
    normalized_text: str
    text_lang: str
    formula_policy_version: str = FORMULA_POLICY_VERSION


_GREEK_SPEECH = {
    "alpha": "alpha",
    "beta": "beta",
    "gamma": "gamma",
    "delta": "delta",
    "Delta": "变化量",
    "epsilon": "epsilon",
    "lambda": "lambda",
    "mu": "mu",
    "pi": "pi",
    "sigma": "sigma",
    "theta": "theta",
    "varphi": "phi",
    "phi": "phi",
    "omega": "omega",
}

_LATEX_COMMANDS = {
    "times": "乘以",
    "cdot": "乘以",
    "pm": "正负",
    "mp": "负正",
    "le": "小于等于",
    "leq": "小于等于",
    "ge": "大于等于",
    "geq": "大于等于",
    "ll": "远小于",
    "gg": "远大于",
    "neq": "不等于",
    "ne": "不等于",
    "approx": "约等于",
    "simeq": "约等于",
    "sim": "相似于",
    "equiv": "恒等于",
    "cong": "全等于",
    "propto": "正比于",
    "in": "属于",
    "notin": "不属于",
    "subset": "真子集",
    "supset": "真包含",
    "subseteq": "子集或等于",
    "supseteq": "包含或等于",
    "cup": "并集",
    "cap": "交集",
    "forall": "任意",
    "exists": "存在",
    "emptyset": "空集",
    "nabla": "梯度",
    "land": "且",
    "lor": "或",
    "neg": "非",
    "Rightarrow": "推出",
    "Leftarrow": "由此可得",
    "Leftrightarrow": "当且仅当",
    "infty": "无穷大",
    "int": "积分",
    "prod": "连乘",
    "ldots": "省略",
    "cdots": "省略",
    "ln": "自然对数",
    "log": "对数",
    "exp": "指数函数",
    "Pr": "概率",
    "to": "趋近于",
    "rightarrow": "到",
    "leftarrow": "来自",
    "mid": "条件为",
    "mathbf": "",
    "mathrm": "",
    "text": "",
    "left": "",
    "right": "",
}

_LATIN_LETTER_SPEECH = {
    "a": "诶",
    "b": "比",
    "c": "西",
    "d": "迪",
    "e": "伊",
    "f": "艾弗",
    "g": "吉",
    "h": "艾尺",
    "i": "艾",
    "j": "杰",
    "k": "凯",
    "l": "艾勒",
    "m": "艾姆",
    "n": "恩",
    "o": "欧",
    "p": "批",
    "q": "丘",
    "r": "阿尔",
    "s": "艾斯",
    "t": "提",
    "u": "优",
    "v": "维",
    "w": "达不溜",
    "x": "艾克斯",
    "y": "歪",
    "z": "兹",
}

_LATIN_SYMBOL_SPEECH = {
    "+": "加",
    "#": "井号",
    ".": "点",
    "_": "下划线",
    "-": "杠",
}

_CJK_TTS_LANGUAGE_ALIASES = {
    "zh",
    "zh-cn",
    "zh-tw",
    "zh-hans",
    "zh-hant",
    "chinese",
    "all_zh",
    "all_yue",
    "yue",
}

_GENIE_HYBRID_ZH_EN_LANGUAGE = "hybrid-zh-en"
_GENIE_HYBRID_LANGUAGE_ALIASES = {
    "hybrid",
    "hybrid-zh-en",
    "hybrid-en-zh",
    "hybrid-chinese-english",
}

_GPT_SOVITS_UNSAFE_CJK_SPEECH = {
    "兙": "克",
    "兡": "百克",
    "呣": "母",
    "嗯": "恩",
    "嗧": "加仑",
    "噷": "亨",
    "桛": "线轴",
    "烪": "火",
    "瓧": "十瓦",
    "瓰": "分瓦",
    "瓱": "毫瓦",
    "瓼": "厘瓦",
    "甅": "厘米",
}

_SYMBOL_SPEECH_REPLACEMENTS = {
    "≤": "小于等于",
    "≥": "大于等于",
    "≪": "远小于",
    "≫": "远大于",
    "≈": "约等于",
    "≃": "约等于",
    "≅": "全等于",
    "≠": "不等于",
    "≡": "恒等于",
    "≲": "小于约等于",
    "≳": "大于约等于",
    "∞": "无穷大",
    "√": "根号",
    "∑": "求和",
    "∏": "连乘",
    "∫": "积分",
    "∇": "梯度",
    "∂": "偏导",
    "∝": "正比于",
    "∈": "属于",
    "∉": "不属于",
    "⊂": "真子集",
    "⊃": "真包含",
    "⊆": "子集或等于",
    "⊇": "包含或等于",
    "∪": "并集",
    "∩": "交集",
    "∀": "任意",
    "∃": "存在",
    "∅": "空集",
    "∧": "且",
    "∨": "或",
    "¬": "非",
    "⇒": "推出",
    "⇐": "由此可得",
    "⇔": "当且仅当",
    "→": "到",
    "←": "来自",
    "↔": "双向箭头",
    "↦": "映射到",
    "×": "乘以",
    "÷": "除以",
    "−": "减",
    "±": "正负",
    "∓": "负正",
    "∴": "所以",
    "∵": "因为",
    "％": "百分之",
    "%": "百分之",
    "℃": "摄氏度",
}

_SRE_COMPLEX_LATEX_RE = re.compile(
    r"\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|oint|iint|iiint|partial|lim|begin|matrix|cases|binom|left|right|overline|underline|vec|hat|bar|tilde)\b|"
    r"\\\\|&|\\over\b"
)

_SRE_PHRASE_REPLACEMENTS = (
    ("partial differential", "偏导"),
    ("sigma summation", "求和"),
    ("integral", "积分"),
    ("normal infinity", "无穷大"),
    ("infinity", "无穷大"),
    ("greater than or equal to", "大于等于"),
    ("less than or equal to", "小于等于"),
    ("much greater than", "远大于"),
    ("much less than", "远小于"),
    ("greater than", "大于"),
    ("less than", "小于"),
    ("not equals", "不等于"),
    ("not equal to", "不等于"),
    ("asymptotically equals", "渐近等于"),
    ("asymptotically equal to", "渐近等于"),
    ("almost equals", "约等于"),
    ("almost equal to", "约等于"),
    ("approximately equals", "约等于"),
    ("approximately equal to", "约等于"),
    ("equivalent to", "等价于"),
    ("identical to", "恒等于"),
    ("proportional to", "正比于"),
    ("element of", "属于"),
    ("not an element of", "不属于"),
    ("subset of", "子集"),
    ("union", "并集"),
    ("intersection", "交集"),
    ("there exists", "存在"),
    ("for all", "任意"),
    ("if and only if", "当且仅当"),
    ("equals", "等于"),
    ("minus", "减"),
    ("plus", "加"),
    ("times", "乘以"),
    ("divided by", "除以"),
    ("over", "除以"),
    ("left parenthesis", "左括号"),
    ("right parenthesis", "右括号"),
    ("left-parenthesis", "左括号"),
    ("right-parenthesis", "右括号"),
    ("left bracket", "左中括号"),
    ("right bracket", "右中括号"),
    ("left-bracket", "左中括号"),
    ("right-bracket", "右中括号"),
    ("left brace", "左大括号"),
    ("right brace", "右大括号"),
    ("left-brace", "左大括号"),
    ("right-brace", "右大括号"),
    ("negative", "负"),
    ("normal", ""),
    ("upper", "大写"),
    ("lower", "小写"),
    ("almost", "约"),
    ("asymptotically", "渐近"),
    ("when", "当"),
)

_SRE_TOKEN_REPLACEMENTS = {
    "StartFraction": "",
    "EndFraction": "",
    "Over": "除以",
    "StartRoot": "根号",
    "EndRoot": "根号结束",
    "Subscript": "下标",
    "Superscript": "上标",
    "Underscript": "下限",
    "Overscript": "上限",
    "Endscripts": "",
    "Baseline": "",
    "StartLayout": "布局开始",
    "EndLayout": "布局结束",
    "StartMatrix": "矩阵开始",
    "EndMatrix": "矩阵结束",
    "StartAbsoluteValue": "绝对值",
    "EndAbsoluteValue": "绝对值结束",
    "Matrix": "矩阵",
    "Row": "行",
    "Column": "列",
    "By": "乘",
    "Start": "开始",
    "End": "结束",
}

_SRE_GREEK_REPLACEMENTS = {
    "alpha": "阿尔法",
    "beta": "贝塔",
    "gamma": "伽马",
    "delta": "德尔塔",
    "epsilon": "艾普西龙",
    "lambda": "兰姆达",
    "mu": "缪",
    "pi": "派",
    "sigma": "西格玛",
    "theta": "西塔",
    "phi": "斐",
    "omega": "欧米伽",
}

_CJK_PAUSE_SENTINELS = {
    "，": "\ue000",
    "。": "\ue001",
    "；": "\ue002",
    "：": "\ue003",
    "？": "\ue004",
    "！": "\ue005",
    "、": "\ue006",
}
_ASCII_TO_CJK_PAUSES = str.maketrans(
    {
        ",": "，",
        ";": "；",
        ":": "：",
        "?": "？",
        "!": "！",
    }
)
_PAUSE_END_RE = re.compile(r"[，。！？；：、,.!?;:]$")

_PUNCTUATION_NORMALIZATION = str.maketrans(
    {
        "“": "\"",
        "”": "\"",
        "„": "\"",
        "‟": "\"",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "「": "\"",
        "」": "\"",
        "『": "\"",
        "』": "\"",
        "\u00a0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }
)

_BARE_MATH_TOKEN = r"(?:\\[A-Za-z]+|[A-Za-zΑ-Ωα-ω0-9]+(?:_\{[^{}]+\}|_[A-Za-z0-9]+|\^\{[^{}]+\}|\^[A-Za-z0-9+-]+)?|\([^()，。！？；：\s]+\)|\{[^{}]+\})"
_BARE_MATH_OPERATOR = (
    r"<=|>=|<<|>>|≤|≥|≪|≫|≲|≳|≈|≃|≅|≠|≡|∝|∈|∉|⊂|⊃|⊆|⊇|∪|∩|"
    r"⇒|⇐|⇔|→|←|↔|↦|=|<|>|\+|-|−|\*|/|×|÷"
)
_BARE_MATH_EXPR_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_BARE_MATH_TOKEN}(?:\s*(?:{_BARE_MATH_OPERATOR})\s*{_BARE_MATH_TOKEN})+)(?![A-Za-z0-9])"
)
_LATEX_SUBSUP_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z](?:"
    r"_\{\\(?:text|mathrm)\{[^{}]+\}\}|"
    r"\^\{\\(?:text|mathrm)\{[^{}]+\}\}|"
    r"_\{[^{}]+\}|_[A-Za-z0-9]+|"
    r"\^\{[^{}]+\}|\^[A-Za-z0-9+-]+"
    r")+)"
    r"(?![A-Za-z0-9])"
)


def _normalize_unicode_preserving_cjk_pauses(text: str) -> str:
    for punctuation, sentinel in _CJK_PAUSE_SENTINELS.items():
        text = text.replace(punctuation, sentinel)
    text = unicodedata.normalize("NFKC", text)
    for punctuation, sentinel in _CJK_PAUSE_SENTINELS.items():
        text = text.replace(sentinel, punctuation)
    return text


def _restore_cjk_pause_punctuation(text: str) -> str:
    if re.search(r"[\u3400-\u9fff]", text):
        text = text.translate(_ASCII_TO_CJK_PAUSES)
        text = re.sub(r"(?<!\d)\.(?!\d)", "。", text)
    text = re.sub(r"\s*([，。！？；：、])\s*", r"\1", text)
    text = re.sub(r"([，。！？；：、]){2,}", lambda match: match.group(0)[-1], text)
    return text


def _line_with_pause(line: str, *, pause: str = "。") -> str:
    line = line.strip()
    if not line:
        return ""
    if _PAUSE_END_RE.search(line):
        return line
    return f"{line}{pause}"


def _markdown_heading_replacement(match: re.Match[str]) -> str:
    return _line_with_pause(match.group(1), pause="。") + " "


def _markdown_list_replacement(match: re.Match[str]) -> str:
    return _line_with_pause(match.group(1), pause="。") + " "


def _line_breaks_to_pauses(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    return " ".join(_line_with_pause(line, pause="。") for line in lines)


def _speech_with_pause(text: str, *, pause: str = "，") -> str:
    spoken = text.strip()
    if not spoken:
        return spoken
    if _PAUSE_END_RE.search(spoken):
        return spoken
    return f"{spoken}{pause}"


def _replace_prose_dashes(text: str) -> str:
    text = re.sub(r"[\u2014\u2013]{2,}", "。", text)
    text = re.sub(r"(?<=[\u3400-\u9fffA-Za-z])[\u2014\u2013](?=[\u3400-\u9fffA-Za-z])", "，", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])[ \t]+-[ \t]+(?=[\u3400-\u9fff])", "，", text)
    return text


@lru_cache(maxsize=1)
def _formula_library_by_id() -> dict[str, dict[str, object]]:
    library_paths = (
        ROOT_DIR / "structured" / "formula_library.json",
        ROOT_DIR / "structured" / "structured" / "formula_library.json",
    )
    library_path = next((path for path in library_paths if path.exists()), None)
    if library_path is None:
        return {}
    try:
        data = json.loads(library_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    formulas = data.get("formulas", []) if isinstance(data, dict) else []
    by_id: dict[str, dict[str, object]] = {}
    for item in formulas:
        if isinstance(item, dict) and item.get("id") is not None:
            by_id[str(item["id"])] = item
    return by_id


def _strip_latex_wrappers(expr: str) -> str:
    expr = expr.strip()
    expr = re.sub(r"\\begin\{[^}]+\}", " ", expr)
    expr = re.sub(r"\\end\{[^}]+\}", " ", expr)
    expr = expr.replace("\\\\", " ")
    expr = expr.replace("\\left", "").replace("\\right", "")
    expr = expr.replace("\\,", " ").replace("\\;", " ")
    return expr


def _replace_balanced_command(expr: str, command: str, replacement: str) -> str:
    pattern = re.compile(rf"\\{command}\s*\{{([^{{}}]+)\}}\s*\{{([^{{}}]+)\}}")
    previous = None
    while previous != expr:
        previous = expr
        expr = pattern.sub(lambda match: replacement.format(match.group(1), match.group(2)), expr)
    return expr


def _replace_single_argument_command(expr: str, command: str, replacement: str = "{}") -> str:
    pattern = re.compile(rf"\\{command}\s*\{{([^{{}}]+)\}}")
    previous = None
    while previous != expr:
        previous = expr
        expr = pattern.sub(lambda match: replacement.format(match.group(1)), expr)
    return expr


def _read_latex_group(expr: str, start: int) -> tuple[str, int] | None:
    index = start
    while index < len(expr) and expr[index].isspace():
        index += 1
    if index >= len(expr) or expr[index] != "{":
        return None
    depth = 0
    content_start = index + 1
    while index < len(expr):
        char = expr[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return expr[content_start:index], index + 1
        index += 1
    return None


def _replace_latex_group_command(expr: str, command: str, arity: int, replacement: str) -> str:
    marker = f"\\{command}"
    output: list[str] = []
    index = 0
    changed = False
    while index < len(expr):
        if expr.startswith(marker, index):
            next_index = index + len(marker)
            if next_index < len(expr) and expr[next_index].isalpha():
                output.append(expr[index])
                index += 1
                continue
            cursor = next_index
            groups: list[str] = []
            for _ in range(arity):
                group = _read_latex_group(expr, cursor)
                if group is None:
                    groups = []
                    break
                group_text, cursor = group
                groups.append(group_text)
            if len(groups) == arity:
                output.append(replacement.format(*groups))
                index = cursor
                changed = True
                continue
        output.append(expr[index])
        index += 1
    return "".join(output) if changed else expr


def _replace_latex_group_command_recursive(expr: str, command: str, arity: int, replacement: str) -> str:
    previous = None
    while previous != expr:
        previous = expr
        expr = _replace_latex_group_command(expr, command, arity, replacement)
    return expr


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _formula_engine() -> str:
    return os.getenv("KGTS_TTS_FORMULA_ENGINE", "sre").strip().lower() or "sre"


def _sre_engine_options() -> tuple[str, str]:
    domain = os.getenv("KGTS_TTS_FORMULA_SRE_DOMAIN", "mathspeak").strip() or "mathspeak"
    style = os.getenv("KGTS_TTS_FORMULA_SRE_STYLE", "default").strip() or "default"
    return domain, style


def _should_use_sre_formula_engine(expr: str) -> bool:
    engine = _formula_engine()
    if engine in {"off", "disabled", "python", "regex", "legacy"}:
        return False
    if engine in {"sre", "mathjax", "mathjax-sre"}:
        return True
    if engine == "auto":
        return bool(_SRE_COMPLEX_LATEX_RE.search(expr))
    return True


def _node_executable() -> str:
    return os.getenv("KGTS_TTS_FORMULA_NODE", "node").strip() or "node"


def _normalize_sre_speech(speech: str) -> str:
    text = speech.strip()
    if not text:
        return text
    text = re.sub(
        r"\bModifyingAbove\s+(.+?)\s+With\s+caret\b",
        r"\1 的估计值",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bModifyingAbove\s+(.+?)\s+With\s+tilde\b",
        r"\1 波浪号",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b([A-Za-zΑ-Ωα-ω]+)\s+overtilde\b", r"\1 波浪号", text, flags=re.IGNORECASE)
    text = re.sub(r"\b([A-Za-zΑ-Ωα-ω]+)\s+overbar\b", r"\1 横线", text, flags=re.IGNORECASE)
    text = re.sub(r"\b([A-Za-zΑ-Ωα-ω]+)\s+overhat\b", r"\1 的估计值", text, flags=re.IGNORECASE)
    text = re.sub(r"\bovertilde\b", "波浪号", text, flags=re.IGNORECASE)
    text = re.sub(r"\boverbar\b", "横线", text, flags=re.IGNORECASE)
    text = re.sub(r"\boverhat\b", "的估计值", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsquared\b", "的 2 次方", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcubed\b", "的 3 次方", text, flags=re.IGNORECASE)
    for phrase, replacement in _SRE_PHRASE_REPLACEMENTS:
        text = re.sub(rf"\b{re.escape(phrase)}\b", replacement, text, flags=re.IGNORECASE)
    for token, replacement in _SRE_TOKEN_REPLACEMENTS.items():
        text = re.sub(rf"\b{re.escape(token)}\b", replacement, text, flags=re.IGNORECASE)
    for token, replacement in _SRE_GREEK_REPLACEMENTS.items():
        text = re.sub(rf"\b{re.escape(token)}\b", replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\bw\s+h\s+e\s+n\b", "当", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)
    text = re.sub(r"约\s+等于", "约等于", text)
    text = re.sub(r"渐近\s+等于", "渐近等于", text)
    text = re.sub(r"根号\s+结束", "根号结束", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return _speech_with_pause(text)


@lru_cache(maxsize=512)
def _sre_latex_to_speech_cached(expr: str, domain: str, style: str) -> str | None:
    return _run_sre_latex_speech_cli([(0, expr)], domain, style).get(0)


def _run_sre_latex_speech_cli(formulas: list[tuple[int, str]], domain: str, style: str) -> dict[int, str]:
    if not SRE_LATEX_SPEECH_CLI.is_file():
        return {}
    payload = {
        "domain": domain,
        "style": style,
        "formulas": [{"id": formula_id, "latex": expr} for formula_id, expr in formulas],
    }
    try:
        completed = subprocess.run(
            [_node_executable(), str(SRE_LATEX_SPEECH_CLI)],
            cwd=str(ROOT_DIR),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=max(_env_float("KGTS_TTS_FORMULA_TIMEOUT_SECONDS", 3.0), 0.2),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0 and not completed.stdout.strip():
        return {}
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict) or not data.get("ok"):
        return {}
    results = data.get("results")
    if not isinstance(results, list):
        return {}
    converted: dict[int, str] = {}
    for result in results:
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        formula_id = result.get("id")
        speech = result.get("speech")
        if not isinstance(formula_id, int) or not isinstance(speech, str):
            continue
        normalized = _normalize_sre_speech(speech)
        if normalized:
            converted[formula_id] = normalized
    return converted


def _sre_latex_to_speech(expr: str) -> str | None:
    if not _should_use_sre_formula_engine(expr):
        return None
    domain, style = _sre_engine_options()
    return _sre_latex_to_speech_cached(expr.strip(), domain, style)


def _sre_latex_batch_to_speech(expressions: list[str]) -> dict[int, str]:
    if not _env_flag("KGTS_TTS_FORMULA_SRE_ENABLED", True):
        return {}
    formulas = [
        (index, expr.strip())
        for index, expr in enumerate(expressions)
        if expr.strip() and _should_use_sre_formula_engine(expr)
    ]
    if not formulas:
        return {}
    domain, style = _sre_engine_options()
    return _run_sre_latex_speech_cli(formulas, domain, style)


def _legacy_latex_to_speech(expr: str) -> str:
    raw_expr = expr.strip()
    expr = _strip_latex_wrappers(raw_expr)
    complex_expr = bool(re.search(r"\\(sum|prod|int|begin|matrix|cases)|\\\\|&", raw_expr))

    expr = _replace_latex_group_command_recursive(expr, "frac", 2, "{} 除以 {}")
    expr = _replace_latex_group_command_recursive(expr, "dfrac", 2, "{} 除以 {}")
    expr = _replace_latex_group_command_recursive(expr, "tfrac", 2, "{} 除以 {}")
    expr = _replace_latex_group_command_recursive(expr, "binom", 2, "{} 中取 {} 的组合数")
    expr = _replace_latex_group_command_recursive(expr, "sqrt", 1, "{} 的平方根")
    expr = _replace_balanced_command(expr, "frac", "{} 除以 {}")
    expr = _replace_balanced_command(expr, "binom", "{} 中取 {} 的组合数")
    for command in ("mathbf", "mathrm", "text", "operatorname", "overline", "bar", "hat", "tilde"):
        expr = _replace_latex_group_command_recursive(expr, command, 1, "{}")
        expr = _replace_single_argument_command(expr, command)
    expr = re.sub(r"\{([^{}]+)\\over\s+([^{}]+)\}", r"\1 除以 \2", expr)
    expr = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r"\1 的平方根", expr)
    expr = re.sub(r"\\sum_\{?([^{}^]+)\}?\^\{?([^{}]+)\}?", r"从 \1 到 \2 的求和", expr)
    expr = re.sub(r"\\prod_\{?([^{}^]+)\}?\^\{?([^{}]+)\}?", r"从 \1 到 \2 的连乘", expr)

    for command, spoken in _GREEK_SPEECH.items():
        if command == "Delta":
            expr = re.sub(r"\\Delta\s*([A-Za-z])", r"\1 的变化量", expr)
        expr = expr.replace(f"\\{command}", spoken)

    for command, spoken in _LATEX_COMMANDS.items():
        expr = expr.replace(f"\\{command}", spoken)

    expr = re.sub(r"([A-Za-z0-9})])_\{([^{}]+)\}", r"\1 下标 \2", expr)
    expr = re.sub(r"([A-Za-z0-9})])_([A-Za-z0-9])", r"\1 下标 \2", expr)
    expr = re.sub(r"([A-Za-z0-9})])\^\{([^{}]+)\}", r"\1 的 \2 次方", expr)
    expr = re.sub(r"([A-Za-z0-9})])\^([+-]?[A-Za-z0-9]+)", r"\1 的 \2 次方", expr)

    replacements = {
        "<<": " 远小于 ",
        ">>": " 远大于 ",
        "≤": " 小于等于 ",
        "≥": " 大于等于 ",
        "≪": " 远小于 ",
        "≫": " 远大于 ",
        "≈": " 约等于 ",
        "≃": " 约等于 ",
        "≅": " 全等于 ",
        "≠": " 不等于 ",
        "≡": " 恒等于 ",
        "≲": " 小于约等于 ",
        "≳": " 大于约等于 ",
        "∝": " 正比于 ",
        "∈": " 属于 ",
        "∉": " 不属于 ",
        "⊂": " 真子集 ",
        "⊃": " 真包含 ",
        "⊆": " 子集或等于 ",
        "⊇": " 包含或等于 ",
        "∪": " 并集 ",
        "∩": " 交集 ",
        "⇒": " 推出 ",
        "⇐": " 由此可得 ",
        "⇔": " 当且仅当 ",
        "→": " 到 ",
        "←": " 来自 ",
        "↔": " 双向箭头 ",
        "↦": " 映射到 ",
        "×": " 乘以 ",
        "÷": " 除以 ",
        "−": " 减 ",
        "=": " 等于 ",
        "+": " 加 ",
        "-": " 减 ",
        "*": " 乘以 ",
        "/": " 除以 ",
        "(": " 左括号 ",
        ")": " 右括号 ",
        "[": " 左中括号 ",
        "]": " 右中括号 ",
        "{": " ",
        "}": " ",
        ",": " 逗号 ",
        "|": " 条件为 ",
        "<": " 小于 ",
        ">": " 大于 ",
    }
    for old, new in replacements.items():
        expr = expr.replace(old, new)

    expr = re.sub(r"\\[A-Za-z]+", " ", expr)
    expr = expr.replace("−", " 减 ")
    expr = re.sub(r"[_^&]", " ", expr)
    expr = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", expr)
    expr = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", expr)
    expr = re.sub(r"\s+", " ", expr).strip()
    expr = spell_latin_terms_for_chinese(expr, mode="formula")
    expr = re.sub(r"\s+", " ", expr).strip()
    if not expr:
        return "这里有一个公式。"
    if complex_expr:
        return f"这里有一个公式，表达式为 {expr}。"
    return _speech_with_pause(expr)


def latex_to_speech(expr: str, *, prefer_sre: bool = True) -> str:
    raw_expr = expr.strip()
    if prefer_sre and _env_flag("KGTS_TTS_FORMULA_SRE_ENABLED", True):
        sre_speech = _sre_latex_to_speech(raw_expr)
        if sre_speech:
            return sre_speech
    return _legacy_latex_to_speech(raw_expr)


def _replace_latex_matches_for_speech(
    text: str,
    pattern: str,
    *,
    expr_group: int | None = 1,
    flags: int = 0,
    prefer_sre: bool = True,
) -> str:
    matches = list(re.finditer(pattern, text, flags))
    if not matches:
        return text
    expressions = [match.group(expr_group) if expr_group is not None else match.group(0) for match in matches]
    sre_speeches = _sre_latex_batch_to_speech(expressions) if prefer_sre else {}

    parts: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        parts.append(text[cursor : match.start()])
        replacement = sre_speeches.get(index)
        if replacement is None:
            replacement = _legacy_latex_to_speech(expressions[index])
        parts.append(replacement)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def _formula_id_to_speech(formula_id: str) -> str:
    item = _formula_library_by_id().get(formula_id)
    if not item:
        return f"公式 {formula_id}。"
    label = str(item.get("label_format") or formula_id)
    for key in ("speech_text", "tts_text", "spoken_text"):
        speech_text = item.get(key)
        if isinstance(speech_text, str) and speech_text.strip():
            return f"公式 {label}，{_speech_with_pause(speech_text.strip(), pause='。')}"
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        return f"公式 {label}，{description.strip()}。"
    latex = str(item.get("latex") or "")
    if latex:
        return f"公式 {label}，{latex_to_speech(latex)}"
    return f"公式 {label}。"


def _replace_bare_math_for_speech(match: re.Match[str]) -> str:
    expr = match.group(1)
    if re.fullmatch(r"[A-Za-z]+(?:-[A-Za-z]+)+", expr):
        return expr
    return latex_to_speech(expr, prefer_sre=False)


def replace_formulas_for_speech(text: str) -> str:
    text = _replace_prose_dashes(text)
    text = re.sub(r"\[\[(?:SEE_)?FORMULA:([^\]]+)\]\]", lambda match: _formula_id_to_speech(match.group(1).strip()), text)
    text = _replace_latex_matches_for_speech(text, r"\$\$(.+?)\$\$", flags=re.DOTALL)
    text = _replace_latex_matches_for_speech(text, r"\\\[(.+?)\\\]", flags=re.DOTALL)
    text = _replace_latex_matches_for_speech(text, r"\\\((.+?)\\\)", flags=re.DOTALL)
    text = _replace_latex_matches_for_speech(text, r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
    text = _replace_latex_matches_for_speech(text, r"\\frac\s*\{[^{}]+\}\s*\{[^{}]+\}", expr_group=None)
    text = _BARE_MATH_EXPR_RE.sub(_replace_bare_math_for_speech, text)
    text = _LATEX_SUBSUP_TOKEN_RE.sub(lambda match: latex_to_speech(match.group(1), prefer_sre=False), text)
    return text


def clean_markdown_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = _expand_markdown_emphasis_for_speech(text)

    processed_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            processed_lines.append("")
            continue
        heading_match = re.match(r"#{1,6}\s*(.+)$", line)
        list_match = re.match(r"(?:[-*+]|\d+\.)\s+(.+)$", line)
        if heading_match:
            processed_lines.append(_line_with_pause(heading_match.group(1), pause="。"))
        elif list_match:
            processed_lines.append(_line_with_pause(list_match.group(1), pause="。"))
        else:
            processed_lines.append(line)
    text = "\n".join(processed_lines)

    text = re.sub(r"[>*_~`|]", " ", text)
    text = text.replace("&nbsp;", " ")
    text = _line_breaks_to_pauses(text)
    return text


def _expand_markdown_emphasis_for_speech(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        emphasized = re.sub(r"\s+", " ", match.group(1)).strip(" ，。；：、")
        if not emphasized:
            return " "
        return emphasized

    return re.sub(r"\*\*([^*\n][\s\S]*?[^*\n])\*\*", replace, text)


def normalize_speech_cues(text: str, speech_cues: list[dict[str, Any]] | None, *, max_repeat_cues: int = 2) -> list[dict[str, str]]:
    source = str(text or "")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in speech_cues or []:
        if not isinstance(raw, dict):
            continue
        cue_type = str(raw.get("type") or raw.get("kind") or "").strip().lower()
        if cue_type not in {"repeat", "key_point"}:
            continue
        target = str(raw.get("target_text") or raw.get("text") or "").strip()
        target = re.sub(r"\s+", " ", target).strip(" ，。；：、")
        if len(target) < 6 or len(target) > 90:
            continue
        if target in seen or target not in source:
            continue
        seen.add(target)
        normalized.append({
            "type": "repeat",
            "target_text": target,
            "style": str(raw.get("style") or "key_point").strip()[:40] or "key_point",
        })
        if len(normalized) >= max_repeat_cues:
            break
    return normalized


def apply_speech_cues_for_tts(text: str, speech_cues: list[dict[str, Any]] | None) -> str:
    # Speech cues are retained as a backwards-compatible payload field, but
    # narration must remain a single pass: no cue may inject repeated text.
    return str(text or "")


def remove_parenthetical_asides_for_speech(text: str) -> str:
    def remove_if_aside(match: re.Match[str]) -> str:
        content = match.group(1)
        if re.search(r"[\u3400-\u9fff]", content):
            return " "
        if re.search(r"\s", content) and re.search(r"[A-Za-z]", content):
            return " "
        return match.group(0)

    text = re.sub(r"（([^（）]*)）", " ", text)
    text = re.sub(r"【([^【】]*)】", " ", text)
    text = re.sub(r"(?<!\[)\[([^\[\]]*)\](?!\])", remove_if_aside, text)
    text = re.sub(r"\(([^()]*)\)", remove_if_aside, text)
    return text


def sanitize_gpt_sovits_text(text: str) -> str:
    text = _normalize_unicode_preserving_cjk_pauses(text)
    text = text.translate(_PUNCTUATION_NORMALIZATION)
    for symbol, speech in _SYMBOL_SPEECH_REPLACEMENTS.items():
        text = text.replace(symbol, f"，{speech}，")
    for char, speech in _GPT_SOVITS_UNSAFE_CJK_SPEECH.items():
        text = text.replace(char, speech)
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"} else char for char in text)
    text = _restore_cjk_pause_punctuation(text)
    return re.sub(r"[ \t\f\v]+", " ", text).strip()


def detect_tts_language(text: str, default_language: str = "zh") -> str:
    stripped = text.strip()
    if not stripped:
        return default_language
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", stripped))
    ascii_letters = len(re.findall(r"[A-Za-z]", stripped))
    meaningful = len(re.findall(r"[\w\u3400-\u9fff]", stripped))
    if cjk_count == 0 and meaningful >= 40 and ascii_letters / max(meaningful, 1) > 0.75:
        return "en"
    return default_language or "zh"


def has_mixed_cjk_latin_text(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text) and re.search(r"[A-Za-z]", text))


def resolve_genie_tts_language(text: str, language: str | None, default_language: str = "zh") -> str:
    lang = (language or default_language or "zh").strip().lower()
    if not lang:
        lang = "zh"
    if lang in _GENIE_HYBRID_LANGUAGE_ALIASES:
        return _GENIE_HYBRID_ZH_EN_LANGUAGE
    if lang in _CJK_TTS_LANGUAGE_ALIASES and has_mixed_cjk_latin_text(text):
        return _GENIE_HYBRID_ZH_EN_LANGUAGE
    if lang in {"all_zh", "all_yue"}:
        return "zh"
    return lang


def _spell_latin_token(token: str) -> str:
    parts: list[str] = []
    for char in token:
        lower = char.lower()
        if lower in _LATIN_LETTER_SPEECH:
            parts.append(_LATIN_LETTER_SPEECH[lower])
        elif char in _LATIN_SYMBOL_SPEECH:
            parts.append(_LATIN_SYMBOL_SPEECH[char])
        elif char.isdigit():
            parts.append(char)
        else:
            parts.append(" ")
    return "".join(parts).strip()


def _should_spell_latin_token(token: str, *, mode: str) -> bool:
    compact = re.sub(r"[^A-Za-z0-9+#._-]", "", token)
    if not compact or not re.search(r"[A-Za-z]", compact):
        return False
    letters = re.sub(r"[^A-Za-z]", "", compact)
    if not letters:
        return False
    if mode == "formula":
        return True
    if compact.isupper() and len(letters) <= 8:
        return True
    if len(letters) == 1:
        return True
    if re.search(r"[\d_+#.]", compact) and len(letters) <= 8:
        return True
    if len(letters) <= 2 and (compact[0].isupper() or compact[-1].isupper()):
        return True
    return False


def spell_latin_terms_for_chinese(text: str, *, mode: str = "prose") -> str:
    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if not _should_spell_latin_token(token, mode=mode):
            return token
        spoken = _spell_latin_token(token)
        return f" {spoken} " if spoken else token

    return re.sub(r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9+#._-]*)(?![A-Za-z0-9])", replace_token, text)


def normalize_tts_text(
    text: str,
    default_language: str = "zh",
    language_override: str | None = None,
    *,
    spell_latin_for_chinese: bool = True,
) -> NormalizedTtsText:
    normalized = remove_parenthetical_asides_for_speech(text)
    normalized = replace_formulas_for_speech(normalized)
    normalized = clean_markdown_for_speech(normalized)
    normalized = sanitize_gpt_sovits_text(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    text_lang = (language_override or "").strip().lower() or detect_tts_language(normalized, default_language)
    if spell_latin_for_chinese and text_lang in {"zh", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "chinese", "all_zh", "all_yue", _GENIE_HYBRID_ZH_EN_LANGUAGE}:
        normalized = spell_latin_terms_for_chinese(normalized)
        normalized = sanitize_gpt_sovits_text(normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
    return NormalizedTtsText(
        original_text=text,
        normalized_text=normalized,
        text_lang=text_lang,
    )
