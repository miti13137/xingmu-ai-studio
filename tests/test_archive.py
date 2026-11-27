import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'production'))
from archive import create_backup, restore_backup


class ArchiveTest(unittest.TestCase):
    def test_restore_does_not_remove_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'clip-0.bin').write_bytes(b'video')
            job = {'dir': source, 'manifest': {'ratio': '9:16', 'shots': [{'video': 'clip-0.bin', 'dialogue': '字幕'}]}}
            data = create_backup(job).read_bytes()
            destination = root / 'existing'
            destination.mkdir()
            original = destination / 'precious.txt'
            original.write_text('keep me')
            with self.assertRaises(FileExistsError):
                restore_backup(data, destination)
            self.assertEqual(original.read_text(), 'keep me')

if __name__ == '__main__':
    unittest.main()
