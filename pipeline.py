#!/usr/bin/env python3
"""Shared pipeline for Mini Connected Papers (CLI + web app).

Similarity between any two papers:
  similarity = 0.5 * bibliographic coupling + 0.5 * co-citation
  (+ 0.1 bonus if they cite each other directly, capped at 1.0)

Data source: OpenAlex. Stdlib only.
"""

import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.openalex.org"
USER_AGENT = "mini-connected-papers/0.1 (personal literature tool)"
NEIGHBOR_FIELDS = ("id,display_name,publication_year,cited_by_count,"
                   "abstract_inverted_index,primary_location,doi,referenced_works")

STOP = set("a an and are as at be by for from has have in into is it its of on or "
           "that the this to was were with we our you your can may might not no via per".split())


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Client:
    """Small OpenAlex client with throttling (10 rps) and retries."""

    def __init__(self, mailto=None, api_key=None):
        self.extra = {}
        if mailto:
            self.extra["mailto"] = mailto
        if api_key:
            self.extra["api_key"] = api_key
        self._last = 0.0

    def get(self, path, params=None, retries=6):
        params = dict(params or {})
        params.update(self.extra)
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(retries):
            self._throttle()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", "replace")[:200]
                if err.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    wait = min(60, 5 * (2 ** attempt))
                    log(f"  HTTP {err.code} - retrying in {wait}s")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"GET {url} -> HTTP {err.code}: {body}") from None
            except urllib.error.URLError as err:
                if attempt < retries - 1:
                    time.sleep(5)
                    continue
                raise RuntimeError(f"GET {url} -> {err}") from None
        raise RuntimeError("unreachable")

    def _throttle(self):
        wait = 0.11 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


def oa_id(work_or_url):
    match = re.search(r"(W\d+)",
                      work_or_url if isinstance(work_or_url, str) else work_or_url["id"])
    return match.group(1)


def uninvert(inverted):
    if not inverted:
        return ""
    positions = {}
    for word, idxs in inverted.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def work_abstract(work):
    return uninvert(work.get("abstract_inverted_index"))


def work_venue(work):
    loc = work.get("primary_location") or {}
    return (loc.get("source") or {}).get("display_name", "")


def work_url(work):
    return work.get("doi") or f"https://openalex.org/{oa_id(work)}"


def first_author_name(work):
    a = (work.get("authorships") or [{}])[0].get("author") or {}
    return a.get("display_name", "") or ""


def last_name(display_name):
    parts = (display_name or "").strip().split()
    return parts[-1] if parts else ""


# ---------------------------------------------------------------- resolution

def resolve_seed(client, query):
    q = query.strip()
    if re.fullmatch(r"W\d+", q):
        return client.get(f"/works/{q}")
    doi = None
    if q.lower().startswith("doi:"):
        doi = q[4:].strip()
    elif re.search(r"10\.\d{4,9}/", q):
        doi = q
    if doi:
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
        return client.get(f"/works/doi:{doi}")
    m = re.fullmatch(r"(arxiv:)?(\d{4}\.\d{4,5})(v\d+)?", q, re.I)
    if m:
        return client.get(f"/works/doi:10.48550/arXiv.{m.group(2)}")
    d = client.get("/works", {"filter": f"title.search:{q}", "per-page": 3,
                              "sort": "cited_by_count:desc",
                              "select": "id,display_name,publication_year,cited_by_count"})
    if not d.get("results"):
        sys.exit(f"No OpenAlex results for: {q}")
    best = d["results"][0]
    log(f"Title search -> {best['display_name']} ({best['publication_year']}, "
        f"{best['cited_by_count']} citations)")
    return client.get(f"/works/{oa_id(best)}")


def fetch_citers(client, seed_id, max_n=300):
    citers, page = [], 1
    while len(citers) < max_n and page <= 5:
        d = client.get("/works", {"filter": f"cites:{seed_id}", "per-page": 100,
                                  "page": page, "sort": "cited_by_count:desc",
                                  "select": NEIGHBOR_FIELDS})
        res = d.get("results") or []
        citers.extend(res)
        if len(res) < 100:
            break
        page += 1
    return citers[:max_n]


def fetch_references(client, ref_ids):
    out = []
    for i in range(0, len(ref_ids), 100):
        ids = "|".join(oa_id(u) for u in ref_ids[i:i + 100])
        d = client.get("/works", {"filter": f"ids.openalex:{ids}", "per-page": 100,
                                  "select": NEIGHBOR_FIELDS})
        out.extend(d.get("results") or [])
    return out


def fetch_first_authors(client, ids):
    """Batch-fetch first-author surnames. Returns {openalex_id: surname}."""
    out = {}
    for i in range(0, len(ids), 100):
        d = client.get("/works", {"filter": "ids.openalex:" + "|".join(ids[i:i + 100]),
                                  "per-page": 100, "select": "id,authorships"})
        for w in d.get("results") or []:
            out[w["id"].split("/")[-1]] = last_name(first_author_name(w))
    return out


BIB_ENTRY_TYPES = {
    "article": "article", "article-journal": "article", "article-newspaper": "article",
    "review": "article", "letter": "article",
    "editorial": "article", "peer-review": "article", "correction": "misc",
    "retraction": "misc", "book": "book", "chapter": "incollection",
    "report": "techreport", "dataset": "misc", "dissertation": "phdthesis",
    "mastersthesis": "mastersthesis", "webpage": "misc", "reference-entry": "misc",
    "standard": "misc", "preprint": "misc", "post-print": "misc",
}


def _bib_key(surname, year, title, used):
    word = re.sub(r"[^a-z0-9]", "", (title or "").split()[0].lower()) if (title or "").split() else "paper"
    base = f"{(surname or 'anon').lower()}{year or 'nd'}{word}"
    key, i = base, 2
    while key in used:
        key = f"{base}{chr(96 + i)}"
        i += 1
    used.add(key)
    return key


def _bib_author(display_name):
    parts = (display_name or "").strip().split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[-1] + ", " + " ".join(parts[:-1])


def work_to_bibtex(work, used):
    wtype = BIB_ENTRY_TYPES.get(work.get("type") or "", "misc")
    year = work.get("publication_year") or (work.get("publication_date") or "")[:4]
    title = work.get("display_name") or work.get("title") or ""
    a0 = ((work.get("authorships") or [{}])[0].get("author") or {}).get("display_name", "")
    surname = a0.strip().split()[-1] if a0.strip() else ""
    key = _bib_key(surname, year, title, used)
    authors = [a for a in (_bib_author((x.get("author") or {}).get("display_name", ""))
                           for x in work.get("authorships") or []) if a]
    biblio = work.get("biblio") or {}
    source = (work.get("primary_location") or {}).get("source") or {}
    venue = source.get("display_name", "")
    pages = ""
    if biblio.get("first_page") and biblio.get("last_page")             and str(biblio["first_page"]) != str(biblio["last_page"]):
        pages = f"{biblio['first_page']}--{biblio['last_page']}"
    elif biblio.get("first_page"):
        pages = str(biblio["first_page"])
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", work.get("doi") or "", flags=re.I)

    lines = [f"@{wtype}{{{key},"]
    if authors:
        lines.append(f"  author = {{{' and '.join(authors)}}},")
    lines.append(f"  title = {{{title}}},")
    if wtype == "article" and venue:
        lines.append(f"  journal = {{{venue}}},")
    if wtype == "incollection" and venue:
        lines.append(f"  booktitle = {{{venue}}},")
    if biblio.get("volume"):
        lines.append(f"  volume = {{{biblio['volume']}}},")
    if biblio.get("issue"):
        lines.append(f"  number = {{{biblio['issue']}}},")
    if pages:
        lines.append(f"  pages = {{{pages}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    lines.append(f"  url = {{{work.get('doi') or f'https://openalex.org/{oa_id(work)}'}}},")
    lines.append("}")
    return "\n".join(lines)


def fetch_bibtex(client, ids):
    """Fetch full records for the given OpenAlex ids and return a BibTeX string."""
    works = []
    for i in range(0, len(ids), 100):
        d = client.get("/works", {"filter": "ids.openalex:" + "|".join(ids[i:i + 100]),
                                  "per-page": 100,
                                  "select": "id,doi,type,display_name,publication_year,"
                                            "publication_date,authorships,biblio,"
                                            "primary_location"})
        works.extend(d.get("results") or [])
    used = set()
    return "\n\n".join(work_to_bibtex(w, used) for w in works) + "\n"


def fetch_citer_ids(client, work_id, max_citers=600):
    ids, page = set(), 1
    while len(ids) < max_citers and page <= 4:
        d = client.get("/works", {"filter": f"cites:{work_id}", "per-page": 200,
                                  "page": page, "select": "id"})
        res = d.get("results") or []
        ids.update(oa_id(w) for w in res)
        if len(res) < 200:
            break
        page += 1
    return ids


# ------------------------------------------------------------------ scoring

def text_vector(title, abstract):
    vec = {}
    for text, weight in ((title or "", 2.0), (abstract or "", 1.0)):
        for tok in re.findall(r"[a-z0-9]{2,}", text.lower()):
            if tok not in STOP:
                vec[tok] = vec.get(tok, 0.0) + weight
    return vec


def cosine(a, b):
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(av * b.get(t, 0.0) for t, av in a.items())
    if dot == 0:
        return 0.0
    return dot / (math.sqrt(sum(v * v for v in a.values()))
                  * math.sqrt(sum(v * v in b.values())))


def ref_id_set(work):
    return {oa_id(u) for u in work.get("referenced_works") or []}


def prelim_score(seed, seed_vec, work):
    """Fast pre-ranking: text similarity + bibliographic coupling with the seed."""
    t = cosine(seed_vec, text_vector(work.get("display_name"), work_abstract(work)))
    srefs, wrefs = seed["refs"], ref_id_set(work)
    c = 0.0
    if srefs and wrefs:
        c = len(srefs & wrefs) / min(len(srefs), len(wrefs))
    return 0.65 * t + 0.35 * c


def pair_similarity(a, b):
    """Connected-Papers-style similarity. Returns (sim, shared_refs, cited_together_by, direct)."""
    coupling = len(a["refs"] & b["refs"])
    cocite = len(a["citers"] & b["citers"])
    c_norm = coupling / min(len(a["refs"]), len(b["refs"])) if a["refs"] and b["refs"] else 0.0
    k_norm = cocite / min(len(a["citers"]), len(b["citers"])) \
        if a["citers"] and b["citers"] else 0.0
    sim = 0.5 * c_norm + 0.5 * k_norm
    direct = b["id"] in a["refs"] or a["id"] in b["refs"]
    if direct:
        sim = min(1.0, sim + 0.1)
    return round(sim, 4), coupling, cocite, direct


# ------------------------------------------------------------------- colors

def year_color(year, ymin, ymax):
    t = 0.0 if ymax <= ymin else (year - ymin) / (ymax - ymin)
    light, dark = (0xd9, 0xe8, 0xf5), (0x08, 0x30, 0x6b)
    return "#" + "".join(f"{int(round(light[i] + (dark[i] - light[i]) * t)):02x}"
                         for i in range(3))


def node_size(citations):
    return round(6 + 1.6 * min(math.sqrt(max(citations or 0, 0)), 20), 1)


# ------------------------------------------------------------------- pipeline

def build(client, query, pool=80, max_citers=300, min_sim=0.12,
          max_edges=500, depth=1, log=log):
    seed = resolve_seed(client, query)
    log(f"Seed: {seed.get('title')} ({seed.get('publication_year')}) "
        f"[{seed.get('cited_by_count')} citations]")
    seed["refs"] = ref_id_set(seed)
    seed_vec = text_vector(seed.get("title"), work_abstract(seed))

    log(f"Fetching citing papers (up to {max_citers})...")
    citers = fetch_citers(client, oa_id(seed), max_citers)
    log(f"  got {len(citers)} citers")
    log("Fetching references...")
    refs = fetch_references(client, seed.get("referenced_works") or [])
    log(f"  got {len(refs)} references")

    cands = {}

    def add_candidate(work, kind):
        wid = oa_id(work)
        if wid == oa_id(seed):
            return
        entry = cands.get(wid)
        if entry is None:
            entry = cands[wid] = {
                "id": wid,
                "title": work.get("display_name") or work.get("title") or wid,
                "year": work.get("publication_year"),
                "citations": work.get("cited_by_count") or 0,
                "venue": work_venue(work),
                "url": work_url(work),
                "abstract": work_abstract(work),
                "refs": ref_id_set(work),
                "kinds": set(),
            }
        entry["kinds"].add(kind)

    for w in citers:
        add_candidate(w, "in")
    for w in refs:
        add_candidate(w, "out")
    log(f"1-hop candidates: {len(cands)}")

    if depth >= 2 and cands:
        top = sorted(cands.values(), key=lambda n: -prelim_score(seed, seed_vec, n))[:8]
        log(f"Expanding 2nd hop around top {len(top)} papers...")
        for n in top:
            d = client.get("/works", {"filter": f"cites:{n['id']}", "per-page": 100,
                                      "sort": "cited_by_count:desc",
                                      "select": NEIGHBOR_FIELDS})
            for w in d.get("results") or []:
                add_candidate(w, "2nd")
            for w in fetch_references(client, list(n["refs"])):
                add_candidate(w, "2nd")
        log(f"candidates after 2nd hop: {len(cands)}")

    pset = sorted(cands.values(), key=lambda n: -prelim_score(seed, seed_vec, n))[:pool]
    log(f"Pool: {len(pset)} of {len(cands)} candidates")

    log("Fetching first authors for the pool...")
    authors = fetch_first_authors(client, [n["id"] for n in pset])
    for n in pset:
        n["first_author"] = authors.get(n["id"], "")

    log("Fetching citing-paper sets for co-citation (1 request per pool paper)...")
    seed_citers = {oa_id(w) for w in citers}
    for n in pset:
        n["citers"] = fetch_citer_ids(client, n["id"])
    seed_node = {"id": oa_id(seed), "refs": seed["refs"], "citers": seed_citers}

    log("Computing pairwise similarity (coupling + co-citation)...")
    allnodes = pset + [seed_node]
    edges = []
    for i in range(len(allnodes)):
        for j in range(i + 1, len(allnodes)):
            sim, coupling, cocite, _ = pair_similarity(allnodes[i], allnodes[j])
            if sim >= min_sim:
                edges.append({"similarity": sim, "a": allnodes[i]["id"], "b": allnodes[j]["id"],
                              "coupling": coupling, "cocite": cocite})
    edges.sort(key=lambda e: -e["similarity"])
    edges = edges[:max_edges]

    for n in pset:
        sim, coupling, cocite, _ = pair_similarity(seed_node, n)
        n["sim_to_seed"] = sim
        n["shared_refs"] = coupling
        n["cocited_with_seed"] = cocite
        kinds = n["kinds"]
        if kinds == {"in", "out"}:
            n["direction"] = "both"
        elif "in" in kinds:
            n["direction"] = "cites seed"
        elif "out" in kinds:
            n["direction"] = "cited by seed"
        else:
            n["direction"] = "2nd hop"
    ranked = sorted(pset, key=lambda n: (-n["sim_to_seed"], -(n["citations"] or 0)))

    years = [seed.get("publication_year") or 0] + [n["year"] or 0 for n in ranked]
    return {
        "seed": {"openalex_id": oa_id(seed), "title": seed.get("title"),
                 "year": seed.get("publication_year"), "venue": work_venue(seed),
                 "citations": seed.get("cited_by_count") or 0, "url": work_url(seed),
                 "abstract": work_abstract(seed),
                 "first_author": last_name(first_author_name(seed))},
        "nodes": [{"openalex_id": n["id"], "title": n["title"], "year": n["year"],
                   "citations": n["citations"], "venue": n["venue"], "url": n["url"],
                   "abstract": n["abstract"], "sim_to_seed": n["sim_to_seed"],
                   "direction": n["direction"], "shared_refs": n["shared_refs"],
                   "cocited_with_seed": n["cocited_with_seed"],
                   "first_author": n["first_author"]} for n in ranked],
        "edges": [{"a": e["a"], "b": e["b"], "similarity": e["similarity"],
                   "coupling": e["coupling"], "cocite": e["cocite"]} for e in edges],
        "stats": {"candidates": len(cands), "pool": len(pset), "edges": len(edges),
                  "min_year": min(years), "max_year": max(years)},
    }
