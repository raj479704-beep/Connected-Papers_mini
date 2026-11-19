# Mini Connected Papers

A personal Connected Papers–style tool. Give it a paper (or search keywords)
and it produces an interactive graph of related papers, where relatedness
comes from **co-citation** and **bibliographic coupling** — so papers link up
even when they never cite each other directly.

Data source: [OpenAlex](https://openalex.org). Free without an API key.
No dependencies — Python 3 stdlib only.

## Web UI (recommended)

```bash
python3 app.py                 # http://127.0.0.1:8000
python3 app.py --port 9000 --api-key KEY --mailto you@example.com
```

Workflow:
1. Type keywords → a ranked list of papers appears on the left
   (pasting a DOI / arXiv id / W-id goes straight to the graph).
2. Click a paper → the graph builds (30–90 s first time, instant after —
   results are cached).
3. Explore:
   - **click** a node → detail panel (abstract, similarity breakdown, links)
   - **double-click** a node → rebuild the graph around that paper
   - hover a node → full title + neighborhood highlight (everything else dims)
   - drag nodes to rearrange; scroll to zoom, drag background to pan
   - "2nd-hop" checkbox → also include papers related to the top neighbors
   - "papers" dropdown → pool size (20 / 40 / 80 / 120)
   - "⬇ CSV" → download the ranked list
4. **BibTeX for selected papers**:
   - **shift+click** a node (or the button in its detail panel) to toggle
     selection — selected nodes get a green ring + ✓ badge
   - the bottom bar shows the selection as removable chips
   - "⬇ BibTeX (.bib)" downloads a `.bib` file with **only the selected
     papers** (authors, journal, volume/issue, pages, year, DOI) via
     `GET /api/bibtex?ids=W1,W2&name=slug`
5. **History**: the 🕘 History button lists your recently built graphs
   (persisted to `history.json`, survives restarts, newest first, max 30).
   Click an entry to reopen it (instant when still in the in-memory cache);
   🗑 removes one, "Clear all" wipes the list.
   API: `GET /api/history`, `DELETE /api/history?id=W…` (or no id = all).

## CLI

```bash
python3 connected.py "10.1038/nature01945"        # DOI
python3 connected.py "arXiv:2103.0001"            # arXiv id
python3 connected.py "W1970335928"                # OpenAlex id
python3 connected.py "red blood cell adhesion to endothelium"   # title search
python3 connected.py "10.1038/nature01945" --depth 2
```

Writes to `out/`: `graph_<slug>.html` (static graph), `graph_<slug>.csv`
(ranked list), `graph_<slug>_edges.csv` (pairwise edges).

## How similarity is computed

For the seed, the candidate pool is **references(seed) ∪ papers citing the
seed** (capers are taken most-cited-first, up to `--max-citers`). With
`--depth 2` / the "2nd-hop" checkbox, the pool is further expanded with the
references and citers of the 8 most relevant 1-hop papers — this is where
papers that never touch the seed directly can enter the graph.

The top `pool` candidates are then compared **pairwise**:

- **Bibliographic coupling** A↔B: how many references A and B share
  (papers that cite the same works), normalized by the smaller bibliography.
- **Co-citation** A↔B: how many papers cite *both* A and B, normalized by
  the smaller citing set.
- **Similarity** = 0.5 × coupling + 0.5 × co-citation, plus a +0.1 bonus
  (capped at 1.0) if one of the two cites the other directly.

Visualization mapping (as in Connected Papers):

- **Node size** = total citation count (√-scale)
- **Node color** = publication year (older → lighter, newer → brighter)
- **Edges** = pairwise similarity — thicker, more opaque, and *shorter*
  (pulled closer) the stronger the link; edges below `--min-sim` (0.12)
  are dropped
- The seed is drawn with a warm amber glow

## Files

| file | purpose |
| --- | --- |
| `app.py` | local web server (search + graph API + UI) |
| `index.html` | the UI (d3-force + SVG, dark theme) |
| `pipeline.py` | shared logic: OpenAlex client, fetching, scoring |
| `connected.py` | CLI wrapper (writes html/csv artifacts) |
| `out/` | CLI output artifacts |
| `history.json` | web-UI history (created at runtime, git-ignored) |

## Notes & limits

- Without a key, requests share a small **free daily budget** with everyone
  on your IP. Get a free key with its own budget at
  <https://openalex.org/settings/api> and pass `--api-key`
  (or set `OPENALEX_API_KEY`). After that, just do 'python3 app.py --api-key KEY'.
- Citing-paper sets are capped at 600 per paper (seed: 300), so co-citation
  is slightly undercounted for extremely cited seeds.
- A typical build makes ~80–120 API calls (10 rps throttle), so expect
  30–90 s; repeated builds of the same seed are served from an in-memory
  cache (up to 6 graphs).
- OpenAlex abstracts may be missing for some records; those papers simply
  rank by coupling/co-citation only.
