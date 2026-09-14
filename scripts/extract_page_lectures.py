#!/usr/bin/env python3
"""Extract page-labelled transcript text into KGTS slide lecture data.

The source transcript uses one heading per page, for example ``第1页``.
The output is suitable for ``/api/education/save-lecture`` and also keeps a
single merged ``lecture_content`` value for clients that do not use the
per-slide field.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PAGE_HEADING = re.compile(r"^第\s*(\d+)\s*页\s*$")


def extract_pages(source: Path) -> list[tuple[int, str]]:
    text = source.read_text(encoding="utf-8-sig")
    pages: list[tuple[int, str]] = []
    current_index: int | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        match = PAGE_HEADING.match(line.strip())
        if match:
            if current_index is not None:
                pages.append((current_index, "\n".join(current_lines).strip()))
            current_index = int(match.group(1))
            current_lines = []
        elif current_index is not None:
            current_lines.append(line)

    if current_index is not None:
        pages.append((current_index, "\n".join(current_lines).strip()))

    if not pages:
        raise ValueError(f"未找到页标题（例如“第1页”）：{source}")
    expected = list(range(1, len(pages) + 1))
    actual = [index for index, _ in pages]
    if actual != expected:
        raise ValueError(f"页码必须从 1 连续递增，实际为 {actual[:5]}...{actual[-5:]}")
    empty = [index for index, content in pages if not content]
    if empty:
        raise ValueError(f"以下页面没有讲稿内容：{empty}")
    return pages


def build_payload(pages: list[tuple[int, str]], title: str, speech_rate_cpm: int) -> dict[str, Any]:
    slide_lectures = [
        {
            "index": index,
            "title": f"第{index}页",
            "lecture": content,
            "estimated_chars": len(content),
            "estimated_duration_seconds": round(len(content) / speech_rate_cpm * 60),
            "generation_status": "imported_transcript",
            "source_node_ids": [],
            "sources": [],
            "graph_paths": [],
            "formula_context": [],
        }
        for index, content in pages
    ]
    lecture_content = "\n\n---\n\n".join(
        f"## 第 {item['index']} 页：{item['title']}\n\n{item['lecture']}"
        for item in slide_lectures
    )
    estimated_chars = sum(item["estimated_chars"] for item in slide_lectures)
    return {
        "title": title,
        "source": "D:\\download\\bimsa-ft-02.txt",
        "page_count": len(slide_lectures),
        "speech_rate_cpm": speech_rate_cpm,
        "estimated_chars": estimated_chars,
        "estimated_duration_seconds": round(estimated_chars / speech_rate_cpm * 60),
        "lecture_content": lecture_content,
        "slide_lectures": slide_lectures,
        "lecture_pacing": {
            "target_duration_minutes": round(estimated_chars / speech_rate_cpm, 2),
            "speech_rate_cpm": speech_rate_cpm,
            "estimated_chars": estimated_chars,
            "estimated_duration_seconds": round(estimated_chars / speech_rate_cpm * 60),
            "slide_count": len(slide_lectures),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title", default="bimsa-ft-02")
    parser.add_argument("--speech-rate-cpm", type=int, default=250)
    args = parser.parse_args()
    if args.speech_rate_cpm <= 0:
        parser.error("--speech-rate-cpm 必须为正整数")
    payload = build_payload(extract_pages(args.source), args.title, args.speech_rate_cpm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("title", "page_count", "estimated_chars", "estimated_duration_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
