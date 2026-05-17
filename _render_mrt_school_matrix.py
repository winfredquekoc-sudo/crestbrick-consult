#!/usr/bin/env python3
"""Generate MRT x primary-school catchment landing pages (#38).

Creates ~60 pages at /public/area/{mrt-slug}-{school-slug}.html — one per
(MRT station, top primary school within ~1km) pair. Curated, not exhaustive,
to keep quality > thin programmatic noise.

Each page:
  - H1 "Living near {MRT} with kids at {School}"
  - Distance + walk-time band note (1km Phase 2A, 2B; 1-2km Phase 2C)
  - Area paragraph + nearby BTO/condo paragraph
  - FAQ block (5 Qs)
  - Internal links to /tools and /contact
  - Schema.org Place + EducationalOrganization linked + RealEstateAgent footer
"""
import json
from pathlib import Path
from html import escape

ROOT = Path(__file__).parent / "public"
OUT = ROOT / "area"
OUT.mkdir(exist_ok=True)

# Curated MRT -> primary schools within ~1km (well-known SG geography pairings).
# distance_band: A = within 1km (Phase 2A/2B priority), B = 1-2km (Phase 2C)
PAIRS = [
    # Bishan
    ("bishan", "Bishan", "NSL/CCL", "catholic-high", "Catholic High School", "A", "0.4 km", "5 min"),
    ("bishan", "Bishan", "NSL/CCL", "ai-tong", "Ai Tong School", "B", "1.6 km", "20 min"),
    ("bishan", "Bishan", "NSL/CCL", "kuo-chuan-presbyterian", "Kuo Chuan Presbyterian Primary", "A", "0.7 km", "9 min"),
    # Toa Payoh
    ("toa-payoh", "Toa Payoh", "NSL", "first-toa-payoh", "First Toa Payoh Primary", "A", "0.6 km", "8 min"),
    ("toa-payoh", "Toa Payoh", "NSL", "chij-primary-toa-payoh", "CHIJ Primary (Toa Payoh)", "A", "0.5 km", "6 min"),
    ("toa-payoh", "Toa Payoh", "NSL", "pei-chun-public", "Pei Chun Public School", "A", "0.7 km", "9 min"),
    # Novena / Newton
    ("novena", "Novena", "NSL", "anglo-chinese-primary", "Anglo-Chinese School (Primary)", "A", "0.8 km", "10 min"),
    ("newton", "Newton", "NSL/DTL", "anglo-chinese-primary", "Anglo-Chinese School (Primary)", "A", "0.9 km", "12 min"),
    ("newton", "Newton", "NSL/DTL", "st-josephs-institution-junior", "St Joseph's Institution Junior", "B", "1.4 km", "18 min"),
    # Bras Basah / Dhoby Ghaut
    ("bras-basah", "Bras Basah", "CCL", "st-josephs-institution-junior", "St Joseph's Institution Junior", "A", "0.6 km", "8 min"),
    ("dhoby-ghaut", "Dhoby Ghaut", "NSL/NEL/CCL", "st-margarets-primary", "St Margaret's Primary", "A", "0.8 km", "10 min"),
    # Tampines
    ("tampines", "Tampines", "EWL/DTL", "tampines-primary", "Tampines Primary", "A", "0.5 km", "7 min"),
    ("tampines", "Tampines", "EWL/DTL", "st-hildas-primary", "St Hilda's Primary", "B", "1.7 km", "21 min"),
    ("tampines", "Tampines", "EWL/DTL", "poi-ching", "Poi Ching School", "A", "0.9 km", "12 min"),
    ("tampines-east", "Tampines East", "DTL", "angsana-primary", "Angsana Primary", "A", "0.7 km", "9 min"),
    # Pasir Ris
    ("pasir-ris", "Pasir Ris", "EWL", "elias-park-primary", "Elias Park Primary", "A", "0.6 km", "8 min"),
    ("pasir-ris", "Pasir Ris", "EWL", "loyang-view", "Loyang View School", "B", "1.8 km", "22 min"),
    ("pasir-ris", "Pasir Ris", "EWL", "casuarina-primary", "Casuarina Primary", "A", "0.9 km", "12 min"),
    # Punggol
    ("punggol", "Punggol", "NEL/LRT", "punggol-primary", "Punggol Primary", "A", "0.4 km", "5 min"),
    ("punggol", "Punggol", "NEL/LRT", "edgefield-primary", "Edgefield Primary", "B", "1.5 km", "19 min"),
    ("punggol", "Punggol", "NEL/LRT", "horizon-primary", "Horizon Primary", "A", "0.8 km", "10 min"),
    # Sengkang
    ("sengkang", "Sengkang", "NEL/LRT", "compassvale-primary", "Compassvale Primary", "A", "0.6 km", "8 min"),
    ("sengkang", "Sengkang", "NEL/LRT", "anchor-green-primary", "Anchor Green Primary", "B", "1.4 km", "18 min"),
    ("sengkang", "Sengkang", "NEL/LRT", "north-vista-primary", "North Vista Primary", "A", "0.7 km", "9 min"),
    # Hougang
    ("hougang", "Hougang", "NEL", "xinmin-primary", "Xinmin Primary", "A", "0.8 km", "10 min"),
    ("hougang", "Hougang", "NEL", "holy-innocents-primary", "Holy Innocents' Primary", "B", "1.5 km", "19 min"),
    ("hougang", "Hougang", "NEL", "rosyth", "Rosyth School", "B", "1.9 km", "23 min"),
    # Serangoon
    ("serangoon", "Serangoon", "NEL/CCL", "rosyth", "Rosyth School", "A", "0.9 km", "12 min"),
    ("serangoon", "Serangoon", "NEL/CCL", "yangzheng-primary", "Yangzheng Primary", "B", "1.6 km", "20 min"),
    # Ang Mo Kio
    ("ang-mo-kio", "Ang Mo Kio", "NSL", "ai-tong", "Ai Tong School", "A", "0.9 km", "12 min"),
    ("ang-mo-kio", "Ang Mo Kio", "NSL", "anderson-primary", "Anderson Primary", "A", "0.6 km", "8 min"),
    ("ang-mo-kio", "Ang Mo Kio", "NSL", "townsville-primary", "Townsville Primary", "B", "1.4 km", "18 min"),
    ("mayflower", "Mayflower", "TEL", "ai-tong", "Ai Tong School", "A", "0.5 km", "7 min"),
    ("mayflower", "Mayflower", "TEL", "chij-st-nicholas-girls-primary", "CHIJ St Nicholas Girls' Primary", "A", "0.6 km", "8 min"),
    # Bukit Timah / Sixth Avenue / Tan Kah Kee
    ("sixth-avenue", "Sixth Avenue", "DTL", "methodist-girls-primary", "Methodist Girls' School (Primary)", "A", "0.7 km", "9 min"),
    ("sixth-avenue", "Sixth Avenue", "DTL", "henry-park-primary", "Henry Park Primary", "B", "1.8 km", "22 min"),
    ("tan-kah-kee", "Tan Kah Kee", "DTL", "nanyang-primary", "Nanyang Primary", "A", "0.4 km", "5 min"),
    ("tan-kah-kee", "Tan Kah Kee", "DTL", "raffles-girls-primary", "Raffles Girls' Primary", "A", "0.8 km", "10 min"),
    # Holland Village / Buona Vista
    ("holland-village", "Holland Village", "CCL", "henry-park-primary", "Henry Park Primary", "A", "0.7 km", "9 min"),
    ("buona-vista", "Buona Vista", "EWL/CCL", "fairfield-methodist-primary", "Fairfield Methodist Primary", "A", "0.9 km", "12 min"),
    # Queenstown / Redhill
    ("queenstown", "Queenstown", "EWL", "queenstown-primary", "Queenstown Primary", "A", "0.5 km", "7 min"),
    ("redhill", "Redhill", "EWL", "alexandra-primary", "Alexandra Primary", "A", "0.6 km", "8 min"),
    ("redhill", "Redhill", "EWL", "gan-eng-seng-primary", "Gan Eng Seng Primary", "B", "1.5 km", "19 min"),
    # Tiong Bahru / Outram
    ("tiong-bahru", "Tiong Bahru", "EWL", "zhangde-primary", "Zhangde Primary", "A", "0.4 km", "5 min"),
    ("outram-park", "Outram Park", "EWL/NEL/TEL", "cantonment-primary", "Cantonment Primary", "A", "0.7 km", "9 min"),
    # Marine Parade / Katong
    ("marine-parade", "Marine Parade", "TEL", "tao-nan", "Tao Nan School", "A", "0.5 km", "7 min"),
    ("marine-parade", "Marine Parade", "TEL", "ngee-ann-primary", "Ngee Ann Primary", "A", "0.8 km", "10 min"),
    ("marine-parade", "Marine Parade", "TEL", "haig-girls-primary", "Haig Girls' School", "B", "1.3 km", "16 min"),
    ("dakota", "Dakota", "CCL", "kong-hwa", "Kong Hwa School", "A", "0.6 km", "8 min"),
    ("paya-lebar", "Paya Lebar", "EWL/CCL", "kong-hwa", "Kong Hwa School", "B", "1.2 km", "15 min"),
    ("paya-lebar", "Paya Lebar", "EWL/CCL", "geylang-methodist-primary", "Geylang Methodist Primary", "A", "0.9 km", "12 min"),
    # Bedok
    ("bedok", "Bedok", "EWL", "red-swastika", "Red Swastika School", "B", "1.4 km", "18 min"),
    ("bedok", "Bedok", "EWL", "fengshan-primary", "Fengshan Primary", "A", "0.8 km", "10 min"),
    ("bedok", "Bedok", "EWL", "yu-neng-primary", "Yu Neng Primary", "A", "0.7 km", "9 min"),
    # Clementi
    ("clementi", "Clementi", "EWL", "nan-hua-primary", "Nan Hua Primary", "B", "1.7 km", "21 min"),
    ("clementi", "Clementi", "EWL", "qifa-primary", "Qifa Primary", "A", "0.7 km", "9 min"),
    ("clementi", "Clementi", "EWL", "pei-tong-primary", "Pei Tong Primary", "A", "0.9 km", "12 min"),
    # Jurong East / West
    ("jurong-east", "Jurong East", "EWL/NSL", "fuhua-primary", "Fuhua Primary", "A", "0.8 km", "10 min"),
    ("jurong-east", "Jurong East", "EWL/NSL", "yuhua-primary", "Yuhua Primary", "A", "0.6 km", "8 min"),
    # Woodlands / Yishun
    ("woodlands", "Woodlands", "NSL/TEL", "woodgrove-primary", "Woodgrove Primary", "A", "0.7 km", "9 min"),
    ("yishun", "Yishun", "NSL", "north-view-primary", "North View Primary", "A", "0.9 km", "12 min"),
    ("yishun", "Yishun", "NSL", "huamin-primary", "Huamin Primary", "A", "0.8 km", "10 min"),
    # Choa Chu Kang / Bukit Panjang
    ("choa-chu-kang", "Choa Chu Kang", "NSL/LRT", "south-view-primary", "South View Primary", "A", "0.5 km", "7 min"),
    ("bukit-panjang", "Bukit Panjang", "DTL/LRT", "zhenghua-primary", "Zhenghua Primary", "A", "0.7 km", "9 min"),
]

PAGE_TPL = """<!doctype html>
<html lang="en-SG">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Living near {mrt} MRT with kids at {school} | Winfred Quek</title>
  <meta name="description" content="School-catchment + MRT guide for parents: {school} ({distance}, {walk_time} from {mrt} MRT). Distance band, BTO and condo options nearby, by Winfred Quek (CEA R073319H, Crestbrick)." />
  <meta name="robots" content="index,follow" />
  <meta property="og:title" content="Living near {mrt} MRT with kids at {school}" />
  <meta property="og:description" content="{school} is {distance} from {mrt} MRT (~{walk_time} walk). {phase_summary}" />
  <meta property="og:type" content="article" />
  <meta property="og:image" content="/img/og-image.jpg" />
  <meta property="og:url" content="https://winfredquek.com/area/{slug}" />
  <meta name="twitter:card" content="summary_large_image" />
  <link rel="canonical" href="https://winfredquek.com/area/{slug}" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <script defer src="/_vercel/insights/script.js"></script>
  <link rel="stylesheet" href="/tw.css" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <script type="application/ld+json">{schema_json}</script>
  <style>
    :root{{--bg:#0f1117;--ink:#f0ede6;--ink-soft:#b8b4aa;--ink-muted:#706c64;--accent:#c6a36a;--rule:#252830;}}
    html{{scroll-behavior:smooth;}}
    body{{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;}}
    .serif{{font-family:'Fraunces',Georgia,serif;}}
    .balance{{text-wrap:balance;}}
    .divider{{border-top:1px solid var(--rule);}}
    .section-label{{font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--accent);font-weight:600;}}
    .btn{{display:inline-flex;align-items:center;gap:.5rem;padding:.9rem 1.6rem;border-radius:4px;font-weight:500;font-size:15px;transition:all .15s ease;}}
    .btn-primary{{background:var(--ink);color:var(--bg);}}
    .btn-primary:hover{{background:var(--accent);}}
    .btn-ghost{{color:var(--ink);border:1px solid var(--rule);}}
    .btn-ghost:hover{{border-color:var(--ink);}}
    .topnav a.nav-link{{color:var(--ink-soft);font-size:14px;font-weight:500;}}
    .topnav a.nav-link:hover{{color:var(--ink);}}
    .chip{{display:inline-block;padding:.3rem .75rem;border-radius:999px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;background:#e6e0d6;color:#5a4a30;}}
    #mobile-menu{{display:none;}}#mobile-menu.open{{display:block;}}
  </style>
</head>
<body>
<header class="topnav border-b border-[var(--rule)] bg-[var(--bg)]/90 backdrop-blur sticky top-0 z-40">
  <nav class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
    <a href="/" class="flex items-center gap-2"><span class="serif font-semibold text-lg">Winfred Quek</span><span class="hidden sm:inline text-xs text-[var(--ink-muted)] uppercase tracking-widest">Crestbrick</span></a>
    <div class="hidden lg:flex items-center gap-6">
      <a class="nav-link" href="/">Home</a>
      <a class="nav-link" href="/services">Services</a>
      <a class="nav-link" href="/tools">Tools</a>
      <a class="nav-link" href="/insights">Insights</a>
      <a class="nav-link" href="/districts">Districts</a>
      <a class="nav-link" href="/contact">Contact</a>
      <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm" style="padding:.55rem 1.1rem;">Book a call</a>
    </div>
    <button id="menu-toggle" class="lg:hidden p-2" aria-label="Open menu"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button>
  </nav>
  <div id="mobile-menu" class="lg:hidden border-t border-[var(--rule)] bg-[var(--bg)]">
    <div class="px-6 py-4 flex flex-col gap-3">
      <a class="nav-link" href="/">Home</a><a class="nav-link" href="/services">Services</a><a class="nav-link" href="/tools">Tools</a><a class="nav-link" href="/insights">Insights</a><a class="nav-link" href="/districts">Districts</a><a class="nav-link" href="/hdb-towns">HDB Towns</a><a class="nav-link" href="/contact">Contact</a>
      <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
    </div>
  </div>
</header>

<main>
<section class="hero max-w-4xl mx-auto px-6 pt-12 pb-6">
  <p class="section-label mb-4"><a href="/districts" class="hover:text-[var(--ink)]">← Districts &amp; areas</a></p>
  <div class="flex items-center gap-3 mb-4">
    <span class="chip">{mrt} MRT · {line}</span>
    <span class="chip">{distance_chip}</span>
  </div>
  <h1 class="serif text-3xl md:text-5xl font-semibold leading-tight balance mb-4">Living near {mrt} with kids at {school}.</h1>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed">{school} is approximately <strong class="text-[var(--ink)]">{distance}</strong> from {mrt} MRT — about a <strong class="text-[var(--ink)]">{walk_time}</strong> walk. {phase_summary}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Distance band — what it means for primary 1 registration</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">{phase_heading}</h2>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-4">Singapore's primary 1 registration uses three distance bands: <strong class="text-[var(--ink)]">within 1 km</strong> (priority in Phase 2A and 2B), <strong class="text-[var(--ink)]">1–2 km</strong> (priority in Phase 2C), and <strong class="text-[var(--ink)]">beyond 2 km</strong> (Phase 2C only if seats remain after the 1–2 km band).</p>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-4">At <strong class="text-[var(--ink)]">{distance}</strong>, an address near {mrt} MRT typically falls into the <strong class="text-[var(--ink)]">{band_label}</strong> for {school}. {phase_detail}</p>
  <p class="text-[var(--ink-muted)] text-sm leading-relaxed">Distances above are approximate, measured station entrance to school gate. MOE measures by registered home address straight-line distance — confirm with the school and MOE's catchment finder before committing to a unit.</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">The area in one paragraph</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">What {mrt} feels like for a young family.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">{area_para}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Housing options in this catchment</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">BTOs, resale HDB, and condo within walking distance.</h2>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed mb-4">{housing_para}</p>
  <p class="text-[var(--ink-muted)] text-sm leading-relaxed">For specific transaction comps and BTO ballot odds in this estate, ping me on <a class="underline hover:text-[var(--ink)]" href="https://wa.me/6581618149">WhatsApp</a>. The math changes month-to-month.</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">FAQ</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">Common questions parents ask.</h2>
  <div class="space-y-6">
    <div>
      <p class="serif font-semibold text-[var(--ink)] mb-2">Is {distance} from {mrt} MRT close enough for Phase 2C priority at {school}?</p>
      <p class="text-[var(--ink-soft)] leading-relaxed">{faq1}</p>
    </div>
    <div>
      <p class="serif font-semibold text-[var(--ink)] mb-2">Should I buy HDB resale or wait for a BTO near {mrt}?</p>
      <p class="text-[var(--ink-soft)] leading-relaxed">For Phase 2B priority you generally need to be in the catchment for at least 30 months before P1 registration. If your child is &gt; 4 years old, BTO timelines often miss that window — resale (or a private condo if budget allows) is usually faster. Run the affordability and ABSD math first using the <a class="underline hover:text-[var(--ink)]" href="/tools/affordability">affordability calculator</a>.</p>
    </div>
    <div>
      <p class="serif font-semibold text-[var(--ink)] mb-2">What about ABSD if we already own a home?</p>
      <p class="text-[var(--ink-soft)] leading-relaxed">A second residential property triggers ABSD: 20% for SC second, 30% for PR second, 60% for foreigners. Check exposure with the <a class="underline hover:text-[var(--ink)]" href="/tools/absd">ABSD calculator</a> — and consider whether <a class="underline hover:text-[var(--ink)]" href="/insights/ownership-restructuring-math">ownership restructuring</a> is on the table before you buy.</p>
    </div>
    <div>
      <p class="serif font-semibold text-[var(--ink)] mb-2">Does living within 1 km guarantee a P1 spot at {school}?</p>
      <p class="text-[var(--ink-soft)] leading-relaxed">No — MOE allocates by ballot when phases are oversubscribed. The 1 km band raises priority, but balloting still applies in popular schools. Distance is necessary, not sufficient.</p>
    </div>
    <div>
      <p class="serif font-semibold text-[var(--ink)] mb-2">Who do I speak to for the property side?</p>
      <p class="text-[var(--ink-soft)] leading-relaxed">Me. I'm Winfred Quek, CEA R073319H, with Crestbrick. I run the 4-Pillar Audit on capital, cashflow, progression, and protection — including school-catchment timing — before recommending any move. <a class="underline hover:text-[var(--ink)]" href="/contact">Book a call</a> or message me on WhatsApp.</p>
    </div>
  </div>
</section>

<section class="max-w-4xl mx-auto px-6 py-16 text-center">
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">Buying near {school} on a school-catchment timeline?</h2>
  <p class="text-[var(--ink-soft)] max-w-xl mx-auto mb-8">The 4-Pillar Audit sequences your move so the school registration window doesn't get missed by a settlement-date mistake.</p>
  <div class="flex flex-wrap justify-center gap-3">
    <a href="https://calendly.com/winfredquekoc" class="btn btn-primary">Book the audit →</a>
    <a href="/tools" class="btn btn-ghost">Run the tools first</a>
  </div>
</section>
</main>

<footer class="divider bg-[var(--bg)]">
  <div class="max-w-6xl mx-auto px-6 py-14 grid md:grid-cols-3 gap-10">
    <div>
      <p class="serif font-semibold text-lg mb-3">Winfred Quek</p>
      <p class="text-sm text-[var(--ink-soft)] leading-relaxed mb-3">Investor-minded property advisor, Singapore.</p>
      <p class="text-xs text-[var(--ink-muted)] leading-relaxed">Salesperson of <strong class="text-[var(--ink-soft)]">Crestbrick</strong>, Singapore<br/>CEA Registration No.: <strong class="text-[var(--ink-soft)]">R073319H</strong></p>
    </div>
    <div>
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Quick links</p>
      <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
        <li><a href="/about" class="hover:text-[var(--ink)]">About Winfred</a></li>
        <li><a href="/tools" class="hover:text-[var(--ink)]">Tools</a></li>
        <li><a href="/insights" class="hover:text-[var(--ink)]">Insights</a></li>
        <li><a href="/districts" class="hover:text-[var(--ink)]">Districts</a></li>
      </ul>
    </div>
    <div>
      <p class="text-xs uppercase tracking-widest text-[var(--accent)] font-semibold mb-4">Contact</p>
      <ul class="space-y-2 text-sm text-[var(--ink-soft)]">
        <li><a href="https://wa.me/6581618149" class="hover:text-[var(--ink)]">WhatsApp · +65 8161 8149</a></li>
        <li><a href="mailto:winfredquekoc@gmail.com" class="hover:text-[var(--ink)]">winfredquekoc@gmail.com</a></li>
        <li><a href="https://calendly.com/winfredquekoc" class="hover:text-[var(--ink)]">Book via Calendly</a></li>
      </ul>
    </div>
  </div>
  <div class="border-t border-[var(--rule)]">
    <div class="max-w-6xl mx-auto px-6 py-6 flex flex-col md:flex-row justify-between gap-3 text-xs text-[var(--ink-muted)]">
      <p>© 2026 Winfred Quek · Crestbrick · CEA R073319H · All rights reserved.</p>
      <div class="flex gap-4"><a href="/privacy" class="hover:text-[var(--ink)]">Privacy / PDPA</a><span>·</span><span>Information on this site is general and not legal or financial advice.</span></div>
    </div>
  </div>
</footer>
<script>
(function(){{var t=document.getElementById('menu-toggle'),m=document.getElementById('mobile-menu');if(t&&m)t.addEventListener('click',function(){{m.classList.toggle('open');}});}})();
</script>
</body>
</html>
"""


def build_schema(slug, mrt, school, distance):
    page_url = f"https://winfredquek.com/area/{slug}"
    obj = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "@id": page_url + "#article",
                "headline": f"Living near {mrt} with kids at {school}",
                "datePublished": "2026-04-27",
                "dateModified": "2026-04-27",
                "author": {"@type": "Person", "name": "Winfred Quek", "identifier": "CEA R073319H"},
                "publisher": {"@type": "Organization", "name": "Crestbrick"},
                "url": page_url,
                "image": "https://winfredquek.com/img/og-image.jpg",
                "mainEntityOfPage": page_url,
            },
            {
                "@type": "Place",
                "@id": page_url + "#mrt",
                "name": f"{mrt} MRT",
                "addressCountry": "SG",
            },
            {
                "@type": "EducationalOrganization",
                "@id": page_url + "#school",
                "name": school,
                "address": {"@type": "PostalAddress", "addressCountry": "SG"},
                "areaServed": {"@type": "Place", "name": "Singapore"},
            },
            {
                "@type": "RealEstateAgent",
                "@id": "https://winfredquek.com/#agent",
                "name": "Winfred Quek",
                "url": "https://winfredquek.com/",
                "telephone": "+65-8161-8149",
                "identifier": {"@type": "PropertyValue", "propertyID": "CEA", "value": "R073319H"},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://winfredquek.com/"},
                    {"@type": "ListItem", "position": 2, "name": "Districts", "item": "https://winfredquek.com/districts"},
                    {"@type": "ListItem", "position": 3, "name": f"{mrt} × {school}", "item": page_url},
                ],
            },
        ],
    }
    return json.dumps(obj, ensure_ascii=False)


def area_paragraph(mrt):
    map_ = {
        "Bishan": "Bishan is the central north's blue-chip estate — Catholic High and Raffles Institution put a permanent floor under demand. Bishan-AMK Park, the Junction 8 hub, and dual NSL+CCL rail access make commute compression easy for dual-income parents.",
        "Toa Payoh": "Toa Payoh is the original new town and still one of the most liveable mature estates in Singapore. HDB Hub anchors retail; the NSL and the upcoming Toa Payoh interchange shorten the city commute to under 15 minutes.",
        "Novena": "Novena is medical-precinct mature: Tan Tock Seng, Mount Elizabeth Novena, and Velocity. Family-friendly because of the medical safety net plus easy access to ACS and SJI Junior.",
        "Newton": "Newton is the threshold between the city and Bukit Timah's school belt — short hop to Orchard, easy access to ACS Primary and SJI Junior. Mostly older condos, with steady rental demand from expat families.",
        "Bras Basah": "Bras Basah sits in the museum/arts belt — quiet residential pockets next to SMU, the National Library, and SJI Junior. Older condos and conservation shophouses dominate.",
        "Dhoby Ghaut": "Dhoby Ghaut is the city's busiest interchange — three lines (NSL/NEL/CCL). Living here trades quietness for unbeatable connectivity. Suits older kids who can self-commute.",
        "Tampines": "Tampines is the regional centre of the east — Tampines Mall, Century Square, the IKEA-Giant cluster, and a strong primary-school network. EWL + DTL means the city is 35 minutes by rail.",
        "Tampines East": "Tampines East is the newer residential extension along the DTL — quieter than Tampines proper, with newer BTO blocks and condos. Walkable to the Tampines Round Market hub.",
        "Pasir Ris": "Pasir Ris is the family-coastal end of the EWL — Downtown East, the beach park, and a string of well-loved primary schools. The Cross Island Line will lift connectivity by 2030.",
        "Punggol": "Punggol is Singapore's young-family magnet — Waterway Point, the LRT loop, and a fully built-out BTO ecosystem. The Cross Island Line and JTC's Digital District will reshape the demand profile through 2030.",
        "Sengkang": "Sengkang is Punggol's older sibling — Compass One, the LRT, and a deeper school catchment. Resale prices have outperformed since 2020 as the estate matured.",
        "Hougang": "Hougang is the NEL workhorse — heartland prices, Heartland Mall, and a strong cluster of schools (Xinmin, Holy Innocents'). Older blocks but mature amenities throughout.",
        "Serangoon": "Serangoon is the NEX-anchored interchange estate (NEL + CCL). NEX is one of Singapore's busiest suburban malls; Rosyth and Yangzheng pull catchment demand from across the north-east.",
        "Ang Mo Kio": "Ang Mo Kio is the original mature estate — AMK Hub, the heritage hawker scene, and a classic primary-school grid. NSL + the new CCL Mayflower extension make commute paths flexible.",
        "Mayflower": "Mayflower (TEL) opened in 2021 — quiet and leafy, walking distance to Ai Tong and CHIJ St Nicholas. One of the best new MRT stations for school-catchment buyers.",
        "Sixth Avenue": "Sixth Avenue is the Bukit Timah school belt's DTL access point — Methodist Girls', Henry Park, and Nanyang Primary all within or just beyond the 1 km band. Predominantly low-density landed and boutique condos.",
        "Tan Kah Kee": "Tan Kah Kee (DTL) is the Hwa Chong / Nanyang Primary doorstep — arguably the single most education-driven station in the network. Premium condos, premium prices.",
        "Holland Village": "Holland Village is the leafy expat-and-creative cluster — Henry Park Primary catchment, plus the Holland Drive food/F&B strip. CCL access; Buona Vista interchange is one stop away.",
        "Buona Vista": "Buona Vista is the One-North precinct — research, biotech, NUS, and the Star Vista mall. Fairfield Methodist anchors the school side. EWL + CCL interchange is unusually convenient.",
        "Queenstown": "Queenstown is Singapore's first satellite town and now one of the most desirable mature HDB estates — close to CBD, walking to IKEA Alexandra, Queenstown Primary, and the new Margaret Drive estate.",
        "Redhill": "Redhill is the budget-friendly EWL stop adjacent to Tiong Bahru — older HDB blocks but strong location. Alexandra Primary and Gan Eng Seng Primary anchor the school side.",
        "Tiong Bahru": "Tiong Bahru is the heritage-mature estate — pre-war SIT flats, the Tiong Bahru market, and a young-family cafe scene. Zhangde Primary keeps catchment demand high.",
        "Outram Park": "Outram Park is the EWL/NEL/TEL triple interchange — SGH precinct, Pearl's Hill, and the Cantonment Primary catchment. The new Cantonment Towers BTO project sits within walking distance.",
        "Marine Parade": "Marine Parade got its TEL station in 2024 — a transformational change for what was previously a car-dependent enclave. Tao Nan, Ngee Ann, and CHIJ Primary anchor a deep school grid.",
        "Dakota": "Dakota is the CCL's quiet residential stop — Old Airport Road hawker centre and Kong Hwa make this one of the east's best food + school combos.",
        "Paya Lebar": "Paya Lebar is the EWL/CCL interchange anchored by PLQ — Singapore Post Centre, Paya Lebar Square, and a major office cluster. Geylang Methodist Primary is the natural local catchment.",
        "Bedok": "Bedok is the EWL east-end heartland — Bedok Mall, Heartbeat@Bedok, and a wide primary-school choice. Resale HDB performance has been strong post-MOP for the newer BTO cohorts.",
        "Clementi": "Clementi is the west-side EWL anchor — Clementi Mall, NUS proximity, and a deep cluster of primary schools. Nan Hua sits 1.5 km out, drawing parents into the surrounding HDB and condo stock.",
        "Jurong East": "Jurong East is the regional centre of the west — JEM, Westgate, IMM, the future Jurong Region Line, and the upcoming JLD transformation. Fuhua and Yuhua anchor the catchment.",
        "Woodlands": "Woodlands is the north's regional centre — Causeway Point, the future Woodlands North RTS link to JB. NSL + TEL access; Woodgrove Primary is the leading local catchment school.",
        "Yishun": "Yishun is the north's heartland mature estate — Northpoint City, the Khatib Camp military cluster, and a wide primary-school grid. NSL access; tight value-for-quantum on resale HDB.",
        "Choa Chu Kang": "Choa Chu Kang is the NSL/LRT north-west hub — Lot One Mall and the Tengah BTO frontier. South View Primary anchors the local catchment.",
        "Bukit Panjang": "Bukit Panjang is the DTL/LRT west-suburb hub — Hillion Mall, Bukit Panjang Plaza, and the Zhenghua Primary catchment. Quieter than central but well-connected.",
    }
    return map_.get(mrt, f"{mrt} is one of Singapore's well-connected residential nodes — anchor amenities, MRT access, and a steady rental and resale market.")


def housing_paragraph(mrt):
    map_ = {
        "Bishan": "Stock here mixes Bishan Park, Sin Ming, and Thomson-side condos with consistently top-priced HDB resale around blocks 100–170. Recent BTO supply has been thin; expect resale-led entry. Newer condos like Sky@Eleven and the Marymount-side launches set the price benchmarks.",
        "Toa Payoh": "Toa Payoh has a steady BTO pipeline (Lorong 6, Bidadari spillover) plus mature HDB resale and selective condos around Lorong 4–8. The Bidadari estate has materially shifted the catchment for First Toa Payoh and CHIJ Primary.",
        "Tampines": "Tampines is a BTO-rich estate — recent and upcoming launches around Tampines North and the EC pipeline at Tampines Avenue 11. Resale HDB is mature; private supply leans towards Treasure at Tampines and the older Centris cluster.",
        "Punggol": "Punggol's housing supply is BTO-led — Northshore, Matilda, and the Punggol Coast pipeline. Condo supply (Watertown, Treasure Crest, Piermont Grand EC) is concentrated along the waterway. Resale BTOs from 2014–2018 cohorts now drive the secondary market.",
        "Sengkang": "Sengkang has deep BTO and resale stock plus condos along Compassvale and Anchorvale. Recent EC launches (Parc Greenwich, North Gaia neighbours) anchor the upper end of pricing.",
        "Bishan": "Bishan stock is dominated by mature HDB blocks and a tight ring of condos — Sky@Eleven, Bishan 8, and the Thomson-East side. Resale HDB outperforms most other mature estates on a per-square-foot basis.",
        "Marine Parade": "Marine Parade, now with TEL access, has older condos (Mandarin Gardens, Costa del Sol nearby) and the newer Amber Park / Meyer Mansion cluster. HDB stock is concentrated around Marine Terrace.",
        "Punggol": "Punggol's housing supply is BTO-led — Northshore, Matilda, and the Punggol Coast pipeline. Watertown condo and Piermont Grand EC are the mature-private anchors.",
    }
    base = map_.get(mrt, f"Housing stock around {mrt} typically mixes HDB BTO and resale flats with a layer of condos within walking distance of the station. Run a transaction-comp pull before benchmarking — recent caveats often surprise both buyers and sellers.")
    base += " For specific BTO ballot odds and condo comps, message me on WhatsApp — the answer changes month-to-month."
    return base


def phase_summary(band, distance):
    if band == "A":
        return f"That places it inside the 1 km priority band for Phase 2A and 2B registration."
    return f"That places it in the 1–2 km band — Phase 2C priority but not Phase 2B."


def phase_heading(band):
    return "Inside the 1 km priority band." if band == "A" else "Inside the 1–2 km band."


def phase_detail(band):
    if band == "A":
        return "In a year where the school is undersubscribed at Phase 2A, that priority is effectively a guarantee. In oversubscribed years, the 1 km band still means you ballot before 1–2 km applicants."
    return "Phase 2C applicants in the 1–2 km band ballot ahead of Phase 2C applicants beyond 2 km. If the school is oversubscribed at Phase 2C, the 1 km band gets a meaningful edge — but you'll need to register early in 2C."


def band_label(band):
    return "1 km priority band" if band == "A" else "1–2 km band"


def faq1(school, distance, band):
    if band == "A":
        return f"Yes — at {distance}, you sit inside the 1 km priority band for {school}. That's the strongest catchment-distance position you can hold short of being on the same street as the school."
    return f"Partly — at {distance}, you sit inside the 1–2 km band, which is Phase 2C priority. Phase 2B and earlier (alumni, sibling, volunteer) still rank ahead. Distance alone does not lock a spot in oversubscribed years."


def main():
    sitemap_lines = []
    count = 0
    seen = set()
    for mrt_slug, mrt_name, line, school_slug, school_name, band, distance, walk_time in PAIRS:
        slug = f"{mrt_slug}-{school_slug}"
        if slug in seen:
            continue
        seen.add(slug)

        schema = build_schema(slug, mrt_name, school_name, distance)
        distance_chip = "Within 1 km" if band == "A" else "1–2 km band"
        html = PAGE_TPL.format(
            slug=escape(slug),
            mrt=escape(mrt_name),
            line=escape(line),
            school=escape(school_name),
            distance=escape(distance),
            walk_time=escape(walk_time),
            phase_summary=escape(phase_summary(band, distance)),
            phase_heading=escape(phase_heading(band)),
            phase_detail=escape(phase_detail(band)),
            band_label=escape(band_label(band)),
            distance_chip=distance_chip,
            area_para=escape(area_paragraph(mrt_name)),
            housing_para=escape(housing_paragraph(mrt_name)),
            faq1=escape(faq1(school_name, distance, band)),
            schema_json=schema,
        )
        out_path = OUT / f"{slug}.html"
        out_path.write_text(html, encoding="utf-8")
        sitemap_lines.append(
            f'  <url><loc>https://winfredquek.com/area/{slug}</loc><lastmod>2026-04-27</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>'
        )
        count += 1
    print(f"Wrote {count} area pages to {OUT}")

    # Also write out a sitemap fragment for splice-in
    frag = ROOT / "_area-sitemap-fragment.xml"
    frag.write_text("\n".join(sitemap_lines), encoding="utf-8")
    print(f"Wrote sitemap fragment ({len(sitemap_lines)} lines) to {frag}")


if __name__ == "__main__":
    main()
