from __future__ import annotations

import base64
import asyncio
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import KGTS.education.courseware_editor as editor
import KGTS.education.beamer_full_router as beamer_router
import KGTS.education.router as education_router
from KGTS.models.education import PreviewTexRequest
from KGTS.education.ppt_parser import MAX_INLINE_IMAGE_BYTES, build_ppt_lecture_prompt_data, parse_courseware


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _overlap_area(left: dict, right: dict) -> float:
    left_x2 = float(left["x"]) + float(left["width"])
    left_y2 = float(left["y"]) + float(left["height"])
    right_x2 = float(right["x"]) + float(right["width"])
    right_y2 = float(right["y"]) + float(right["height"])
    return max(0.0, min(left_x2, right_x2) - max(float(left["x"]), float(right["x"]))) * max(
        0.0,
        min(left_y2, right_y2) - max(float(left["y"]), float(right["y"])),
    )


class CoursewareEditorTest(unittest.TestCase):
    def test_build_editable_model_extracts_objects_and_assets(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Figure 26.2}
  \begin{itemize}
    \item Selection response
  \end{itemize}
  \[
  R = h^2 S
  \]
  \includegraphics[width=0.5\textwidth]{fig/chart}
\end{frame}
\end{document}
"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("main.tex", tex)
            archive.writestr("fig/chart.png", TINY_PNG)

        parsed = parse_courseware(payload.getvalue(), "lecture.zip")
        prompt = build_ppt_lecture_prompt_data(parsed)
        model = editor.build_editable_model(parsed, prompt)

        self.assertEqual(model["slide_count"], 1)
        self.assertTrue(model["assets"])
        objects = model["slides"][0]["objects"]
        object_types = {item["type"] for item in objects}
        self.assertIn("title", object_types)
        self.assertIn("richText", object_types)
        self.assertIn("equation", object_types)
        self.assertIn("image", object_types)
        body = next(item for item in objects if item["type"] == "richText")
        equation = next(item for item in objects if item["type"] == "equation")
        image = next(item for item in objects if item["type"] == "image")
        self.assertLess(body["bbox"]["height"], 140)
        self.assertGreater(equation["bbox"]["y"], body["bbox"]["y"] + body["bbox"]["height"])
        self.assertEqual(equation["style"]["fontSize"], 24)
        self.assertEqual(equation["style"]["lineHeight"], 1.25)
        self.assertGreater(image["bbox"]["x"], equation["bbox"]["x"])
        self.assertEqual(image["width_ratio"], 0.5)

    def test_assets_from_zip_keeps_aliases_and_data_uri(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("images/Figure-26.2.PNG", TINY_PNG)

        assets = editor.assets_from_upload(payload.getvalue(), "figures.zip")
        asset = next(iter(assets.values()))

        self.assertEqual(asset["source_path"], "images/Figure-26.2.PNG")
        self.assertIn("Figure-26.2", asset["aliases"])
        self.assertTrue(asset["data_uri"].startswith("data:image/png;base64,"))

    def test_missing_tex_image_ref_becomes_placeholder_object(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Missing Figure}
  \includegraphics[width=0.5\textwidth]{fig/chart}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        prompt = build_ppt_lecture_prompt_data(parsed)
        model = editor.build_editable_model(parsed, prompt)
        image_object = next(item for item in model["slides"][0]["objects"] if item["type"] == "placeholder")
        asset = model["assets"][image_object["asset_id"]]

        self.assertEqual(parsed["missing_image_refs"], ["fig/chart"])
        self.assertIsNone(asset["data_uri"])
        self.assertEqual(image_object["source_path"], "fig/chart")
        self.assertEqual(image_object["tex_ref"], "fig/chart")

    def test_title_slide_images_use_cover_layout(self):
        tex = r"""
\documentclass{ctexbeamer}
\usepackage{graphicx}
\usepackage{tikz}
\title[]{ Evolutionary Theory on\\ Polygenic Trait}
\subtitle{XII - Long-term Response}
\author{Qi WU}
\date{2026-5-26}
\setbeamertemplate{title page}{%
  \includegraphics[height=39pt, keepaspectratio]{fig/logo-a.png}
  \includegraphics[height=39pt, keepaspectratio]{fig/logo-b.png}
}
\begin{document}
{
\setbeamertemplate{footline}{%
  \makebox[\paperwidth][l]{\includegraphics[width=\paperwidth]{fig/footer.png}}%
}
\begin{frame}
  \titlepage
\end{frame}
}
\end{document}
"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("main.tex", tex)
            archive.writestr("fig/logo-a.png", TINY_PNG)
            archive.writestr("fig/logo-b.png", TINY_PNG)
            archive.writestr("fig/footer.png", TINY_PNG)

        parsed = parse_courseware(payload.getvalue(), "lecture.zip")
        prompt = build_ppt_lecture_prompt_data(parsed)
        model = editor.build_editable_model(parsed, prompt)
        images = [item for item in model["slides"][0]["objects"] if item["type"] == "image"]

        self.assertEqual(len(images), 3)
        self.assertLess(images[0]["bbox"]["y"], 10)
        self.assertLess(images[1]["bbox"]["y"], 10)
        self.assertGreater(images[2]["bbox"]["y"], 490)
        self.assertEqual(images[2]["bbox"]["width"], 1000.0)

    def test_serialize_model_to_tex_round_trips_layout_metadata(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Canvas}
  \begin{itemize}
    \item Old text
  \end{itemize}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        prompt = build_ppt_lecture_prompt_data(parsed)
        model = editor.build_editable_model(parsed, prompt)
        body = next(item for item in model["slides"][0]["objects"] if item["type"] == "richText")
        body["text"] = "- New text"
        body["bbox"]["x"] = 222

        serialized = editor.serialize_editable_model_to_tex(model, title="Deck")
        reparsed = parse_courseware(serialized.encode("utf-8"), "edited.tex")
        slide = build_ppt_lecture_prompt_data(reparsed)["slide_details"][0]

        self.assertIn("New text", serialized)
        self.assertIn("% KGTS_LAYOUT", serialized)
        self.assertEqual(slide["layout"]["canvas"]["items"][1]["x"], 222)

    def test_layout_metadata_keeps_manual_canvas_positions(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Manual}
% KGTS_LAYOUT {"items":[{"id":"title","type":"title","x":48,"y":34,"width":904,"height":58},{"id":"body","type":"content","x":222,"y":130,"width":444,"height":80}]}
\begin{itemize}
\item Manual text
\end{itemize}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "manual.tex")
        prompt = build_ppt_lecture_prompt_data(parsed)
        model = editor.build_editable_model(parsed, prompt)
        body = next(item for item in model["slides"][0]["objects"] if item["type"] == "richText")

        self.assertEqual(body["bbox"]["x"], 222)
        self.assertEqual(body["bbox"]["width"], 444)

    def test_custom_image_macro_and_callout_do_not_overlap_content(self):
        tex = r"""
\documentclass{beamer}
\usepackage{tikz}
\newcommand{\safecontentimage}[1]{\IfFileExists{#1}{\includegraphics[width=0.7\textwidth]{#1}}{\fbox{\parbox[c][0.34\textheight][c]{0.7\textwidth}{Missing image\\\texttt{\detokenize{#1}}}}}}
\begin{document}
\begin{frame}{Figure 27.1}
  \centering
  \safecontentimage{fig/27.1.png}
  \begin{center}
    \parbox{0.95\textwidth}{\scriptsize Fisher caption with inline math $r$, $x$, and $\theta$.}
  \end{center}
\end{frame}
\begin{frame}{Fisher Scaling Parameter}
  \begin{itemize}
    \item Inline variables $n$, $p_b$, and $x$ stay in the body.
  \end{itemize}
  \[
    x = \frac{r\sqrt{n}}{2d}
  \]
  \[
    p_b = 1 - \Phi(x)
  \]
  \begin{tikzpicture}[remember picture, overlay]
    \node[rectangle callout, draw=blue, fill=white, text width=2.20cm, align=center] at ([xshift=10.41cm,yshift=-3.63cm] current page.north west)
      {$p_b$ 仅依赖于 $x$};
  \end{tikzpicture}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        prompt = build_ppt_lecture_prompt_data(parsed)
        model = editor.build_editable_model(parsed, prompt)

        figure_objects = model["slides"][0]["objects"]
        figure_types = [item["type"] for item in figure_objects]
        self.assertEqual(figure_types.count("image"), 1)
        self.assertEqual(figure_types.count("placeholder"), 0)
        self.assertNotIn("equation", figure_types)
        figure_image = next(item for item in figure_objects if item["type"] == "image")
        self.assertEqual(figure_image["source_path"], "figures/fig_0132.png")
        figure_body = next(item for item in figure_objects if item["type"] == "richText")
        self.assertGreaterEqual(figure_body["bbox"]["y"], figure_image["bbox"]["y"] + figure_image["bbox"]["height"])

        callout_objects = model["slides"][1]["objects"]
        self.assertEqual([item["type"] for item in callout_objects].count("equation"), 2)
        callout = next(item for item in callout_objects if item["type"] == "callout")
        for item in callout_objects:
            if item["id"] == callout["id"] or item["type"] == "title":
                continue
            self.assertEqual(_overlap_area(callout["bbox"], item["bbox"]), 0)

    def test_project_save_and_pptx_export(self):
        old_project_dir = editor.PROJECT_DIR
        old_artifact_dir = editor.ARTIFACT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            editor.PROJECT_DIR = Path(temp_dir) / "projects"
            editor.ARTIFACT_DIR = Path(temp_dir) / "artifacts"
            try:
                model = editor.build_editable_model_from_slide_details(
                    [
                        {
                            "index": 1,
                            "title": "One",
                            "content": "- A",
                            "body_texts": ["A"],
                            "images": [],
                            "tables": [],
                        }
                    ],
                    title="Deck",
                )
                project = editor.save_courseware_project({"title": "Deck", "editable_model": model})
                loaded = editor.load_courseware_project(project["id"])
                artifact = editor.build_pptx_artifact_from_editable_model("Deck", model)

                self.assertEqual(loaded["title"], "Deck")
                self.assertTrue(Path(artifact["pptx_path"]).exists())
                self.assertTrue(Path(artifact["tex_path"]).exists())
            finally:
                editor.PROJECT_DIR = old_project_dir
                editor.ARTIFACT_DIR = old_artifact_dir

    def test_project_save_preserves_uploaded_source_tex_from_model(self):
        old_project_dir = editor.PROJECT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            editor.PROJECT_DIR = Path(temp_dir) / "projects"
            try:
                model = editor.build_editable_model_from_slide_details(
                    [{"index": 1, "title": "One", "content": "A"}],
                    title="Deck",
                    source_tex="\\documentclass{beamer}\n\\begin{document}\n\\end{document}",
                )

                project = editor.save_courseware_project({"title": "Deck", "editable_model": model})
                loaded = editor.load_courseware_project(project["id"])

                self.assertIn("\\documentclass{beamer}", loaded["tex_content"])
                self.assertEqual(loaded["editable_model"]["source_tex"], loaded["tex_content"])
            finally:
                editor.PROJECT_DIR = old_project_dir

    def test_preview_tex_reuses_uploaded_image_assets_after_edit(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Edited title}
  \includegraphics[width=0.5\textwidth]{fig/chart}
\end{frame}
\end{document}
"""
        asset_id = "asset-existing-image"
        data_uri = "data:image/png;base64," + base64.b64encode(TINY_PNG).decode("ascii")
        asset_map = {
            asset_id: {
                "id": asset_id,
                "name": "chart.png",
                "source_path": "fig/chart.png",
                "tex_ref": "fig/chart",
                "mime_type": "image/png",
                "data_uri": data_uri,
                "aliases": ["fig/chart.png", "fig/chart", "chart.png", "chart"],
            }
        }
        captured = {}
        original_render = education_router._render_courseware_pdf_pages

        def fake_render(tex_content, assets, namespace=None):
            captured["tex_content"] = tex_content
            captured["assets"] = assets
            return [{"page_index": 0, "image": "/rendered/page.png"}], ""

        education_router._render_courseware_pdf_pages = fake_render
        try:
            result = asyncio.run(
                education_router.preview_tex(
                    PreviewTexRequest(tex_content=tex, filename="edited.tex", asset_map=asset_map)
                )
            )
        finally:
            education_router._render_courseware_pdf_pages = original_render

        self.assertEqual(result["missing_image_refs"], [])
        self.assertEqual(result["slides"][0]["images"][0]["data_uri"], data_uri)
        image_object = next(item for item in result["editable_model"]["slides"][0]["objects"] if item["type"] == "image")
        self.assertEqual(result["editable_model"]["assets"][image_object["asset_id"]]["data_uri"], data_uri)
        self.assertEqual(captured["tex_content"], tex)
        self.assertTrue(any(asset.get("data_uri") == data_uri for asset in captured["assets"].values()))
        with tempfile.TemporaryDirectory() as temp_dir:
            beamer_router._materialize_latex_assets(
                Path(temp_dir),
                tex,
                education_router._courseware_asset_urls_from_map(captured["assets"]),
            )
            self.assertEqual((Path(temp_dir) / "fig" / "chart.png").read_bytes(), TINY_PNG)

    def test_preview_tex_reuses_persisted_oversized_zip_image_after_edit(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Large Figure}
  \includegraphics[width=0.5\textwidth]{fig/chart}
\end{frame}
\end{document}
"""
        image_bytes = TINY_PNG + b"x" * (MAX_INLINE_IMAGE_BYTES + 1)
        old_upload_dir = beamer_router.UPLOAD_DIR
        old_router_upload_dir = education_router.UPLOAD_DIR
        old_asset_upload_dir = education_router.COURSEWARE_ASSET_UPLOAD_DIR
        original_render = education_router._render_courseware_pdf_pages
        captured = {}

        def fake_render(tex_content, assets, namespace=None):
            captured["assets"] = assets
            return [], ""

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            upload_dir = workspace / "uploads"
            zip_path = workspace / "deck.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("main.tex", tex)
                archive.writestr("fig/chart.png", image_bytes)
            parsed = parse_courseware(zip_path.read_bytes(), "deck.zip")
            model = editor.build_editable_model(parsed, build_ppt_lecture_prompt_data(parsed))
            beamer_router.UPLOAD_DIR = upload_dir
            education_router.UPLOAD_DIR = upload_dir
            education_router.COURSEWARE_ASSET_UPLOAD_DIR = upload_dir / "courseware"
            education_router._render_courseware_pdf_pages = fake_render
            try:
                assets = education_router._persist_courseware_zip_assets(zip_path, model["assets"], "large-image")
                result = asyncio.run(education_router.preview_tex(PreviewTexRequest(tex_content=tex, asset_map=assets)))
                self.assertEqual(result["missing_image_refs"], [])
                persisted = next(asset for asset in assets.values() if asset.get("source_path") == "fig/chart.png")
                self.assertTrue(str(persisted.get("path") or "").startswith("/beamer-generator/uploads/courseware/"))
                with tempfile.TemporaryDirectory() as render_dir:
                    beamer_router._materialize_latex_assets(
                        Path(render_dir), tex, education_router._courseware_asset_urls_from_map(captured["assets"])
                    )
                    self.assertEqual((Path(render_dir) / "fig" / "chart.png").read_bytes(), image_bytes)
            finally:
                education_router._render_courseware_pdf_pages = original_render
                beamer_router.UPLOAD_DIR = old_upload_dir
                education_router.UPLOAD_DIR = old_router_upload_dir
                education_router.COURSEWARE_ASSET_UPLOAD_DIR = old_asset_upload_dir

    def test_project_save_retains_slide_lectures(self):
        old_project_dir = editor.PROJECT_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            editor.PROJECT_DIR = Path(temp_dir) / "projects"
            try:
                project = editor.save_courseware_project(
                    {
                        "title": "Deck",
                        "editable_model": {"slides": []},
                        "slide_lectures": [{"index": 1, "slide_id": "slide-1", "lecture": "原始文案"}],
                    }
                )
                loaded = editor.load_courseware_project(project["id"])
                self.assertEqual(loaded["slide_lectures"][0]["lecture"], "原始文案")
            finally:
                editor.PROJECT_DIR = old_project_dir


if __name__ == "__main__":
    unittest.main()
