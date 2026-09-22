import asyncio

from KGTS.education.router import _plan_slide_speech_cues_with_model
from KGTS.models.education import PlanSlideSpeechRequest


def test_plan_slide_speech_disables_repeat_cues(monkeypatch):
    async def fail_model_call(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("KGTS.education.router.DeepSeekAPIClient._call_deepseek", fail_model_call)
    lecture = "这一页的核心结论是遗传漂变会影响固定概率。因此要记住这个重点。"

    cues = asyncio.run(
        _plan_slide_speech_cues_with_model(
            PlanSlideSpeechRequest(
                chapter_title="测试课程",
                slide={"index": 1, "title": "遗传漂变", "content": "固定概率"},
                lecture=lecture,
                max_cues=1,
            )
        )
    )

    assert cues == []
