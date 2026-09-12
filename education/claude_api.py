"""DeepSeek API client kept under the legacy module path for compatibility."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from KGTS.education.kg_constraints import (
    KG_CONSTRAINED_SYSTEM_PROMPT,
    build_lecture_gc_dpg_requirements,
    build_constrained_generation_prompt,
    build_learning_plan,
    clean_generated_lecture_output,
    evidence_from_graph,
    evidence_from_rag,
    expand_formula_references,
    format_evidence,
    relation_evidence_from_graph,
)
from KGTS.core.graph_context import build_graphrag_context
from KGTS.config import load_root_env

DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash-vision-exp"
# Compatibility aliases for callers importing the old names.
DEFAULT_DEEPSEEK_FLASH_MODEL = DEFAULT_DEEPSEEK_MODEL
DEFAULT_DEEPSEEK_PRO_MODEL = DEFAULT_DEEPSEEK_MODEL
_DEFAULT_TIMEOUT = object()

QA_SYSTEM_PROMPT = """你是一个公式友好的教学问答助手。
图谱和检索内容是参考上下文，不是拒答规则。用户问公式、定义、推导或概念时，先直接回答。
如果上下文有相关材料，优先使用并保留原文术语；如果上下文没有命中，但问题属于常见数学/机器学习/课程知识，可以用通用知识回答，并简短标注"通用说明"。
不要因为 LearningPlan 或 evidence 不足而拒答；只在问题涉及具体课程私有内容且上下文完全缺失时说明需要补充材料。
公式必须用 LaTeX 输出，并解释主要符号。"""


def get_deepseek_model(kind: str = "flash") -> str:
    """Return the single model used by all education generation flows."""
    return DEFAULT_DEEPSEEK_MODEL


def _parse_read_timeout(value: str | None, default: float | None) -> float | None:
    text = str(value or "").strip().lower()
    if not text or text == "default":
        return default
    if text in {"0", "none", "off", "false"}:
        return None
    try:
        return max(float(text), 1.0)
    except ValueError:
        return default


def _generation_read_timeout() -> float | None:
    return _parse_read_timeout(os.getenv("DEEPSEEK_GENERATION_READ_TIMEOUT_SECONDS"), None)


def _exercise_generation_read_timeout() -> float | None:
    return _parse_read_timeout(
        os.getenv("DEEPSEEK_EXERCISE_READ_TIMEOUT_SECONDS")
        or os.getenv("DEEPSEEK_GENERATION_READ_TIMEOUT_SECONDS"),
        None,
    )


def _redact_deepseek_error_text(text: str) -> str:
    clean = str(text or "")
    clean = re.sub(r"(?i)(api\s*key[:\s]+)([A-Za-z0-9_\-]{8,})", r"\1[redacted]", clean)
    clean = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-[redacted]", clean)
    clean = re.sub(r"\*{2,}[A-Za-z0-9]{2,}", "[redacted]", clean)
    return clean.strip()


def _format_deepseek_http_error(response: httpx.Response) -> str:
    if response.status_code in {401, 403}:
        return (
            "DeepSeek API 鉴权失败：当前 API Key 无效、已过期或无权限。"
            "请在右上角齿轮设置中更新 DeepSeek API Key，或检查 .env 中的 DEEPSEEK_API_KEY 后重试。"
        )

    provider_message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                provider_message = str(error_payload.get("message") or error_payload.get("code") or "")
            else:
                provider_message = str(payload.get("message") or payload.get("error") or "")
    except Exception:
        provider_message = response.text

    provider_message = _redact_deepseek_error_text(provider_message)
    if response.status_code == 429:
        return f"DeepSeek API 请求受限或额度不足（HTTP 429）：{provider_message or '请稍后重试或检查账户额度。'}"
    if response.status_code >= 500:
        return f"DeepSeek API 服务暂时不可用（HTTP {response.status_code}）：{provider_message or '请稍后重试。'}"
    return f"DeepSeek API 调用失败（HTTP {response.status_code}）：{provider_message or '请检查 API Base、模型名和请求参数。'}"


def _message_content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("value")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or value.get("value")
        return text if isinstance(text, str) else ""
    return ""


def _extract_deepseek_response_text(result: Dict[str, Any]) -> str:
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise Exception("DeepSeek API 返回格式不正确：缺少 choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise Exception("DeepSeek API 返回格式不正确：choices[0] 不是对象")
    finish_reason = str(choice.get("finish_reason") or "").strip()
    message = choice.get("message")
    if finish_reason == "length":
        reasoning_note = ""
        if isinstance(message, dict) and _message_content_to_text(message.get("reasoning_content")).strip():
            reasoning_note = "；返回中只有 reasoning_content，已拒绝作为正文使用"
        raise Exception(f"DeepSeek API 输出达到 max_tokens 限制，结果可能被截断，请提高输出长度或减少单次生成内容{reasoning_note}。")

    if isinstance(message, dict):
        content = _message_content_to_text(message.get("content")).strip()
        if content:
            return content
        for key in ("text", "output_text"):
            content = _message_content_to_text(message.get(key)).strip()
            if content:
                return content

    for key in ("text", "content", "output_text"):
        content = _message_content_to_text(choice.get(key)).strip()
        if content:
            return content

    finish_reason = finish_reason or "unknown"
    message_keys = ",".join(sorted(message.keys())) if isinstance(message, dict) else "none"
    reasoning_note = ""
    if isinstance(message, dict) and _message_content_to_text(message.get("reasoning_content")).strip():
        reasoning_note = "；返回中只有 reasoning_content，已拒绝作为正文使用"
    raise Exception(f"DeepSeek API 返回正文为空：finish_reason={finish_reason}, message_keys={message_keys}{reasoning_note}")


def _format_graphrag_context_for_prompt(graphrag_context: Dict[str, Any]) -> str:
    lines = []
    for item in graphrag_context.get("llm_context") or []:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        label = metadata.get("label") or metadata.get("id") or "context"
        source = metadata.get("source") or "graphrag"
        node_type = metadata.get("type") or "context"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"- [{source}/{node_type}] {label}: {content[:700]}")
    for path in graphrag_context.get("graph_paths") or []:
        source_label = path.get("source_label") or path.get("source")
        target_label = path.get("target_label") or path.get("target")
        lines.append(f"- path: {source_label} --{path.get('type') or 'related'}--> {target_label}")
    return "\n".join(lines)


class DeepSeekAPIClient:
    """DeepSeek chat-completions client for lecture, QA, and exercise generation."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        if not str(api_key or "").strip():
            load_root_env()
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
        self.model = DEFAULT_DEEPSEEK_MODEL

    async def generate_lecture(self, graph_data: Dict, chapter_data: Dict, style: str = "引导式教学") -> str:
        prompt = self._build_lecture_prompt(graph_data, chapter_data, style)
        lecture = expand_formula_references(
            await self._call_deepseek(
                prompt,
                max_tokens=5200,
                system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
                read_timeout_seconds=_generation_read_timeout(),
            ),
            expand_labels=True,
        )
        lecture = clean_generated_lecture_output(lecture)
        if self._lecture_needs_completion(lecture):
            repair_prompt = f"""
请基于原始任务重写一份完整授课文案，不要只补几句话。

原始任务：
{prompt}

上一次输出不完整或结构不足：
{lecture}

必须满足：
1. 输出完整可直接授课的 Markdown 文案。
2. 至少覆盖导入、核心概念、关系链路、例题/推导、课堂提问、易错点、总结。
3. 不要输出题纲、备注、自检说明或 JSON。
4. 保留知识图谱/章节证据约束，不要扩展未授权内容。
"""
            lecture = expand_formula_references(
                await self._call_deepseek(
                    repair_prompt,
                    max_tokens=5200,
                    system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
                    read_timeout_seconds=_generation_read_timeout(),
                ),
                expand_labels=True,
            )
            lecture = clean_generated_lecture_output(lecture)
        return lecture

    @staticmethod
    def _lecture_needs_completion(text: str) -> bool:
        content = str(text or "").strip()
        if len(content) < 900:
            return True
        required_markers = ["导入", "核心", "关系", "例", "提问", "易错", "总结"]
        present = sum(1 for marker in required_markers if marker in content)
        if present < 5:
            return True
        trailing_markers = ("...", "……", "如下", "包括", "首先", "例如")
        return content.endswith(trailing_markers)

    async def answer_question(self, graph_data: Dict, question: str, search_results: Optional[List[Dict]] = None) -> str:
        prompt = self._build_qa_prompt(graph_data, question, search_results)
        return expand_formula_references(
            await self._call_deepseek(prompt, max_tokens=1800, system_prompt=QA_SYSTEM_PROMPT),
            expand_labels=True,
        )

    async def natural_supplement(self, original_text: str, supplement: str) -> str:
        prompt = f"""
请将以下补充内容自然地融入原文中。

原文：
{original_text}

补充内容：
{supplement}

要求：
1. 不要使用生硬的过渡词。
2. 保持原文的逻辑结构和语气。
3. 输出直接可用的最终文本，不要附加说明。
"""
        return await self._call_deepseek(prompt)

    async def generate_exercises(
        self,
        chapter_title: str,
        chapter_content: str,
        count: int = 5,
        graph_data: Optional[Dict] = None,
        feedback_guidance: Optional[str] = None,
    ) -> Dict:
        chapter_content = expand_formula_references(chapter_content)
        chapter_data = {"title": chapter_title, "content": chapter_content}
        evidence = evidence_from_graph(
            graph_data,
            query=f"{chapter_title}\n{chapter_content[:800]}",
            chapter_data=chapter_data,
            limit=8,
        )
        if chapter_content.strip():
            chapter_evidence = {
                "index": 1,
                "id": "chapter_content",
                "label": chapter_title,
                "type": "chapter_content",
                "content": chapter_content[:1200],
                "source": "chapter",
            }
            evidence = [chapter_evidence] + [
                item for item in evidence if isinstance(item, dict) and item.get("id") != "chapter_content"
            ]
        relations = relation_evidence_from_graph(graph_data, evidence)
        learning_plan = build_learning_plan(
            query=chapter_title,
            evidence=evidence,
            relations=relations,
            learner_intent="practice",
            learning_level="beginner",
            task="exercise",
            chapter_data=chapter_data,
        )
        requirements = [
            f"Generate {max(1, count)} natural multiple-choice questions for students, similar to NotebookLM or ChatGPT study quizzes.",
            "Use the chapter content first. Use graph evidence only as supporting context; do not turn evidence constraints into question text.",
            "Each question must test one clear concept, formula, variable meaning, relation, or condition from the material.",
            "Prefer conceptual understanding questions: ask why a condition matters, what a theorem implies, how two quantities relate, or what would change if an assumption changes.",
            "Do not make the whole quiz definition recall. At least 60% of questions should test reasoning about implications, conditions, or relationships.",
            "Do not ask generic meta-questions such as 'Which statement is directly stated in the material?', 'What is this?', or 'Which option is correct?'.",
            "Do not use repetitive stems such as 'Which option best explains ...' for every question; vary the wording naturally.",
            "Each question must have exactly four concise options and one correct answer. Options should answer the same type of thing the question asks.",
            "Keep each option under 14 English words or 36 Chinese characters unless it is a formula.",
            "Keep the correct option similar in length and style to the three distractors. Do not make the correct option a long source sentence while the distractors are short fragments.",
            "For concept questions, options should usually be short answer phrases of comparable length, not full paragraphs.",
            "If a question asks for a formula, all four options must be formula-like expressions. If it asks for a definition or relation, do not use unrelated formulas as distractors.",
            "Do not generate more than one formula-recognition question unless formulas are the explicit focus of the chapter.",
            "When equations are relevant, use the actual LaTeX formula from the source, not only an equation number or placeholder.",
            "Do not reuse the same four options across different questions.",
            "Keep English source wording, formulas, variables, and technical terms in English. Use Chinese only as light explanation when helpful.",
            "Return valid JSON only with this shape: {\"exercises\":[{\"id\":\"ex_1\",\"question\":\"...\",\"options\":[\"A. ...\",\"B. ...\",\"C. ...\",\"D. ...\"],\"correct_answer\":\"A\",\"explanation\":\"...\"}]}",
        ]
        if feedback_guidance:
            requirements.append("教师历史赞踩反馈（用于本次生成方向，不是模型训练）：\n" + feedback_guidance)

        prompt = build_constrained_generation_prompt(
            task_title="生成课程材料测验题",
            user_input=chapter_title,
            source_content=chapter_content,
            learning_plan=learning_plan,
            requirements=requirements,
        )
        response = await self._call_deepseek(
            prompt,
            max_tokens=4200,
            system_prompt=KG_CONSTRAINED_SYSTEM_PROMPT,
            read_timeout_seconds=_exercise_generation_read_timeout(),
        )
        try:
            payload = json.loads(_strip_json_fence(response))
            if isinstance(payload, list):
                return {"exercises": payload}
            if isinstance(payload, dict) and isinstance(payload.get("exercises"), list):
                return payload
            if isinstance(payload, dict) and "id" not in payload:
                payload["id"] = f"ex_{chapter_title.replace(' ', '_')}_1"
            return payload
        except json.JSONDecodeError as exc:
            raise ValueError(f"DeepSeek did not return valid exercise JSON: {response[:300]}") from exc
    def _build_lecture_prompt(self, graph_data: Dict, chapter_data: Dict, style: str) -> str:
        chapter_data = dict(chapter_data or {})
        title = chapter_data.get("title", "未命名章节")
        content = expand_formula_references(chapter_data.get("content", ""))
        graph_context = str(chapter_data.get("graph_context") or "").strip()
        teacher_guidance = str(chapter_data.get("teacher_guidance") or "").strip()
        chapter_data["content"] = content
        graphrag_context = chapter_data.get("graphrag_context") if isinstance(chapter_data.get("graphrag_context"), dict) else None
        if graphrag_context is None:
            source_node_ids = chapter_data.get("source_node_ids")
            try:
                graphrag_context = build_graphrag_context(
                    f"{title}\n{content[:1200]}",
                    seed_node_ids=source_node_ids if isinstance(source_node_ids, list) else None,
                    limit=8,
                )
            except Exception:
                graphrag_context = None
        if graphrag_context and graphrag_context.get("llm_context"):
            evidence = evidence_from_rag(graphrag_context.get("llm_context") or [], limit=10)
            graph_data = graphrag_context.get("graph_data") or graph_data
            if not graph_context:
                graph_context = _format_graphrag_context_for_prompt(graphrag_context)
        else:
            evidence = evidence_from_graph(graph_data, query=f"{title}\n{content[:1200]}", chapter_data=chapter_data, limit=10)
        if content.strip():
            chapter_evidence = {
                "index": 1,
                "id": "chapter_content",
                "label": title,
                "type": "chapter_content",
                "content": content[:1800],
                "source": "chapter",
            }
            evidence = [chapter_evidence] + [
                item for item in evidence if isinstance(item, dict) and item.get("id") != "chapter_content"
            ]
        relations = relation_evidence_from_graph(graph_data, evidence)
        learning_plan = build_learning_plan(
            query=title,
            evidence=evidence,
            relations=relations,
            learner_intent="explain",
            learning_level="beginner",
            task="lecture",
            chapter_data=chapter_data,
        )
        requirements = [
            *build_lecture_gc_dpg_requirements(style),
            "Generate a complete lecture script with Markdown level-2/level-3 headings, not just an outline.",
            "Cover: opening, core concept explanation, relation path, example or derivation, classroom questions, common mistakes, and closing summary.",
            "Use an evidence-first order: start from chapter content and retrieved evidence, then connect only the few necessary terms into a coherent teaching flow.",
            "When graph relation paths are provided, use them to decide the teaching order instead of listing graph entities.",
            "When formula derivation or scoped symbol context is provided, explain only the symbols in the current chapter/formula scope and mention immediate derivation dependencies where useful.",
            "Keep the length around 1400-2200 Chinese characters when possible; if the material is complex, prioritize completeness and do not cut off abruptly.",
            "Output only the lecture script itself.",
        ]
        if teacher_guidance:
            requirements.append(
                "Teacher guidance for emphasis, selection, and pacing. Treat it as generation guidance only; it must not override source/graph facts or become quoted textbook content:\n"
                + teacher_guidance[:1600]
            )
        return build_constrained_generation_prompt(
            task_title="生成授课文案",
            user_input=title,
            source_content=f"{content}\n\n{graph_context}" if graph_context else content,
            learning_plan=learning_plan,
            requirements=requirements,
        )

    def _build_qa_prompt(self, graph_data: Dict, question: str, search_results: Optional[List[Dict]] = None) -> str:
        if search_results:
            evidence = evidence_from_rag(search_results, limit=8)
            relations = []
        else:
            evidence = evidence_from_graph(graph_data, query=question, limit=8)
            relations = relation_evidence_from_graph(graph_data, evidence)
        learning_plan = build_learning_plan(
            query=question,
            evidence=evidence,
            relations=relations,
            learner_intent=None,
            learning_level="beginner",
            task="qa",
        )

        return f"""请回答学生问题。

学生问题：
{question}

可参考的课程/图谱上下文：
{format_evidence(learning_plan.get("evidence") or [])}

回答规则：
1. 先直接回答问题，不要先说"依据不足"。
2. 如果问题是公式、符号、定义、推导、例题，请给出公式本身，并用 LaTeX 写清楚，例如 `$...$` 或 `$$...$$`。
3. 如果上下文没有命中，但这是常见数学、机器学习或课程基础知识，可以用通用知识回答，并标注"通用说明"；不要空泛拒答。
4. 如果上下文命中英文材料，英文术语、公式、变量名和关键定义保持英文。
5. 只有在问题询问某个课程私有材料、而上下文完全没有该材料时，才说明需要补充原文。
6. 可以引用依据编号，但不要输出 LearningPlan、自检过程或内部约束说明。"""

    async def _call_deepseek(
        self,
        prompt: str,
        max_tokens: int = 2000,
        system_prompt: Optional[str] = None,
        read_timeout_seconds: object = _DEFAULT_TIMEOUT,
        image_urls: Optional[List[str]] = None,
    ) -> str:
        if not self.api_key:
            raise ValueError("未配置 DeepSeek API 密钥，请设置 DEEPSEEK_API_KEY 环境变量")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        user_content: Any = prompt
        valid_image_urls = [str(value).strip() for value in (image_urls or []) if str(value).strip()]
        if valid_image_urls:
            user_content = [{"type": "text", "text": prompt}]
            user_content.extend(
                {"type": "image_url", "image_url": {"url": image_url}}
                for image_url in valid_image_urls[:3]
            )
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt or "You are a concise, reliable teaching assistant."},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
        }

        if read_timeout_seconds is _DEFAULT_TIMEOUT:
            read_timeout_value = _parse_read_timeout(os.getenv("DEEPSEEK_READ_TIMEOUT_SECONDS"), 90.0)
        else:
            read_timeout_value = read_timeout_seconds
        timeout = httpx.Timeout(connect=10.0, read=read_timeout_value, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            if response.status_code != 200:
                raise Exception(_format_deepseek_http_error(response))
            result = response.json()
            return _extract_deepseek_response_text(result)


ClaudeAPIClient = DeepSeekAPIClient
_deepseek_client: Optional[DeepSeekAPIClient] = None


def get_deepseek_client() -> DeepSeekAPIClient:
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = DeepSeekAPIClient()
    return _deepseek_client


def get_claude_client() -> DeepSeekAPIClient:
    return get_deepseek_client()


def _strip_json_fence(text: str) -> str:
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = clean.removeprefix("```json").removeprefix("```").strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
    first_obj = clean.find("{")
    last_obj = clean.rfind("}")
    first_arr = clean.find("[")
    last_arr = clean.rfind("]")
    if first_obj >= 0 and last_obj > first_obj:
        clean = clean[first_obj:last_obj + 1]
    elif first_arr >= 0 and last_arr > first_arr:
        clean = clean[first_arr:last_arr + 1]
    return clean
