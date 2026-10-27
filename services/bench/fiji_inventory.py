"""The Fiji extensions inventory (phase 3's E3, the review's correction 4): every row of imagej.net/list-of-extensions,
as listed, each with how it installs, written to catalog/inventories/fiji-extensions.json for review.

    make fiji-inventory FIJI_DB=<the pinned build's db.xml.gz>   # fetch, map, write (reads the web, writes one file)

The extension list indexes projects; update sites index distributions. They are different inventories, so each row is
mapped explicitly, with the evidence:

- **Its page's own word first:** the front matter of its page (imagej/imagej.github.io) may name its update site
  (`update-site:`), and names its artifact (`artifact: group:artifactId`).
- **Where that artifact's jar is:** every update site on imagej.net/list-of-update-sites, by its own file index
  (`db.xml.gz`). A jar in the core sites (ImageJ, Fiji) "ships with Fiji"; in one other site, that site; in several,
  each is listed and the review picks (`fiji-routes.toml`).
- **Otherwise** the row stays, "no install route yet", with why (its page names no artifact; no site holds its jar).
  An unmapped extension is never left out.

Routes reviewed by a person are `catalog/inventories/fiji-routes.toml` (an extension's page path -> its route), and win
over what this finds. Nothing here installs or runs anything.
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import html
import json
import re
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "catalog" / "inventories" / "fiji-extensions.json"
ROUTES = REPO / "catalog" / "inventories" / "fiji-routes.toml"
EXTENSIONS = "https://imagej.net/list-of-extensions"
SITES = "https://imagej.net/list-of-update-sites/"
PAGE_SOURCE = "https://raw.githubusercontent.com/imagej/imagej.github.io/main/_pages{path}.md"
# What a fresh Fiji has on: Fiji Latest has one site (its own db.xml.gz, the pinned build of catalog/models/fiji).
CORE = {"Fiji-Latest": "https://sites.imagej.net/Fiji/"}


def extensions(client: httpx.Client) -> list[dict]:
    page = client.get(EXTENSIONS).text
    rows = re.findall(r'<li class="([^"]*)">\s*<img[^>]*>\s*<div>\s*<a href="([^"]+)">([^<]+)</a><br />\s*'
                      r'<span class="categories">([^<]*)</span>', page)
    return [{"name": html.unescape(name).strip(), "page": path, "categories": [c.strip() for c in cats.split(",") if c.strip()]}
            for _cls, path, name, cats in rows]


def update_sites(client: httpx.Client) -> dict[str, str]:
    page = client.get(SITES).text
    out = dict(CORE)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 2:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
        url = re.search(r'href="(https?://[^"]+)"', cells[1])
        if name and url and len(name) < 80 and "mirror" not in name.lower():  # one site, not its mirrors
            out.setdefault(name, url.group(1).rstrip("/") + "/")
    return out


def front_matter(client: httpx.Client, path: str) -> dict:
    """{"artifact", "sites"} from its page's own front matter ("" and [] when it names none)."""
    for candidate in (path, path.rstrip("/") + "/index"):
        r = client.get(PAGE_SOURCE.format(path=candidate))
        if r.status_code == 200:
            front = r.text.split("---", 2)[1] if r.text.startswith("---") else ""
            art = re.search(r"^artifact:\s*(\S+)\s*$", front, re.M)
            site = re.search(r"^update-site:\s*(.+?)\s*$", front, re.M)
            sites = [x.strip().strip("'\"") for x in site.group(1).strip("[]").split(",")] if site else []
            return {"artifact": art.group(1).strip("'\"") if art else "", "sites": [x for x in sites if x]}
    return {"artifact": "", "sites": []}


def site_files(client: httpx.Client, url: str) -> list[str] | None:
    try:
        r = client.get(url + "db.xml.gz", timeout=30)
        if r.status_code != 200:
            return None
        root = ET.fromstring(gzip.decompress(r.content))
        return [p.get("filename", "") for p in root.iter("plugin")]
    except Exception:  # noqa: BLE001 - a site that can't be read is said as such
        return None


def core_sites(sites: dict[str, str]) -> set[str]:
    """The update sites a fresh Fiji of the pinned build has on (CORE)."""
    return {name for name in CORE if name in sites}


def build_files() -> list[str] | None:
    """What the pinned Fiji build ships with, by its own index (FIJI_DB: its db.xml.gz), so "ships with Fiji" means
    exactly that build, never the site as it is today."""
    import os

    db = os.environ.get("FIJI_DB")
    if not db or not Path(db).exists():
        return None
    root = ET.fromstring(gzip.decompress(Path(db).read_bytes()))
    return [p.get("filename", "") for p in root.iter("plugin")]


def main() -> int:
    t0 = time.monotonic()
    with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": "MicroscopeMind catalog inventory"}) as client:
        rows = extensions(client)
        sites = update_sites(client)
        print(f"{len(rows)} extensions, {len(sites)} update sites", file=sys.stderr)
        with cf.ThreadPoolExecutor(16) as pool:
            fronts = list(pool.map(lambda r: front_matter(client, r["page"]), rows))
            indexes = dict(zip(sites, pool.map(lambda u: site_files(client, u), sites.values())))
    shipped = build_files()
    if shipped is not None:
        indexes["Fiji-Latest"] = shipped  # the pinned build's own files
    reviewed = tomllib.loads(ROUTES.read_text()) if ROUTES.exists() else {}
    core = core_sites(sites)
    out = []
    for row, front in zip(rows, fronts):
        art = front["artifact"]
        aid = art.split(":")[1] if art.count(":") >= 1 else art  # group:artifact[:version]
        named = [x for x in front["sites"] if x in sites]
        jar = re.compile(rf"^(jars|plugins)/(.*/)?{re.escape(aid)}(-[0-9][^/]*)?\.jar$") if aid else None
        found = sorted(name for name, files in indexes.items() if jar and files and any(jar.match(f) for f in files))
        entry = {**row, "artifact": art}
        if row["page"] in reviewed:
            entry["route"] = {**reviewed[row["page"]], "reviewed": True}
        elif named and all(x in core for x in named):
            entry["route"] = {"kind": "ships-with-fiji", "sites": named, "evidence": "its page names the site"}
        elif named:
            first = next(x for x in named if x not in core)
            entry["route"] = {"kind": "update-site", "site": first, "url": sites[first],
                              "evidence": "its page names the site", **({"also": named[1:]} if len(named) > 1 else {})}
        elif any(f in core for f in found):
            entry["route"] = {"kind": "ships-with-fiji", "sites": [f for f in found if f in core],
                              "evidence": "its jar is in the pinned Fiji build"}
        elif len(found) == 1:
            entry["route"] = {"kind": "update-site", "site": found[0], "url": sites[found[0]],
                              "evidence": f"the only site whose index holds {aid}'s jar"}
        elif found:
            entry["route"] = {"kind": "choose", "sites": [{"site": f, "url": sites[f]} for f in found],
                              "why": "its jar is on several update sites: the review picks one (fiji-routes.toml)"}
        else:
            entry["route"] = {"kind": "none-known", "why": "its page names no artifact" if not art else
                              f"no update site holds {aid}'s jar"}
        out.append(entry)
    unreadable = sorted(name for name, files in indexes.items() if files is None)
    counts: dict[str, int] = {}
    for e in out:
        counts[e["route"]["kind"]] = counts.get(e["route"]["kind"], 0) + 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%d"), "sources": {"extensions": EXTENSIONS, "sites": SITES,
                                                            "pages": PAGE_SOURCE.format(path="<page>")},
        "counts": counts, "sitesRead": len(sites) - len(unreadable), "sitesUnreadable": unreadable,
        "extensions": out}, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(out)} extensions in {time.monotonic() - t0:.0f} s: "
          + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) + f"; {len(unreadable)} sites couldn't be read",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
