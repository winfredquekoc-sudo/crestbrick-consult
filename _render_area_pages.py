#!/usr/bin/env python3
"""Generate 28 district + 26 HDB town landing pages from JSON data."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).parent / "public"
DISTRICTS_JSON = ROOT / "districts.json"
TOWNS_JSON = ROOT / "hdb-towns.json"
DISTRICTS_OUT = ROOT / "districts"
TOWNS_OUT = ROOT / "hdb-towns"
TOWNS_OUT.mkdir(exist_ok=True)
DISTRICTS_OUT.mkdir(exist_ok=True)


NAV_LG = """    <div class="hidden lg:flex items-center gap-6">
      <a class="nav-link" href="/">Home</a>
      <a class="nav-link" href="/services">Services</a>
      <a class="nav-link" href="/pricing">Pricing</a>
      <a class="nav-link" href="/tools">Tools</a>
      <a class="nav-link" href="/insights">Insights</a>
      <a class="nav-link" href="/contact">Contact</a>
      <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm" style="padding:.55rem 1.1rem;">Book a call</a>
    </div>"""

NAV_MOBILE = """    <div class="px-6 py-4 flex flex-col gap-3">
      <a class="nav-link" href="/">Home</a><a class="nav-link" href="/services">Services</a><a class="nav-link" href="/pricing">Pricing</a><a class="nav-link" href="/tools">Tools</a><a class="nav-link" href="/insights">Insights</a><a class="nav-link" href="/districts">Districts</a><a class="nav-link" href="/hdb-towns">HDB Towns</a><a class="nav-link" href="/contact">Contact</a>
      <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
    </div>"""

FOOTER = """<footer class="divider bg-white">
  <div class="max-w-6xl mx-auto px-6 py-14 grid md:grid-cols-3 gap-10">
    <div>
      <p class="serif font-semibold text-lg mb-3">Winfred Quek</p>
      <p class="text-sm text-[var(--ink-soft)] leading-relaxed mb-3">Investor-minded property advisor, Singapore.</p>
      <p class="text-xs text-[var(--ink-muted)] leading-relaxed">Salesperson of <strong class="text-[var(--ink-soft)]">Crestbrick</strong>, Singapore<br/>CEA Registration No.: <strong class="text-[var(--ink-soft)]">R073319H</strong></p>
    </div>
    <div>
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Quick links</p>
      <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
        <li><a href="/about" class="hover:text-[var(--ink)]">About Winfred</a></li><li><a href="/services" class="hover:text-[var(--ink)]">Services</a></li><li><a href="/pricing" class="hover:text-[var(--ink)]">Pricing</a></li><li><a href="/tools" class="hover:text-[var(--ink)]">Tools</a></li><li><a href="/audit" class="hover:text-[var(--ink)]">AI Audit</a></li><li><a href="/insights" class="hover:text-[var(--ink)]">Insights</a></li><li><a href="/districts" class="hover:text-[var(--ink)]">District guides</a></li><li><a href="/hdb-towns" class="hover:text-[var(--ink)]">HDB town guides</a></li>
      </ul>
    </div>
    <div>
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Contact</p>
      <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
        <li><a href="https://wa.me/6581618149" class="hover:text-[var(--ink)]">WhatsApp · +65 8161 8149</a></li><li><a href="tel:+6581618149" class="hover:text-[var(--ink)]">Phone · +65 8161 8149</a></li><li><a href="mailto:winfredquekoc@gmail.com" class="hover:text-[var(--ink)]">winfredquekoc@gmail.com</a></li><li><a href="https://calendly.com/winfredquekoc" class="hover:text-[var(--ink)]">Book via Calendly</a></li>
      </ul>
    </div>
  </div>
  <div class="border-t border-[var(--rule)]">
    <div class="max-w-6xl mx-auto px-6 py-6 flex flex-col md:flex-row justify-between gap-3 text-xs text-[var(--ink-muted)]">
      <p>© 2026 Winfred Quek · Crestbrick · CEA R073319H · All rights reserved.</p>
      <div class="flex gap-4"><a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a><span>·</span><span>Information on this site is general and not legal or financial advice.</span></div>
    </div>
  </div>
  <!-- Disclaimer -->
  <div class="disclaimer-block" style="border-top:1px solid rgba(255,255,255,0.08); padding:1.25rem 1.5rem;">
    <p style="font-size:0.72rem; line-height:1.65; color:#706c64; max-width:900px; margin:0 auto; text-align:center;">
      The information and insights provided on this page are for informational purposes only and are based on Winfred's independent research and views. While we strive to ensure accuracy and reliability, we do not guarantee the completeness, correctness, or timeliness of the data presented. Real estate investments are subject to various risks, including but not limited to market fluctuations, changes in economic conditions, interest rate volatility, regulatory shifts, liquidity constraints, and unforeseen property-specific risks. Past performance is not indicative of future results, and investment outcomes may vary. This page does not constitute investment, financial, or professional advice and should not be relied upon as such. Investors should conduct their own due diligence and seek advice from qualified professionals before making any investment decisions.
    </p>
  </div>
</footer>"""

WA_FLOAT = """<a id="wa-float" href="https://wa.me/6581618149?text=Hi%20Winfred%2C%20I%20have%20a%20question%20about%20{wa_ctx}." aria-label="Message Winfred on WhatsApp" class="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-full bg-[#25D366] px-5 py-3 text-white font-medium shadow-lg hover:shadow-xl hover:scale-[1.03] transition-all duration-200" style="box-shadow:0 10px 30px -5px rgba(37,211,102,.4);">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
  <span class="hidden sm:inline">Chat</span>
</a>"""

HEAD_BASE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{meta_desc}" />
  <meta property="og:title" content="{og_title}" />
  <meta property="og:description" content="{og_desc}" />
  <meta property="og:type" content="article" />
  <meta property="og:image" content="/img/og-image.jpg" />
  <meta property="og:url" content="{canonical}" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:image" content="/img/og-image.jpg" />
  <link rel="canonical" href="{canonical}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <script defer src="/_vercel/insights/script.js"></script>
  <link rel="stylesheet" href="/tw.css" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">

  <script type="application/ld+json">{schema}</script>

  <style>
    :root{{--bg:#faf8f4;--ink:#1a1a1a;--ink-soft:#4a4a4a;--ink-muted:#7a7a7a;--accent:#8b6f47;--rule:#e6e0d6;}}
    html{{scroll-behavior:smooth;}}
    body{{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;}}
    .serif{{font-family:'Fraunces',Georgia,serif;}}
    .balance{{text-wrap:balance;}}
    .divider{{border-top:1px solid var(--rule);}}
    .section-label{{font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--accent);font-weight:600;}}
    .btn{{display:inline-flex;align-items:center;gap:.5rem;padding:.9rem 1.6rem;border-radius:4px;font-weight:500;font-size:15px;transition:all .15s ease;}}
    .btn-primary{{background:var(--ink);color:var(--bg);}}
    .btn-primary:hover{{background:var(--accent);transform:translateY(-1px);}}
    .btn-ghost{{color:var(--ink);border:1px solid var(--rule);}}
    .btn-ghost:hover{{border-color:var(--ink);}}
    .topnav a.nav-link{{color:var(--ink-soft);font-size:14px;font-weight:500;}}
    .topnav a.nav-link:hover{{color:var(--ink);}}
    .topnav a.nav-link.active{{color:var(--ink);}}
    .topnav a.nav-link.active::after{{content:"";display:block;height:1px;background:var(--accent);margin-top:4px;}}
    @keyframes wa-pulse{{0%,100%{{box-shadow:0 10px 30px -5px rgba(37,211,102,.4),0 0 0 0 rgba(37,211,102,.55);}}50%{{box-shadow:0 10px 30px -5px rgba(37,211,102,.4),0 0 0 14px rgba(37,211,102,0);}}}}
    #wa-float.wa-pulsing{{animation:wa-pulse 1s ease-out 2;}}
    #mobile-menu{{display:none;}}#mobile-menu.open{{display:block;}}
    .chip{{display:inline-block;padding:.3rem .75rem;border-radius:999px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;}}
    .chip-ccr{{background:#f3ead9;color:#7a5a28;}}
    .chip-rcr{{background:#e9e0d3;color:#5b4a2a;}}
    .chip-ocr{{background:#e0e4d6;color:#4a5a30;}}
    .chip-mature{{background:#e6e0d6;color:#5a4a30;}}
    .chip-non-mature{{background:#d8d8d8;color:#3a3a3a;}}
  </style>
</head>
<body>

<header class="topnav border-b border-[var(--rule)] bg-[var(--bg)]/90 backdrop-blur sticky top-0 z-40">
  <nav class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
    <a href="/" class="flex items-center gap-2"><span class="serif font-semibold text-lg">Winfred Quek</span><span class="hidden sm:inline text-xs text-[var(--ink-muted)] uppercase tracking-widest">Crestbrick</span></a>
{nav_lg}
    <button id="menu-toggle" class="lg:hidden p-2" aria-label="Open menu"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button>
  </nav>
  <div id="mobile-menu" class="lg:hidden border-t border-[var(--rule)] bg-[var(--bg)]">
{nav_mobile}
  </div>
</header>"""

def slug_for_district(code, name):
    """D9 + Orchard · River Valley → d9-orchard-river-valley"""
    clean = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return f"{code.lower()}-{clean}"


def render_district(d):
    code = d["code"]
    name = d["name"]
    region = d["region"]
    areas = d["areas"]
    pov = d["pov"]
    slug = slug_for_district(code, name)
    canonical = f"https://winfredquek.com/districts/{slug}"

    title = f"{code} {name} Property Guide — Singapore | Winfred Quek"
    meta_desc = f"{code} ({name}) Singapore property guide: {region} tier, areas include {areas}. Investor POV, upgrade logic, and where {code} sits in the 4-Pillar framework. By Winfred Quek, Crestbrick. CEA R073319H."
    og_title = f"{code} {name} — Singapore Property Guide"
    og_desc = f"{region} district · {name}. Investor-minded POV by Winfred Quek."

    schema = json.dumps({
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": f"{code} {name} — Singapore Property District Guide",
                "datePublished": "2026-04-20",
                "dateModified": "2026-04-20",
                "author": {"@type": "Person", "name": "Winfred Quek", "identifier": "CEA R073319H"},
                "publisher": {"@type": "Organization", "name": "Crestbrick"},
                "url": canonical,
                "image": "https://winfredquek.com/img/og-image.jpg"
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
                    {"@type": "ListItem", "position": 2, "name": "Districts", "item": "https://winfredquek.com/districts"},
                    {"@type": "ListItem", "position": 3, "name": f"{code} {name}", "item": canonical}
                ]
            }
        ]
    }, separators=(',', ':'))

    region_class = {"CCR": "chip-ccr", "RCR": "chip-rcr", "OCR": "chip-ocr"}.get(region, "chip-ocr")

    head = HEAD_BASE.format(
        title=title, meta_desc=meta_desc, og_title=og_title, og_desc=og_desc,
        canonical=canonical, schema=schema,
        nav_lg=NAV_LG, nav_mobile=NAV_MOBILE,
    )

    # Prev/next nav
    wa = WA_FLOAT.replace("{wa_ctx}", f"{code} {name}")
    html = head + f"""

<main>
<section class="max-w-4xl mx-auto px-6 pt-12 pb-6">
  <p class="section-label mb-4"><a href="/districts" class="hover:text-[var(--ink)]">← All 28 districts</a></p>
  <div class="flex items-center gap-3 mb-4">
    <span class="chip {region_class}">{region}</span>
    <span class="chip chip-mature">District {code[1:]}</span>
  </div>
  <h1 class="serif text-4xl md:text-6xl font-semibold leading-tight balance mb-4">{code} <span class="italic text-[var(--accent)]">{name}</span></h1>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed max-w-3xl">{areas}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Winfred's read</p>
  <h2 class="serif text-3xl md:text-4xl font-semibold balance leading-tight mb-6">The investor POV on {code}.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-8">{pov}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <div class="grid md:grid-cols-2 gap-6">
    <div class="p-6 bg-white border border-[var(--rule)] rounded-xl">
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-2">Region</p>
      <h3 class="serif text-xl font-semibold mb-2">{region}</h3>
      <p class="text-[var(--ink-soft)] text-sm leading-relaxed">{"Core Central Region — premium, currency-sensitive, foreign-buyer exposed." if region == "CCR" else "Rest of Central Region — city-fringe sweet spot; mature MRT + school continuity." if region == "RCR" else "Outside Central Region — family-suburban, upgrader-heartland, strongest future-growth zones."}</p>
    </div>
    <div class="p-6 bg-white border border-[var(--rule)] rounded-xl">
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-2">Key areas</p>
      <h3 class="serif text-xl font-semibold mb-2">{code} neighbourhoods</h3>
      <p class="text-[var(--ink-soft)] text-sm leading-relaxed">{areas}</p>
    </div>
  </div>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<!-- 4-Pillar mapping -->
<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">4-Pillar mapping</p>
  <h2 class="serif text-3xl font-semibold balance leading-tight mb-6">How I'd think about {code} through the framework.</h2>
  <div class="grid md:grid-cols-2 gap-6">
    <div>
      <p class="serif text-4xl font-semibold text-[var(--accent)] mb-2">01</p>
      <h3 class="serif text-xl font-semibold mb-2">Capital</h3>
      <p class="text-[var(--ink-soft)] text-sm leading-relaxed">{code} entry prices sit in the {"premium band — cash reserves and CPF positioning matter more than LTV optimization here" if region == "CCR" else "mid-band — LTV, CPF OA, and bank package selection drive the ceiling" if region == "RCR" else "accessible band — grant eligibility + sequencing matter as much as headline affordability"}.</p>
    </div>
    <div>
      <p class="serif text-4xl font-semibold text-[var(--accent)] mb-2">02</p>
      <h3 class="serif text-xl font-semibold mb-2">Cashflow</h3>
      <p class="text-[var(--ink-soft)] text-sm leading-relaxed">{"Yield is tight in " + code + " — treat this as capital-appreciation positioning, not rental-yield. Vacancy and tenant quality matter." if region == "CCR" else "Yield in " + code + " is workable if selection is disciplined — comparables within the district vary meaningfully." if region == "RCR" else code + " yields tend to be healthier but tenant pools narrower — understand the local renter base before committing."}</p>
    </div>
    <div>
      <p class="serif text-4xl font-semibold text-[var(--accent)] mb-2">03</p>
      <h3 class="serif text-xl font-semibold mb-2">Progression</h3>
      <p class="text-[var(--ink-soft)] text-sm leading-relaxed">Where {code} sits in your portfolio depends on what you're progressing FROM and TO. Entry without a planned exit is speculation — <a href="/insights/property-exit-strategy" class="underline hover:text-[var(--ink)]">see exit strategy</a>.</p>
    </div>
    <div>
      <p class="serif text-4xl font-semibold text-[var(--accent)] mb-2">04</p>
      <h3 class="serif text-xl font-semibold mb-2">Protection</h3>
      <p class="text-[var(--ink-soft)] text-sm leading-relaxed">Stress-test interest-rate doubling, 6-month vacancy, MCST special levy. {"CCR adds FX exposure if you're selling into a foreign-buyer pool." if region == "CCR" else ""}</p>
    </div>
  </div>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Related</p>
  <h2 class="serif text-3xl font-semibold balance leading-tight mb-6">Keep reading.</h2>
  <div class="grid md:grid-cols-3 gap-4">
    <a href="/insights/ccr-rcr-ocr-framework" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">Framework</p>
      <p class="serif font-semibold">CCR vs RCR vs OCR framework</p>
    </a>
    <a href="/insights/rental-yield-vs-appreciation" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">Yield</p>
      <p class="serif font-semibold">Rental yield vs capital appreciation</p>
    </a>
    <a href="/tools/rental-yield" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">Tool</p>
      <p class="serif font-semibold">Rental yield calculator →</p>
    </a>
  </div>
</section>

<section class="max-w-4xl mx-auto px-6 py-16 text-center">
  <h2 class="serif text-3xl md:text-4xl font-semibold balance leading-tight mb-6">Thinking about {code}?</h2>
  <p class="text-[var(--ink-soft)] max-w-xl mx-auto mb-8">Let's run the 4-Pillar Audit on your specific numbers — not the district's averages.</p>
  <div class="flex flex-wrap justify-center gap-3">
    <a href="https://calendly.com/winfredquekoc" class="btn btn-primary">Book the audit →</a>
    <a href="/audit" class="btn btn-ghost">Try the AI Audit</a>
  </div>
</section>
</main>
""" + FOOTER + "\n" + wa + """
<script>
(function(){var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){m.classList.toggle('open');});})();
(function(){var b=document.getElementById('wa-float');if(!b)return;var f=false;function p(){if(f)return;f=true;b.classList.add('wa-pulsing');setTimeout(function(){b.classList.remove('wa-pulsing');},2200);window.removeEventListener('scroll',p);}window.addEventListener('scroll',p,{passive:true});})();
</script>
</body>
</html>
"""
    out = DISTRICTS_OUT / f"{slug}.html"
    out.write_text(html)
    return slug


def render_town(t):
    slug = t["slug"]
    name = t["name"]
    region = t["region"]
    maturity = t["maturity"]
    mrt = t["mrt"]
    character = t["character"]
    upgrade = t["upgrader_angle"]
    canonical = f"https://winfredquek.com/hdb-towns/{slug}"

    title = f"{name} HDB Town Guide — Singapore Upgrade, MOP, MRT | Winfred Quek"
    meta_desc = f"{name} HDB town guide: {region} region, {maturity} estate. MRT access: {mrt}. MOP upgrade logic and progression paths by Winfred Quek, Crestbrick. CEA R073319H."
    og_title = f"{name} HDB Town Guide — Winfred Quek"
    og_desc = f"{region} · {maturity}. {character[:150]}..."

    schema = json.dumps({
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": f"{name} HDB Town Guide — Singapore Upgrade &amp; MOP",
                "datePublished": "2026-04-20",
                "dateModified": "2026-04-20",
                "author": {"@type": "Person", "name": "Winfred Quek", "identifier": "CEA R073319H"},
                "publisher": {"@type": "Organization", "name": "Crestbrick"},
                "url": canonical,
                "image": "https://winfredquek.com/img/og-image.jpg"
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
                    {"@type": "ListItem", "position": 2, "name": "HDB Towns", "item": "https://winfredquek.com/hdb-towns"},
                    {"@type": "ListItem", "position": 3, "name": name, "item": canonical}
                ]
            }
        ]
    }, separators=(',', ':'))

    mat_chip = "chip-mature" if maturity == "mature" else "chip-non-mature"

    head = HEAD_BASE.format(
        title=title, meta_desc=meta_desc, og_title=og_title, og_desc=og_desc,
        canonical=canonical, schema=schema,
        nav_lg=NAV_LG, nav_mobile=NAV_MOBILE,
    )

    wa = WA_FLOAT.replace("{wa_ctx}", f"upgrading from {name}")
    html = head + f"""

<main>
<section class="max-w-4xl mx-auto px-6 pt-12 pb-6">
  <p class="section-label mb-4"><a href="/districts#hdb-towns" class="hover:text-[var(--ink)]">← All districts &amp; towns</a></p>
  <div class="flex items-center gap-3 mb-4">
    <span class="chip chip-ocr">{region}</span>
    <span class="chip {mat_chip}">{maturity.title()} estate</span>
  </div>
  <h1 class="serif text-4xl md:text-6xl font-semibold leading-tight balance mb-4">{name}</h1>
  <p class="text-sm text-[var(--ink-muted)] uppercase tracking-widest mb-6">MRT: {mrt}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Character</p>
  <h2 class="serif text-3xl md:text-4xl font-semibold balance leading-tight mb-6">The town in one paragraph.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-8">{character}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">The upgrade angle</p>
  <h2 class="serif text-3xl font-semibold balance leading-tight mb-6">If you're an upgrader in {name}.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-8">{upgrade}</p>

  <div class="bg-white border border-[var(--rule)] rounded-xl p-6">
    <p class="text-xs text-[var(--accent)] uppercase tracking-widest font-semibold mb-3">The sequencing that matters</p>
    <ul class="list-disc pl-6 text-[var(--ink-soft)] text-sm space-y-2">
      <li><strong class="text-[var(--ink)]">MOP timing</strong> — have you cleared the 5-year minimum occupation period? If not, when will you?</li>
      <li><strong class="text-[var(--ink)]">Sell-first vs buy-first</strong> — the single most expensive sequencing decision for HDB upgraders. Varies by ABSD exposure and financing.</li>
      <li><strong class="text-[var(--ink)]">Cash proceeds vs CPF refund</strong> — running the CPF accrued interest math (<a href="/insights/cpf-accrued-interest-trap" class="underline hover:text-[var(--ink)]">see article</a>) before you list.</li>
      <li><strong class="text-[var(--ink)]">ABSD remission window</strong> — 6-month rule after completion of your new property, strictly enforced.</li>
    </ul>
  </div>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Related</p>
  <h2 class="serif text-3xl font-semibold balance leading-tight mb-6">Keep reading.</h2>
  <div class="grid md:grid-cols-3 gap-4">
    <a href="/insights/hdb-mop-upgrade-timeline" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">Pillar article</p>
      <p class="serif font-semibold">HDB MOP to Condo Upgrade Timeline</p>
    </a>
    <a href="/insights/cpf-accrued-interest-trap" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">Pillar article</p>
      <p class="serif font-semibold">CPF accrued interest trap</p>
    </a>
    <a href="/tools/affordability" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
      <p class="text-xs text-[var(--accent)] uppercase tracking-widest mb-2">Tool</p>
      <p class="serif font-semibold">Affordability calculator →</p>
    </a>
  </div>
</section>

<section class="max-w-4xl mx-auto px-6 py-16 text-center">
  <h2 class="serif text-3xl md:text-4xl font-semibold balance leading-tight mb-6">Thinking of upgrading from {name}?</h2>
  <p class="text-[var(--ink-soft)] max-w-xl mx-auto mb-8">The 4-Pillar Audit looks at your capital position, cashflow, progression path, and protection buffers — not the town's averages.</p>
  <div class="flex flex-wrap justify-center gap-3">
    <a href="https://calendly.com/winfredquekoc" class="btn btn-primary">Book the audit →</a>
    <a href="/audit" class="btn btn-ghost">Try the AI Audit</a>
  </div>
</section>
</main>
""" + FOOTER + "\n" + wa + """
<script>
(function(){var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){m.classList.toggle('open');});})();
(function(){var b=document.getElementById('wa-float');if(!b)return;var f=false;function p(){if(f)return;f=true;b.classList.add('wa-pulsing');setTimeout(function(){b.classList.remove('wa-pulsing');},2200);window.removeEventListener('scroll',p);}window.addEventListener('scroll',p,{passive:true});})();
</script>
</body>
</html>
"""
    out = TOWNS_OUT / f"{slug}.html"
    out.write_text(html)
    return slug


def render_towns_index(towns):
    """Build /hdb-towns/index.html hub page"""
    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Singapore HDB Town Guides — 26 towns",
        "url": "https://winfredquek.com/hdb-towns",
    }, separators=(',', ':'))

    head = HEAD_BASE.format(
        title="Singapore HDB Town Guides — All 26 Towns | Winfred Quek",
        meta_desc="Complete HDB town guides for all 26 Singapore HDB towns. MOP upgrade logic, MRT access, character, and progression paths. By Winfred Quek, Crestbrick. CEA R073319H.",
        og_title="Singapore HDB Town Guides — Winfred Quek",
        og_desc="All 26 HDB towns — MOP upgrade angle + investor POV per town.",
        canonical="https://winfredquek.com/hdb-towns",
        schema=schema,
        nav_lg=NAV_LG, nav_mobile=NAV_MOBILE,
    )

    regions = {}
    for t in towns:
        regions.setdefault(t["region"], []).append(t)

    region_blocks = ""
    for region in ["Central", "East", "North-East", "North", "West"]:
        if region not in regions: continue
        cards = "\n".join([
            f'''      <a href="/hdb-towns/{t["slug"]}" class="block p-5 bg-white border border-[var(--rule)] rounded-xl hover:border-[var(--ink)] transition">
        <div class="flex items-center gap-2 mb-2">
          <span class="chip {"chip-mature" if t["maturity"]=="mature" else "chip-non-mature"}" style="font-size:10px;padding:.2rem .5rem;">{t["maturity"]}</span>
        </div>
        <h3 class="serif text-xl font-semibold mb-2">{t["name"]}</h3>
        <p class="text-[var(--ink-soft)] text-xs leading-relaxed">{t["mrt"][:70]}...</p>
      </a>''' for t in regions[region]
        ])
        region_blocks += f'''
  <section class="max-w-6xl mx-auto px-6 py-8">
    <p class="section-label mb-4">{region}</p>
    <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
{cards}
    </div>
  </section>
'''

    html = head + f"""

<main>
<section class="max-w-5xl mx-auto px-6 pt-16 pb-8">
  <p class="section-label mb-6">HDB town guides</p>
  <h1 class="serif text-4xl md:text-6xl font-semibold leading-tight balance mb-6">All 26 HDB towns — the <span class="italic text-[var(--accent)]">upgrader's map</span>.</h1>
  <p class="text-lg text-[var(--ink-soft)] max-w-2xl leading-relaxed">Per-town character, MRT access, maturity status, and the upgrade angle. For HDB owners approaching MOP or already past it.</p>
</section>

<div class="divider max-w-5xl mx-auto"></div>
{region_blocks}
<section class="max-w-4xl mx-auto px-6 py-16 text-center">
  <h2 class="serif text-3xl font-semibold balance mb-6">Ready to run the numbers?</h2>
  <div class="flex flex-wrap justify-center gap-3">
    <a href="https://calendly.com/winfredquekoc" class="btn btn-primary">Book the audit →</a>
    <a href="/audit" class="btn btn-ghost">Try the AI Audit</a>
  </div>
</section>
</main>
""" + FOOTER + "\n" + WA_FLOAT.replace("{wa_ctx}", "my HDB town") + """
<script>
(function(){var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){m.classList.toggle('open');});})();
</script>
</body>
</html>
"""
    out = ROOT / "hdb-towns" / "index.html"
    out.write_text(html)


def main():
    districts_data = json.loads(DISTRICTS_JSON.read_text())
    towns_data = json.loads(TOWNS_JSON.read_text())

    d_slugs = []
    for d in districts_data["districts"]:
        slug = render_district(d)
        d_slugs.append(slug)
    print(f"✅ Generated {len(d_slugs)} district pages")

    for t in towns_data["towns"]:
        render_town(t)
    # towns hub removed — merged into /districts#hdb-towns
    print(f"✅ Generated {len(towns_data['towns'])} HDB town pages + hub index")

    # Print URLs for sitemap
    print("\n=== URLs for sitemap ===")
    for slug in d_slugs:
        print(f"  /districts/{slug}")
    for t in towns_data["towns"]:
        print(f"  /hdb-towns/{t['slug']}")


if __name__ == "__main__":
    main()
