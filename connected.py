#!/usr/bin/env python3
"""Mini Connected Papers CLI.

Usage:
  python3 connected.py "10.1038/nature01945"
  python3 connected.py "arXiv:2103.0001"
  python3 connected.py "W1970335928"
  python3 connected.py "red blood cell adhesion to endothelium"

For the interactive web UI (keyword search -> click -> graph) use:
  python3 app.py

Writes out/graph_<slug>.html, out/graph_<slug>.csv, out/graph_<slug>_edges.csv
"""

import argparse
import csv
import json
import os
import re

import pipeline as P


def slugify(title, maxlen=45):
    s = re.sub(r"[^a-z0-9]+", "_", (title or "paper").lower()).strip("_")
    return s[:maxlen] or "paper"


def write_html(path, result, top_n):
    seed, ranked, edges = result["seed"], result["nodes"], result["edges"]
    min_year, max_year = result["stats"]["min_year"], result["stats"]["max_year"]
    id_to_node = {"seed": "seed"}
    for i, n in enumerate(ranked):
        id_to_node[n["openalex_id"]] = f"n{i}"

    nodes_js = [{
        "id": "seed",
        "label": f"SEED · {seed.get('first_author', '') + ' ' if seed.get('first_author') else ''}{seed['year']}",
        "title": (f"<b>SEED</b><br>{seed['title']}<br>{seed['year']} — {seed['venue'] or 'n/a'}"
                  f"<br>citations: {seed['citations']}"),
        "size": P.node_size(seed["citations"]),
        "color": {"background": P.year_color(seed["year"] or 0, min_year, max_year),
                  "border": "#d62728", "highlight": {"border": "#d62728", "background": "#fff"}},
        "borderWidth": 4,
        "url": seed["url"],
        "font": {"size": 15, "face": "monospace"},
    }]
    for i, n in enumerate(ranked):
        nodes_js.append({
            "id": f"n{i}",
            "label": f"{n.get('first_author', '') + ' ' if n.get('first_author') else ''}{n['year']}",
            "title": (f"<b>{n['title']}</b><br>{n['year']} — {n['venue'] or 'n/a'}<br>"
                      f"citations: {n['citations']}<br>"
                      f"similarity to seed: {n['sim_to_seed']} "
                      f"(shared refs: {n['shared_refs']}, co-cited with: {n['cocited_with_seed']})"
                      f"<br><i>click to open</i>"),
            "size": P.node_size(n["citations"]),
            "color": {"background": P.year_color(n["year"] or 0, min_year, max_year),
                      "border": "#3b5b7d"},
            "url": n["url"],
            "font": {"size": 13},
        })
    edges_js = []
    for e in edges[:top_n * 8]:
        a = "seed" if e["a"] == seed["openalex_id"] else id_to_node.get(e["a"])
        b = "seed" if e["b"] == seed["openalex_id"] else id_to_node.get(e["b"])
        if not a or not b or a == b:
            continue
        sim = e["similarity"]
        edges_js.append({"from": a, "to": b,
                         "width": round(0.8 + 4.5 * sim, 2),
                         "color": {"color": "#4a6b8a", "opacity": round(0.15 + 0.55 * sim, 2)},
                         "length": round(60 + 340 * (1 - sim), 0),
                         "stiffness": round(0.05 + 0.75 * sim, 3)})
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Mini Connected Papers — {seed['title']}</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  body {{ margin:0; font-family:-apple-system,system-ui,sans-serif; }}
  #g {{ width:100vw; height:100vh; }}
  #bar {{ position:fixed; top:10px; left:10px; z-index:10; background:rgba(255,255,255,.94);
         border:1px solid #ddd; border-radius:10px; padding:10px 14px; max-width:450px; font-size:13px; }}
  #bar b {{ font-size:14px; }}
  .grad {{ width:150px; height:10px; border-radius:5px; display:inline-block; vertical-align:middle;
           background:linear-gradient(to right, {P.year_color(min_year, min_year, max_year)}, {P.year_color(max_year, min_year, max_year)}); }}
  .seeddot {{ display:inline-block; width:12px; height:12px; border-radius:50%;
              background:{P.year_color(seed['year'] or 0, min_year, max_year)};
              border:3px solid #d62728; vertical-align:middle; }}
</style></head>
<body>
<div id="bar">
  <b>Mini Connected Papers</b><br>
  Seed: {seed['title']} ({seed['year']})<br>
  {len(ranked)} papers, {len(edges_js)} edges. Similarity = bibliographic coupling + co-citation.<br>
  Node size = citations · color = year
  (<span class="grad"></span> {min_year} → {max_year}) · red ring = seed · thicker edge = stronger link<br>
  <span class="seeddot"></span> click any node to open the paper
</div>
<div id="g"></div>
<script>
const NODES = {json.dumps(nodes_js)};
const EDGES = {json.dumps(edges_js)};
const nodes = new vis.DataSet(NODES);
const edges = new vis.DataSet(EDGES);
const net = new vis.Network(document.getElementById('g'), {{ nodes, edges }}, {{
  physics: {{ solver: 'barnesHut',
              barnesHut: {{ gravitationalConstant: -2500, springConstant: 0.06, damping: 0.4 }},
              avoidOverlap: 0.4,
              stabilization: {{ iterations: 400 }} }},
  interaction: {{ hover: true, tooltipDelay: 80, zoomView: true }}
}});
nodes.on('click', p => {{
  const n = nodes.get(p.nodes[0]);
  if (n && n.url) window.open(n.url, '_blank');
}});
</script>
</body></html>
"""
    with open(path, "w") as fh:
        fh.write(html)


def main():
    ap = argparse.ArgumentParser(description="Mini Connected Papers (co-citation + bibliographic coupling).")
    ap.add_argument("query", help="DOI, OpenAlex W-id, arXiv id, or paper title")
    ap.add_argument("--pool", type=int, default=80, help="papers to analyze pairwise (default 80)")
    ap.add_argument("--max-citers", type=int, default=300,
                    help="max seed citers to consider (default 300, most cited first)")
    ap.add_argument("--min-sim", type=float, default=0.12,
                    help="minimum pairwise similarity for an edge (default 0.12)")
    ap.add_argument("--max-edges", type=int, default=500, help="max edges (default 500)")
    ap.add_argument("--depth", type=int, default=1, choices=[1, 2],
                    help="2 = also expand neighbors of the top related papers (default 1)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    ap.add_argument("--mailto", help="your email for OpenAlex's polite pool")
    ap.add_argument("--api-key", help="OpenAlex API key (free, raises daily budget)")
    args = ap.parse_args()

    client = P.Client(mailto=args.mailto, api_key=args.api_key)
    result = P.build(client, args.query, pool=args.pool, max_citers=args.max_citers,
                     min_sim=args.min_sim, max_edges=args.max_edges, depth=args.depth)

    seed, ranked = result["seed"], result["nodes"]
    os.makedirs(args.out, exist_ok=True)
    slug = slugify(seed["title"])
    html_path = os.path.join(args.out, f"graph_{slug}.html")
    csv_path = os.path.join(args.out, f"graph_{slug}.csv")
    edges_path = os.path.join(args.out, f"graph_{slug}_edges.csv")
    write_html(html_path, result, args.pool)
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "sim_to_seed", "shared_refs_with_seed", "cocited_with_seed",
                    "direction", "year", "title", "venue", "citations", "url", "openalex_id"])
        for i, n in enumerate(ranked, 1):
            w.writerow([i, n["sim_to_seed"], n["shared_refs"], n["cocited_with_seed"],
                        n["direction"], n["year"], n["title"], n["venue"],
                        n["citations"], n["url"], f"W{n['openalex_id']}"])
    with open(edges_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["similarity", "id_a", "id_b", "shared_refs", "cited_together_by"])
        for e in result["edges"]:
            w.writerow([e["similarity"], e["a"], e["b"], e["coupling"], e["cocite"]])

    P.log("")
    P.log(f"{'#':>3} {'sim':>6} {'shr':>4} {'coc':>4}  {'year':<5} {'cites':>6}  {'dir':<13} title")
    for i, n in enumerate(ranked[:15], 1):
        P.log(f"{i:>3} {n['sim_to_seed']:>6.3f} {n['shared_refs']:>4} {n['cocited_with_seed']:>4}  "
              f"{str(n['year']):<5} {n['citations']:>6}  {n['direction']:<13} {n['title'][:62]}")
    P.log("")
    P.log(f"Graph: {html_path}")
    P.log(f"CSV:   {csv_path}")
    P.log(f"Edges: {edges_path}")


if __name__ == "__main__":
    main()
