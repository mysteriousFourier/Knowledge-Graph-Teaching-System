import asyncio
import unittest
from unittest.mock import Mock, patch

from KGTS.education import router
from KGTS.models.education import SaveLectureRequest


class CoursewarePageDeletionTests(unittest.TestCase):
    def test_save_lecture_expands_against_submitted_deck_after_deletion(self):
        old_slides = [{"index": i, "slide_id": f"s{i}", "title": f"Page {i}"} for i in range(1, 4)]
        new_slides = [old_slides[0], {**old_slides[2], "index": 2}]
        request = SaveLectureRequest(
            chapter_id="ppt_deletion_test",
            lecture_content="## 第1 页：Page 1\n\none\n\n---\n\n## 第2 页：Page 3\n\nthree",
            ppt_slides=new_slides,
            slide_lectures=[
                {"index": 1, "slide_id": "s1", "lecture": "one"},
                {"index": 2, "slide_id": "s3", "lecture": "three"},
            ],
        )
        store = Mock()
        store.get_chapter.return_value = {"ppt_slides": old_slides}
        store.save_lecture.side_effect = lambda **payload: payload
        with patch.object(router, "chapter_store", store), patch.object(router, "clear_course_tts_cache", return_value={}):
            result = asyncio.run(router.save_lecture(request))
        self.assertTrue(result["success"])
        saved = store.save_lecture.call_args.kwargs
        self.assertEqual(saved["ppt_slides"], new_slides)
        self.assertEqual([item["slide_id"] for item in saved["slide_lectures"]], ["s1", "s3"])
        self.assertEqual([item["lecture"] for item in saved["slide_lectures"]], ["one", "three"])


if __name__ == "__main__":
    unittest.main()
