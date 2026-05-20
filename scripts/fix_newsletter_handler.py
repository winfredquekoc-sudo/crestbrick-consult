#!/usr/bin/env python3
"""Replace the silent-failure newsletter handler in insight articles.

Old handler: on success just changes the button text; on any error `catch(_){}`
swallows it silently and the subscriber is lost without knowing.

New handler: shows a visible inline success confirmation + fires a GA4
newsletter_signup event; on failure shows a visible error message telling the
user to email Winfred directly. Idempotent — only files with the old string change.
"""
import pathlib

INSIGHTS = pathlib.Path(__file__).parent.parent / "public" / "insights"

OLD = ("if(r.ok){b.textContent='Subscribed ✓';return false;}}"
       "catch(_){}b.disabled=false;b.textContent='Subscribe';return false;")

NEW = ("if(r.ok){b.textContent='Subscribed ✓';"
       "var sm=document.createElement('div');"
       "sm.style.cssText='width:100%;font-size:.85rem;margin-top:.6rem;"
       "text-align:center;color:#7bbf8f;';"
       "sm.textContent='Subscribed. You are on the list.';f.appendChild(sm);"
       "try{window.gtag&&window.gtag('event','newsletter_signup');}catch(_){}"
       "return false;}throw 0;}"
       "catch(_){b.disabled=false;b.textContent='Subscribe';"
       "var em2=f.querySelector('.nl-err');"
       "if(!em2){em2=document.createElement('div');em2.className='nl-err';"
       "em2.style.cssText='width:100%;font-size:.85rem;margin-top:.6rem;"
       "text-align:center;color:#e0a96d;';f.appendChild(em2);}"
       "em2.textContent='Could not subscribe. Please email "
       "winfredquekoc@gmail.com directly.';}return false;")

changed, skipped = 0, 0
for f in sorted(INSIGHTS.glob("*.html")):
    text = f.read_text(encoding="utf-8")
    if OLD in text:
        f.write_text(text.replace(OLD, NEW), encoding="utf-8")
        changed += 1
    elif "nl-err" in text:
        skipped += 1  # already fixed
    else:
        pass

print(f"newsletter handler: {changed} files updated, {skipped} already fixed")
