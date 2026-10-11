import tempfile
import unittest
from pathlib import Path

from KGTS.core.bridge import ChapterStore


class ChapterStoreLessonIdentityTests(unittest.TestCase):
    def test_explicit_temporary_ids_allow_same_title_in_one_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ChapterStore(root / "chapters.json", root / "progress.json")

            first = store.save_chapter(
                title="同名课节",
                content="第一节内容",
                chapter_id="ppt_1700000000001",
                course_id="course_demo",
                sync_backend=False,
            )
            second = store.save_chapter(
                title="同名课节",
                content="第二节内容",
                chapter_id="ppt_1700000000002",
                course_id="course_demo",
                sync_backend=False,
            )

            self.assertNotEqual(first["id"], second["id"])
            lessons = store.list_chapters("course_demo")
            self.assertEqual(2, len(lessons))
            self.assertEqual({"第一节内容", "第二节内容"}, {item["content"] for item in lessons})

    def test_saving_again_with_same_id_updates_that_lesson(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ChapterStore(root / "chapters.json", root / "progress.json")

            first = store.save_chapter(
                title="第一节",
                content="旧内容",
                chapter_id="ppt_1700000000003",
                course_id="course_demo",
                sync_backend=False,
            )
            updated = store.save_chapter(
                title="第一节（已修改）",
                content="新内容",
                chapter_id="ppt_1700000000003",
                course_id="course_demo",
                sync_backend=False,
            )

            self.assertEqual(first["id"], updated["id"])
            lessons = store.list_chapters("course_demo")
            self.assertEqual(1, len(lessons))
            self.assertEqual("新内容", lessons[0]["content"])
            self.assertEqual("第一节（已修改）", lessons[0]["title"])


if __name__ == "__main__":
    unittest.main()
