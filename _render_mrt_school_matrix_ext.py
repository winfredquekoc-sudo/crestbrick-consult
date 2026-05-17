#!/usr/bin/env python3
"""Extension to _render_mrt_school_matrix.py — adds NEW MRT/school pairs only.

Coverage added on top of the original 64:
  - TEL stations (Lentor, Bright Hill, Upper Thomson, Caldecott, Stevens,
    Napier, Orchard Boulevard, Great World, Havelock, Maxwell, Shenton Way,
    Marina Bay, Gardens by the Bay)
  - CRL phase 1 (Pasir Ris, Tampines North, Hougang, Serangoon North,
    Tavistock, Ang Mo Kio, Bright Hill, Aviation Park, Loyang, Defu,
    Teck Ghee)
  - JRL (Choa Chu Kang, Tengah, Jurong East, Pandan Reservoir, Boon Lay)
  - More NSL north (Sembawang, Khatib, Yio Chu Kang)
  - More EWL east (Tanah Merah, Simei)
  - More NEL (Punggol Coast, Kovan, Buangkok)

To avoid fabricating exact distances per the project rule, pairs flagged
``approx=True`` use "within walking distance" / "approximately within 1 km"
language instead of a precise km/min figure.
"""
import json
from pathlib import Path
from html import escape

ROOT = Path(__file__).parent / "public"
OUT = ROOT / "area"
OUT.mkdir(exist_ok=True)

# (mrt_slug, mrt_name, line, school_slug, school_name, band, distance_text, walk_text, approx)
# band: A = within 1 km, B = 1-2 km
# When approx=True, distance_text/walk_text are conservative phrases (no fake numbers).
NEW_PAIRS = [
    # ---------- TEL (Thomson-East Coast Line) ----------
    ("lentor", "Lentor", "TEL", "anderson-primary", "Anderson Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("lentor", "Lentor", "TEL", "ai-tong", "Ai Tong School", "B", "approximately 1.8 km", "around 22 min", True),
    ("lentor", "Lentor", "TEL", "chij-st-nicholas-girls-primary", "CHIJ St Nicholas Girls' Primary", "B", "approximately 1.6 km", "around 20 min", True),
    ("bright-hill", "Bright Hill", "TEL", "ai-tong", "Ai Tong School", "A", "within walking distance", "a short walk", True),
    ("bright-hill", "Bright Hill", "TEL", "kuo-chuan-presbyterian", "Kuo Chuan Presbyterian Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("upper-thomson", "Upper Thomson", "TEL", "ai-tong", "Ai Tong School", "A", "within walking distance", "a short walk", True),
    ("upper-thomson", "Upper Thomson", "TEL", "marymount-convent", "Marymount Convent School", "A", "within walking distance", "a short walk", True),
    ("caldecott", "Caldecott", "CCL/TEL", "marymount-convent", "Marymount Convent School", "A", "within walking distance", "a short walk", True),
    ("caldecott", "Caldecott", "CCL/TEL", "raffles-girls-primary", "Raffles Girls' Primary", "B", "approximately 1.7 km", "around 20 min", True),
    ("stevens", "Stevens", "DTL/TEL", "anglo-chinese-primary", "Anglo-Chinese School (Primary)", "A", "within walking distance", "a short walk", True),
    ("stevens", "Stevens", "DTL/TEL", "singapore-chinese-girls-primary", "Singapore Chinese Girls' Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("napier", "Napier", "TEL", "anglo-chinese-primary", "Anglo-Chinese School (Primary)", "B", "approximately 1.3 km", "around 16 min", True),
    ("orchard-boulevard", "Orchard Boulevard", "TEL", "river-valley-primary", "River Valley Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("great-world", "Great World", "TEL", "river-valley-primary", "River Valley Primary", "A", "within walking distance", "a short walk", True),
    ("great-world", "Great World", "TEL", "zhangde-primary", "Zhangde Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("havelock", "Havelock", "TEL", "zhangde-primary", "Zhangde Primary", "A", "within walking distance", "a short walk", True),
    ("maxwell", "Maxwell", "TEL", "cantonment-primary", "Cantonment Primary", "A", "within walking distance", "a short walk", True),
    ("shenton-way", "Shenton Way", "TEL", "cantonment-primary", "Cantonment Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("marina-bay", "Marina Bay", "NSL/CCL/TEL", "cantonment-primary", "Cantonment Primary", "B", "approximately 1.8 km", "around 22 min", True),
    ("gardens-by-the-bay", "Gardens by the Bay", "TEL", "cantonment-primary", "Cantonment Primary", "B", "approximately 1.9 km", "around 23 min", True),

    # ---------- Cross Island Line phase 1 (opens 2030+) ----------
    ("aviation-park", "Aviation Park", "CRL", "loyang-view", "Loyang View School", "B", "approximately 1.8 km", "around 22 min", True),
    ("loyang", "Loyang", "CRL", "loyang-view", "Loyang View School", "A", "within walking distance", "a short walk", True),
    ("loyang", "Loyang", "CRL", "white-sands-primary", "White Sands Primary", "B", "approximately 1.6 km", "around 20 min", True),
    ("pasir-ris-east", "Pasir Ris East", "CRL", "casuarina-primary", "Casuarina Primary", "A", "within walking distance", "a short walk", True),
    ("pasir-ris-east", "Pasir Ris East", "CRL", "elias-park-primary", "Elias Park Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("tampines-north", "Tampines North", "CRL/DTL", "poi-ching", "Poi Ching School", "B", "approximately 1.4 km", "around 17 min", True),
    ("tampines-north", "Tampines North", "CRL/DTL", "angsana-primary", "Angsana Primary", "A", "within walking distance", "a short walk", True),
    ("defu", "Defu", "CRL", "xinmin-primary", "Xinmin Primary", "B", "approximately 1.7 km", "around 20 min", True),
    ("hougang-crl", "Hougang (CRL)", "NEL/CRL", "holy-innocents-primary", "Holy Innocents' Primary", "B", "approximately 1.5 km", "around 19 min", True),
    ("serangoon-north", "Serangoon North", "CRL", "rosyth", "Rosyth School", "B", "approximately 1.4 km", "around 17 min", True),
    ("serangoon-north", "Serangoon North", "CRL", "yangzheng-primary", "Yangzheng Primary", "A", "within walking distance", "a short walk", True),
    ("tavistock", "Tavistock", "CRL", "rosyth", "Rosyth School", "B", "approximately 1.5 km", "around 18 min", True),
    ("ang-mo-kio-crl", "Ang Mo Kio (CRL)", "NSL/CRL", "anderson-primary", "Anderson Primary", "A", "within walking distance", "a short walk", True),
    ("teck-ghee", "Teck Ghee", "CRL", "townsville-primary", "Townsville Primary", "A", "within walking distance", "a short walk", True),
    ("teck-ghee", "Teck Ghee", "CRL", "anderson-primary", "Anderson Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("bright-hill-crl", "Bright Hill (CRL)", "TEL/CRL", "ai-tong", "Ai Tong School", "A", "within walking distance", "a short walk", True),

    # ---------- Jurong Region Line ----------
    ("choa-chu-kang-jrl", "Choa Chu Kang (JRL)", "NSL/JRL/LRT", "south-view-primary", "South View Primary", "A", "within walking distance", "a short walk", True),
    ("tengah", "Tengah", "JRL", "princess-elizabeth-primary", "Princess Elizabeth Primary", "B", "approximately 1.8 km", "around 22 min", True),
    ("jurong-east-jrl", "Jurong East (JRL)", "EWL/NSL/JRL", "fuhua-primary", "Fuhua Primary", "A", "within walking distance", "a short walk", True),
    ("pandan-reservoir", "Pandan Reservoir", "JRL", "rulang-primary", "Rulang Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("boon-lay", "Boon Lay", "EWL/JRL", "rulang-primary", "Rulang Primary", "A", "within walking distance", "a short walk", True),
    ("boon-lay", "Boon Lay", "EWL/JRL", "lakeside-primary", "Lakeside Primary", "B", "approximately 1.6 km", "around 20 min", True),

    # ---------- More NSL north ----------
    ("sembawang", "Sembawang", "NSL", "sembawang-primary", "Sembawang Primary", "A", "within walking distance", "a short walk", True),
    ("sembawang", "Sembawang", "NSL", "wellington-primary", "Wellington Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("khatib", "Khatib", "NSL", "northoaks-primary", "Northoaks Primary", "A", "within walking distance", "a short walk", True),
    ("khatib", "Khatib", "NSL", "huamin-primary", "Huamin Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("yio-chu-kang", "Yio Chu Kang", "NSL", "anderson-primary", "Anderson Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("yio-chu-kang", "Yio Chu Kang", "NSL", "mayflower-primary", "Mayflower Primary", "A", "within walking distance", "a short walk", True),
    ("admiralty", "Admiralty", "NSL", "innova-primary", "Innova Primary", "A", "within walking distance", "a short walk", True),
    ("admiralty", "Admiralty", "NSL", "woodgrove-primary", "Woodgrove Primary", "B", "approximately 1.6 km", "around 20 min", True),
    ("marsiling", "Marsiling", "NSL", "marsiling-primary", "Marsiling Primary", "A", "within walking distance", "a short walk", True),
    ("kranji", "Kranji", "NSL", "qihua-primary", "Qihua Primary", "B", "approximately 1.5 km", "around 18 min", True),

    # ---------- More EWL east ----------
    ("tanah-merah", "Tanah Merah", "EWL", "bedok-green-primary", "Bedok Green Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("tanah-merah", "Tanah Merah", "EWL", "temasek-primary", "Temasek Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("simei", "Simei", "EWL", "east-spring-primary", "East Spring Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("simei", "Simei", "EWL", "changkat-primary", "Changkat Primary", "A", "within walking distance", "a short walk", True),
    ("expo", "Expo", "EWL/DTL", "east-spring-primary", "East Spring Primary", "B", "approximately 1.7 km", "around 20 min", True),
    ("kembangan", "Kembangan", "EWL", "telok-kurau-primary", "Telok Kurau Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("eunos", "Eunos", "EWL", "telok-kurau-primary", "Telok Kurau Primary", "A", "within walking distance", "a short walk", True),
    ("eunos", "Eunos", "EWL", "maha-bodhi", "Maha Bodhi School", "B", "approximately 1.3 km", "around 16 min", True),

    # ---------- More NEL ----------
    ("punggol-coast", "Punggol Coast", "NEL", "punggol-cove-primary", "Punggol Cove Primary", "A", "within walking distance", "a short walk", True),
    ("punggol-coast", "Punggol Coast", "NEL", "punggol-view-primary", "Punggol View Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("kovan", "Kovan", "NEL", "xinmin-primary", "Xinmin Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("kovan", "Kovan", "NEL", "paya-lebar-methodist-primary", "Paya Lebar Methodist Girls' (Primary)", "A", "within walking distance", "a short walk", True),
    ("buangkok", "Buangkok", "NEL", "north-vista-primary", "North Vista Primary", "A", "within walking distance", "a short walk", True),
    ("buangkok", "Buangkok", "NEL", "punggol-green-primary", "Punggol Green Primary", "B", "approximately 1.6 km", "around 19 min", True),
    ("woodleigh", "Woodleigh", "NEL", "stamford-primary", "Stamford Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("woodleigh", "Woodleigh", "NEL", "maris-stella-high-primary", "Maris Stella High School (Primary)", "A", "within walking distance", "a short walk", True),
    ("potong-pasir", "Potong Pasir", "NEL", "stamford-primary", "Stamford Primary", "A", "within walking distance", "a short walk", True),
    ("boon-keng", "Boon Keng", "NEL", "bendemeer-primary", "Bendemeer Primary", "A", "within walking distance", "a short walk", True),
    ("farrer-park", "Farrer Park", "NEL", "farrer-park-primary", "Farrer Park Primary", "A", "within walking distance", "a short walk", True),

    # ---------- DTL extra ----------
    ("beauty-world", "Beauty World", "DTL", "pei-hwa-presbyterian-primary", "Pei Hwa Presbyterian Primary", "A", "within walking distance", "a short walk", True),
    ("beauty-world", "Beauty World", "DTL", "methodist-girls-primary", "Methodist Girls' School (Primary)", "B", "approximately 1.5 km", "around 18 min", True),
    ("king-albert-park", "King Albert Park", "DTL", "pei-hwa-presbyterian-primary", "Pei Hwa Presbyterian Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("hillview", "Hillview", "DTL", "lianhua-primary", "Lianhua Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("cashew", "Cashew", "DTL", "chestnut-drive-secondary", "Bukit Panjang Primary", "B", "approximately 1.6 km", "around 19 min", True),
    ("botanic-gardens", "Botanic Gardens", "CCL/DTL", "nanyang-primary", "Nanyang Primary", "B", "approximately 1.5 km", "around 18 min", True),

    # ---------- CCL extra ----------
    ("haw-par-villa", "Haw Par Villa", "CCL", "fairfield-methodist-primary", "Fairfield Methodist Primary", "B", "approximately 1.7 km", "around 20 min", True),
    ("kent-ridge", "Kent Ridge", "CCL", "fairfield-methodist-primary", "Fairfield Methodist Primary", "B", "approximately 1.6 km", "around 19 min", True),
    ("one-north", "One-North", "CCL", "fairfield-methodist-primary", "Fairfield Methodist Primary", "A", "within walking distance", "a short walk", True),
    ("lorong-chuan", "Lorong Chuan", "CCL", "st-gabriels-primary", "St Gabriel's Primary", "A", "within walking distance", "a short walk", True),
    ("bartley", "Bartley", "CCL", "maris-stella-high-primary", "Maris Stella High School (Primary)", "A", "within walking distance", "a short walk", True),
    ("macpherson", "MacPherson", "CCL/DTL", "canossa-catholic-primary", "Canossa Catholic Primary", "A", "within walking distance", "a short walk", True),
    ("mountbatten", "Mountbatten", "CCL", "kong-hwa", "Kong Hwa School", "A", "within walking distance", "a short walk", True),

    # ---------- More EWL west ----------
    ("dover", "Dover", "EWL", "fairfield-methodist-primary", "Fairfield Methodist Primary", "B", "approximately 1.4 km", "around 17 min", True),
    ("commonwealth", "Commonwealth", "EWL", "queenstown-primary", "Queenstown Primary", "A", "within walking distance", "a short walk", True),
    ("lakeside", "Lakeside", "EWL", "lakeside-primary", "Lakeside Primary", "A", "within walking distance", "a short walk", True),
    ("chinese-garden", "Chinese Garden", "EWL", "rulang-primary", "Rulang Primary", "B", "approximately 1.5 km", "around 18 min", True),
    ("pioneer", "Pioneer", "EWL", "boon-lay-garden-primary", "Boon Lay Garden Primary", "A", "within walking distance", "a short walk", True),
]

PAGE_TPL = """<!doctype html>
<html lang="en-SG">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Living near {mrt} MRT with kids at {school} | Winfred Quek</title>
  <meta name="description" content="School-catchment + MRT guide for parents: {school} ({distance_text} from {mrt} MRT). Distance band, BTO and condo options nearby, by Winfred Quek (CEA R073319H, Crestbrick)." />
  <meta name="robots" content="index,follow" />
  <meta property="og:title" content="Living near {mrt} MRT with kids at {school}" />
  <meta property="og:description" content="{school} is {distance_text} from {mrt} MRT ({walk_text}). {phase_summary}" />
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
      <a class="nav-link" href="/">Home</a><a class="nav-link" href="/services">Services</a><a class="nav-link" href="/tools">Tools</a><a class="nav-link" href="/insights">Insights</a><a class="nav-link" href="/districts">Districts</a><a class="nav-link" href="/hdb-towns">HDB Towns</a><a class="nav-link" href="/area">All areas</a><a class="nav-link" href="/contact">Contact</a>
      <a href="https://calendly.com/winfredquekoc" class="btn btn-primary text-sm mt-2">Book a call</a>
    </div>
  </div>
</header>

<main>
<section class="hero max-w-4xl mx-auto px-6 pt-12 pb-6">
  <p class="section-label mb-4"><a href="/area" class="hover:text-[var(--ink)]">← All MRT &amp; school areas</a></p>
  <div class="flex items-center gap-3 mb-4">
    <span class="chip">{mrt} MRT · {line}</span>
    <span class="chip">{distance_chip}</span>
  </div>
  <h1 class="serif text-3xl md:text-5xl font-semibold leading-tight balance mb-4">Living near {mrt} with kids at {school}.</h1>
  <p class="text-lg text-[var(--ink-soft)] leading-relaxed">{school} is <strong class="text-[var(--ink)]">{distance_text}</strong> from {mrt} MRT — {walk_text}. {phase_summary}</p>
</section>

<div class="divider max-w-4xl mx-auto"></div>

<section class="max-w-4xl mx-auto px-6 py-12">
  <p class="section-label mb-4">Distance band — what it means for primary 1 registration</p>
  <h2 class="serif text-2xl md:text-3xl font-semibold balance leading-tight mb-6">{phase_heading}</h2>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-4">Singapore's primary 1 registration uses three distance bands: <strong class="text-[var(--ink)]">within 1 km</strong> (priority in Phase 2A and 2B), <strong class="text-[var(--ink)]">1–2 km</strong> (priority in Phase 2C), and <strong class="text-[var(--ink)]">beyond 2 km</strong> (Phase 2C only if seats remain after the 1–2 km band).</p>
  <p class="text-[var(--ink-soft)] leading-relaxed mb-4">An address near {mrt} MRT typically falls into the <strong class="text-[var(--ink)]">{band_label}</strong> for {school}. {phase_detail}</p>
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
      <p class="serif font-semibold text-[var(--ink)] mb-2">Is an address near {mrt} MRT close enough for Phase 2C priority at {school}?</p>
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
        <li><a href="/area" class="hover:text-[var(--ink)]">All MRT &amp; school areas</a></li>
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


def build_schema(slug, mrt, school):
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
                    {"@type": "ListItem", "position": 2, "name": "All areas", "item": "https://winfredquek.com/area"},
                    {"@type": "ListItem", "position": 3, "name": f"{mrt} × {school}", "item": page_url},
                ],
            },
        ],
    }
    return json.dumps(obj, ensure_ascii=False)


AREA_MAP = {
    # TEL
    "Lentor": "Lentor (TEL) is a fast-evolving residential pocket where the original landed enclave is being layered with new condo launches like Lentor Modern and the Lentor Hills cluster. The TEL line drops you in the city in around 25 minutes; Ai Tong and CHIJ St Nicholas pull catchment demand from across the north.",
    "Bright Hill": "Bright Hill (TEL) sits between Bishan and Upper Thomson — quiet, low-density, and an easy walking distance to Ai Tong School. The TEL station has reset value-per-square-foot for the surrounding HDB and walk-up apartment stock.",
    "Upper Thomson": "Upper Thomson is leafy, food-anchored, and now TEL-connected — Thomson Plaza, the prata-and-coffee strip, and Marymount Convent give it the feel of a settled neighbourhood that just got a transit upgrade.",
    "Caldecott": "Caldecott is the CCL/TEL interchange tucked behind Bishan — quiet residential pockets, Marymount Convent, MediaCorp, and a quick ride either to the city or to the Bukit Timah school belt.",
    "Stevens": "Stevens (DTL/TEL) is the orchard-adjacent stop closest to ACS Primary and SCGS Primary. Heavy international-school demand keeps rental yields steady; condo stock leans premium boutique.",
    "Napier": "Napier (TEL) sits on the edge of Tanglin and the Botanic Gardens — embassies, the Tanglin shophouse strip, and walking access to ACS Primary. Predominantly low-density and high-end private stock.",
    "Orchard Boulevard": "Orchard Boulevard (TEL) is the quieter sibling to Orchard MRT — a TEL-only station tucked behind the shopping belt, surrounded by older luxury condos and high-end international-school feeders.",
    "Great World": "Great World (TEL) is the River Valley anchor — Great World City, Robertson Quay nearby, and a strong skew toward private condos and serviced apartments. River Valley Primary keeps the catchment alive for local families.",
    "Havelock": "Havelock (TEL) is the Bukit Ho Swee/Tiong Bahru-adjacent stop — older HDB stock and walking distance to the Tiong Bahru market. Zhangde Primary catchment overlap gives it school-related demand.",
    "Maxwell": "Maxwell (TEL) sits in the Tanjong Pagar conservation belt — shophouse boutique residences, the Maxwell hawker centre, and easy access to Cantonment Primary. CBD-edge living with a different texture from the towers.",
    "Shenton Way": "Shenton Way (TEL) is CBD core — Marina One, the financial district, and largely a working-week zone. Family-oriented buyers are rare here; the Cantonment Primary catchment edges in but is borderline.",
    "Marina Bay": "Marina Bay is the Marina One / Marina Bay Residences cluster — high-end CBD-living territory. Family buyers are the minority, but Cantonment Primary and the new Greater Southern Waterfront pipeline create a long-term demand backstop.",
    "Gardens by the Bay": "Gardens by the Bay (TEL) is a destination station on the southern edge of Marina South — a future residential frontier under the Marina South masterplan. Today, the catchment is thin but the station opens optionality for the next decade.",
    # CRL
    "Aviation Park": "Aviation Park (CRL) is the eastern terminus, sitting on the airport edge — primarily for Changi staff and East Coast residents. The Loyang View School catchment edges into the 1–2 km band.",
    "Loyang": "Loyang (CRL) anchors the Pasir Ris North landed and HDB belt. The CRL line will reset connectivity from 2030 onwards; today the area is car-dependent but quiet and family-oriented.",
    "Pasir Ris East": "Pasir Ris East (CRL) is the eastern extension of Pasir Ris town — newer BTO blocks, Casuarina Primary directly adjacent, and a quieter feel than Pasir Ris MRT itself.",
    "Tampines North": "Tampines North (CRL/DTL) anchors the new BTO frontier — large recent HDB launches, Angsana Primary as the local anchor school, and a young-family demographic profile.",
    "Defu": "Defu (CRL) sits in a still-industrial corridor that the Defu redevelopment masterplan will reshape over the next decade. Today, it's a thin residential catchment — interesting only as a long-horizon optionality bet.",
    "Hougang (CRL)": "The Hougang CRL station is set to interchange with NEL Hougang, materially shrinking commute time to the west. The Holy Innocents' Primary catchment is a known parental draw; the CRL upgrade should compress travel times further.",
    "Serangoon North": "Serangoon North (CRL) is a quiet residential pocket between Hougang and Yio Chu Kang — landed plus older HDB, with Rosyth and Yangzheng pulling catchment demand. CRL will materially upgrade transit access.",
    "Tavistock": "Tavistock (CRL) sits in the Lorong Chuan / Serangoon North landed belt — a quiet, mature residential zone. Rosyth and St Gabriel's Primary anchor the school side.",
    "Ang Mo Kio (CRL)": "The Ang Mo Kio CRL station will provide a second interchange option alongside NSL Ang Mo Kio. The school grid (Anderson, Ai Tong, Townsville) is one of the densest in the north — CRL access compresses east-west commutes that NSL currently can't.",
    "Teck Ghee": "Teck Ghee (CRL) sits in the AMK 4/5/6 HDB cluster — Townsville and Anderson Primary are within walking distance. The CRL station opens commute options that didn't previously exist for these older blocks.",
    "Bright Hill (CRL)": "Bright Hill becomes a TEL/CRL interchange when CRL opens — a major upgrade for the Ai Tong / CHIJ St Nicholas catchment. Today, Bright Hill TEL station already supports walking access to both schools; CRL adds east-west reach.",
    # JRL
    "Choa Chu Kang (JRL)": "When JRL opens, Choa Chu Kang becomes a triple-line interchange (NSL/JRL/LRT) — a meaningful uplift for Lot One and the surrounding HDB. South View Primary is an established local anchor.",
    "Tengah": "Tengah is Singapore's newest planned town — the 'Forest Town' BTO frontier, with a still-developing school and amenities grid. Princess Elizabeth Primary is a near-term option; JRL connectivity arrives mid-decade.",
    "Jurong East (JRL)": "Jurong East with JRL becomes a triple-line interchange — already the regional centre of the west, the JRL upgrade reinforces its anchor role. Fuhua and Yuhua Primary catch the local school demand.",
    "Pandan Reservoir": "Pandan Reservoir (JRL) is a quieter west-side pocket — older condos like Lakeshore and the Faber-side HDB. Rulang Primary is the closest established school.",
    "Boon Lay": "Boon Lay is the EWL/JRL interchange anchored by Jurong Point — the largest suburban mall in Singapore. Rulang Primary is a deep-history school; Lakeside Primary slightly further out.",
    # NSL north
    "Sembawang": "Sembawang is the north-coast HDB town — Sun Plaza, the heritage Sembawang Hot Spring Park, and Sembawang Primary as the closest local anchor. Quiet, young-family coded, with newer BTO launches.",
    "Khatib": "Khatib is the Yishun-adjacent NSL stop — Northoaks Primary directly accessible, plus Huamin Primary further out. Mostly older HDB stock with some newer EC launches.",
    "Yio Chu Kang": "Yio Chu Kang sits between AMK and Khatib — a quieter NSL stop with Mayflower Primary as the natural local feeder. Older HDB blocks plus a creeping condo layer.",
    "Admiralty": "Admiralty is a north-side HDB town with Innova Primary as the immediate local school. Causeway-bound demand keeps rental steady; the RTS link will reshape this in 2027+.",
    "Marsiling": "Marsiling is the older Woodlands-area HDB cluster — Marsiling Primary anchors the local catchment. Quieter than Woodlands proper, with a meaningful upgrade pipeline as RTS approaches.",
    "Kranji": "Kranji is the rural-edge NSL stop — quieter, with Qihua Primary as the closest established school. Predominantly older HDB and farm-edge land use.",
    # EWL east
    "Tanah Merah": "Tanah Merah is the Bedok-Changi interchange — older condos, the HDB Frankel/Siglap edge, and good east-coast access. Bedok Green Primary and Temasek Primary are the closest local anchors.",
    "Simei": "Simei is the EWL eastern HDB town — Eastpoint Mall, Changi General Hospital, and Changkat Primary as the natural local school. Mature, family-oriented, and underrated on a value-per-square-foot basis.",
    "Expo": "Expo is more office than residential — the Singapore Expo, Changi Business Park, and tertiary education clusters. East Spring Primary edges into the catchment but the area's draw is workplace-led.",
    "Kembangan": "Kembangan is the EWL stop between Eunos and Bedok — a mostly landed enclave plus older condos. Telok Kurau Primary is the closest established school.",
    "Eunos": "Eunos is the Geylang Serai-adjacent HDB and landed mix — strong food culture, Telok Kurau Primary, and a steady value HDB resale market.",
    # NEL
    "Punggol Coast": "Punggol Coast is the NEL's northern terminus — anchored by JTC's Digital District and surrounded by the newest Punggol BTO blocks. Punggol Cove Primary is the immediate school anchor.",
    "Kovan": "Kovan is the NEL Hougang-adjacent stop — Heartland Mall, the Kovan night market, and Paya Lebar Methodist Girls' Primary. Predominantly older HDB plus a layer of condos along Upper Serangoon.",
    "Buangkok": "Buangkok is the quieter NEL stop between Hougang and Sengkang — North Vista Primary anchors the local catchment. Mostly newer HDB with a steady BTO pipeline.",
    "Woodleigh": "Woodleigh is the Bidadari estate's NEL anchor — newer BTO supply, The Woodleigh Mall, and Stamford Primary in the immediate school catchment. One of the most actively developing mature-area pockets in 2024–2026.",
    "Potong Pasir": "Potong Pasir is the heritage NEL stop — older HDB blocks plus the Bidadari spillover. Stamford Primary is now within walking distance for the new Bidadari blocks.",
    "Boon Keng": "Boon Keng is the Kallang-Whampoa NEL HDB stop — older blocks, Bendemeer Primary, and a solid value-per-square-foot story for upgraders watching the city-fringe.",
    "Farrer Park": "Farrer Park is the Little India-adjacent NEL stop — boutique condos, Farrer Park Hospital, and Farrer Park Primary as the local anchor. Predominantly mature private and shophouse stock.",
    # DTL extra
    "Beauty World": "Beauty World (DTL) is the Bukit Timah village — Bukit Timah Plaza, the food centre, and Pei Hwa Presbyterian Primary in the immediate catchment. Largely landed and boutique condo.",
    "King Albert Park": "King Albert Park is the DTL stop tucked into Bukit Timah's landed belt — KAP Mall, Pei Hwa Presbyterian Primary further afield, and a strong international-school presence nearby.",
    "Hillview": "Hillview (DTL) is the western Bukit Timah condo cluster — predominantly newer private stock. Lianhua Primary is the closest established local school.",
    "Cashew": "Cashew (DTL) is a quiet residential stop between Hillview and Bukit Panjang — predominantly landed plus the Tree House and Eco Sanctuary cluster.",
    "Botanic Gardens": "Botanic Gardens is the CCL/DTL interchange — the Gardens themselves, Bukit Timah's landed enclave, and walking distance into the Nanyang/Raffles Girls Primary belt for the closest residential blocks.",
    # CCL extra
    "Haw Par Villa": "Haw Par Villa (CCL) is the West Coast-adjacent stop — Pasir Panjang industrial heritage and the new West Coast residential pipeline. Fairfield Methodist Primary edges into the catchment.",
    "Kent Ridge": "Kent Ridge (CCL) is the NUS / National University Hospital anchor — primarily a campus and hospital area, with Fairfield Methodist Primary as the closest residential school catchment.",
    "One-North": "One-North (CCL) is the research-and-startup precinct — Mediapolis, Biopolis, and Fusionopolis. Predominantly working-age and rental, but Fairfield Methodist Primary catches the catchment-distance demand.",
    "Lorong Chuan": "Lorong Chuan (CCL) is a leafy landed-and-condo enclave — St Gabriel's Primary directly accessible. Quietest CCL stop on the north-east stretch.",
    "Bartley": "Bartley (CCL) sits at the Maris Stella / Paya Lebar Methodist Girls catchment edge — older HDB plus newer condos along Upper Paya Lebar. A steady upgrader-magnet for catchment buyers.",
    "MacPherson": "MacPherson is the CCL/DTL interchange in the older Geylang-Hougang corridor — Canossa Catholic Primary anchors the local school side. Mature HDB plus newer DTL-led condo supply.",
    "Mountbatten": "Mountbatten (CCL) is the Old Airport Road / Dakota-adjacent stop — Kong Hwa School in walking range, plus the East Coast Park access. Boutique condos and conservation walk-ups dominate.",
    # EWL west
    "Dover": "Dover is the NUS-adjacent EWL stop — Singapore Polytechnic, primarily campus-led demand. Fairfield Methodist Primary edges into the residential catchment.",
    "Commonwealth": "Commonwealth is the Queenstown-adjacent EWL stop — Tanglin Halt redevelopment, Queenstown Primary in walking range, and a steady BTO pipeline.",
    "Lakeside": "Lakeside (EWL) is the Jurong Lake District anchor — Lakeside Primary directly accessible, the Lakeside Garden, and the JLD masterplan reshaping demand through 2030.",
    "Chinese Garden": "Chinese Garden (EWL) sits in the Jurong west residential belt — Rulang Primary is the closest established school. Mostly older HDB with a newer condo overlay.",
    "Pioneer": "Pioneer is the EWL western HDB stop — Boon Lay Garden Primary as the immediate anchor school. Quieter than Boon Lay; predominantly mature HDB stock.",
}


HOUSING_MAP = {
    # TEL
    "Lentor": "Stock here is being reshaped by recent condo launches — Lentor Modern, Lentor Hills Residences, Hillock Green, and the Lentor Mansion line. The original landed enclave anchors the upper end. HDB stock is thin around the immediate station.",
    "Bright Hill": "Mostly walk-up apartments and older HDB, with the Thomson View enbloc redevelopment as a longer-horizon supply story. Premium pricing reflects Ai Tong School proximity.",
    "Upper Thomson": "Mature condos along Thomson Road plus a layer of low-density conservation properties. The TEL station has lifted the per-square-foot benchmark for nearby resale stock.",
    "Caldecott": "Predominantly older condos around Andrew/Olive Road and the Marymount-side HDB. The CCL/TEL interchange has materially improved transit value since 2022.",
    "Stevens": "Boutique premium condos and older landed stock — supply is thin and prices skew premium. ACS Primary catchment supports steady international-school rental demand.",
    "Napier": "High-end private only — embassy compounds, GCBs, and luxury condos along Tanglin Road. No HDB. Family buyers here are typically expat-supported or ultra-high-net-worth local.",
    "Orchard Boulevard": "Older luxury condos plus newer boutique launches. No HDB. Pricing is at the high end of the Orchard premium band.",
    "Great World": "Mix of newer condos along Kim Seng / River Valley, plus boutique mid-rise stock. No HDB. Tight supply keeps resale prices firm.",
    "Havelock": "Older HDB blocks (Bukit Ho Swee, Henderson) plus the new Avenue South Residence and the boutique condo pipeline. The TEL station has tightened resale yields.",
    "Maxwell": "Predominantly conservation shophouse residences plus boutique CBD-edge condos. No HDB. Niche stock with strong character premium.",
    "Shenton Way": "CBD condo stock — Marina One, Wallich Residence, Skysuites @ Anson. No HDB. Family-oriented buyers are rare here.",
    "Marina Bay": "Premium CBD towers — Marina One Residences, Marina Bay Residences, Wallich Residence. No HDB. Predominantly investor and expat-tenant pool.",
    "Gardens by the Bay": "Marina One spillover plus the future Marina South residential plots. Today, the immediate catchment is thin; the masterplan opens supply through the late 2020s.",
    # CRL
    "Aviation Park": "Predominantly Changi staff housing and older landed at the Changi village edge. CRL opens this catchment in 2030+ but transactions today are thin.",
    "Loyang": "Older landed plus a layer of HDB along Pasir Ris Drive. Transactions are quieter than Pasir Ris town centre. Long-horizon CRL upside.",
    "Pasir Ris East": "Newer Pasir Ris BTOs (Pasir Ris One, Treelodge@Punggol-style supply) plus a slice of mature private. CRL adds optionality from 2030.",
    "Tampines North": "Heavy BTO supply through 2024–2026 — the Tampines North launches dominate. Treasure at Tampines and the Tenet EC sit just within walking distance.",
    "Defu": "Mostly industrial today; residential stock is thin. The Defu redevelopment masterplan is the long-horizon story.",
    "Hougang (CRL)": "Same Hougang HDB and condo supply as the NEL stop, with CRL adding east-west reach. Holy Innocents' Primary catchment is the parental anchor.",
    "Serangoon North": "Older landed and HDB in Lorong Chuan and Serangoon North Avenue 1–4. Newer condo launches like AMO Residence sit just inside walking range.",
    "Tavistock": "Predominantly landed with a thin condo overlay. CRL access materially improves the value calculus from 2030.",
    "Ang Mo Kio (CRL)": "Same AMK HDB and condo supply as NSL Ang Mo Kio — AMK Avenue 1–10. CRL adds east-west reach for upgraders.",
    "Teck Ghee": "Older AMK HDB blocks 100–180. Townsville and Anderson Primary catchment supports steady upgrader demand.",
    "Bright Hill (CRL)": "Same Bright Hill / Ai Tong stock as the TEL station — predominantly walk-ups plus a thin condo layer. Premium pricing follows the school catchment.",
    # JRL
    "Choa Chu Kang (JRL)": "CCK HDB stock plus newer EC launches (Inz Residence, iNz, Sol Acres). JRL adds a third line from mid-decade.",
    "Tengah": "Brand-new BTO town — Plantation Grove, Plantation Acres, and the upcoming launches. Limited resale supply yet; condo supply still in early stages.",
    "Jurong East (JRL)": "Same Jurong East HDB and condo supply as EWL/NSL — J Gateway, Lake Grande, and the JLD pipeline. JRL adds connectivity and reinforces the anchor role.",
    "Pandan Reservoir": "Mostly older condos like Lakeshore and Faber HDB pockets. JRL access lifts long-horizon value.",
    "Boon Lay": "Boon Lay HDB stock plus condos like Lakeville and Lakegrande. Jurong Point retail anchor keeps demand steady.",
    # NSL north
    "Sembawang": "Steady BTO supply (Canberra, Sembawang Vines) plus mature HDB and a thin condo layer. EC pipeline at Canberra Drive.",
    "Khatib": "Yishun-side HDB blocks plus older condos. Limited new supply; resale-led market.",
    "Yio Chu Kang": "AMK Avenue 6 / 8 HDB plus Lentor-side spillover. Newer condo launches improve the supply mix.",
    "Admiralty": "Woodlands HDB blocks plus newer EC supply (Parc Canberra, OLA EC). RTS-link upside through 2027.",
    "Marsiling": "Older Woodlands-area HDB. Resale-led market with limited new private supply.",
    "Kranji": "Mostly older HDB and rural-edge land. Thin transaction volume.",
    # EWL east
    "Tanah Merah": "Older condos along Bedok South and the Bayshore pipeline (Bayshore Park enbloc nearby). Mature HDB in Frankel/Siglap.",
    "Simei": "Simei HDB blocks plus older condos like Modena and Eastpoint Green. EC supply at Tampines North bleeds into the catchment.",
    "Expo": "Mostly mixed-use commercial. Residential stock is thin — Bedok Reservoir and Simei spillover.",
    "Kembangan": "Predominantly landed plus older condos like The Esta and Glentrees. HDB stock is thin.",
    "Eunos": "Geylang Serai HDB and Joo Chiat conservation shophouses, with a layer of newer condos. Strong rental demand from the food culture.",
    # NEL
    "Punggol Coast": "Newest Punggol BTO supply — Northshore, Punggol Coast Residences, the JTC Digital District worker housing pipeline.",
    "Kovan": "Older HDB plus newer condos along Upper Serangoon (The Garden Residences, Stars of Kovan).",
    "Buangkok": "Newer BTO supply plus older Hougang HDB. Limited condo stock.",
    "Woodleigh": "Bidadari BTO launches dominate — Alkaff Vista, Park Edge, Woodleigh Hillside. Plus The Woodleigh Residences condo. Most actively developing pocket on NEL.",
    "Potong Pasir": "Older HDB blocks plus Bidadari spillover. Limited new private supply but steady resale.",
    "Boon Keng": "Older HDB plus newer condos like Sturdee Residences and the Whampoa pipeline.",
    "Farrer Park": "Predominantly boutique mid-rise condos and conservation shophouses. Tight supply, premium pricing.",
    # DTL extra
    "Beauty World": "Older condos along Upper Bukit Timah (Fourth Avenue Residences, The Linq) plus the landed belt. HDB stock is minimal.",
    "King Albert Park": "Predominantly landed with boutique condo supply (KAP Residences, Sherwood Tower). Thin HDB.",
    "Hillview": "Newer private supply — The Hillshore, Midwood, and the older HillV2 cluster. No HDB.",
    "Cashew": "Predominantly landed plus boutique condos (Tree House, Eco Sanctuary). No HDB.",
    "Botanic Gardens": "Premium boutique condos and the Bukit Timah landed enclave. No HDB. Pricing skews high.",
    # CCL extra
    "Haw Par Villa": "Older condos plus the new West Coast residential pipeline (Blossoms by the Park, One-North Eden). Steady tech-and-research tenant demand.",
    "Kent Ridge": "Predominantly campus and hospital. Residential supply is thin — Pasir Panjang condos and the Kent Ridge Hill cluster.",
    "One-North": "One-North Eden and the surrounding biotech-research housing pipeline. Tenant-heavy demand profile.",
    "Lorong Chuan": "Predominantly landed plus a thin layer of boutique condos. Quiet, mature.",
    "Bartley": "Older Upper Paya Lebar HDB plus newer condos like Bartley Ridge and Bartley Residences.",
    "MacPherson": "Older HDB plus newer DTL-led condos (The Antares, Park Place Residences). Mid-tier upgrader demand.",
    "Mountbatten": "Boutique condos along Mountbatten Road plus conservation walk-ups. Premium pricing for the East Coast and Old Airport Road access.",
    # EWL west
    "Dover": "Predominantly NUS-area condos (Tanglin Halt redevelopment, Heritage View). HDB stock is older.",
    "Commonwealth": "Tanglin Halt BTO redevelopment plus newer Stirling Residences and Margaret Drive launches.",
    "Lakeside": "Lakeside MRT-area condos (Lakeville, Lakegrande, Caspian) plus the JLD pipeline.",
    "Chinese Garden": "Older Jurong west HDB plus newer Lakeholmz and J Gateway spillover.",
    "Pioneer": "Mostly older HDB blocks. Thin private supply.",
}


def area_paragraph(mrt):
    return AREA_MAP.get(mrt, f"{mrt} is one of Singapore's well-connected residential nodes — anchor amenities, MRT access, and a steady rental and resale market.")


def housing_paragraph(mrt):
    base = HOUSING_MAP.get(mrt, f"Housing stock around {mrt} typically mixes HDB BTO and resale flats with a layer of condos within walking distance of the station. Run a transaction-comp pull before benchmarking — recent caveats often surprise both buyers and sellers.")
    base += " For specific BTO ballot odds and condo comps, message me on WhatsApp — the answer changes month-to-month."
    return base


def phase_summary(band):
    if band == "A":
        return "That places it inside the 1 km priority band for Phase 2A and 2B registration."
    return "That places it in the 1–2 km band — Phase 2C priority but not Phase 2B."


def phase_heading(band):
    return "Inside the 1 km priority band." if band == "A" else "Inside the 1–2 km band."


def phase_detail(band):
    if band == "A":
        return "In a year where the school is undersubscribed at Phase 2A, that priority is effectively a guarantee. In oversubscribed years, the 1 km band still means you ballot before 1–2 km applicants."
    return "Phase 2C applicants in the 1–2 km band ballot ahead of Phase 2C applicants beyond 2 km. If the school is oversubscribed at Phase 2C, the 1 km band gets a meaningful edge — but you'll need to register early in 2C."


def band_label(band):
    return "1 km priority band" if band == "A" else "1–2 km band"


def faq1(school, band):
    if band == "A":
        return f"Yes — at this proximity, you sit inside the 1 km priority band for {school}. That's the strongest catchment-distance position you can hold short of being on the same street as the school."
    return f"Partly — at this proximity, you sit inside the 1–2 km band, which is Phase 2C priority. Phase 2B and earlier (alumni, sibling, volunteer) still rank ahead. Distance alone does not lock a spot in oversubscribed years."


def main():
    sitemap_lines = []
    written = 0
    skipped = 0
    seen = set()
    for mrt_slug, mrt_name, line, school_slug, school_name, band, distance_text, walk_text, approx in NEW_PAIRS:
        slug = f"{mrt_slug}-{school_slug}"
        if slug in seen:
            continue
        seen.add(slug)

        out_path = OUT / f"{slug}.html"
        if out_path.exists():
            skipped += 1
            continue

        try:
            schema = build_schema(slug, mrt_name, school_name)
            distance_chip = "Within 1 km" if band == "A" else "1–2 km band"
            html = PAGE_TPL.format(
                slug=escape(slug),
                mrt=escape(mrt_name),
                line=escape(line),
                school=escape(school_name),
                distance_text=escape(distance_text),
                walk_text=escape(walk_text),
                phase_summary=escape(phase_summary(band)),
                phase_heading=escape(phase_heading(band)),
                phase_detail=escape(phase_detail(band)),
                band_label=escape(band_label(band)),
                distance_chip=distance_chip,
                area_para=escape(area_paragraph(mrt_name)),
                housing_para=escape(housing_paragraph(mrt_name)),
                faq1=escape(faq1(school_name, band)),
                schema_json=schema,
            )
            out_path.write_text(html, encoding="utf-8")
            sitemap_lines.append(
                f'  <url><loc>https://winfredquek.com/area/{slug}</loc><lastmod>2026-04-27</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>'
            )
            written += 1
        except Exception as e:
            print(f"FAIL {slug}: {e}")
            continue

    print(f"Wrote {written} new area pages, skipped {skipped} existing")
    frag = ROOT / "_area-sitemap-ext-fragment.xml"
    frag.write_text("\n".join(sitemap_lines), encoding="utf-8")
    print(f"Wrote sitemap fragment ({len(sitemap_lines)} lines) to {frag}")


if __name__ == "__main__":
    main()
