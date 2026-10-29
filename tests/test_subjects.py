import json
from pathlib import Path
import tempfile
import unittest
from workbench.subjects import TestSubjects


class TestSubjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'subjects.json'
        self.subjects = TestSubjects(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_selection_persists_and_previous_snapshot_does_not_change(self):
        self.assertEqual(self.subjects.selection(), {'motor': None, 'driverBoard': None})
        motor = self.subjects.create('motor', ' 电机 A ')['item']
        board = self.subjects.create('driverBoard', '驱动板 N32')['item']
        self.subjects.select({'motorId': motor['id'], 'driverBoardId': board['id']})
        original = self.subjects.selection()
        replacement = self.subjects.create('motor', '电机 B')['item']
        self.subjects.select({'motorId': replacement['id'], 'driverBoardId': ''})
        reloaded = TestSubjects(self.path)
        self.assertEqual(reloaded.selection()['motor']['name'], '电机 B')
        self.assertIsNone(reloaded.selection()['driverBoard'])
        self.assertEqual(original, {'motor': motor, 'driverBoard': board})
        self.assertEqual(len(reloaded.status()['motors']), 2)

    def test_duplicate_names_reuse_entry_and_invalid_selection_does_not_overwrite(self):
        first = self.subjects.create('motor', 'Motor A')['item']
        self.assertEqual(self.subjects.create('motor', 'motor a')['item'], first)
        saved = self.path.read_bytes()
        for kind, name in [('other', 'A'), ('motor', ''), ('motor', 'x'*81)]:
            with self.assertRaises(ValueError):
                self.subjects.create(kind, name)
        with self.assertRaises(ValueError):
            self.subjects.select({'motorId': 'missing'})
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(len(json.loads(saved)['motors']), 1)
