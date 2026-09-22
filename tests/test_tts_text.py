from KGTS.core import tts_text
from KGTS.core.tts_text import apply_speech_cues_for_tts, normalize_speech_cues, normalize_tts_text, resolve_genie_tts_language


def test_normalize_tts_text_replaces_gpt_sovits_unsafe_cjk() -> None:
    unsafe_text = "兙兡呣嗯嗧噷桛烪瓧瓰瓱瓼甅"

    normalized = normalize_tts_text(unsafe_text, "all_zh").normalized_text

    assert normalized == "克百克母恩加仑亨线轴火十瓦分瓦毫瓦厘瓦厘米。"
    assert not any(char in normalized for char in unsafe_text)


def test_normalize_tts_text_speaks_common_math_symbols() -> None:
    normalized = normalize_tts_text("固定概率≤86%，p₀→1；QTL 等位基因。", "all_zh").normalized_text

    assert "小于等于" in normalized
    assert "百分之" in normalized
    assert "到" in normalized
    assert "丘提艾勒" in normalized


def test_normalize_tts_text_speaks_extended_math_symbols() -> None:
    normalized = normalize_tts_text("当 Ne≫1 且 p≪q，A∈B，A⊆C，x≈y，a⇒b，∴成立。", "zh").normalized_text

    assert "恩伊" in normalized
    assert "批" in normalized
    assert "丘" in normalized
    assert "诶" in normalized
    assert "远大于" in normalized
    assert "远小于" in normalized
    assert "属于" in normalized
    assert "子集或等于" in normalized
    assert "约等于" in normalized
    assert "推出" in normalized
    assert "所以" in normalized
    assert "≫" not in normalized
    assert "≪" not in normalized


def test_normalize_tts_text_speaks_latex_comparison_symbols() -> None:
    normalized = normalize_tts_text(r"条件是 $N_e \gg 1$ 且 $\theta \ll 1$。", "all_zh").normalized_text

    assert "远大于" in normalized
    assert "远小于" in normalized
    assert r"\gg" not in normalized
    assert r"\ll" not in normalized


def test_normalize_tts_text_preserves_chinese_pause_punctuation() -> None:
    normalized = normalize_tts_text("同学们,今天看选择:它会持续吗?", "zh").normalized_text

    assert normalized == "同学们，今天看选择：它会持续吗？"


def test_normalize_tts_text_adds_pauses_for_markdown_structure() -> None:
    normalized = normalize_tts_text(
        "## 标题\n\n- 第一点：选择会改变频率\n- 第二点：漂移会带来随机性",
        "zh",
    ).normalized_text

    assert normalized == "标题。第一点：选择会改变频率。第二点：漂移会带来随机性。"


def test_normalize_tts_text_does_not_repeat_markdown_bold_emphasis() -> None:
    normalized = normalize_tts_text("**最终响应依赖有效群体大小。**", "zh").normalized_text

    assert "重复一遍" not in normalized
    assert normalized.count("最终响应依赖有效群体大小") == 1


def test_apply_speech_cues_ignores_legacy_repeat_target_text() -> None:
    text = "最终响应依赖有效群体大小。后面我们再看固定概率。"
    planned = apply_speech_cues_for_tts(text, [{"type": "repeat", "target_text": "最终响应依赖有效群体大小"}])

    assert planned == text
    assert planned.count("最终响应依赖有效群体大小") == 1


def test_normalize_speech_cues_rejects_missing_target_text() -> None:
    cues = normalize_speech_cues("正文只有这一句。", [{"type": "repeat", "target_text": "不存在的重点"}])

    assert cues == []


def test_normalize_tts_text_adds_pause_around_symbol_speech() -> None:
    normalized = normalize_tts_text("固定概率≤86%，p₀→1；QTL 等位基因。", "zh").normalized_text

    assert "固定概率，小于等于，86，百分之，" in normalized
    assert "批0，到，1" in normalized


def test_normalize_tts_text_turns_prose_dashes_into_pauses() -> None:
    normalized = normalize_tts_text("选择——漂移；选择—迁移。", "zh").normalized_text

    assert normalized == "选择。漂移；选择，迁移。"
    assert "-" not in normalized


def test_normalize_tts_text_removes_parenthetical_asides() -> None:
    normalized = normalize_tts_text("这里要朗读（这里不要读）正文继续。[备注也不要读]结束。", "zh").normalized_text

    assert "这里要朗读" in normalized
    assert "正文继续" in normalized
    assert "结束" in normalized
    assert "不要读" not in normalized
    assert "备注" not in normalized


def test_normalize_tts_text_speaks_unwrapped_formulas() -> None:
    normalized = normalize_tts_text(r"条件是 p_0 > 1/(2Ns)，所以 P_{\text{fix}} 增加。", "all_zh").normalized_text

    assert "批 下标 0 大于 1 除以 左括号 2 恩艾斯 右括号" in normalized
    assert "批 下标 艾弗艾艾克斯" in normalized
    assert r"\text" not in normalized


def test_normalize_tts_text_spells_abbreviations_without_breaking_english_terms() -> None:
    normalized = normalize_tts_text("今天讲 Hardy-Weinberg equilibrium 和 QTL。", "zh").normalized_text

    assert "Hardy-Weinberg equilibrium" in normalized
    assert "丘提艾勒" in normalized
    assert "艾尺诶阿尔迪歪" not in normalized


def test_normalize_tts_text_spells_formula_variables_in_chinese_mode(monkeypatch) -> None:
    monkeypatch.setattr(tts_text, "_sre_latex_to_speech", lambda expr: None)
    monkeypatch.setattr(tts_text, "_sre_latex_batch_to_speech", lambda expressions: {})

    normalized = normalize_tts_text(r"条件是 $N_e \gg 1$ 且 $P_{\text{fix}}$ 增加。", "zh").normalized_text

    assert "恩 下标 伊 远大于 1" in normalized
    assert "批 下标 艾弗艾艾克斯" in normalized
    assert "N_e" not in normalized
    assert r"\text" not in normalized


def test_normalize_tts_text_uses_sre_for_structural_latex() -> None:
    normalized = normalize_tts_text(
        r"二次公式是 $\frac{-b+\sqrt{b^2-4ac}}{2a}$。",
        "zh",
    ).normalized_text

    assert "根号" in normalized or "平方根" in normalized
    assert "除以" in normalized
    assert r"\frac" not in normalized
    assert r"\sqrt" not in normalized


def test_normalize_tts_text_speaks_sum_and_integral() -> None:
    normalized = normalize_tts_text(
        r"看 $$\sum_{i=1}^{n} x_i$$ 和 $\int_0^\infty e^{-x} dx$。",
        "zh",
    ).normalized_text

    assert "求和" in normalized
    assert "下限" in normalized or "下标" in normalized
    assert "上限" in normalized or "上标" in normalized
    assert "积分" in normalized
    assert "无穷大" in normalized


def test_normalize_tts_text_speaks_matrix() -> None:
    normalized = normalize_tts_text(
        r"矩阵为 \[\begin{pmatrix}a&b\\c&d\end{pmatrix}\]。",
        "zh",
    ).normalized_text

    assert "矩阵" in normalized
    assert r"\begin" not in normalized
    assert "&" not in normalized


def test_normalize_tts_text_falls_back_when_sre_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(tts_text, "_sre_latex_to_speech", lambda expr: None)
    monkeypatch.setattr(tts_text, "_sre_latex_batch_to_speech", lambda expressions: {})

    normalized = normalize_tts_text(r"比例为 $\frac{a}{b}$。", "all_zh").normalized_text

    assert "诶 除以 比" in normalized
    assert r"\frac" not in normalized


def test_normalize_tts_text_falls_back_for_nested_latex(monkeypatch) -> None:
    monkeypatch.setattr(tts_text, "_sre_latex_to_speech", lambda expr: None)
    monkeypatch.setattr(tts_text, "_sre_latex_batch_to_speech", lambda expressions: {})

    normalized = normalize_tts_text(r"二次公式是 $\frac{-b+\sqrt{b^2-4ac}}{2a}$。", "zh").normalized_text

    assert "除以" in normalized
    assert "平方根" in normalized
    assert "比 的 2 次方" in normalized
    assert "4 诶西" in normalized
    assert r"\frac" not in normalized
    assert r"\sqrt" not in normalized


def test_resolve_genie_tts_language_detects_mixed_chinese_english() -> None:
    lang = resolve_genie_tts_language("今天讲 Hardy-Weinberg equilibrium。", "zh")

    assert lang == "hybrid-zh-en"


def test_resolve_genie_tts_language_normalizes_explicit_hybrid_alias() -> None:
    lang = resolve_genie_tts_language("今天讲 Hardy-Weinberg equilibrium。", "hybrid-chinese-english")

    assert lang == "hybrid-zh-en"


def test_resolve_genie_tts_language_preserves_pure_chinese_and_english() -> None:
    assert resolve_genie_tts_language("今天讲遗传漂变。", "zh") == "zh"
    assert resolve_genie_tts_language("Hardy-Weinberg equilibrium.", "en") == "en"
