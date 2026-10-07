"""Cached free ISBN/edition enrichment. Build never contacts an API."""

import argparse
import datetime
import io
import json
import re
import unicodedata
from pathlib import Path
import requests
from PIL import Image
from common import ROOT, digest as sha, load_profile


def isbn(value):
    value = re.sub(r"[\s-]", "", value).upper()
    if len(value) == 10 and re.fullmatch(r"\d{9}[\dX]", value):
        if (
            sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(value))
            % 11
            == 0
        ):
            return value
    if len(value) == 13 and value.isdigit() and value.startswith(("978", "979")):
        if (
            sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10
            == 0
        ):
            return value
    raise ValueError("Invalid ISBN checksum")


def isbn13(value):
    value = isbn(value)
    if len(value) == 13:
        return value
    stem = "978" + value[:9]
    return stem + str(
        (-sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(stem))) % 10
    )


def norm(value):
    # Catalogues mix composed accents, combining accents and unaccented names.
    value = unicodedata.normalize("NFKD", value.lower())
    return re.sub(
        r"[^\w]", "", "".join(c for c in value if not unicodedata.combining(c))
    )


def verify_edition(record, book, authors):
    identities = {
        isbn13(v) for v in record.get("isbn_10", []) + record.get("isbn_13", [])
    }
    if book.get("isbn") and isbn13(book["isbn"]) not in identities:
        raise ValueError("API record does not contain the requested ISBN")
    if book.get("openlibrary_edition"):
        if record.get("key") != "/books/" + book["openlibrary_edition"]:
            raise ValueError("API record does not contain the requested edition")
        if record.get("publish_date") != book["date"]:
            raise ValueError("Edition date conflicts with the source profile")
    if not book.get("isbn") and not book.get("openlibrary_edition"):
        raise ValueError("An ISBN or exact Open Library edition is required")
    if norm(record.get("title", "")) != norm(book["title"]):
        raise ValueError("ISBN title conflicts with the source profile")
    # Reviewed catalog aliases accommodate transliteration without relaxing
    # the independent ISBN, title and publisher checks.
    identities = [book["author"]] + book.get("metadata_author_aliases", [])
    if not any(norm(a) == norm(identity) for a in authors for identity in identities):
        raise ValueError("ISBN author conflicts with the source profile")
    if not any(
        norm(p) in norm(book["publisher"]) or norm(book["publisher"]) in norm(p)
        for p in record.get("publishers", [])
    ):
        raise ValueError("ISBN publisher conflicts with the source profile")


def cover_info(data, min_edge=300):
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        w, h = image.size
        fmt = image.format
    if fmt not in {"JPEG", "PNG"}:
        raise ValueError("Unsupported cover image format")
    if not 0.4 <= w / h <= 1.2:
        raise ValueError("Implausible cover aspect ratio")
    return {
        "width": w,
        "height": h,
        "format": fmt,
        "sha256": sha(data),
        "suitable_main_cover": min(w, h) >= min_edge,
    }


class Cache:
    def __init__(self, path, offline=False):
        self.path = path
        self.offline = offline
        path.mkdir(parents=True, exist_ok=True)
        self.sources = []

    def get(self, url, binary=False):
        name = sha(url.encode())
        target = self.path / (name + (".bin" if binary else ".json"))
        meta = self.path / (name + ".provenance.json")
        if target.exists() and meta.exists():
            provenance = json.loads(meta.read_text())
            data = target.read_bytes()
            if sha(data) != provenance["sha256"]:
                raise ValueError("Metadata cache checksum mismatch")
        else:
            if self.offline:
                raise FileNotFoundError("No cached response for " + url)
            r = requests.get(
                url,
                headers={"User-Agent": "Docomotive/0.1 (single-book ISBN research)"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.content
            if not binary:
                json.loads(data)
            provenance = {
                "url": url,
                "resolved_url": r.url,
                "sha256": sha(data),
                "retrieved_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
            }
            target.write_bytes(data)
            meta.write_text(json.dumps(provenance, indent=2) + "\n")
        self.sources.append(provenance)
        return data if binary else json.loads(data)


def record_url(key):
    if not re.fullmatch(r"/(?:works|authors|books)/OL\d+[WAM]", key):
        raise ValueError("Unexpected Open Library record key")
    return "https://openlibrary.org" + key + ".json"


def enrich_doi(book, cache):
    from urllib.parse import quote

    doi = book["doi"].strip().lower()
    if not re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi):
        raise ValueError("Invalid DOI")
    record = cache.get("https://api.crossref.org/works/" + quote(doi, safe=""))[
        "message"
    ]
    authors = [
        " ".join([a.get("given", ""), a.get("family", "")]).strip()
        for a in record.get("author", [])
    ]
    title = book["title"] + (": " + book["subtitle"] if book.get("subtitle") else "")
    if record["DOI"].lower() != doi or not any(
        norm(a) == norm(book["author"]) for a in authors
    ):
        raise ValueError("DOI identity conflicts with source profile")
    if not any(
        norm(t) == norm(title + book.get("metadata_title_suffix", ""))
        for t in record.get("title", [])
    ):
        raise ValueError("DOI title conflicts with source profile")
    dates = record.get("published-print", record.get("published", {})).get(
        "date-parts", [[]]
    )[0]
    date = "-".join(str(v) if i == 0 else f"{v:02}" for i, v in enumerate(dates))
    return {
        "source_sha256": book["source_sha256"],
        "doi": doi,
        "authors": authors,
        "publication_date": book["date"],
        "final_publication_date": date,
        "journal": record.get("container-title", [""])[0],
        "volume": record.get("volume", ""),
        "issue": record.get("issue", ""),
        "page": record.get("page", ""),
        "identifiers": {"issn": record.get("ISSN", [])},
        "subjects": sorted(set(record.get("subject", []) + book.get("subjects", []))),
        "description": book.get("description", ""),
        "links": ["https://doi.org/" + doi],
        "rights": book.get("rights", ""),
        "covers": [],
        "sources": cache.sources,
        "source_version": book.get("source_version", "Article"),
        "conflicts": (
            [
                {
                    "field": "publication_date",
                    "api": date,
                    "source_profile": book["date"],
                    "resolution": "Preserve advance-article source date; record final journal date separately.",
                }
            ]
            if date != book["date"]
            else []
        ),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", type=Path, default=ROOT / "config/book.json")
    p.add_argument("--cache", type=Path, default=ROOT / "work/metadata/cache")
    p.add_argument("--offline", action="store_true")
    a = p.parse_args()
    book = load_profile(a.profile)
    cache = Cache(a.cache, a.offline)
    if book.get("doi") and not book.get("isbn"):
        enrich = enrich_doi(book, cache)
        (a.profile.parent / "enrichment.json").write_text(
            json.dumps(enrich, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps({"doi": enrich["doi"], "journal": enrich["journal"]}))
        return
    number = isbn(book["isbn"]) if book.get("isbn") else None
    edition_id = book.get("openlibrary_edition")
    if edition_id and not re.fullmatch(r"OL\d+M", edition_id):
        raise ValueError("Invalid Open Library edition identifier")
    if not number and not edition_id:
        raise ValueError("An ISBN or exact Open Library edition is required")
    edition = cache.get(
        "https://openlibrary.org/isbn/" + number + ".json"
        if number
        else "https://openlibrary.org/books/" + edition_id + ".json"
    )
    authors = [
        cache.get(record_url(author["key"]))["name"]
        for author in edition.get("authors", [])
    ]
    overrides = []
    for override in book.get("metadata_record_overrides", []):
        field = override["field"]
        if edition.get(field) != override["before"]:
            raise ValueError("Metadata override precondition changed: " + field)
        if not override.get("evidence"):
            raise ValueError("Metadata override lacks source evidence")
        edition = dict(edition, **{field: override["after"]})
        overrides.append(
            {
                "field": field,
                "api": override["before"],
                "source_profile": override["after"],
                "resolution": override["evidence"],
            }
        )
    verify_edition(edition, book, authors)
    work = (
        cache.get(record_url(edition["works"][0]["key"]))
        if edition.get("works")
        else {}
    )
    archive = {}
    item = book.get("archive_identifier")
    if item:
        if not number:
            raise ValueError("Archive enrichment requires a source ISBN")
        if not re.fullmatch(r"[\w-]+", item):
            raise ValueError("Invalid archive identifier")
        archive = cache.get("https://archive.org/metadata/" + item).get("metadata", {})
        if isbn13(number) not in {
            isbn13(i)
            for i in archive.get("isbn", [])
            if len(re.sub(r"[\s-]", "", i)) in [10, 13]
        }:
            raise ValueError("Archive item ISBN does not match this edition")
    desc = (
        edition.get("description")
        or work.get("description")
        or archive.get("description")
        or edition.get("notes")
        or ""
    )
    if isinstance(desc, dict):
        desc = desc.get("value", "")
    if isinstance(desc, list):
        desc = "\n".join(desc)
    published = edition.get("publish_date", "")
    date = (
        published
        if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", published)
        else book["date"]
    )
    subjects = sorted(set(edition.get("subjects", []) + work.get("subjects", [])))
    enrich = {
        "source_sha256": book["source_sha256"],
        "isbn": number,
        "isbn_13": isbn13(number) if number else None,
        "edition_key": edition["key"],
        "authors": authors,
        "publication_date": date,
        "publication_date_raw": published,
        "description": desc,
        "subjects": subjects,
        "identifiers": {
            "isbn_10": edition.get("isbn_10", []),
            "isbn_13": sorted(
                set(edition.get("isbn_13", []) + ([isbn13(number)] if number else []))
            ),
            "lccn": edition.get("lccn", []),
            "oclc": edition.get("oclc_numbers", []),
            "openlibrary": [edition["key"].split("/")[-1]],
        },
        "classifications": {
            k: edition.get(k, []) for k in ["lc_classifications", "dewey_decimal_class"]
        },
        "publication_places": edition.get("publish_places", []),
        "print_pages": edition.get("number_of_pages"),
        "links": ["https://openlibrary.org" + edition["key"]]
        + (["https://openlibrary.org" + work["key"]] if work else []),
        "rights": book.get("rights", ""),
        "covers": [],
        "conflicts": overrides,
    }
    if edition_id:
        enrich["openlibrary_edition"] = edition_id
    if published != book["date"]:
        enrich["conflicts"].append(
            {
                "field": "publication_date",
                "api": published,
                "source_profile": book["date"],
                "resolution": "Preserve normalized source-profile date unless API is ISO formatted; retain raw edition date.",
            }
        )
    # Exact ISBN image first; exact scanned archive item second. Work-level covers can be another edition.
    urls = (
        ["https://covers.openlibrary.org/b/isbn/" + number + "-L.jpg?default=false"]
        if number
        else [
            "https://covers.openlibrary.org/b/olid/"
            + edition_id
            + "-L.jpg?default=false"
        ]
    )
    if item:
        urls.append("https://archive.org/services/img/" + item)
    for url in urls:
        try:
            data = cache.get(url, True)
            info = cover_info(data)
            info["url"] = url
            asset = (
                a.cache.parent
                / "assets"
                / (info["sha256"] + (".jpg" if info["format"] == "JPEG" else ".png"))
            )
            asset.parent.mkdir(exist_ok=True)
            asset.write_bytes(data)
            info["path"] = (
                str(asset.resolve().relative_to(ROOT))
                if asset.resolve().is_relative_to(ROOT)
                else str(asset.resolve())
            )
            info["accepted"] = book.get("approved_cover_url") == url
            info["reason"] = (
                "Profile-approved cover visually checked against scan"
                if info["accepted"]
                else "Requires visual edition/cover-role review; ISBN association alone is insufficient"
            )
            enrich["covers"].append(info)
        except (requests.RequestException, ValueError, FileNotFoundError) as error:
            enrich["covers"].append(
                {"url": url, "accepted": False, "error": type(error).__name__}
            )
    enrich["sources"] = cache.sources
    (a.profile.parent / "enrichment.json").write_text(
        json.dumps(enrich, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "isbn": number,
                "edition": edition["key"],
                "subjects": len(subjects),
                "cover_candidates": len(enrich["covers"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
