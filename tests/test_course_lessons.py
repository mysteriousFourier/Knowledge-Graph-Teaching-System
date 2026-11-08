import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from KGTS.core.bridge import ChapterStore
from KGTS.education import router
from KGTS.models.education import CourseLessonOrderRequest


class CourseLessonsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = ChapterStore(root / 'chapters.json', root / 'progress.json')

    def save(self, title, chapter_id, course='course_demo', **extra):
        return self.store.save_chapter(title=title, chapter_id=chapter_id, course_id=course,
                                       content=extra.pop('content', 'lesson text'), sync_backend=False, **extra)

    def test_republish_project_preserves_identity_order_and_exercises(self):
        first = self.save('AT-05', 'ppt_1700000000001', source_project_id='cw_one')
        self.store.reorder_chapters('course_demo', [first['id']])
        records = self.store._load_chapters()
        records[first['id']]['exercise_bank'] = [{'id': 'keep-me'}]
        self.store._save_chapters(records)
        updated = self.save('AT-05 revised', 'ppt_1700000000002', source_project_id='cw_one', content='new deck')
        self.assertEqual(first['id'], updated['id'])
        self.assertEqual(0, updated['course_order'])
        self.assertEqual([{'id': 'keep-me'}], updated['exercise_bank'])
        self.assertEqual(1, len(self.store.list_chapters('course_demo')))
        other = self.save('AT-05', 'ppt_1700000000003', course='course_other', source_project_id='cw_one')
        self.assertNotEqual(updated['id'], other['id'])

    def test_multiple_historical_project_links(self):
        first = self.save('FT-06', 'ppt_1700000000001', source_project_id='cw_old')
        self.store.link_courseware_project(first['id'], 'cw_new', 'course_demo')
        updated = self.save('FT-06', 'ppt_1700000000002', source_project_id='cw_old')
        self.assertEqual(first['id'], updated['id'])
        with self.assertRaises(ValueError):
            self.store.link_courseware_project(first['id'], 'cw_wrong', 'course_other')

    def test_natural_order_and_saved_order_survive_edits_and_reload(self):
        for number in [10, 2, 1]:
            self.save(f'AT_{number:02}', f'ppt_17000000000{number:02}')
        lessons = self.store.list_chapters('course_demo')
        self.assertEqual(['AT_01', 'AT_02', 'AT_10'], [c['title'] for c in lessons])
        ids = [c['id'] for c in reversed(lessons)]
        self.store.reorder_chapters('course_demo', ids)
        reloaded = ChapterStore(self.store.chapters_file, self.store.progress_file)
        self.assertEqual(ids, [c['id'] for c in reloaded.list_chapters('course_demo')])
        self.save('AT_02', lessons[1]['id'], content='edited')
        self.assertEqual(ids, [c['id'] for c in self.store.list_chapters('course_demo')])
        for invalid in [ids[:-1], [ids[0]] * len(ids), [*ids[:-1], 'another-course-lesson']]:
            with self.assertRaises(ValueError):
                self.store.reorder_chapters('course_demo', invalid)
        self.assertEqual(ids, [c['id'] for c in self.store.list_chapters('course_demo')])

    def test_archived_duplicate_remains_on_disk_and_out_of_lists(self):
        first = self.save('AT-05', 'ppt_1700000000001')
        second = self.save('AT-05', 'ppt_1700000000002')
        records = self.store._load_chapters()
        records[second['id']]['archived_into'] = first['id']
        self.store._save_chapters(records)
        self.assertEqual([first['id']], [c['id'] for c in self.store.list_chapters('course_demo')])
        graph = {'nodes': [{'id': second['id'], 'type': 'chapter', 'label': 'Saved lesson', 'content': 'substantial course data'}]}
        with patch('KGTS.core.bridge.build_frontend_graph', return_value=graph):
            self.assertEqual([first['id']], [c['id'] for c in self.store.list_chapters()])
        raw = json.loads(self.store.chapters_file.read_text(encoding='utf-8'))
        self.assertIn(second['id'], raw['chapters'])
        self.assertIsNotNone(self.store.get_chapter(second['id']))

    def test_order_endpoint_rejects_stale_list_and_missing_course(self):
        first = self.save('FT-01', 'ppt_1700000000001')
        with patch.object(router, 'chapter_store', self.store), patch.object(router.course_store, 'get', return_value={'id': 'course_demo'}):
            result = asyncio.run(router.reorder_course_lessons('course_demo', CourseLessonOrderRequest(chapter_ids=[first['id']])))
            self.assertTrue(result['success'])
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(router.reorder_course_lessons('course_demo', CourseLessonOrderRequest(chapter_ids=[])))
            self.assertEqual(409, caught.exception.status_code)
        with patch.object(router.course_store, 'get', return_value=None):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(router.reorder_course_lessons('missing', CourseLessonOrderRequest(chapter_ids=[])))
            self.assertEqual(404, caught.exception.status_code)
