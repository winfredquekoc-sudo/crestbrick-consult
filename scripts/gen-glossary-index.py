#!/usr/bin/env python3
"""Rebuild /glossary/index.html as an A to Z dictionary from the files on disk.

Reads every glossary entry, pulls its term and definition, and regenerates the
index body: search filter, A to Z jump nav, and alphabetical sections. Reusing
the existing page shell keeps head, nav, footer and styling untouched.

Re-run this whenever glossary entries are added.
"""
import re, html, json
from pathlib import Path

ROOT = Path("/Users/winfredquek/crestbrick-consult/public/glossary")
INDEX = ROOT / "index.html"

TAG = re.compile(r"<[^>]+>")


def strip(s):
    return html.unescape(TAG.sub("", s)).strip()


def entry(p):
    h = p.read_text(encoding="utf-8")
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    term = strip(m.group(1)) if m else p.stem.replace("-", " ").title()
    d = re.search(r'name="description" content="([^"]*)"', h)
    desc = html.unescape(d.group(1)).strip() if d else ""
    # trim the definition to a card-sized lead sentence
    if len(desc) > 155:
        cut = desc[:155].rsplit(" ", 1)[0]
        desc = cut + "..."
    c = re.search(r"Glossary\s*(?:&middot;|·)\s*([^<]{2,40})", h)
    cat = strip(c.group(1)) if c else ""
    return {"slug": p.stem, "term": term, "desc": desc, "cat": cat}


def main():
    entries = [entry(p) for p in sorted(ROOT.glob("*.html")) if p.stem != "index"]
    entries.sort(key=lambda e: e["term"].lower())

    def bucket(e):
        ch = e["term"][0].upper()
        return ch if ch.isalpha() else "#"

    letters = sorted({bucket(e) for e in entries})
    order = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c in letters] + (["#"] if "#" in letters else [])

    nav = "".join(
        f'<a href="#letter-{c if c != "#" else "num"}" class="az-link">{c}</a>' for c in order
    )

    body = []
    for c in order:
        group = [e for e in entries if bucket(e) == c]
        anchor = c if c != "#" else "num"
        body.append(f'  <h2 class="cat-label" id="letter-{anchor}">{c}</h2>\n  <div class="term-grid">')
        for e in group:
            body.append(
                f'    <div class="term-card" data-term="{html.escape(e["term"].lower())} {html.escape(e["desc"].lower()[:120])}">'
                f'<a href="/glossary/{e["slug"]}">{html.escape(e["term"])}</a>'
                f'<p>{html.escape(e["desc"])}</p></div>'
            )
        body.append("  </div>")
    body = "\n".join(body)

    controls = f'''  <div class="dict-controls">
    <label for="gloss-search" class="sr-only">Search the glossary</label>
    <input id="gloss-search" type="search" placeholder="Search {len(entries)} terms, for example ABSD, sinking fund, lease decay" autocomplete="off" />
    <p id="gloss-count" class="dict-count" aria-live="polite"></p>
    <nav class="az-nav" aria-label="Jump to letter">{nav}</nav>
  </div>
'''

    css = '''    .dict-controls{margin:0 0 2rem;}
    .dict-controls input{box-sizing:border-box;width:100%;background:var(--highlight);border:1px solid var(--rule);border-radius:8px;color:var(--ink);padding:.85rem 1.1rem;font-size:1rem;}
    .dict-controls input:focus{outline:2px solid var(--accent);outline-offset:2px;border-color:var(--accent);}
    .dict-count{color:var(--ink-muted);font-size:.82rem;margin:.6rem 0 0;}
    .az-nav{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:1rem;}
    .az-link{display:inline-flex;align-items:center;justify-content:center;min-width:2rem;padding:.3rem .5rem;border:1px solid var(--rule);border-radius:6px;color:var(--ink-soft);font-size:.82rem;font-weight:600;text-decoration:none;}
    .az-link:hover,.az-link:focus{border-color:var(--accent);color:var(--accent);}
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
'''

    js = '''<script>
(function(){
  var box=document.getElementById('gloss-search'),
      cards=[].slice.call(document.querySelectorAll('.term-card')),
      heads=[].slice.call(document.querySelectorAll('.cat-label')),
      count=document.getElementById('gloss-count'),
      total=cards.length;
  function apply(){
    var q=(box.value||'').trim().toLowerCase(), shown=0;
    cards.forEach(function(c){
      var hit=!q||(c.getAttribute('data-term')||'').indexOf(q)>-1;
      c.style.display=hit?'':'none'; if(hit)shown++;
    });
    heads.forEach(function(h){
      var grid=h.nextElementSibling, any=false;
      if(grid){[].slice.call(grid.children).forEach(function(c){if(c.style.display!=='none')any=true;});
        h.style.display=any?'':'none'; grid.style.display=any?'':'none';}
    });
    count.textContent=q?(shown+' of '+total+' terms match'):(total+' terms');
  }
  box.addEventListener('input',apply); apply();
})();
</script>'''

    src = INDEX.read_text(encoding="utf-8")

    # replace the article body
    a = src.index('<article class="max-w-4xl mx-auto px-6 pt-12 pb-24">')
    a_end = src.index("</article>", a)
    open_tag = '<article class="max-w-4xl mx-auto px-6 pt-12 pb-24">'
    new_article = open_tag + "\n\n" + controls + "\n" + body + "\n\n"
    src = src[:a] + new_article + src[a_end:]

    # intro line
    src = re.sub(
        r"Forty five terms[^<]*",
        f"{len(entries)} terms every Singapore property buyer, first time home buyer and investor runs into, "
        f"ABSD to Zoning, explained in plain English with a worked example where it helps. "
        f"By Winfred Quek, CEA R073319H.",
        src,
    )
    title_text = f"Singapore Property Glossary: {len(entries)} Terms Explained | Winfred Quek"
    src = re.sub(r"<title>.*?</title>", f"<title>{title_text}</title>", src, flags=re.S)
    for prop in ("description", "og:description", "twitter:description"):
        src = re.sub(
            rf'(<meta (?:name|property)="{prop}" content=")[^"]*(")',
            rf"\g<1>A plain English dictionary of {len(entries)} Singapore property terms for first time home buyers and new investors, from ABSD to Zoning. Searchable and A to Z.\g<2>",
            src,
        )
    # og:title/twitter:title previously drifted from <title> since only the
    # description trio was kept in sync here -- keep all three title-bearing
    # tags identical so social shares and the browser tab never disagree.
    for prop in ("og:title", "twitter:title"):
        src = re.sub(
            rf'(<meta (?:name|property)="{prop}" content=")[^"]*(")',
            rf"\g<1>{title_text}\g<2>",
            src,
        )

    if ".dict-controls{" not in src:
        src = src.replace("</style>", css + "  </style>", 1)
    if "gloss-search" not in src.split("</body>")[-2][-2000:]:
        src = src.replace("</body>", js + "\n</body>", 1)

    INDEX.write_text(src, encoding="utf-8")
    print(f"rebuilt {INDEX.name}: {len(entries)} terms across {len(order)} letters")


if __name__ == "__main__":
    main()
