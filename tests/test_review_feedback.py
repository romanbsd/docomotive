"""Confirmed spellings remain local, source-bound and advisory."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import digest
from review_feedback import FeedbackLedger


def fixture(decision="ocr-error", before="cafè", after="café", reviewed=True):
    text = f"old {before} story today"
    row = dict(page=1, row_id="row", bbox=[0.1, 0.2, 0.9, 0.3], text=text)
    binding = dict(
        page=1,
        row_id="row",
        bbox=row["bbox"],
        row_text_sha256=digest(text.encode()),
        before=before,
        start=4,
        end=4 + len(before),
    )
    record = dict(
        id="seed",
        decision=decision,
        reviewed=reviewed,
        binding=binding,
        replacement_or_note=after,
    )
    payload = dict(
        format_version=2,
        source_sha256="source",
        model_sha256="model",
        profile_sha256="profile",
        decisions=[record],
    )
    return payload, {1: [row]}


def ledger(payload, pages):
    return FeedbackLedger(payload, pages, "source", "model", "profile")


def item(word="cafè", page=2):
    return dict(
        word=word,
        suggestions=[],
        review_priority=1,
        occurrences=[dict(original=word, sources=[dict(page=page)])],
    )


class FeedbackTests(unittest.TestCase):
    def test_corrected_literal_is_only_proposed_on_later_pages(self):
        payload, pages = fixture()
        before = deepcopy(pages)
        bank = ledger(payload, pages)
        self.assertEqual(bank.suggestions("cafè", item(page=1)["occurrences"]), [])
        result = bank.suggestions("cafè", item()["occurrences"])
        self.assertEqual(result[0]["term"], "café")
        self.assertEqual(result[0]["attested_forms"], ["café"])
        self.assertEqual(pages, before)

    def test_all_source_model_profile_mismatches_fail_closed(self):
        payload, pages = fixture()
        for key in ("source_sha256", "model_sha256", "profile_sha256"):
            bad = deepcopy(payload)
            bad[key] = "other-book"
            with self.assertRaisesRegex(ValueError, "stale"):
                ledger(bad, pages)

    def test_keep_needs_acknowledgement_and_exact_current_binding(self):
        payload, pages = fixture("keep", reviewed=False)
        self.assertEqual(ledger(payload, pages).records, [])
        payload["decisions"][0]["reviewed"] = True
        pages[1][0]["text"] = "changed source row"
        with self.assertRaisesRegex(ValueError, "changed"):
            ledger(payload, pages)

    def test_keep_only_deprioritizes_and_peer_conflict_vetoes_it(self):
        payload, pages = fixture("keep")
        bank = ledger(payload, pages)
        original = item()
        same = bank.annotate(original)
        self.assertIs(same, original)
        self.assertEqual(same["review_priority"], 3)
        self.assertEqual(len(same["occurrences"]), 1)
        disputed = item()
        disputed["occurrences"][0]["witness_proposal"] = "café"
        self.assertEqual(bank.annotate(disputed)["review_priority"], 1)
        peer = item()
        peer["suggestions"] = [dict(term="café", generators=["aligned-ocr-witnesses"])]
        self.assertEqual(bank.annotate(peer)["review_priority"], 1)

    def test_latest_snapshot_revokes_feedback_and_editorial_is_not_seeded(self):
        for choice in ("uncertain", "printed-typo"):
            payload, pages = fixture(choice)
            self.assertEqual(ledger(payload, pages).records, [])
        payload, pages = fixture()
        payload["decisions"] = []
        self.assertEqual(ledger(payload, pages).records, [])

    def test_conflicting_keep_and_correction_cannot_certify_same_occurrence(self):
        payload, pages = fixture()
        second = deepcopy(payload["decisions"][0])
        second.update(id="keep", decision="keep")
        payload["decisions"].append(second)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            ledger(payload, pages)

    def test_future_and_same_page_feedback_cannot_change_group_priority(self):
        payload, pages = fixture("keep")
        bank = ledger(payload, pages)
        earliest = item(page=1)
        earliest["occurrences"].extend(item(page=3)["occurrences"])
        self.assertEqual(bank.annotate(earliest)["review_priority"], 1)
        self.assertEqual(bank.annotate(item(page=0))["review_priority"], 1)

    def test_invalid_correction_and_multiword_target_cannot_be_seeded(self):
        payload, pages = fixture(reviewed=False)
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            ledger(payload, pages)
        payload, pages = fixture(after="two words")
        self.assertEqual(ledger(payload, pages).records, [])

    def test_keep_cannot_certify_a_substring_of_a_word(self):
        payload, pages = fixture("keep", before="cafeteria")
        binding = payload["decisions"][0]["binding"]
        binding.update(before="cafe", end=8)
        with self.assertRaisesRegex(ValueError, "complete source token"):
            ledger(payload, pages)


if __name__ == "__main__":
    unittest.main()
