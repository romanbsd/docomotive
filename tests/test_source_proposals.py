import sys
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from note_zones import separator_proposals, small_type_proposals
from extract import clipped_observation
from figure_proposals import group_proposals, propose_figures


class SourceProposalTests(unittest.TestCase):
    def test_subpixel_edge_boxes_are_clipped_but_bad_geometry_is_rejected(self):
        self.assertEqual(
            clipped_observation([-0.0005, 0.1, 0.9, 1.0001], 1000, 1200),
            [0, 0.1, 0.9, 1],
        )
        with self.assertRaises(ValueError):
            clipped_observation([-0.01, 0.1, 0.9, 0.2], 1000, 1200)
        with self.assertRaises(ValueError):
            clipped_observation([0, 0.1, -0.0005, 0.2], 1000, 1200)

    def test_small_marked_footer_requires_body_type_and_no_prose_below(self):
        rows = [
            dict(
                text="Ordinary body prose establishing a dominant line height.",
                bbox=[0.1, 0.15 + i * 0.02, 0.9, 0.165 + i * 0.02],
            )
            for i in range(20)
        ]
        note = dict(
            text="1 Smaller bibliographic reference.", bbox=[0.1, 0.9, 0.8, 0.909]
        )
        self.assertEqual(len(small_type_proposals(rows + [note])), 1)
        self.assertEqual(
            small_type_proposals(rows + [note], [[0.05, 0.8, 0.95, 0.95]]), []
        )
        below = dict(text=rows[0]["text"], bbox=[0.1, 0.94, 0.9, 0.955])
        self.assertEqual(small_type_proposals(rows + [note, below]), [])

    def test_footer_rule_excludes_header_decorations_and_artwork(self):
        image = Image.new("L", (800, 1200), 255)
        d = ImageDraw.Draw(image)
        d.line((80, 100, 190, 100), fill=0, width=2)
        d.line((80, 1000, 190, 1000), fill=0, width=2)
        self.assertEqual(len(separator_proposals(image)), 1)
        self.assertEqual(separator_proposals(image, [[0.05, 0.8, 0.4, 0.9]]), [])
        d.rectangle((80, 1000, 190, 1020), fill=0)
        self.assertEqual(separator_proposals(image), [])
        image = Image.new("L", (800, 1200), 255)
        ImageDraw.Draw(image).line((20, 1000, 130, 1000), fill=0, width=2)
        self.assertEqual(len(separator_proposals(image)), 1)

    def test_graphic_group_does_not_swallow_intervening_prose(self):
        a = dict(rect=[0.1, 0.1, 0.8, 0.2], ink_pixels=300, caption="")
        b = dict(rect=[0.1, 0.23, 0.8, 0.3], ink_pixels=300, caption="")
        self.assertEqual(len(group_proposals([a, b], [])), 1)
        prose = [
            dict(
                text="A broad paragraph between graphic fragments with enough letters to establish prose in the source.",
                bbox=[0.1, 0.205, 0.8, 0.225],
            )
        ]
        self.assertEqual(len(group_proposals([a, b], prose)), 2)

    def test_substantial_drawing_is_proposed_without_editing_pixels(self):
        image = Image.new("L", (800, 1200), 255)
        d = ImageDraw.Draw(image)
        d.rectangle((150, 400, 650, 700), outline=0, width=4)
        before = image.tobytes()
        self.assertEqual(len(propose_figures(image, [])), 1)
        self.assertEqual(image.tobytes(), before)


if __name__ == "__main__":
    unittest.main()
