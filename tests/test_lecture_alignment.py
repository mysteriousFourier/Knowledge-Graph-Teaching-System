import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from KGTS.education.lecture_alignment import extract_materials, generate_aligned_lectures, reflow_pages, speech_chars


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    return output.getvalue()


def slides(count):
    return [{"index": i, "slide_id": f"new-{i}", "title": f"遗传漂变 第{i}步", "raw_text": "遗传漂变与随机抽样"}
            for i in range(1, count + 1)]


class LectureMaterialTests(unittest.TestCase):
    def test_text_encodings_and_timestamps_survive_import(self):
        text = "[00:01:20] 我们先看遗传漂变。"
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            self.assertEqual(extract_materials(text.encode(encoding), "原稿.srt")[0]["text"], text)

    def test_zip_keeps_all_candidate_sources_and_roles(self):
        materials = extract_materials(archive({"第一讲.txt": "遗传漂变。", "第二讲.md": "自然选择。"}), "原稿.zip", "reference")
        self.assertEqual(len(materials), 2)
        self.assertEqual(materials[1]["name"], "原稿.zip/第二讲.md")
        self.assertTrue(all(m["role"] == "reference" for m in materials))

    def test_docx_preserves_paragraph_and_table_text(self):
        data = archive({"word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>原来的问法？</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格里的回答。</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
        self.assertEqual(extract_materials(data, "原稿.docx")[0]["text"], "原来的问法？\n表格里的回答。")

    def test_invalid_archives_reject_instead_of_silently_skipping_files(self):
        for files in ({"../escape.txt": "不可写出"}, {"valid.txt": "有效", "recording.mp3": b"audio"}):
            with self.assertRaises(ValueError):
                extract_materials(archive(files), "materials.zip")

    def test_empty_pdf_page_is_not_presented_as_read_material(self):
        from pypdf import PdfWriter
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=200)
        writer.write(output)
        with self.assertRaisesRegex(ValueError, "OCR"):
            extract_materials(output.getvalue(), "扫描原稿.pdf")


class LectureWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_material_upload_endpoint_accepts_multiple_files_and_returns_readable_errors(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from KGTS.education import router
        app = FastAPI()
        app.include_router(router.router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/education/lecture-materials", data={"role": "transcript"},
                files=[("files", ("one.txt", "遗传漂变。".encode("utf-8"))), ("files", ("two.md", "随机抽样。".encode("utf-8")))])
            self.assertEqual(response.status_code, 200)
            self.assertEqual([m["text"] for m in response.json()["materials"]], ["遗传漂变。", "随机抽样。"])
            response = await client.post("/api/education/lecture-materials", files={"files": ("audio.mp3", b"audio")})
            self.assertEqual(response.status_code, 400)
            self.assertIn("转写", response.json()["detail"])

    async def test_more_and_fewer_pages_keep_every_original_character(self):
        original = [{"index": 1, "lecture": "我们先看频率。为什么会变化？随机抽样会带来差异。"},
                    {"index": 2, "lecture": "再看方差。这个推导保留全部系数。"}]
        text = "".join(s["lecture"] for s in original)
        for count in (1, 3):
            async def boundary_response(prompt, **kwargs):
                units = json.loads(prompt.split("原稿片段（0-based）：\n", 1)[1])
                return json.dumps({"ends": [len(units)] if count == 1 else [1, 3, len(units)]})
            client = SimpleNamespace(_call_deepseek=AsyncMock(side_effect=boundary_response))
            result = await reflow_pages(client, slides(count), original, lambda *_: None)
            self.assertEqual("".join(s["lecture"] for s in result).replace("\n", ""), text)
            self.assertEqual([s["slide_id"] for s in result], [f"new-{i}" for i in range(1, count + 1)])

    async def test_invalid_or_truncated_boundaries_cannot_drop_old_content(self):
        existing = [{"index": 1, "lecture": "第一句话。第二句话。"}]
        for ends in ([1], [2, 1], [0, 1], [False, 2]):
            client = SimpleNamespace(_call_deepseek=AsyncMock(return_value=json.dumps({"ends": ends})))
            with self.assertRaises(ValueError):
                await reflow_pages(client, slides(2), existing, lambda *_: None)
            self.assertEqual(existing[0]["lecture"], "第一句话。第二句话。")

    async def test_new_page_is_supplemented_labeled_and_uses_slide_image(self):
        client = SimpleNamespace(_call_deepseek=AsyncMock(side_effect=['{"ends":[1,1]}', "再看这个箭头，它指向随机抽样后的频率。",
                                                                     "再看这个箭头，它指向随机抽样后的频率。 "]))
        original = [{"index": 1, "lecture": "我们先看频率。"}]
        result = await generate_aligned_lectures(client, slides(2), [], workflow="reflow", existing=original,
            title="测试", guidance="", duration=10, speech_rate=250, image_urls=lambda _: ["https://example.test/page.png"])
        self.assertTrue(result["success"])
        self.assertEqual(result["slide_lectures"][0]["lecture"], original[0]["lecture"])
        self.assertEqual(result["slide_lectures"][1]["alignment"]["source"], "simulated")
        self.assertGreater(result["lecture_alignment_audit"]["chars"], speech_chars(original[0]["lecture"]))
        self.assertEqual(client._call_deepseek.call_args.kwargs["image_urls"], ["https://example.test/page.png"])

    async def test_alignment_keeps_provenance_without_inventing_timestamps(self):
        async def response(prompt, **kwargs):
            if "候选原稿段落（文件名不代表匹配）：\n" in prompt:
                candidates = json.loads(prompt.split("候选原稿段落（文件名不代表匹配）：\n", 1)[1])
                return json.dumps({"pages": [{"index": 1, "source_ids": [candidates[0]["id"]], "reason": "概念与公式匹配"},
                                             {"index": 2, "source_ids": [], "reason": "本次原稿未覆盖"}]})
            return "我们先看这个频率。随机抽样为什么会产生差异？因为每次抽到的等位基因可能不同。"
        client = SimpleNamespace(_call_deepseek=AsyncMock(side_effect=response))
        result = await generate_aligned_lectures(client, slides(2), [{"name": "lecture.txt", "role": "transcript", "text": "遗传漂变与随机抽样。"}],
            workflow="align", existing=[], title="测试", guidance="", duration=10, speech_rate=250, image_urls=lambda _: [])
        self.assertTrue(result["success"])
        first, second = result["slide_lectures"]
        self.assertEqual(first["alignment"]["source_files"], ["lecture.txt"])
        self.assertEqual(first["alignment"]["timestamps"], [])
        self.assertEqual(second["alignment"]["source"], "simulated")
        self.assertIn("## 第2页", result["lecture_content"])

    async def test_alignment_rejects_fabricated_source_ids_and_missing_pages(self):
        for pages in ([{"index": 1, "source_ids": ["invented"]}, {"index": 2, "source_ids": []}],
                      [{"index": 1, "source_ids": []}]):
            client = SimpleNamespace(_call_deepseek=AsyncMock(return_value=json.dumps({"pages": pages})))
            with self.assertRaises(ValueError):
                await generate_aligned_lectures(client, slides(2), [{"name": "lecture.txt", "role": "transcript", "text": "随机抽样。"}],
                    workflow="align", existing=[], title="测试", guidance="", duration=10, speech_rate=250, image_urls=lambda _: [])

    async def test_failed_supplement_is_not_reported_as_complete(self):
        client = SimpleNamespace(_call_deepseek=AsyncMock(side_effect=['{"ends":[0,1]}', "", ""]))
        result = await generate_aligned_lectures(client, slides(2), [], workflow="reflow", existing=[{"index": 1, "lecture": "原有讲法。"}],
            title="测试", guidance="", duration=10, speech_rate=250, image_urls=lambda _: [])
        self.assertFalse(result["success"])
        self.assertEqual(result["lecture_alignment_audit"]["empty_pages"], [1])
        self.assertIn("原有讲法。", result["lecture_content"])

    async def test_router_workflow_does_not_require_graph_nodes(self):
        from KGTS.education import router
        from KGTS.models.education import GenerateSlideLecturesRequest
        request = GenerateSlideLecturesRequest(slides=slides(1), lecture_workflow="reflow",
            existing_slide_lectures=[{"index": 1, "lecture": "保留这句话。"}])
        client = SimpleNamespace(_call_deepseek=AsyncMock(return_value='{"ends":[1]}'))
        with patch.object(router, "DeepSeekAPIClient", return_value=client), patch.object(router, "build_node_contexts", side_effect=AssertionError("must not load graph")):
            result = await router._generate_slide_lectures_sync(request)
        self.assertTrue(result["success"])
        self.assertEqual(result["slide_lectures"][0]["lecture"], "保留这句话。")


if __name__ == "__main__":
    unittest.main()
