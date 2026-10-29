import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build import CSS


class TocNavigationTests(unittest.TestCase):
    def test_contents_list_does_not_add_visual_numbers_to_numbered_titles(self):
        self.assertIn("#toc ol {list-style:none; padding-left:0;}", CSS)


if __name__ == "__main__":
    unittest.main()
