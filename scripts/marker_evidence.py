"""Shared source observations and deterministic, abstaining marker alignment."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MarkerEvidence:
    """One actually observed reading at one measured source/text location."""

    candidate: int
    option_index: int
    position: tuple
    bbox: tuple
    normalized: tuple
    raw: tuple
    engines: tuple
    symbol: bool
    option: dict

    @classmethod
    def from_candidate(cls, candidate, option, index=0, option_index=0):
        return cls(
            index,
            option_index,
            (
                candidate.get("page", 0),
                candidate.get("row", index),
                option.get("marker_start", 0),
            ),
            tuple(candidate.get("bbox", ())),
            tuple(candidate.get("readings", ())),
            tuple(candidate.get("raw_readings", candidate.get("retry_readings", ()))),
            tuple(
                sorted({w["engine"] for w in option.get("corroborating_engines", [])})
            ),
            bool(candidate.get("raw_symbol_evidence")),
            option,
        )

    @property
    def number(self):
        return self.option["number"]

    @property
    def weight(self):
        # Preserve the initial selector's evidence scale. Repeated modes are
        # correlated: ten reads do not outweigh two reads. These are not odds.
        if self.option.get("anchor"):
            return 6  # Existing source superscript, not invented OCR votes.
        return (6 if self.option["votes"] >= 2 else 3) - self.option.get(
            "confusion_cost", 0
        )

    @property
    def rejected_symbol(self):
        return self.symbol and str(self.number) not in self.raw

    @property
    def rejection(self):
        label = str(self.number)
        if self.option.get("anchor"):
            return None
        if self.rejected_symbol:
            return "positive-symbol-evidence"
        raw_numeric = {v for v in self.raw if v.isascii() and v.isdigit()}
        if (
            not self.option.get("raw_corroborated")
            and len(raw_numeric) == 1
            and label not in raw_numeric
            and self.raw.count(next(iter(raw_numeric))) >= 2
        ):
            return "contradictory-raw-glyph"
        readings = self.normalized
        if self.option.get("raw_corroborated"):
            if not self.engines or not self.raw:
                return "missing-corroboration"
            readings = self.raw
            if readings.count(label) < 2:
                readings += self.normalized
        numeric = {v for v in readings if v.isascii() and v.isdigit()}
        if self.option["votes"] < 2 or readings.count(label) < 2:
            return "insufficient-glyph-reads"
        if numeric != {label}:
            return "conflicting-glyph-reads"
        return None

    def report(self):
        return dict(
            source_kind=(
                "existing-superscript"
                if self.option.get("anchor")
                else "glyph-observation"
            ),
            position=list(self.position),
            bbox=list(self.bbox),
            normalized_readings=list(self.normalized),
            raw_readings=list(self.raw),
            corroborating_engines=list(self.engines),
            number=self.number,
            evidence_score=self.weight,
            rejection=self.rejection,
        )


def align_markers(evidence, minimum_margin=3):
    """Maximum-weight increasing paths with per-assignment exclusion checks.

    Missing labels and unused glyphs are allowed; no synthetic observations
    exist. A three-point margin exceeds the two-point adjacency bonus, so one
    convenient neighbor alone cannot resolve otherwise equal placements.
    """
    nodes = sorted(
        evidence, key=lambda e: (e.position, e.number, e.candidate, e.option_index)
    )

    def same_ink(a, b):
        if a.candidate >= 0 and a.candidate == b.candidate:
            return True
        if a.position[0] != b.position[0] or len(a.bbox) != 4 or len(b.bbox) != 4:
            return False
        x, y, xx, yy = a.bbox
        u, v, uu, vv = b.bbox
        intersection = max(0, min(xx, uu) - max(x, u)) * max(0, min(yy, vv) - max(y, v))
        area = min((xx - x) * (yy - y), (uu - u) * (vv - v))
        # Majority overlap identifies duplicate crops, including observations
        # assigned to different OCR rows. Touching crop padding is insufficient.
        return area > 0 and intersection > 0.5 * area

    # Pairwise path edges cannot detect A/B/A reuse. Quarantine every conflicting
    # physical observation before DP; consistent source order is then sufficient.
    collisions = set()
    for i, node in enumerate(nodes):
        for j in range(i):
            if same_ink(nodes[j], node) and nodes[j].position != node.position:
                collisions.update((i, j))
    changed = True
    while changed:
        changed = False
        for i, node in enumerate(nodes):
            if i not in collisions and any(
                same_ink(node, nodes[j]) for j in collisions
            ):
                collisions.add(i)
                changed = True
                break
    blockers = {
        e.number
        for e in nodes
        if not e.option.get("anchor")
        and e.rejection is not None
        and not e.rejected_symbol
    }
    inactive = {
        i for i, e in enumerate(nodes) if i in collisions or e.rejection is not None
    }

    def edge(a, b):
        if same_ink(a, b):
            return False
        if (
            a.position >= b.position
            or a.number > b.number
            or (
                a.number == b.number
                and not (a.option.get("anchor") and b.option.get("anchor"))
            )
        ):
            return False
        # Distinct labels cannot rewrite overlapping gaps in the same row.
        return a.position[:2] != b.position[:2] or a.option.get(
            "end", a.position[2]
        ) <= b.option.get("start", b.position[2])

    # Geometry is evaluated once rather than during every exclusion replay.
    predecessors = [
        [j for j in range(i) if j not in inactive and edge(nodes[j], node)]
        for i, node in enumerate(nodes)
    ]

    def solve(exclude=None):
        scores, paths = [], []
        for i, node in enumerate(nodes):
            if i == exclude or i in inactive:
                scores.append(float("-inf"))
                paths.append(())
                continue
            choices = [(node.weight, (i,))]
            for j in predecessors[i]:
                if paths[j]:
                    bonus = 2 if node.number == nodes[j].number + 1 else 0
                    choices.append((scores[j] + node.weight + bonus, paths[j] + (i,)))
            # Sorting ties makes replay stable; the exclusion margin, not this
            # arbitrary ordering, determines whether a reading can be applied.
            score, path = min(choices, key=lambda v: (-v[0], v[1]))
            scores.append(score)
            paths.append(path)
        return min([(0, ())] + list(zip(scores, paths)), key=lambda v: (-v[0], v[1]))

    best, path = solve()
    decisions = []
    for i, node in enumerate(nodes):
        competing = solve(i)[0] if i in path else best
        margin = best - competing
        status = (
            "source-position-conflict"
            if i in collisions
            else node.rejection
            or (
                "competing-unverified-marker"
                if node.number in blockers and not node.option.get("anchor")
                else (
                    "selected"
                    if i in path and margin >= minimum_margin
                    else (
                        "sequence-ambiguous"
                        if margin < minimum_margin
                        else "chapter-order-conflict"
                    )
                )
            )
        )
        decisions.append(
            dict(
                candidate=node.candidate,
                option_index=node.option_index,
                status=status,
                best_score=best,
                competing_score=competing,
                margin=margin,
                **node.report(),
            )
        )
    return decisions


def trusted_anchors(anchors, maximum):
    """Keep anchors common to every longest nondecreasing source sequence.

    Existing conflicting superscripts are quarantined, never silently edited.
    Repeated citations are allowed. Sequence alone cannot choose between tied
    explanations for an existing anchor error.
    """
    valid = [a for a in anchors if 1 <= a["number"] <= maximum]

    def length(exclude=None):
        lengths = []
        for i, a in enumerate(valid):
            lengths.append(
                0
                if i == exclude
                else 1
                + max(
                    [0]
                    + [
                        lengths[j]
                        for j in range(i)
                        if lengths[j] and valid[j]["number"] <= a["number"]
                    ]
                )
            )
        return max(lengths, default=0)

    best = length()
    return [
        dict(a, trusted=(a in valid and length(valid.index(a)) < best)) for a in anchors
    ]
