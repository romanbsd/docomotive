"""Attributed quotations reuse established translations found on Wikiquote."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import quotations as Q
from common import write_epub
from translate import translate_epub

WIKITEXT = """{{Персоналия}}
== Цитаты ==
{{Q|Всё [[течение|течёт]], всё меняется.<ref name="x"/>|Комментарий=1}}
{{Q|Солнце — {{comment|ново|новое}} ежедневно.|Комментарий=6}}
* Характер человека — его божество.
** — источник
"""


class QuotationTests(unittest.TestCase):
    def test_wikiquote_templates_and_bullets_become_plain_quotes(self):
        self.assertEqual(
            Q.template_quotes(WIKITEXT),
            [
                "Всё течёт, всё меняется.",
                "Солнце — ново ежедневно.",
                "Характер человека — его божество.",
            ],
        )

    def test_established_offers_length_matched_candidates_to_the_chooser(self):
        offered = []

        def choose(quote, options):
            offered.extend(options)
            return 0

        with patch.object(
            Q, "candidates", return_value=["Коротко.", "Всё течёт, всё меняется."]
        ):
            found = Q.established(
                "Everything flows, all things change.",
                "Heraclitus",
                "en",
                "ru",
                "x",
                choose,
            )
        self.assertEqual(found, "Всё течёт, всё меняется.")
        self.assertEqual(offered, ["Всё течёт, всё меняется."])
        with patch.object(Q, "candidates", return_value=[]):
            self.assertIsNone(
                Q.established("Anything.", "Nobody", "en", "ru", "x", choose)
            )

    def test_quoted_saying_is_taken_from_a_framing_sentence(self):
        framed = "по Гераклиту „именно, должны во многом сведущи быть философы“."
        quote = "Those who love wisdom must investigate many things."
        self.assertEqual(
            Q.quoted_part(framed, quote),
            "Именно, должны во многом сведущи быть философы.",
        )
        self.assertEqual(Q.quoted_part("Всё течёт.", quote), "Всё течёт.")

    def test_jev_choice_needs_a_confident_pick(self):
        import jev_judge

        def answer(choice, p):
            return {
                "answers": {"match": {"choice": choice, "probabilities": {choice: p}}}
            }

        choose = Q.jev_chooser("cache")
        with patch.object(jev_judge, "post", return_value=answer("2", 0.9)):
            self.assertEqual(choose("q", ["a", "b"]), 1)
        with patch.object(jev_judge, "post", return_value=answer("2", 0.4)):
            self.assertIsNone(choose("q", ["a", "b"]))
        with patch.object(jev_judge, "post", return_value=answer("none", 0.9)):
            self.assertIsNone(choose("q", ["a", "b"]))

    def test_translation_uses_established_quotation_for_epigraph(self):
        lines = "".join(
            f'<p class="epi">{w}</p>' for w in "EVERYTHING FLOWS AND CHANGES.".split()
        )
        xhtml = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>'
            f'{lines}<p class="epi">HERACLITUS</p><p>Body text of the book follows.</p></body></html>'
        )
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x</dc:identifier>'
            "<dc:title>T</dc:title><dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c"/></spine></package>'
        )
        folder = Path(tempfile.mkdtemp())
        container = (
            b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            b'<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
            b"</rootfiles></container>"
        )
        write_epub(
            folder / "in.epub",
            {
                "content.opf": opf.encode(),
                "c.xhtml": xhtml.encode(),
                "META-INF/container.xml": container,
            },
        )
        asked = []

        def quotes(quotation, author):
            asked.append((quotation, author))
            return "Всё течёт, всё меняется."

        model = lambda text, *a: text.upper()
        report = translate_epub(
            folder / "in.epub",
            folder / "out.epub",
            model,
            "ru",
            progress=False,
            quotes=quotes,
        )
        self.assertEqual(asked, [("Everything flows and changes.", "Heraclitus")])
        self.assertEqual(len(report["established_quotations"]), 1)
        with zipfile.ZipFile(folder / "out.epub") as z:
            out = z.read("c.xhtml").decode()
        self.assertIn(
            '<p class="epi">ВСЁ</p><p class="epi">ТЕЧЁТ,</p><p class="epi">ВСЁ</p><p class="epi">МЕНЯЕТСЯ.</p>',
            out,
        )


if __name__ == "__main__":
    unittest.main()
