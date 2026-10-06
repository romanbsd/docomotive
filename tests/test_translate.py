"""Translation keeps every ID, link and inline element; only text changes."""

import io
import json
import re
import tempfile
import sys
from contextlib import redirect_stderr, redirect_stdout
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lxml import etree
from translate import (
    apparatus,
    apparatus_document,
    bibliography,
    chunks,
    citation_entry,
    citation_sentence,
    encode,
    glossary_lines,
    glossary_terms,
    Evaluated,
    evaluator,
    leaf_blocks,
    translate_block,
    Translator,
    model_config,
    narrator_note,
    segments,
    translate_batch,
)

XHTML = (
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
    "<head><title>Sea</title></head><body><blockquote><p id='p1'>The <em>old</em> keeper"
    '<span epub:type="pagebreak" id="page-3"/> wrote<a href="n.xhtml#n1" id="r1"><sup>1</sup></a>.</p>'
    "</blockquote><p>42</p></body></html>"
)


def body_text(text):
    return text


def upper(text, *args):
    return re.sub(r"</?(X|SEG)\d+/?>|&\w+;", lambda m: m[0].lower(), text.upper())


class TranslateTests(unittest.TestCase):
    def test_leaf_blocks_skip_containers_and_letterless_text(self):
        root = etree.fromstring(XHTML)
        self.assertEqual(
            [etree.QName(b).localname for b in leaf_blocks(root)], ["title", "p"]
        )

    def test_placeholders_restore_markup_ids_and_links(self):
        block = leaf_blocks(etree.fromstring(XHTML))[1]
        self.assertEqual(translate_block(block, upper, "en", "ru"), 0)
        out = etree.tostring(block, encoding="unicode")
        self.assertIn("THE <em>OLD</em> KEEPER", out)
        self.assertIn('id="page-3"', out)
        self.assertIn('href="n.xhtml#n1" id="r1"><sup>1</sup></a>.', out)

    def test_dropped_placeholders_fall_back_without_losing_atomic_elements(self):
        block = leaf_blocks(etree.fromstring(XHTML))[1]
        lossy = lambda p, *a: "СТАРЫЙ СМОТРИТЕЛЬ"
        self.assertEqual(translate_block(block, lossy, "en", "ru"), 2)
        out = etree.tostring(block, encoding="unicode")
        self.assertIn('id="page-3"', out)
        self.assertIn('id="r1"', out)
        self.assertNotIn("<em>", out)

    def test_line_breaks_and_page_breaks_survive_reordering(self):
        root = etree.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">first line<br/>second'
            '<span id="page-9"/> line</p>'
        )
        self.assertEqual(translate_block(root, upper, "en", "ru"), 0)
        out = etree.tostring(root, encoding="unicode")
        self.assertRegex(out, r'FIRST LINE<br/>SECOND<span id="page-9"/> LINE</p>$')

    def test_links_survive_when_model_drops_their_markup(self):
        root = etree.fromstring(
            '<ol xmlns="http://www.w3.org/1999/xhtml"><li><a href="c1.xhtml">Storm</a></li>'
            '<li>See <a href="c2.xhtml">Sea</a> and <em>sky</em></li></ol>'
        )
        bare = lambda p, *a: "БУРЯ" if "Storm" in p else "СМ. МОРЕ И НЕБО"
        toc, cross = leaf_blocks(root)
        self.assertEqual(etree.QName(toc).localname, "a")
        self.assertEqual(translate_block(toc, bare, "en", "ru"), 0)
        self.assertEqual(translate_block(cross, bare, "en", "ru"), 2)
        out = etree.tostring(root, encoding="unicode")
        self.assertIn('<a href="c1.xhtml">БУРЯ</a>', out)
        self.assertIn('<a href="c2.xhtml">Sea</a>', out)

    def test_wrapped_source_text_is_not_a_line_break(self):
        root = etree.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">wrapped\n  source<br/>line</p>'
        )
        self.assertEqual(translate_block(root, upper, "en", "ru"), 0)
        self.assertIn(
            "WRAPPED SOURCE<br/>LINE", etree.tostring(root, encoding="unicode")
        )

    def test_evaluation_stops_at_first_prose_chunk(self):
        root = etree.fromstring(
            '<body xmlns="http://www.w3.org/1999/xhtml"><h1>Title</h1><p>'
            + "The keeper wrote every night. " * 10
            + "</p></body>"
        )
        sent = []
        record = lambda p, *a: sent.append(body_text(p)) or "ПЕРЕВОД"
        title, body = leaf_blocks(root)
        translate_block(title, evaluator(record), "en", "ru")
        self.assertEqual(sent, [])
        with self.assertRaises(Evaluated), redirect_stdout(io.StringIO()) as out:
            with redirect_stderr(io.StringIO()):
                translate_block(body, evaluator(record), "en", "ru")
        self.assertEqual(out.getvalue(), "ПЕРЕВОД\n")
        sent.clear()
        evaluate = evaluator(record, count=2)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            translate_block(body, evaluate, "en", "ru")
        self.assertEqual(len(evaluate.done), 1)
        self.assertEqual(len(sent), 1)

    def translator(self, **config):
        base = {"prompt": "instruct", "chunk_chars": 2000, "batch": True}
        base |= {"context_chars": 30, "think": False, "num_ctx": 4096}
        base |= {"options": {"temperature": 0.0}, "retry_temperature": 0.4}
        cache = Path(tempfile.mkdtemp()) / "cache.jsonl"
        return Translator("m", cache, base | config, note=narrator_note("female"))

    def test_narrator_note_reaches_both_prompt_styles(self):
        note = 'narrator ("I") is a woman: use feminine forms'
        system = self.translator().messages("The old keeper", "en", "ru")[0]
        self.assertIn(note, system["content"])
        gemma = self.translator(prompt="translategemma").messages("x", "en", "ru")
        self.assertEqual(len(gemma), 1)
        self.assertIn(note, gemma[0]["content"])

    def test_context_is_recent_turns_within_budget(self):
        model = self.translator()
        model.history = [
            ("first " * 5, "ПЕРВЫЙ"),
            ("second", "ВТОРОЙ"),
            ("third", "ТРЕТИЙ"),
        ]
        turns = [m["content"] for m in model.messages("now", "en", "ru")[1:]]
        self.assertEqual(turns, ["second", "ВТОРОЙ", "third", "ТРЕТИЙ", "now"])
        model.config["context_chars"] = 0
        self.assertEqual(len(model.messages("now", "en", "ru")), 2)

    def test_model_config_falls_back_to_untagged_name_then_default(self):
        path = Path(tempfile.mkdtemp()) / "models.json"
        path.write_text(
            json.dumps(
                {
                    "default": {
                        "chunk_chars": 2000,
                        "options": {"temperature": 0.0, "top_k": 20},
                    },
                    "qwen": {"chunk_chars": 4000, "options": {"temperature": 0.3}},
                }
            )
        )
        qwen = model_config("qwen:latest", path)
        self.assertEqual((qwen["name"], qwen["chunk_chars"]), ("qwen", 4000))
        self.assertEqual(qwen["options"], {"temperature": 0.3, "top_k": 20})
        self.assertEqual(model_config("other", path)["name"], "default")
        path.write_text(
            json.dumps({"default": {"chunk_chars": 1}, "q:latest": {"chunk_chars": 2}})
        )
        self.assertEqual(model_config("q", path)["chunk_chars"], 2)

    def test_batches_decode_per_segment_and_retry_broken_ones_alone(self):
        root = etree.fromstring(
            '<body xmlns="http://www.w3.org/1999/xhtml"><p>One <em>old</em> keeper.</p>'
            "<p>Second line.</p><p>Third &amp; last.</p></body>"
        )
        blocks = leaf_blocks(root)
        self.assertEqual(translate_batch(blocks, upper, "en", "ru"), [0, 0, 0])
        self.assertIn(
            "ONE <em>OLD</em> KEEPER.", etree.tostring(blocks[0], encoding="unicode")
        )
        self.assertEqual(blocks[2].text, "THIRD & LAST.")
        calls = []

        def lose_emphasis(text, *a):
            calls.append(text)
            out = upper(text)
            return (
                out.replace("<x1>", "").replace("</x1>", "") if "<seg" in text else out
            )

        blocks = leaf_blocks(etree.fromstring(etree.tostring(root)))
        self.assertEqual(translate_batch(blocks, lose_emphasis, "en", "ru"), [0, 0, 0])
        self.assertEqual(len(calls), 2)  # the batch, then block one alone
        with self.assertRaises(ValueError):
            segments("<seg1>A</seg1>", 2)
        wrapped = lambda text, *a: "<seg1>" + upper(text) + "</seg1>"
        block = leaf_blocks(etree.fromstring(etree.tostring(root)))[0]
        self.assertEqual(translate_block(block, wrapped, "en", "ru"), 0)

    def test_glossary_finds_recurring_names_and_rare_words_only(self):
        texts = [
            "The Ashaninka drank ayahuasca with Crick. Later the Ashaninka slept.",
            "Ayahuasca visions came. Crick and the Ashaninka and the river sang.",
            "The river was cold, entwined. The river was wide, entwined, entwined.",
            "Strangely it rained. Strangely they drank ayahuasca, said Crick.",
            "Strangely, nobody slept. Strangely ayahuasca. Rain fell, strangely",
        ]
        known = {"river", "entwined", "crick", "strangely", "the", "rain"}.__contains__
        terms = dict(glossary_terms(texts, known))
        self.assertEqual(set(terms), {"Ashaninka", "ayahuasca", "Crick"})
        self.assertIn("ayahuasca", terms["ayahuasca"])
        glossary = {"Ashaninka": "ашанинка", "ayahuasca": "аяуаска", "river": "река"}
        lines = glossary_lines(glossary, "Two Ashaninka's ayahuasca dreams")
        self.assertEqual(lines, "Ashaninka = ашанинка\nayahuasca = аяуаска")
        model = self.translator()
        model.glossary = glossary
        system = model.messages("ayahuasca", "en", "ru")[0]["content"]
        self.assertIn("ayahuasca = аяуаска", system)
        self.assertNotIn("river", system)

    def test_words_split_by_markup_are_unwrapped_but_links_are_not(self):
        root = etree.fromstring(
            '<h1 xmlns="http://www.w3.org/1999/xhtml">F<span class="sc">OREST</span> '
            '<em>Tele</em>vision<a href="#n1">2</a> <em>old</em> keeper</h1>'
        )
        text, slots, _, _ = encode(root)
        self.assertEqual(text, "FOREST Television<x1>2</x1> <x2>old</x2> keeper")
        self.assertEqual(len(slots), 2)

    def test_glossary_drops_sentence_echoes(self):
        model = self.translator()
        answer = {"ayahuasca": "аяуаска", "DNA": "ДНК и происхождение знания в книге"}
        model.chat = lambda *a, **k: json.dumps(answer, ensure_ascii=False)
        glossary = model.translate_terms([("ayahuasca", "x"), ("DNA", "y")], "en", "ru")
        self.assertEqual(glossary, {"ayahuasca": "аяуаска"})

    def test_long_text_splits_only_outside_placeholders(self):
        text = "One two. <x1>Three. Four.</x1> Five six. Seven."
        pieces = [p for _, p in chunks(text, limit=12)]
        self.assertEqual("".join(pieces), text)
        self.assertIn("<x1>Three. Four.</x1> ", pieces[1])

    def test_citations_are_classified_from_text(self):
        for entry in [
            "PERVIN, L. A. (1996). The science of personality. New York: Wiley.",
            "————. Training Trances. Portland, Oregon: Metamorphous Press, 1990.",
            "12. Michael Cox, Mysticism (Wellingborough, 1983), 23–25.",
            "4. Ibid., vol. 2, p. 17.",
        ]:
            self.assertTrue(citation_entry(entry), entry)
        for prose in [
            "9. Cox, Mysticism, 23. This model is more subtle than it was in his earlier work.",
            "Ayahuasca: a drink that is brewed from the vine and was used by healers.",
        ]:
            self.assertFalse(citation_entry(prose), prose)
        self.assertTrue(citation_sentence("Cox, Mysticism (Wellingborough, 1983), 23."))
        self.assertFalse(citation_sentence("He was born in 1950."))

    def test_mostly_citation_lists_are_kept_whole_but_glossaries_are_not(self):
        entry = '<p class="reference">SMITH, J. ({}). A book. New York: Wiley.</p>'
        split = '<p class="reference">possibilities for growth</p>'
        gloss = '<p class="reference">Term: a word that is used when it was {}.</p>'
        wrap = '<body xmlns="http://www.w3.org/1999/xhtml">{}</body>'
        refs = leaf_blocks(
            etree.fromstring(
                wrap.format("".join(entry.format(1990 + i) for i in range(9)) + split)
            )
        )
        self.assertEqual(bibliography(refs), set(refs))
        terms = leaf_blocks(
            etree.fromstring(wrap.format("".join(gloss.format(i) for i in range(9))))
        )
        self.assertEqual(bibliography(terms), set())
        self.assertTrue(citation_entry("5. Matthew 16:26; compare Luke 10:24."))

    def test_unmarked_notes_documents_are_recognised_from_citation_share(self):
        wrap = '<body xmlns="http://www.w3.org/1999/xhtml"><h1>Notes</h1>{}</body>'
        note = "<p>{}. Cox, Mysticism (London, 1983), 23.</p>"
        prose = "<p>He walked to the river and it was cold when they arrived.</p>"
        notes = leaf_blocks(
            etree.fromstring(wrap.format(note.format(1) * 4 + prose * 7))
        )
        chapter = leaf_blocks(
            etree.fromstring(wrap.format(note.format(1) + prose * 10))
        )
        self.assertTrue(apparatus_document(notes))
        self.assertFalse(apparatus_document(chapter))

    def test_index_and_citation_notes_stay_untranslated(self):
        root = etree.fromstring(
            '<body xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<div class="index"><p>Ayahuasca, 12, 40</p></div>'
            '<aside epub:type="endnote"><p class="reference">1. Ibid., p. 7.</p></aside>'
            '<aside epub:type="endnote"><p class="reference">2. Cox, <em>Mysticism</em> '
            "(Wellingborough, 1983), 23. He was not convinced by it.</p></aside></body>"
        )
        index, note, mixed = leaf_blocks(root)
        self.assertEqual(
            [apparatus(b) for b in (index, note, mixed)][:2], ["index", "reference"]
        )
        self.assertTrue(citation_entry(encode(note)[0]))
        sent = []
        record = lambda p, *a: sent.append(body_text(p)) or body_text(p).upper()
        translate_block(mixed, record, "en", "ru", keep=citation_sentence)
        self.assertEqual(sent, ["He was not convinced by it."])
        self.assertIn(
            "<em>Mysticism</em> (Wellingborough, 1983), 23. HE WAS NOT",
            etree.tostring(mixed, encoding="unicode"),
        )


if __name__ == "__main__":
    unittest.main()
