"""Sequence constraints rank real observations, never invent missing digits."""

import itertools
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from marker_evidence import MarkerEvidence, align_markers, trusted_anchors


def marker(index, number, votes=2, readings=None, **option):
    return MarkerEvidence.from_candidate(
        dict(page=1, row=index, readings=readings or [str(number)] * votes),
        dict(number=number, votes=votes, start=10, end=11, marker_start=10, **option),
        index,
    )


class MarkerEvidenceTests(unittest.TestCase):
    def test_sequence_resolves_duplicate_only_with_joint_support(self):
        # The late 2 conflicts with a visible 3; the earlier 2 fits both.
        decisions = align_markers(
            [marker(0, 1), marker(1, 2), marker(2, 3), marker(3, 2)]
        )
        self.assertEqual(
            [d["number"] for d in decisions if d["status"] == "selected"], [1, 2, 3]
        )
        self.assertGreaterEqual(decisions[1]["margin"], 3)

    def test_equal_and_near_equal_paths_abstain(self):
        for evidence in (
            [marker(0, 2), marker(1, 2)],
            [marker(0, 2), marker(1, 3), marker(2, 2)],
        ):
            decisions = align_markers(evidence)
            if len(evidence) == 2:
                self.assertFalse(any(d["status"] == "selected" for d in decisions))
        decisions = align_markers([marker(0, 1), marker(1, 3), marker(2, 2)])
        self.assertFalse(
            any(d["status"] == "selected" and d["number"] in (2, 3) for d in decisions)
        )

    def test_missing_label_is_not_manufactured(self):
        decisions = align_markers([marker(0, 1), marker(1, 3)])
        self.assertEqual([d["number"] for d in decisions], [1, 3])

    def test_conflicting_pixels_are_never_accepted_by_sequence(self):
        decisions = align_markers(
            [marker(0, 1), marker(1, 2, readings=["2", "2", "8"]), marker(2, 3)]
        )
        self.assertEqual(decisions[1]["status"], "conflicting-glyph-reads")

    def test_one_glyph_cannot_supply_two_numbers(self):
        first = marker(0, 1)
        second = MarkerEvidence.from_candidate(
            dict(page=1, row=0, readings=["2", "2"]),
            dict(number=2, votes=2, start=12, end=13, marker_start=12),
            0,
            1,
        )
        self.assertFalse(
            any(d["status"] == "selected" for d in align_markers([first, second]))
        )

    def test_overlapping_edits_and_duplicate_source_crops_abstain(self):
        first = marker(0, 1)
        second = MarkerEvidence.from_candidate(
            dict(page=1, row=0, readings=["2", "2"]),
            dict(number=2, votes=2, start=10, end=13, marker_start=12),
            1,
        )
        self.assertFalse(
            any(d["status"] == "selected" for d in align_markers([first, second]))
        )
        first = MarkerEvidence.from_candidate(
            dict(page=1, row=0, bbox=[0.1, 0.1, 0.2, 0.2], readings=["1", "1"]),
            dict(number=1, votes=2, marker_start=10),
            0,
        )
        second = MarkerEvidence.from_candidate(
            dict(page=1, row=1, bbox=[0.11, 0.1, 0.21, 0.2], readings=["2", "2"]),
            dict(number=2, votes=2, marker_start=10),
            1,
        )
        self.assertFalse(
            any(d["status"] == "selected" for d in align_markers([first, second]))
        )

    def test_anchor_quarantine_preserves_uncontested_regions(self):
        anchors = [
            dict(number=n, position=[1, i, 0]) for i, n in enumerate([1, 8, 2, 3, 4])
        ]
        result = trusted_anchors(anchors, 8)
        self.assertEqual([a["number"] for a in result if a["trusted"]], [1, 2, 3, 4])
        tied = trusted_anchors(
            [dict(number=n, position=[1, i, 0]) for i, n in enumerate([1, 3, 2, 4])], 4
        )
        self.assertEqual([a["number"] for a in tied if a["trusted"]], [1, 4])
        self.assertEqual(anchors[1]["number"], 8)

    def test_repeated_and_out_of_range_anchors(self):
        anchors = [
            dict(number=n, position=[1, i, 0]) for i, n in enumerate([1, 1, 2, 99, 3])
        ]
        self.assertEqual(
            [a["number"] for a in trusted_anchors(anchors, 3) if a["trusted"]],
            [1, 1, 2, 3],
        )

    def test_nonadjacent_reuse_and_transitive_crop_collisions_quarantine(self):
        first = marker(0, 1)
        last = MarkerEvidence.from_candidate(
            dict(page=1, row=0, readings=["3", "3"]),
            dict(number=3, votes=2, marker_start=30),
            0,
            1,
        )
        middle = MarkerEvidence.from_candidate(
            dict(page=1, row=0, readings=["2", "2"]),
            dict(number=2, votes=2, marker_start=20),
            1,
        )
        decisions = align_markers([first, middle, last])
        self.assertEqual(
            [d["number"] for d in decisions if d["status"] == "selected"], [2]
        )
        self.assertEqual(decisions[0]["status"], "source-position-conflict")
        self.assertEqual(decisions[2]["status"], "source-position-conflict")
        chain = [
            MarkerEvidence.from_candidate(
                dict(
                    page=1,
                    row=i,
                    bbox=[x, 0.1, x + 0.1, 0.2],
                    readings=[str(i + 1)] * 2,
                ),
                dict(number=i + 1, votes=2, marker_start=10),
                i,
            )
            for i, x in enumerate([0.1, 0.14, 0.18])
        ]
        self.assertTrue(
            all(d["status"] == "source-position-conflict" for d in align_markers(chain))
        )

    def test_rejected_reads_cannot_supply_sequence_bridges(self):
        decisions = align_markers(
            [marker(0, 1), marker(1, 2, readings=["2", "2", "8"]), marker(2, 3)]
        )
        self.assertEqual(decisions[0]["best_score"], 12)
        self.assertEqual(decisions[1]["status"], "conflicting-glyph-reads")
        self.assertEqual(
            [d["number"] for d in decisions if d["status"] == "selected"], [1, 3]
        )

    def test_contradictory_raw_pixels_and_legacy_retry_evidence(self):
        option = dict(number=2, votes=2, marker_start=10)
        candidate = dict(
            page=1, row=0, readings=["2", "2"], retry_readings=["8", "8", "8"]
        )
        evidence = MarkerEvidence.from_candidate(candidate, option)
        self.assertEqual(evidence.raw, ("8", "8", "8"))
        self.assertEqual(evidence.rejection, "contradictory-raw-glyph")
        noise = MarkerEvidence.from_candidate(
            dict(candidate, raw_readings=["2", "2", "4", "7"]), option
        )
        self.assertIsNone(noise.rejection)
        decisions = align_markers([evidence, marker(1, 2)])
        self.assertEqual(decisions[1]["status"], "competing-unverified-marker")

    def test_score_and_exclusion_margins_match_exhaustive_paths(self):
        rng = random.Random(42)
        for _ in range(30):
            evidence = [marker(i, rng.randint(1, 5)) for i in range(6)]
            paths = []
            for size in range(7):
                for ids in itertools.combinations(range(6), size):
                    ns = [evidence[i].number for i in ids]
                    if any(a >= b for a, b in zip(ns, ns[1:])):
                        continue
                    score = 6 * size + 2 * sum(b == a + 1 for a, b in zip(ns, ns[1:]))
                    paths.append((score, ids))
            best = max(score for score, _ in paths)
            decisions = align_markers(evidence)
            for d in decisions:
                alternate = max(
                    score for score, ids in paths if d["candidate"] not in ids
                )
                self.assertEqual(d["best_score"], best)
                self.assertEqual(d["margin"], best - alternate)

    def test_multi_digit_labels_require_complete_readings(self):
        valid = marker(0, 12)
        invalid = marker(1, 13, readings=["1", "3", "13"])
        decisions = align_markers([valid, invalid])
        self.assertEqual(decisions[0]["status"], "selected")
        self.assertEqual(decisions[1]["status"], "insufficient-glyph-reads")

    def test_existing_anchor_is_not_reported_as_fresh_crop_votes(self):
        anchor = MarkerEvidence.from_candidate(
            dict(page=1, row=0),
            dict(number=1, votes=0, anchor=True, marker_start=10),
            -1,
        )
        self.assertEqual(anchor.report()["source_kind"], "existing-superscript")
        self.assertEqual(anchor.normalized, ())
        self.assertIsNone(anchor.rejection)
        self.assertEqual(anchor.weight, 6)

    def test_retries_do_not_multiply_evidence_strength(self):
        self.assertEqual(marker(0, 2, 2).weight, marker(0, 2, 10).weight)
        self.assertEqual(align_markers([]), [])


if __name__ == "__main__":
    unittest.main()
