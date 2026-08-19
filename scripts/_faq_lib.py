"""Shared FAQ extraction/serialization core for scripts/faq-schema-parity.py.

Split out purely to keep faq-schema-parity.py under the repo's 500 line cap
(see scripts/_prose_rules.py for the same pattern with content-rulecheck.py).
This module owns: parsing visible FAQ pairs and FAQPage schema out of an
HTML page, and rendering pairs back into JSON/HTML text. The comparison
policy (what counts as drift vs kept vs missing) and all CLI/reporting stay
in faq-schema-parity.py -- this is extraction and serialization only.
"""
import html as H, json, re

TAG = re.compile(r"<[^>]+>")
SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
NOSCRIPT_COMMENT = re.compile(r"<noscript\b[^>]*>.*?</noscript>|<!--.*?-->", re.S | re.I)
LD_BLOCK = re.compile(r'(<script[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)', re.S | re.I)
H2_TAG = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.S | re.I)
H1_TAG = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S | re.I)
ARTICLE_TAG = re.compile(r"<article\b[^>]*>(.*?)</article>", re.S | re.I)
FAQQ_PAIR = re.compile(r'<p\s+class="faq-q"(?:\s[^>]*)?>((?:(?!</p>).)*)</p>\s*<p(?![^>]*class="faq-q")(?:\s[^>]*)?>(.*?)</p>', re.S | re.I)
H3_PAIR = re.compile(r"<h3\b(?:\s[^>]*)?>((?:(?!</h3>).)*)</h3>\s*<p(?:\s[^>]*)?>(.*?)</p>", re.S | re.I)
DETAILS_PAIR = re.compile(r"<details\b(?:\s[^>]*)?>\s*<summary\b(?:\s[^>]*)?>(.*?)</summary>(.*?)</details>", re.S | re.I)
DETAILS_P = re.compile(r"<p(?:\s[^>]*)?>(.*?)</p>", re.S | re.I)

# Insertion anchor for --create-missing: every page in this generation
# carries a fixed "..._enhance.js/_schema.js/_nav.js" script cluster right
# before </head>, and already-schema'd siblings insert their FAQPage block
# immediately after it (marked "<!-- faq-geo -->"). Verified byte-identical
# across all 68 NO-SCHEMA files before relying on it as the sole anchor.
NAV_ANCHOR = re.compile(r'<script defer src="/_nav\.js(?:\?[^"]*)?"></script>(\s*)(?=<link\b)')


def _collapse(s):
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()

def norm_visible(s):
    # Decode entities, drop tags, collapse whitespace -- deliberately minimal,
    # no case/quote folding: a curly vs straight apostrophe is drift, not noise.
    return _collapse(H.unescape(TAG.sub("", s)))

def norm_schema(s):
    return _collapse(str(s))  # json.loads already resolved \uXXXX and JSON escapes


def visible_text_corpus(src):
    # Whole-page text for the removal-safety containment test: deliberately
    # unscoped, since h1s / pre-FAQ h3 blocks / question-form h2s live outside
    # the FAQ section. Callers compare case-insensitively -- "any evidence it
    # survives", not byte parity.
    s = SCRIPT_STYLE.sub(" ", src)
    s = NOSCRIPT_COMMENT.sub(" ", s)
    s = H.unescape(TAG.sub(" ", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def is_faq_heading(attrs, text):
    if re.search(r'id=["\']frequently-asked-questions["\']', attrs, re.I):
        return True
    if re.search(r'id=["\']more-questions["\']', attrs, re.I):
        return True
    # Marks a heading as the FAQ span opener without touching its id -- for
    # pages where the natural anchor for a run of question headings is an
    # existing, differently-purposed heading (an intro "N Steps to..." block,
    # a "Real Example" section) whose id is meaningful on its own and must
    # not be overwritten with a generic one.
    if re.search(r'\bdata-faq-section\b', attrs, re.I):
        return True
    plain = TAG.sub("", text)
    return bool(re.fullmatch(r"\s*Frequently [Aa]sked [Qq]uestions?\s*", plain))

def faq_spans(src):
    """Byte ranges (in `src`) following each FAQ-relevant h2, up to the next h2."""
    h2s = list(H2_TAG.finditer(src))
    spans = []
    for i, m in enumerate(h2s):
        if not is_faq_heading(m.group(1), m.group(2)):
            continue
        start = m.end()
        end = h2s[i + 1].start() if i + 1 < len(h2s) else len(src)
        spans.append((start, end))
    return spans


def page_is_faq_only(src):
    """A few pages ARE the FAQ (h1 ends "... FAQ"), questions under thematic
    h2 sub-sections instead of one "Frequently Asked Questions" heading."""
    m = H1_TAG.search(src)
    if not m:
        return False
    text = H.unescape(TAG.sub("", m.group(1))).strip()
    return bool(re.search(r"\bFAQs?\s*$", text, re.I))


def extract_visible_pairs(src):
    """[(pos, question_raw_html, answer_raw_html), ...] in document order.

    Each answer is the single <p> immediately after its question marker
    (verified: every apparent multi-paragraph answer was really the next
    paragraph bleeding in from a CTA/disclaimer/"Related:" block; none matched
    schema when joined). Details/summary may join multiple <p>s since
    </details> is a hard boundary. A candidate is dropped unless its question
    ends with "?", so a non-question heading followed by a bare <p> can never
    be mistaken for one.
    """
    vis_src = SCRIPT_STYLE.sub(" ", src)
    spans = faq_spans(vis_src)
    if not spans and page_is_faq_only(vis_src):
        am = ARTICLE_TAG.search(vis_src)
        spans = [(am.start(1), am.end(1))] if am else [(0, len(vis_src))]
    pairs = []
    for a, b in spans:
        section = vis_src[a:b]
        for m in FAQQ_PAIR.finditer(section):
            pairs.append((a + m.start(), m.group(1), m.group(2)))
        for m in H3_PAIR.finditer(section):
            pairs.append((a + m.start(), m.group(1), m.group(2)))
        for m in DETAILS_PAIR.finditer(section):
            ans_raw = " ".join(DETAILS_P.findall(m.group(2)))
            pairs.append((a + m.start(), m.group(1), ans_raw))
    pairs = [p for p in pairs if norm_visible(p[1]).endswith("?")]
    pairs.sort(key=lambda t: t[0])
    return pairs


def find_faqpage_node(data):
    if not isinstance(data, dict):
        return None
    if data.get("@type") == "FAQPage":
        return data
    for n in data.get("@graph") or []:
        if isinstance(n, dict) and n.get("@type") == "FAQPage":
            return n
    return None

def extract_schema(src):
    """(pairs, raw_entries, block_info, bad_json_errors, faqpage_block_count).
    raw_entries holds the exact original Question dict objects (same order as
    pairs) so a "kept" entry carries into a rebuild byte-identical, never
    reconstructed from just its name/text. block_info: that script element."""
    pairs, raw_entries, block_info, bad = [], [], None, []
    faqpage_blocks = 0
    for m in LD_BLOCK.finditer(src):
        open_tag, content, close_tag = m.group(1), m.group(2), m.group(3)
        if "FAQPage" not in content:
            continue
        try:
            data = json.loads(content)
        except Exception as e:
            bad.append(str(e))
            continue
        node = find_faqpage_node(data)
        if node is None:
            continue
        faqpage_blocks += 1
        pairs, raw_entries = [], []
        for qa in node.get("mainEntity") or []:
            if not isinstance(qa, dict):
                continue
            q = qa.get("name", "")
            a = (qa.get("acceptedAnswer") or {}).get("text", "")
            pairs.append((q, a))
            raw_entries.append(qa)
        block_info = {"start": m.start(), "end": m.end(), "open_tag": open_tag,
                       "content": content, "close_tag": close_tag, "data": data}
    return pairs, raw_entries, block_info, bad, faqpage_blocks


def rebuild_block(block_info, entries):
    """Render the FAQPage script's new inner content (entries: fresh dicts for
    visible-derived pairs, the exact original object for a kept entry),
    matching the original's indentation/ascii-escaping style. None if no-op."""
    content = block_info["content"]
    ensure_ascii = bool(re.search(r"\\u[0-9a-fA-F]{4}", content))

    full_expand = bool(re.search(r'"acceptedAnswer"\s*:\s*\{\s*\n', content))
    if full_expand:
        node = find_faqpage_node(block_info["data"])
        if node is None:
            return None
        node["mainEntity"] = entries
        new_json = json.dumps(block_info["data"], indent=2, ensure_ascii=ensure_ascii)
        lead = content[:len(content) - len(content.lstrip())]
        trail = content[len(content.rstrip()):]
        new_content = lead + new_json + trail
    else:
        km = re.search(r'"mainEntity"\s*:\s*', content)
        if not km:
            return None
        value_start = km.end()
        try:
            _, value_end = json.JSONDecoder().raw_decode(content, value_start)
        except Exception:
            return None
        old_array = content[value_start:value_end]
        pretty = "\n" in old_array
        cm = re.search(r'"@type"\s*:(\s*)"Question"', old_array)
        spaced = bool(cm and cm.group(1) == " ")
        sep = (", ", ": ") if spaced else (",", ":")
        item_texts = [json.dumps(e, ensure_ascii=ensure_ascii, separators=sep) for e in entries]
        if pretty:
            im = re.match(r"\[\s*\n([ \t]*)", old_array)
            indent_unit = im.group(1) if im else "    "
            cm2 = re.search(r"\n([ \t]*)\]\s*\Z", old_array)
            closing_indent = cm2.group(1) if cm2 else ""
            inner = "\n" + indent_unit + (",\n" + indent_unit).join(item_texts) + "\n" + closing_indent
        else:
            inner = (", " if spaced else ",").join(item_texts)
        new_array = "[" + inner + "]"
        new_content = content[:value_start] + new_array + content[value_end:]

    return None if new_content == content else new_content


def build_entries(visible_pairs, kept, raw_entries):
    entries = [{"@type": "Question", "name": norm_visible(q),
                "acceptedAnswer": {"@type": "Answer", "text": norm_visible(a)}}
               for _, q, a in visible_pairs]
    for k in sorted(kept, key=lambda k: k["orig_index"]):
        entries.append(raw_entries[k["orig_index"]])  # byte-identical, never rewritten
    return entries


def insert_missing_block(src, visible_pairs):
    """For --create-missing: build a FAQPage block from visible_pairs verbatim
    (build_entries with no kept/raw_entries -- there is no existing schema to
    carry over) and splice it in right after the page's trailing _nav.js tag,
    matching the "faq-geo" convention already used by schema'd sibling pages
    of the same generation. Compact JSON, no \\u escapes (never observed in
    this generation's real blocks, so ensure_ascii=False matches convention).

    Returns (new_src, None) or (None, error) if the anchor isn't exactly one
    match -- callers must not guess at a fallback insertion point.
    """
    matches = list(NAV_ANCHOR.finditer(src))
    if len(matches) != 1:
        return None, f"_nav.js anchor found {len(matches)} times (expected exactly 1)"
    entries = build_entries(visible_pairs, [], [])
    obj = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entries}
    json_str = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    json.loads(json_str)  # must be valid JSON before it touches disk
    block = f'<script type="application/ld+json">\n{json_str}\n  </script><!-- faq-geo -->\n  '
    pos = matches[0].end()
    new_src = src[:pos] + block + src[pos:]
    assert new_src[:pos] == src[:pos] and new_src[pos + len(block):] == src[pos:], \
        "bytes outside the inserted block changed"
    return new_src, None
