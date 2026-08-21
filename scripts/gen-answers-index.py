#!/usr/bin/env python3
"""Rebuild /answers/index.html from the answer pages on disk.

Keeps the existing category sections and their ordering where they already
exist, appends any answer not yet listed into a themed section, and adds a
search filter. Re-run whenever answers are added.
"""
import re, html
from pathlib import Path

ROOT = Path("/Users/winfredquek/crestbrick-consult/public/answers")
INDEX = ROOT / "index.html"
TAG = re.compile(r"<[^>]+>")


def strip(s):
    return html.unescape(TAG.sub("", s)).strip()


def entry(p):
    h = p.read_text(encoding="utf-8")
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    q = strip(m.group(1)) if m else p.stem.replace("-", " ").capitalize()
    d = re.search(r'name="description" content="([^"]*)"', h)
    desc = html.unescape(d.group(1)).strip() if d else ""
    if len(desc) > 145:
        desc = desc[:145].rsplit(" ", 1)[0] + "..."
    return {"slug": p.stem, "q": q, "desc": desc}


# theme routing for answers not already placed in a section
THEMES = [
    ("Buying process, contracts and costs",
     ("otp", "option", "valuation", "legal-fee", "conveyanc", "inspection", "company-name",
      "joint-tenancy", "tenancy-in-common", "inherit", "seller-backs-out", "cash-over",
      "subject-to-financing", "fees", "cost", "stamp-duty", "commission")),
    ("HDB rules and edge cases",
     ("hdb", "flat", "mop", "bto", "resale-levy", "ethnic", "divorce", "lease-buyback",
      "reinstate", "renovation", "extension-of-stay", "grant", "hfe", "singles", "ec")),
    ("Condo, strata and new launches",
     ("sinking-fund", "special-levy", "en-bloc", "defects", "progressive", "new-launch",
      "dual-key", "strata", "mortgagee", "condo")),
    ("Mortgage and financing",
     ("loan", "refinanc", "lock-in", "cpf", "tdsr", "borrow", "downpayment", "bridging",
      "interest", "income")),
    ("Renting and yield",
     ("rent", "yield", "tenant")),
    ("Selling and exit",
     ("sell", "sale", "proceeds", "ssd", "contra")),
]


def theme_for(e):
    s = (e["slug"] + " " + e["q"].lower())
    for name, keys in THEMES:
        if any(k in s for k in keys):
            return name
    return "Other questions"


def main():
    src = INDEX.read_text(encoding="utf-8")
    entries = {p.stem: entry(p) for p in sorted(ROOT.glob("*.html")) if p.stem != "index"}
    listed = set(re.findall(r'href="/answers/([a-z0-9-]+)"', src))
    missing = [e for s, e in entries.items() if s not in listed]

    # group everything fresh so ordering is stable and nothing is orphaned
    groups = {}
    for e in entries.values():
        groups.setdefault(theme_for(e), []).append(e)
    for g in groups.values():
        g.sort(key=lambda e: e["q"].lower())

    order = [n for n, _ in THEMES] + ["Other questions"]
    order = [n for n in order if n in groups]

    body = []
    for name in order:
        anchor = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        body.append(
            f'  <h2 class="serif" style="font-size:1.5rem;font-weight:600;margin:2.5rem 0 1rem;'
            f'color:var(--ink);" id="{anchor}">{html.escape(name)} '
            f'<span style="font-size:.8rem;color:var(--ink-muted);font-weight:400;">({len(groups[name])})</span></h2>'
        )
        body.append('  <div class="link-grid">')
        for e in groups[name]:
            body.append(
                f'    <div class="link-card" data-q="{html.escape((e["q"] + " " + e["desc"]).lower())}">'
                f'<span class="kind">Answer</span><br>'
                f'<a href="/answers/{e["slug"]}">{html.escape(e["q"])}</a>'
                f'<p>{html.escape(e["desc"])}</p></div>'
            )
        body.append("  </div>")
    body = "\n".join(body)

    total = len(entries)
    controls = f'''  <div class="ans-controls">
    <label for="ans-search" class="sr-only">Search the answers</label>
    <input id="ans-search" type="search" placeholder="Search {total} answers, for example ABSD, MOP, option fee" autocomplete="off" />
    <p id="ans-count" class="ans-count" aria-live="polite"></p>
  </div>
'''

    css = '''    .ans-controls{margin:0 0 2rem;}
    .ans-controls input{box-sizing:border-box;width:100%;background:var(--highlight);border:1px solid var(--rule);border-radius:8px;color:var(--ink);padding:.85rem 1.1rem;font-size:1rem;}
    .ans-controls input:focus{outline:2px solid var(--accent);outline-offset:2px;border-color:var(--accent);}
    .ans-count{color:var(--ink-muted);font-size:.82rem;margin:.6rem 0 0;}
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
'''

    js = '''<script>
(function(){
  var box=document.getElementById('ans-search');
  if(!box)return;
  var cards=[].slice.call(document.querySelectorAll('.link-card[data-q]')),
      heads=[].slice.call(document.querySelectorAll('article h2')),
      count=document.getElementById('ans-count'), total=cards.length;
  function apply(){
    var q=(box.value||'').trim().toLowerCase(), shown=0;
    cards.forEach(function(c){
      var hit=!q||(c.getAttribute('data-q')||'').indexOf(q)>-1;
      c.style.display=hit?'':'none'; if(hit)shown++;
    });
    heads.forEach(function(h){
      var g=h.nextElementSibling,any=false;
      if(g&&g.classList.contains('link-grid')){
        [].slice.call(g.children).forEach(function(c){if(c.style.display!=='none')any=true;});
        h.style.display=any?'':'none'; g.style.display=any?'':'none';}
    });
    count.textContent=q?(shown+' of '+total+' answers match'):(total+' answers');
  }
  box.addEventListener('input',apply); apply();
})();
</script>'''

    a = src.index('<article class="max-w-4xl mx-auto px-6 pt-10 pb-24">')
    a_end = src.index("</article>", a)
    src = (src[:a] + '<article class="max-w-4xl mx-auto px-6 pt-10 pb-24">\n\n'
           + controls + "\n" + body + "\n\n" + src[a_end:])

    title_text = f"Singapore Property Questions Answered: {total} Direct Answers | Winfred Quek"
    src = re.sub(r"<title>.*?</title>", f"<title>{title_text}</title>", src, flags=re.S)
    for prop in ("description", "og:description", "twitter:description"):
        src = re.sub(rf'(<meta (?:name|property)="{prop}" content=")[^"]*(")',
                     rf"\g<1>{total} direct answers to the Singapore property questions buyers, sellers and landlords actually ask. ABSD, HDB rules, CPF, mortgages, en bloc and more.\g<2>", src)
    # og:title/twitter:title previously drifted from <title> since only the
    # description trio was kept in sync here -- keep all three title-bearing
    # tags identical so social shares and the browser tab never disagree.
    for prop in ("og:title", "twitter:title"):
        src = re.sub(rf'(<meta (?:name|property)="{prop}" content=")[^"]*(")',
                     rf"\g<1>{title_text}\g<2>", src)

    if ".ans-controls{" not in src:
        src = src.replace("</style>", css + "  </style>", 1)
    if "ans-search" not in src.rsplit("</body>", 1)[0][-2500:]:
        src = src.replace("</body>", js + "\n</body>", 1)

    INDEX.write_text(src, encoding="utf-8")
    print(f"rebuilt answers index: {total} answers, {len(order)} sections, {len(missing)} were previously unlisted")


if __name__ == "__main__":
    main()
