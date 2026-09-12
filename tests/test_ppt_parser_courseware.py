from __future__ import annotations

import base64
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from KGTS.education.ppt_parser import build_ppt_lecture_prompt_data, parse_courseware


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class CoursewareParserTest(unittest.TestCase):
    def test_tex_zip_beamer_preview_is_browsable(self):
        tex = r"""
\documentclass{ctexbeamer}
\usepackage{graphicx}
\usepackage{tikz}
\title[]{ Evolutionary Theory on\\ Polygenic Trait}
\subtitle{XI - Long-term Response: 2. Finite Population Size and Mutation}
\author{Qi WU(吴琦)}
\date{2026-5-19}
\setbeamertemplate{title page}{%
  \includegraphics[height=39pt]{fig/logo.png}
}
\begin{document}
\begin{frame}
  \titlepage
\end{frame}

\begin{frame}{\textbf{26} Long-term Response: 2. Finite Population Size and Mutation}
  \begin{tikzpicture}[remember picture, overlay]
    \node[anchor=north west, font=\small] at ([yshift=-1.3cm] current page.north west){
      \begin{minipage}{0.9\textwidth}
        \begin{itemize}
          \setlength{\itemsep}{0pt}
          \item \textcolor{black}{From neutrality to selection: scaling parameter $4N_e s$}
        \end{itemize}
      \end{minipage}
    };
  \end{tikzpicture}
  \begin{itemize}
    \item<1-> \textcolor{black}{Neutral fixation probability:}
      \[
      \nu(p_0) = p_0 \tikzmark{neut}
      \]
  \end{itemize}
\end{frame}

\begin{frame}{Figure 26.2}
  \centering
  \includegraphics[width=0.6\textwidth]{fig/chart}
\end{frame}
\end{document}
"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("main.tex", tex)
            archive.writestr("fig/logo.png", TINY_PNG)
            archive.writestr("fig/chart.png", TINY_PNG)

        parsed = parse_courseware(payload.getvalue(), "lecture.zip")
        prompt_data = build_ppt_lecture_prompt_data(parsed)
        slides = prompt_data["slide_details"]

        self.assertTrue(parsed["success"])
        self.assertIn("\\begin{frame}", parsed["tex_content"])
        self.assertEqual(parsed["tex_source_file"], "main.tex")
        self.assertEqual(prompt_data["chapter_title"], "Evolutionary Theory on Polygenic Trait")
        self.assertEqual(len(slides), 3)
        self.assertEqual(slides[0]["image_count"], 1)
        self.assertEqual(slides[0]["images"][0]["source_path"], "fig/logo.png")
        self.assertEqual(slides[0]["layout"]["mode"], "title")
        self.assertIn("XI - Long-term Response", slides[0]["content"])

        self.assertEqual(slides[1]["title"], "26 Long-term Response: 2. Finite Population Size and Mutation")
        self.assertIn("- From neutrality to selection: scaling parameter $4N_e s$", slides[1]["content"])
        self.assertIn("$$", slides[1]["content"])
        self.assertNotIn("\\begin{tikzpicture}", slides[1]["content"])
        self.assertNotIn("\\textcolor", slides[1]["content"])
        self.assertNotIn("\\tikzmark", slides[1]["content"])

        self.assertEqual(slides[2]["image_count"], 1)
        self.assertEqual(slides[2]["images"][0]["source_path"], "fig/chart.png")
        self.assertTrue(str(slides[2]["images"][0]["data_uri"]).startswith("data:image/png;base64,"))
        self.assertIn("\\begin{frame}{Figure 26.2}", slides[2]["source_tex"])
        self.assertEqual(
            parsed["tex_content"][slides[2]["source_start"]:slides[2]["source_end"]],
            slides[2]["source_tex"],
        )
        self.assertEqual(slides[2]["layout"]["mode"], "image_only")

    def test_tex_frame_layout_metadata_detects_columns(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Two Column}
  \begin{columns}[T]
    \begin{column}{0.5\textwidth}
      \begin{itemize}
        \item Left text
      \end{itemize}
    \end{column}
    \begin{column}{0.5\textwidth}
      \includegraphics[width=0.75\textwidth]{fig/chart}
    \end{column}
  \end{columns}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertEqual(slide["layout"]["mode"], "columns")
        self.assertTrue(slide["layout"]["has_columns"])
        self.assertEqual(slide["image_count"], 0)
        self.assertEqual(slide["layout"]["columns"][1]["image_count"], 0)
        self.assertIn("Left text", slide["content"])

    def test_tex_unmatched_image_reference_is_not_displayed(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Figure 26.3}
  \begin{columns}[T]
    \begin{column}{0.85\textwidth}
      \begin{itemize}
        \centering
        \includegraphics[width=0.65\textwidth]{fig/chart}
      \end{itemize}
    \end{column}
  \end{columns}
  \begin{center}
    \parbox{0.95\textwidth}{\scriptsize Caption below the image.}
  \end{center}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertEqual(slide["layout"]["mode"], "text")
        self.assertEqual(slide["image_count"], 0)
        self.assertTrue(slide["layout"]["has_columns"])
        self.assertEqual(slide["layout"]["column_count"], 1)
        self.assertEqual(slide["layout"]["columns"][0]["width_ratio"], 0.85)
        self.assertEqual(slide["layout"]["columns"][0]["image_count"], 0)
        self.assertIn("Caption below the image", slide["layout"]["outside_content"])
        self.assertEqual(parsed["missing_image_refs"], ["fig/chart"])
        self.assertEqual(slide["missing_image_refs"], ["fig/chart"])

    def test_tex_image_ref_resolves_project_structured_figure_by_filename(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Figure 26.5}
  \includegraphics[width=0.65\textwidth]{fig/fig_0128.png}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertEqual(parsed["missing_image_refs"], [])
        self.assertEqual(slide["image_count"], 1)
        self.assertEqual(slide["images"][0]["source_path"], "figures/fig_0128.png")
        self.assertTrue(str(slide["images"][0]["data_uri"]).startswith("data:image/png;base64,"))

    def test_tex_custom_safecontentimage_is_treated_as_image(self):
        tex = r"""
\documentclass{beamer}
\newcommand{\safecontentimage}[1]{\IfFileExists{#1}{\includegraphics[width=0.7\textwidth]{#1}}{\fbox{\parbox[c][0.34\textheight][c]{0.7\textwidth}{Missing image\\\texttt{\detokenize{#1}}}}}}
\begin{document}
\begin{frame}{Figure 27.1}
  \centering
  \safecontentimage{fig/27.1.png}
  \vspace{0.3cm}
  \begin{center}
    \parbox{0.95\textwidth}{\scriptsize Caption with inline math $r$ and $x$.}
  \end{center}
\end{frame}
\end{document}
"""
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertEqual(slide["image_count"], 1)
        self.assertEqual(slide["images"][0]["source_path"], "figures/fig_0132.png")
        self.assertEqual(slide["images"][0]["tex_ref"], "figures/fig_0132.png")
        self.assertTrue(str(slide["images"][0]["data_uri"]).startswith("data:image/png;base64,"))
        self.assertEqual(slide["images"][0]["width_ratio"], 0.7)
        self.assertEqual(slide["layout"]["mode"], "image_text")
        self.assertTrue(slide["layout"]["image_first"])
        self.assertIn("Caption with inline math $r$ and $x$", slide["content"])
        self.assertNotIn("fig/27.1.png", slide["content"])
        self.assertNotIn("Missing image", slide["content"])

    def test_tex_zip_custom_safeverticalimage_is_treated_as_image(self):
        tex = r"""
\documentclass{beamer}
\newcommand{\safeverticalimage}[1]{\IfFileExists{#1}{\includegraphics[width=\textwidth]{#1}}{\fbox{Missing image}}}
\begin{document}
\begin{frame}{Figure 5.1}
  \begin{columns}[T]
    \begin{column}{0.45\textwidth}
      Selection can maintain polymorphism under overdominance.
    \end{column}
    \begin{column}{0.45\textwidth}
      \centering
      \safeverticalimage{fig/chart.png}
    \end{column}
  \end{columns}
\end{frame}
\end{document}
"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("main.tex", tex)
            archive.writestr("fig/chart.png", TINY_PNG)

        parsed = parse_courseware(payload.getvalue(), "lecture.zip")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertEqual(parsed["missing_image_refs"], [])
        self.assertEqual(slide["image_count"], 1)
        self.assertEqual(slide["images"][0]["source_path"], "fig/chart.png")
        self.assertTrue(str(slide["images"][0]["data_uri"]).startswith("data:image/png;base64,"))
        self.assertEqual(slide["layout"]["columns"][1]["image_count"], 1)

    def test_tex_zip_image_paths_match_common_root_without_extension(self):
        tex = r"""
\documentclass{beamer}
\begin{document}
\begin{frame}{Image}
  \includegraphics[width=0.5\textwidth]{fig/chart}
\end{frame}
\end{document}
"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("course/main.tex", tex)
            archive.writestr("course/fig/chart.PNG", TINY_PNG)

        parsed = parse_courseware(payload.getvalue(), "lecture.zip")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertTrue(parsed["success"])
        self.assertEqual(slide["images"][0]["source_path"], "course/fig/chart.PNG")
        self.assertTrue(str(slide["images"][0]["data_uri"]).startswith("data:image/png;base64,"))

    def test_tex_standalone_step_image_macros_count_as_pages(self):
        tex = r"""
\documentclass{beamer}
\usepackage{graphicx}
\newcommand{\StepImageFrame}[1]{%
  \begin{frame}[plain]{}
    \includegraphics[width=\textwidth]{assets/#1}%
  \end{frame}}
\begin{document}
\StepImageFrame{slide01.png}
\begin{frame}{Text}
  A text slide.
\end{frame}
\StepImageFrame{slide02.png}
\end{document}
"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("main.tex", tex)
            archive.writestr("assets/slide01.png", TINY_PNG)
            archive.writestr("assets/slide02.png", TINY_PNG)

        parsed = parse_courseware(payload.getvalue(), "lecture.zip")

        self.assertEqual(parsed["slide_count"], 3)
        self.assertEqual(parsed["missing_image_refs"], [])
        self.assertEqual(
            [slide["images"][0]["source_path"] for slide in parsed["slides"] if slide["images"]],
            ["assets/slide01.png", "assets/slide02.png"],
        )

    def test_tex_canvas_layout_comment_round_trips(self):
        tex = r'''
\documentclass{beamer}
\begin{document}
\begin{frame}{Canvas}
% KGTS_LAYOUT {"items":[{"id":"title","type":"title","x":40,"y":30,"width":900,"height":60},{"id":"image-fig-chart","type":"image","ref":"fig/chart","x":500,"y":120,"width":420,"height":250}]}
  \includegraphics[width=0.42\textwidth]{fig/chart}
\end{frame}
\end{document}
'''
        parsed = parse_courseware(tex.encode("utf-8"), "edited.tex")
        slide = build_ppt_lecture_prompt_data(parsed)["slide_details"][0]

        self.assertEqual(slide["layout"]["canvas"]["items"][0]["id"], "title")
        self.assertEqual(slide["layout"]["canvas"]["items"][1]["ref"], "fig/chart")
        self.assertEqual(slide["layout"]["canvas"]["items"][1]["width"], 420)


if __name__ == "__main__":
    unittest.main()
