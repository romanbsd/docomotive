"""Source-checked, book-local feedback for advisory suggestions and priority only."""

from collections import defaultdict

from symspellpy import SymSpell, Verbosity

from book_model import row_id
from review_edits import checked_binding, checked_overlays
from vocabulary import TOKEN


class FeedbackLedger:
    def __init__(self, payload, pages, source_hash, model_hash, profile_hash):
        # Reuse complete correction validation, including conflicting/overlapping edits.
        checked_overlays(payload, pages, source_hash, model_hash, profile_hash)
        self.records = []
        index = defaultdict(list)
        for rows in pages.values():
            for original in rows:
                row = dict(original)
                row.setdefault("row_id", row_id(row["page"], row, source_hash))
                index[row["page"], row["row_id"]].append(row)
        seen, spans = set(), {}
        for record in payload["decisions"]:
            if (
                record["decision"] not in {"keep", "ocr-error"}
                or record.get("reviewed") is not True
            ):
                continue
            ident = record.get("id")
            if not isinstance(ident, str) or not ident or ident in seen:
                raise ValueError("Feedback needs unique occurrence IDs")
            seen.add(ident)
            row = checked_binding(record, index)
            binding = record["binding"]
            span = binding["page"], binding["row_id"], binding["start"], binding["end"]
            value = (
                binding["before"]
                if record["decision"] == "keep"
                else record["replacement_or_note"].strip()
            )
            if span in spans:
                raise ValueError("Conflicting or duplicate feedback for one occurrence")
            spans[span] = record["decision"], value
            if not value.isalpha() or len(value) < 3 or not binding["before"].isalpha():
                continue
            if not any(
                m.start() == binding["start"] and m.end() == binding["end"]
                for m in TOKEN.finditer(row["text"])
            ):
                raise ValueError("Feedback must cover a complete source token")
            self.records.append(
                dict(
                    id=ident,
                    page=binding["page"],
                    form=value,
                    decision=record["decision"],
                    binding=dict(binding),
                )
            )
        self.sym = SymSpell(max_dictionary_edit_distance=2)
        for record in self.records:
            if record["decision"] == "ocr-error":
                self.sym.create_dictionary_entry(record["form"].lower(), 1)

    def prior_records(self, occurrences, decision):
        pages = [s["page"] for o in occurrences for s in o["sources"]]
        if not pages:
            return []
        first = min(pages)
        return [
            r for r in self.records if r["decision"] == decision and r["page"] < first
        ]

    def suggestions(self, word, occurrences):
        prior = self.prior_records(occurrences, "ocr-error")
        result = []
        for candidate in self.sym.lookup(word, Verbosity.ALL, 2):
            records = [r for r in prior if r["form"].lower() == candidate.term]
            if candidate.term == word or not records:
                continue
            result.append(
                dict(
                    term=candidate.term,
                    distance=candidate.distance,
                    kind="confirmed-book-feedback",
                    generators=["confirmed-book-feedback"],
                    attested_forms=sorted({r["form"] for r in records}),
                    feedback_records=records,
                )
            )
        return sorted(result, key=lambda r: (r["distance"], r["term"]))[:5]

    def annotate(self, item):
        """Keep every item; never let unchanged feedback override peer conflicts."""
        from candidates import merge_suggestions

        occurrences = item["occurrences"]
        merge_suggestions(
            item["suggestions"], self.suggestions(item["word"], occurrences)
        )
        kept = [
            r
            for r in self.prior_records(occurrences, "keep")
            if r["form"].lower() == item["word"]
        ]
        conflict = any(
            o.get("witness_proposal")
            and o["witness_proposal"].lower() != o["original"].lower()
            for o in occurrences
        )
        conflict |= any(
            "aligned-ocr-witnesses" in p.get("generators", [])
            for p in item["suggestions"]
        )
        if kept:
            item["unchanged_feedback"] = dict(
                records=kept,
                peer_conflict=conflict,
                effect="priority-unchanged" if conflict else "deprioritized",
            )
            if not conflict:
                item["review_priority"] = max(3, item["review_priority"])
        return item
