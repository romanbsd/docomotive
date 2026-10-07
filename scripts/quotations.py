"""Established translations of famous quotations, from Wikiquote.

An attributed quotation (an epigraph, a saying with its author's name) often
has a published translation readers know; a fresh machine translation of it
reads wrong. The author's target-language name comes from Wikipedia's
interlanguage link, their target-language Wikiquote page lists sayings in
established translations, and a chooser picks the one that translates the
source quotation, or none. Only the author's name and page titles are looked
up; the chooser sees the quotation and the candidates. Responses are cached.
"""

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

AGENT = {"User-Agent": "docomotive/1.0 (translation quotation lookup)"}


def fetch(url, params, cache):
    """GET a MediaWiki API answer, cached by URL and parameters."""
    key = hashlib.sha256(
        json.dumps([url, params], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    path = Path(cache) / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    request = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params), headers=AGENT
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        answer = json.load(response)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answer, ensure_ascii=False))
    return answer


def author_title(author, source, target, cache):
    """The target-language Wikipedia title of the author's article, "Surname,
    Name" reordered and qualifiers dropped; None without an article."""
    answer = fetch(
        f"https://{source}.wikipedia.org/w/api.php",
        {
            "action": "query",
            "format": "json",
            "titles": author,
            "prop": "langlinks|pageprops",
            "lllang": target,
            "ppprop": "disambiguation",
            "redirects": "1",
        },
        cache,
    )
    for page in answer.get("query", {}).get("pages", {}).values():
        links = page.get("langlinks")
        if links and "disambiguation" not in page.get("pageprops", {}):
            title = re.sub(r"\s*\([^)]*\)", "", links[0]["*"]).strip()
            if "," in title:
                surname, _, given = (part.strip() for part in title.partition(","))
                title = f"{given} {surname}"
            return title
    return None


def clean(wikitext):
    """Plain text of a quotation in wiki markup."""
    text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", wikitext, flags=re.S)
    text = re.sub(r"\{\{comment\|([^|}]*)\|[^}]*\}\}", r"\1", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>|'''?", "", text)
    return re.sub(r"\s+", " ", text).strip()


def template_quotes(wikitext):
    """First parameters of {{Q|...}} templates and top-level bullet lines."""
    quotes = []
    for match in re.finditer(r"\{\{Q\|", wikitext):
        depth, i, start = 0, match.end(), match.end()
        while i < len(wikitext):
            pair = wikitext[i : i + 2]
            if pair in ("{{", "[["):
                depth += 1
                i += 2
                continue
            if pair in ("}}", "]]"):
                if depth == 0 and pair == "}}":
                    break
                depth -= 1
                i += 2
                continue
            if wikitext[i] == "|" and depth == 0:
                break
            i += 1
        quotes.append(wikitext[start:i])
    quotes += re.findall(r"^\* (.+)$", wikitext, flags=re.M)
    return [q for q in (clean(q) for q in quotes) if len(q) >= 10]


def wikisource_quotes(title, target, cache, pages=2):
    """Paragraphs of the author's own works on target-language Wikisource
    (titles naming the author in parentheses, as in "Фрагменты (Гераклит;
    Нилендер)"; encyclopedia entries are skipped), numbering stripped."""
    found = fetch(
        f"https://{target}.wikisource.org/w/api.php",
        {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": title,
            "srlimit": "10",
        },
        cache,
    )
    surname = title.split()[-1]
    works = [
        hit["title"]
        for hit in found.get("query", {}).get("search", [])
        if re.search(rf"\([^)]*{re.escape(surname)}[^)]*\)", hit["title"])
    ][:pages]
    quotes = []
    for work in works:
        answer = fetch(
            f"https://{target}.wikisource.org/w/api.php",
            {
                "action": "parse",
                "format": "json",
                "page": work,
                "prop": "wikitext",
                "redirects": "1",
            },
            cache,
        )
        wikitext = answer.get("parse", {}).get("wikitext", {}).get("*", "")
        for line in wikitext.splitlines():
            line = re.sub(r"^[\s:*#]*(?:\(?\d+[a-z]?[.)]\s*)+", "", line)
            text = clean(line)
            if 10 <= len(text) <= 600 and not text.startswith(("{", "|", "=")):
                quotes.append(text)
    return quotes


def candidates(author, source, target, cache):
    """Sayings on the author's target-language Wikiquote page, then paragraphs
    of their works on Wikisource."""
    title = author_title(author, source, target, cache)
    if not title:
        return []
    answer = fetch(
        f"https://{target}.wikiquote.org/w/api.php",
        {
            "action": "parse",
            "format": "json",
            "page": title,
            "prop": "wikitext",
            "redirects": "1",
        },
        cache,
    )
    wikitext = answer.get("parse", {}).get("wikitext", {}).get("*", "")
    quotes = template_quotes(wikitext) + wikisource_quotes(title, target, cache)
    return list(dict.fromkeys(quotes))


def established(quote, author, source, target, cache, choose):
    """The established translation of quote by author, or None. choose(quote,
    candidates) returns the index of the candidate that translates quote, or
    None; candidates far from the quote's length are not offered."""
    options = [
        c
        for c in candidates(author, source, target, cache)
        if 0.4 * len(quote) <= len(c) <= 3 * len(quote) + 40
    ]
    # A Choice holds at most 254 options: pick within rounds of 100, then
    # among the round winners.
    while len(options) > 100:
        winners = []
        for i in range(0, len(options), 100):
            index = choose(quote, options[i : i + 100])
            if index is not None:
                winners.append(options[i + index])
        options = winners
    if not options:
        return None
    index = choose(quote, options)
    return quoted_part(options[index], quote) if index is not None else None


def quoted_part(text, quote):
    """The saying itself when a source sentence frames it in quotation marks
    ("по Гераклиту „…“"): the longest quoted span of comparable length,
    capitalized and closed with a full stop; otherwise the whole text."""
    spans = [
        span.strip()
        for span in re.findall(r"[„«“\"](.+?)[“»”\"]", text)
        if len(span.strip()) >= 0.4 * len(quote)
    ]
    if not spans:
        return text
    span = max(spans, key=len)
    span = span[:1].upper() + span[1:]
    return span if re.search(r"[.!?…]$", span) else span + "."


def jev_chooser(cache):
    """Choice over the candidates with TypeSafe Jev; a pick needs probability
    0.6 or more, else None."""
    import jev_judge

    def choose(quote, options):
        criteria = {"none": "None of the candidates translates the quotation."}
        criteria |= {str(i): text for i, text in enumerate(options, 1)}
        payload = {
            "model": jev_judge.MODEL,
            "state": {"quotation": quote, "candidates": options},
            "questions": {
                "match": {
                    "type": "choice",
                    "instructions": "Which candidate is a translation of `quotation` "
                    "(the same saying, possibly worded differently)?",
                    "criteria": criteria,
                }
            },
        }
        answer = jev_judge.post(payload, cache)["answers"]["match"]
        pick = answer.get("choice")
        if pick in (None, "none") or answer["probabilities"].get(pick, 0) < 0.6:
            return None
        return int(pick) - 1

    return choose


def model_chooser(translator):
    """The same choice asked of a local model as JSON."""

    def choose(quote, options):
        listing = "\n".join(f"{i}. {text}" for i, text in enumerate(options, 1))
        output = translator.chat(
            [
                {
                    "role": "system",
                    "content": "You match quotations to their published "
                    'translations. Reply with a JSON object {"index": n}: the '
                    "number of the candidate that translates the quotation (the "
                    "same saying, possibly worded differently), or 0 if none does.",
                },
                {
                    "role": "user",
                    "content": f"Quotation:\n{quote}\n\nCandidates:\n{listing}",
                },
            ],
            {"temperature": 0.0},
            40,
            format="json",
        )
        try:
            index = int(json.loads(output).get("index", 0))
        except ValueError, AttributeError, TypeError:
            return None
        return index - 1 if 1 <= index <= len(options) else None

    return choose
