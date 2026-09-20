import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('registry', Path(__file__).resolve().parents[1] / 'scripts/registry.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.data = r.verify_catalog()
        self.entry = copy.deepcopy(self.data['modules'][0]['releases'][0])

    def test_catalog_signature(self):
        self.assertGreaterEqual(len(self.data['modules']), 2)

    def test_manifest_tampering(self):
        self.entry['manifest']['title'] = 'Tampered'
        with self.assertRaisesRegex(ValueError, 'signature'):
            r.validate_entry(self.entry)

    def test_external_download_rejected(self):
        self.entry['url'] = self.entry['url'].replace('github.com/PaNasMs', 'github.com/attacker')
        with self.assertRaisesRegex(ValueError, 'official'):
            r.validate_entry(self.entry)

    def test_traversal_rejected(self):
        self.entry['manifest']['files']['ui/../../secret'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'payload path'):
            r.validate_entry(self.entry)

    def test_duplicate_release_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'entries').mkdir()
            (root / 'keys').symlink_to(r.ROOT / 'keys', target_is_directory=True)
            for name in ('one', 'two'):
                (root / 'entries' / (name + '.json')).write_text(json.dumps(self.entry))
            with patch.object(r, 'ROOT', root), self.assertRaisesRegex(ValueError, 'Duplicate'):
                r.catalog()

    def test_unknown_key_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown signing key'):
            r.verify_signature(b'data', bytes(64), 'unknown')

    def test_catalog_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('entries', 'keys'):
                (root / name).symlink_to(r.ROOT / name, target_is_directory=True)
            (root / 'catalog.json').write_bytes(r.canonical(self.data) + b' ')
            (root / 'catalog.sig').write_bytes((r.ROOT / 'catalog.sig').read_bytes())
            with patch.object(r, 'ROOT', root), self.assertRaisesRegex(ValueError, 'Rebuild'):
                r.verify_catalog()


if __name__ == '__main__':
    unittest.main()
