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
    quality_flags,
    term_flags,
    timing_summary,
    brief_inputs,
    brief_lines,
    faux_small_caps,
    small_caps,
    typeset,
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

    def test_hy_mt_prompt_is_one_user_turn_with_published_sections(self):
        model = self.translator(prompt="hy-mt")
        model.glossary = {"ayahuasca": "аяуаска", "river": "река"}
        first = model.messages("Drink <x1>ayahuasca</x1>.", "en", "ru")
        self.assertEqual([m["role"] for m in first], ["user"])
        self.assertNotIn("[Background Information]", first[0]["content"])
        self.assertIn("ayahuasca translates to аяуаска", first[0]["content"])
        self.assertNotIn("river", first[0]["content"])
        self.assertIn("woman", first[0]["content"])
        model.history = [("Before.", "РАНЬШЕ.")]
        later = model.messages("After.", "en", "ru")[0]["content"]
        self.assertTrue(later.startswith("[Background Information]\nРАНЬШЕ."))
        self.assertTrue(later.endswith("[Source Text]\nAfter."))
        self.assertNotIn("tags", later)

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

    def test_quality_flags_catch_truncation_english_and_wrong_gender(self):
        source = "I walked to the river. It was cold. I sat down and waited for the boat to come back."
        good = "Я дошёл до реки. Было холодно. Я сел и стал ждать, когда вернётся лодка с людьми."
        self.assertEqual(quality_flags(source, good, "ru", "male"), [])
        cut = "Я дошёл до реки."
        self.assertEqual(
            quality_flags(source, cut, "ru", "male"), ["short", "lost_sentence"]
        )
        english = "Я дошёл до реки, and the boat was late with the cargo of the day."
        self.assertIn("untranslated", quality_flags(source, english, "ru"))
        self.assertIn(
            "mixed_script", quality_flags(source, good + " аяхуаскeros", "ru")
        )
        self.assertIn(
            "narrator_gender",
            quality_flags(
                source,
                "Я сидела и ждала лодку долго-долго у реки, пока не стемнело.",
                "ru",
                "male",
            ),
        )
        self.assertEqual(quality_flags("Short.", "Коротко.", "ru"), [])

    def test_typeset_russian_quotes_dashes_initials_and_note_spacing(self):
        root = etree.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">Он сказал: "Это <em>"чудо"</em>" - '
            'писал В. Г. Богораз в 1980-х годах. <a href="#n1"><sup>5</sup></a> Конец.</p>'
        )
        typeset(root, "ru")
        out = "".join(root.itertext())
        self.assertIn("«Это „чудо“»", out)
        self.assertIn("\u00a0— писал", out)
        self.assertIn("В.\u00a0Г.\u00a0Богораз", out)
        self.assertIn("1980-х", out)
        self.assertIn("годах.5 Конец", out)
        english = etree.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">"Hi" - there</p>'
        )
        typeset(english, "de")
        self.assertEqual(english.text, '"Hi" - there')

    def test_flagged_blocks_are_retried_alone_and_reported(self):
        import zipfile
        from common import write_epub
        from translate import translate_epub

        body = "".join(
            f"<p>I walked to the river number {i} at dawn. It was cold. I sat down and waited for the boat.</p>"
            for i in range(3)
        )
        xhtml = (
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>T</title></head>'
            f"<body>{body}</body></html>"
        )
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x</dc:identifier>'
            "<dc:title>T</dc:title><dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c"/></spine></package>'
        )
        folder = Path(tempfile.mkdtemp())
        write_epub(
            folder / "in.epub",
            {
                "content.opf": opf.encode(),
                "c.xhtml": xhtml.encode(),
                "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            },
        )
        calls = []

        def model(text, source, target, retry=False):
            calls.append(retry)
            out = upper(text)
            # The batch truncates its second paragraph; the lone retry is complete.
            return out.replace(
                "IT WAS COLD. I SAT DOWN AND WAITED FOR THE BOAT.</seg2>", "</seg2>"
            )

        model.batch, model.chunk_chars = True, 4000
        checks = lambda s, t: quality_flags(s, t, "en", min_ratio=0.8)
        report = translate_epub(
            folder / "in.epub",
            folder / "out.epub",
            model,
            "ru",
            progress=False,
            checks=checks,
        )
        self.assertEqual(report["flagged_blocks"], [])
        self.assertIn(True, calls)
        with zipfile.ZipFile(folder / "out.epub") as z:
            self.assertIn(
                "RIVER NUMBER 1 AT DAWN. IT WAS COLD.", z.read("c.xhtml").decode()
            )

    def test_commentary_notes_batch_and_citations_stay_verbatim(self):
        import zipfile
        from common import write_epub
        from translate import translate_epub

        notes = (
            '<aside epub:type="endnote" id="n1"><p>1. He was never convinced by that '
            "argument, and later abandoned it.</p></aside>"
            '<aside epub:type="endnote" id="n2"><p>2. The healers disagreed with him '
            "about the plants.</p></aside>"
            '<aside epub:type="endnote" id="n3"><p>3. Cox, Mysticism (London, 1983), 23. '
            "He was not convinced by it, and he said so later.</p></aside>"
        )
        xhtml = (
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f"<head><title>N</title></head><body><p>Body text here.</p>{notes}</body></html>"
        )
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x</dc:identifier>'
            "<dc:title>N</dc:title><dc:language>en</dc:language></metadata><manifest>"
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c"/></spine></package>'
        )
        folder = Path(tempfile.mkdtemp())
        write_epub(
            folder / "in.epub",
            {
                "content.opf": opf.encode(),
                "c.xhtml": xhtml.encode(),
                "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            },
        )
        sent = []
        model = lambda text, *a: sent.append(text) or upper(text)
        model.batch, model.chunk_chars = True, 4000
        translate_epub(
            folder / "in.epub", folder / "out.epub", model, "ru", progress=False
        )
        batched = [t for t in sent if "never convinced" in t]
        self.assertEqual(len(batched), 1)
        self.assertIn("healers disagreed", batched[0])
        self.assertNotIn("Cox", batched[0])
        self.assertNotIn("Body text", batched[0])
        with zipfile.ZipFile(folder / "out.epub") as z:
            out = z.read("c.xhtml").decode()
        self.assertIn("Cox, Mysticism (London, 1983), 23. HE WAS NOT CONVINCED", out)

    def test_faux_small_caps_headings_are_reset_after_translation(self):
        head = etree.fromstring(
            '<a xmlns="http://www.w3.org/1999/xhtml" href="#c">F<span class="sc">OREST</span> '
            'T<span class="sc">ELEVISION</span></a>'
        )
        style = faux_small_caps(head)
        self.assertEqual(style[1], {"class": "sc"})
        self.assertEqual(
            translate_block(head, lambda t, *a: "Лесное телевидение", "en", "ru"), 0
        )
        small_caps(head, style)
        out = etree.tostring(head, encoding="unicode")
        self.assertIn(
            'Л<span class="sc">ЕСНОЕ</span> Т<span class="sc">ЕЛЕВИДЕНИЕ</span>', out
        )
        body = etree.fromstring(
            '<p xmlns="http://www.w3.org/1999/xhtml">A <span>NASA</span> probe</p>'
        )
        self.assertIsNone(faux_small_caps(body))

    def test_brief_gives_each_chunk_only_the_people_it_names(self):
        brief = {
            "genre": "anthropological memoir",
            "people": {"Ruperto": "male", "Rachel": "female"},
        }
        lines = brief_lines(brief, "Ruperto laughed.")
        self.assertIn("About the book: anthropological memoir.", lines)
        self.assertIn("Ruperto (man)", lines)
        self.assertNotIn("Rachel", lines)
        self.assertNotIn("People", brief_lines(brief, "Rupertos laughed."))
        model = self.translator()
        model.brief = brief
        self.assertIn(
            "Rachel (woman)", model.messages("Rachel left.", "en", "ru")[0]["content"]
        )
        hy = self.translator(prompt="hy-mt")
        hy.brief = brief
        prompt = hy.messages("Rachel left.", "en", "ru")[0]["content"]
        self.assertTrue(prompt.startswith("[Background Information]\nAbout the book"))
        self.assertTrue(prompt.endswith("[Source Text]\nRachel left."))

    def test_brief_inputs_and_gender_filter(self):
        texts = ["Ruperto smiled. He drank. The Pichis river rose.", "Ruperto left."]
        excerpt, names = brief_inputs(
            texts, {"Ruperto": "Руперто", "DNA": "ДНК", "toé": "тоэ"}
        )
        self.assertIn("Ruperto smiled.", excerpt)
        self.assertEqual(names, {"Ruperto": ["Ruperto smiled."]})
        _, two = brief_inputs(texts, {"Ruperto": "Руперто"}, per_name=2)
        self.assertEqual(two, {"Ruperto": ["Ruperto smiled.", "Ruperto left."]})
        model = self.translator()
        answer = {
            "genre": "memoir",
            "people": {"Ruperto": "male", "Pichis": "unknown", "X": "male"},
        }
        model.chat = lambda *a, **k: json.dumps(answer)
        self.assertEqual(
            model.make_brief(excerpt, names, "en"),
            {"genre": "memoir", "people": {"Ruperto": "male"}},
        )
        calls = []
        model.chat = lambda *a, **k: calls.append(1) or json.dumps(answer)
        many = {f"Name{i}": ["x"] for i in range(65)}
        model.make_brief("", many, "en")
        self.assertEqual(len(calls), 3)

    def test_timing_summary_splits_prefill_and_decode(self):
        rows = [
            {
                "prompt_eval_count": 100,
                "prompt_eval_duration": 1e9,
                "eval_count": 50,
                "eval_duration": 3e9,
            },
            {
                "prompt_eval_count": 200,
                "prompt_eval_duration": 1e9,
                "eval_count": 50,
                "eval_duration": 5e9,
            },
        ]
        summary = timing_summary(rows)
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["prompt_tokens"], 300)
        self.assertEqual(summary["output_seconds"], 8.0)
        self.assertEqual(summary["prefill_share"], 0.2)
        self.assertEqual(timing_summary([])["requests"], 0)

    def epub(self, body):
        from common import write_epub

        xhtml = (
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f"<head><title>T</title></head><body>{body}</body></html>"
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
        return folder

    def test_reviewer_restores_markup_and_bad_edits_are_rejected(self):
        import zipfile
        from translate import translate_epub

        folder = self.epub(
            "<p>The <em>old</em> keeper wrote in his logbook every single night of the year.</p>"
        )
        # The main model always drops the emphasis tags, so the block falls back.
        lossy = lambda text, *a: re.sub(r"</?x\d+>", "", upper(text))
        good = lambda source_text, draft, s, t: upper(source_text)
        report = translate_epub(
            folder / "in.epub",
            folder / "a.epub",
            lossy,
            "ru",
            progress=False,
            review=good,
        )
        self.assertEqual([r["accepted"] for r in report["reviewed_blocks"]], [True])
        with zipfile.ZipFile(folder / "a.epub") as z:
            self.assertIn("THE <em>OLD</em> KEEPER", z.read("c.xhtml").decode())
        broken = lambda source_text, draft, s, t: "<x7>BROKEN</x7>"
        report = translate_epub(
            folder / "in.epub",
            folder / "b.epub",
            lossy,
            "ru",
            progress=False,
            review=broken,
        )
        self.assertEqual([r["accepted"] for r in report["reviewed_blocks"]], [False])
        with zipfile.ZipFile(folder / "b.epub") as z:
            self.assertIn("THE OLD KEEPER", z.read("c.xhtml").decode())

    def test_corrections_replace_model_output_and_stale_ones_stop_the_run(self):
        import zipfile
        from common import digest
        from translate import review_sheet, translate_epub

        folder = self.epub(
            "<p>The <em>old</em> keeper wrote in his logbook every night.</p><p>Second one.</p>"
        )
        report = translate_epub(
            folder / "in.epub", folder / "a.epub", upper, "ru", progress=False
        )
        pair = next(p for p in report["pairs"] if p["source"].startswith("The"))
        self.assertEqual(
            pair["source"], "The <x1>old</x1> keeper wrote in his logbook every night."
        )
        fix = {
            pair["key"]: {
                "source_sha": pair["source_sha"],
                "translation": "Старый <x1>смотритель</x1>.",
            }
        }
        calls = []
        model = lambda text, *a: calls.append(text) or upper(text)
        report = translate_epub(
            folder / "in.epub",
            folder / "b.epub",
            model,
            "ru",
            progress=False,
            corrections=fix,
        )
        self.assertEqual(report["corrections_applied"], [pair["key"]])
        self.assertFalse(any("keeper" in c for c in calls))
        with zipfile.ZipFile(folder / "b.epub") as z:
            self.assertIn("Старый <em>смотритель</em>.", z.read("c.xhtml").decode())
        stale = {pair["key"]: {"source_sha": digest(b"other"), "translation": "x"}}
        with self.assertRaises(SystemExit):
            translate_epub(
                folder / "in.epub",
                folder / "c.epub",
                upper,
                "ru",
                progress=False,
                corrections=stale,
            )
        pairs = report["pairs"]
        pairs[1]["flags"] = ["short"]
        review_sheet(pairs, [], folder / "r.html", "Book")
        page = (folder / "r.html").read_text()
        self.assertIn('class="flag"', page)
        self.assertIn("&lt;x1&gt;", page)

    def test_parallel_documents_match_serial_output(self):
        import zipfile
        from common import write_epub
        from translate import translate_epub

        folder = Path(tempfile.mkdtemp())
        files = {
            "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
        }
        items = ""
        for i in range(4):
            files[f"c{i}.xhtml"] = (
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>'
                + "".join(
                    f"<p>Chapter {i} paragraph {j} has <em>words</em>.</p>"
                    for j in range(5)
                )
                + "</body></html>"
            ).encode()
            items += f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
        files["content.opf"] = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x</dc:identifier>'
            f"<dc:title>T</dc:title><dc:language>en</dc:language></metadata><manifest>{items}</manifest>"
            "<spine/></package>"
        ).encode()
        write_epub(folder / "in.epub", files)

        class Model:
            batch, chunk_chars = True, 80

            def __init__(self, parallel):
                self.parallel = parallel

            def __call__(self, text, *a):
                return upper(text)

            def fork(self):
                return Model(self.parallel)

        outputs = []
        for parallel in (1, 3):
            out = folder / f"out{parallel}.epub"
            translate_epub(
                folder / "in.epub", out, Model(parallel), "ru", progress=False
            )
            with zipfile.ZipFile(out) as z:
                outputs.append({n: z.read(n) for n in z.namelist()})
        self.assertEqual(outputs[0], outputs[1])
        self.assertIn(
            b"CHAPTER 3 PARAGRAPH 4 HAS <em>WORDS</em>.", outputs[1]["c3.xhtml"]
        )

    def test_reviewer_edit_with_the_same_flags_is_rejected(self):
        from translate import translate_epub

        folder = self.epub(
            "<p>I walked to the river at dawn. It was cold. I sat and waited for the boat to come back.</p>"
        )
        cut = lambda text, *a: upper(text).split(" IT WAS")[0]
        still_cut = lambda source_text, draft, s, t: "I WALKED TO THE RIVER."
        checks = lambda s, t: quality_flags(s, t, "en")
        report = translate_epub(
            folder / "in.epub",
            folder / "a.epub",
            cut,
            "ru",
            progress=False,
            checks=checks,
            review=still_cut,
        )
        self.assertEqual([r["accepted"] for r in report["reviewed_blocks"]], [False])
        self.assertEqual(len(report["flagged_blocks"]), 1)

    def test_term_flags_catch_spelling_drift_but_not_inflection(self):
        glossary = {"ayahuasca": "аяуаска", "Peru": "Перу"}
        source = "They drank ayahuasca in Peru."
        self.assertEqual(term_flags(source, "Они пили аяуаску в Перу.", glossary), [])
        self.assertEqual(
            term_flags(source, "Они пили айяуаску в Перу.", glossary), ["terminology"]
        )
        self.assertEqual(
            term_flags("They drank tea.", "Они пили айяуаску.", glossary), []
        )
        self.assertEqual(
            term_flags("In Peru first.", "Сначала в Перу, первый.", glossary), []
        )
        names = {"Ashaninca": "ашанинка", "maninkari": "манинкари", "Egypt": "Египет"}
        text = "The Ashaninca maninkari of Egypt."
        self.assertEqual(term_flags(text, "манинкари ашанинка из Египта", names), [])

    def test_judge_scores_blocks_and_reviews_only_the_worst(self):
        import zipfile
        from translate import translate_epub

        folder = self.epub(
            "".join(
                f"<p>Paragraph {i} tells how the keeper watched the sea at night.</p>"
                for i in range(10)
            )
        )

        # The judge dislikes paragraph 3 until the reviewer rewrites it.
        def judge(pairs):
            return [
                (
                    40 if "PARAGRAPH 3 " in t else 90,
                    ["omission"] if "PARAGRAPH 3 " in t else [],
                )
                for _, t in pairs
            ]

        fixed = lambda source_text, draft, s, t: upper(source_text).replace(
            "PARAGRAPH 3 ", "PARAGRAPH THREE "
        )
        report = translate_epub(
            folder / "in.epub",
            folder / "a.epub",
            upper,
            "ru",
            progress=False,
            judge=judge,
            judge_share=0.1,
            review=fixed,
        )
        self.assertEqual(report["judge"]["scored"], 11)
        reviewed = report["judge"]["reviewed"]
        self.assertEqual(
            [(r["score"], r["new_score"], r["accepted"]) for r in reviewed],
            [(40, 90, True)],
        )
        with zipfile.ZipFile(folder / "a.epub") as z:
            self.assertIn("PARAGRAPH THREE TELLS", z.read("c.xhtml").decode())
        scores = {p["key"]: p["score"] for p in report["pairs"]}
        self.assertEqual(scores["c.xhtml#4"], 90)

    def test_glossary_includes_recurring_multiword_names(self):
        texts = [
            "We met Carlos Perez Shuma at dawn. Later Carlos Perez Shuma sang.",
            "Then Carlos Perez Shuma left the Pichis Valley. The Pichis Valley was wet.",
            "In the Pichis Valley it rained. Once Upon a time.",
        ]
        terms = dict(glossary_terms(texts))
        self.assertIn("Carlos Perez Shuma", terms)
        self.assertIn("Pichis Valley", terms)
        self.assertNotIn("Once Upon", terms)
        credits = ["Figure 1. From Clark (1959). See From Clark, p. 2. Map From Clark."]
        self.assertNotIn("From Clark", dict(glossary_terms(credits)))
        self.assertIn("Carlos", terms)

    def test_corrections_become_style_examples_in_prompts(self):
        model = self.translator()
        model.examples = [("Source <x1>one</x1>.", "Перевод <x1>один</x1>.")]
        model.history = [("Before.", "РАНЬШЕ.")]
        roles = [(m["role"], m["content"]) for m in model.messages("Now.", "en", "ru")]
        self.assertEqual(
            roles[1:3],
            [("user", "Source <x1>one</x1>."), ("assistant", "Перевод <x1>один</x1>.")],
        )
        self.assertEqual(roles[3:5], [("user", "Before."), ("assistant", "РАНЬШЕ.")])
        hy = self.translator(prompt="hy-mt")
        hy.examples = model.examples
        self.assertIn(
            "Approved example:\nSource <x1>one</x1>.\n=>\nПеревод <x1>один</x1>.",
            hy.messages("Now.", "en", "ru")[0]["content"],
        )

    def test_translate_epub_takes_examples_from_long_corrections(self):
        from translate import translate_epub

        long = (
            "The keeper wrote in his logbook every night, and the sea was calm again. "
            * 3
        )
        folder = self.epub(f"<p>{long}</p><p>Short one here.</p>")

        class Model:
            batch, chunk_chars, example_count = False, 4000, 3

            def __init__(self):
                self.examples = []

            def __call__(self, text, *a):
                return upper(text)

        report = translate_epub(
            folder / "in.epub", folder / "a.epub", Model(), "ru", progress=False
        )
        pairs = {p["source"][:10]: p for p in report["pairs"]}
        fixes = {
            p["key"]: {"source_sha": p["source_sha"], "translation": "Исправлено."}
            for p in report["pairs"]
            if p["source"].startswith(("The keeper", "Short"))
        }
        model = Model()
        translate_epub(
            folder / "in.epub",
            folder / "b.epub",
            model,
            "ru",
            progress=False,
            corrections=fixes,
        )
        self.assertEqual(len(model.examples), 1)
        self.assertTrue(model.examples[0][0].startswith("The keeper"))

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
