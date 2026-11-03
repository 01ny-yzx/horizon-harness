"""Release assembly refuses fake checksums, missing targets and unmanaged output."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_installers import prepare
from tui.version import __version__


class PrepareInstallers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / f'horizon-{__version__}-windows-x64.zip'
        self.archive.write_bytes(b'local fixture, not a published native executable')
        sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive.with_name(self.archive.name + '.sha256').write_text(sha + '  ' + self.archive.name)
        metadata = {'version':__version__, 'target':'windows-x64', 'filename':self.archive.name, 'sha256':sha}
        self.archive.with_name(self.archive.name + '.build.json').write_text(json.dumps(metadata))
        self.out = self.root / 'generated'

    def test_only_present_targets_are_generated(self):
        manifest = prepare(self.root, self.out)
        self.assertEqual(list(manifest['targets']), ['windows-x64'])
        config = json.loads((self.out / 'npm' / 'package.json').read_text())
        self.assertFalse(config['private'])
        self.assertFalse((self.out / 'install.sh').exists())
        self.assertFalse((self.out / 'Formula').exists())

    def test_checksum_and_required_targets_fail_before_output(self):
        with self.assertRaisesRegex(ValueError, 'Missing required'):
            prepare(self.root, self.out, required=['macos-arm64'])
        self.assertFalse(self.out.exists())
        self.archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            prepare(self.root, self.out)
        self.assertFalse(self.out.exists())

    def test_unmanaged_output_is_preserved(self):
        package = self.out / 'npm'; package.mkdir(parents=True)
        keep = package / 'keep.txt'; keep.write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            prepare(self.root, self.out)
        self.assertEqual(keep.read_text(), 'keep me')
        self.assertFalse((self.out / 'release-manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
