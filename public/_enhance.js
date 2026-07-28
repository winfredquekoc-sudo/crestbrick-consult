/* Global enhancement bundle. Single script attached to all pages.
 * Covers: #246 dark/light toggle, #250 sticky CTA, #251 reading progress,
 * #256 bookmark/save, #257 calc state persistence, #258 calc URL state,
 * #259 cookie consent, #280 exit-intent, #289 geo-targeted CTA, #290 retargeting pixel,
 * #287 tool email-gate, #332 visitor tracking (privacy-respecting).
 *
 * Inject in every page <head>:
 *   <script defer src="/_enhance.js"></script>
 */
(function(){
  'use strict';

  // ============================================================
  // #259 Cookie consent ,  minimal, PDPA-aligned
  // ============================================================
  function initConsent() {
    if (localStorage.getItem('wf_consent')) return;
    const bar = document.createElement('div');
    bar.id = 'wf-consent-bar';
    bar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#171410;color:#f0e9d8;padding:14px 20px;z-index:10001;font-size:13px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;border-top:1px solid rgba(198,163,106,0.3);';
    bar.innerHTML = `<div style="flex:1;min-width:200px;">We use essential localStorage for calculator inputs, and Google Analytics to understand site usage. Analytics runs only if you accept. <a href="/privacy" style="color:#c6a36a;">PDPA notice</a></div><div style="display:flex;gap:8px;"><button id="wf_decline" style="background:transparent;color:#a89980;border:1px solid rgba(198,163,106,0.4);padding:8px 14px;border-radius:6px;cursor:pointer;font-weight:500;">Decline</button><button id="wf_ok" style="background:#c6a36a;color:#0e0c08;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:500;">Accept</button></div>`;
    document.body.appendChild(bar);

    // #a11y-cookie-overlap: the fixed consent bar covers the bottom-left
    // theme-toggle/bookmark buttons on mobile, push them up by the bar's
    // measured height while it's visible.
    function adjustFloatingButtons(extraPx) {
      var themeBtn = document.getElementById('wf-theme-toggle');
      var bookmarkBtn = document.getElementById('wf-bookmark-btn');
      if (themeBtn) themeBtn.style.bottom = (16 + extraPx) + 'px';
      if (bookmarkBtn) bookmarkBtn.style.bottom = (64 + extraPx) + 'px';
    }
    adjustFloatingButtons(bar.offsetHeight);

    function dismissBar() {
      bar.remove();
      adjustFloatingButtons(0);
    }
    // PDPA: analytics is OFF by default and only starts on explicit Accept.
    document.getElementById('wf_ok').onclick = () => { localStorage.setItem('wf_consent','1'); dismissBar(); initGA4(); };
    document.getElementById('wf_decline').onclick = () => { localStorage.setItem('wf_consent','declined'); dismissBar(); };
  }

  // ============================================================
  // #246 Dark/light mode toggle (default dark)
  // ============================================================
  function initThemeToggle() {
    const saved = localStorage.getItem('wf_theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    // Inject toggle button in bottom-left (clears the top nav CTAs)
    const btn = document.createElement('button');
    btn.id = 'wf-theme-toggle';
    btn.setAttribute('aria-label','Toggle dark/light mode');
    btn.style.cssText = 'position:fixed;bottom:16px;left:16px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#c6a36a;width:44px;height:44px;border-radius:50%;cursor:pointer;z-index:50;font-size:14px;';
    btn.textContent = saved === 'dark' ? '☀' : '☾';
    btn.onclick = () => {
      const cur = document.documentElement.getAttribute('data-theme');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('wf_theme', next);
      btn.textContent = next === 'dark' ? '☀' : '☾';
    };
    document.body.appendChild(btn);
  }

  // ============================================================
  // #250 Sticky CTA bar on long pages (>1500px)
  // ============================================================
  function initStickyCTA() {
    if (window.location.pathname === '/' || document.body.offsetHeight < 1500) return;
    const cta = document.createElement('a');
    cta.href = 'https://wa.me/6581618149';
    cta.target = '_blank';
    cta.rel = 'noopener';
    cta.style.cssText = 'position:fixed;bottom:80px;right:14px;background:#25D366;color:#fff;padding:12px 18px;border-radius:30px;text-decoration:none;font-weight:500;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,0.4);z-index:10002;';
    cta.textContent = '💬 WhatsApp Winfred';
    cta.addEventListener('click', () => {
      try { window.dispatchEvent(new CustomEvent('wf_wa_click', {detail:{source:'sticky'}})); } catch(e){}
    });
    document.body.appendChild(cta);
  }

  // ============================================================
  // #251 Reading progress indicator on long-form pages
  // ============================================================
  function initReadingProgress() {
    if (!window.location.pathname.startsWith('/insights') && !window.location.pathname.startsWith('/blog')) return;
    const bar = document.createElement('div');
    bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;background:#c6a36a;width:0;z-index:1000;transition:width 0.1s;';
    document.body.appendChild(bar);
    window.addEventListener('scroll', () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      const pct = max > 0 ? (h.scrollTop / max) * 100 : 0;
      bar.style.width = pct + '%';
    }, {passive:true});
  }

  // ============================================================
  // #256 Bookmark / save-for-later
  // ============================================================
  function initBookmark() {
    if (!window.location.pathname.startsWith('/insights') && !window.location.pathname.startsWith('/tools')) return;
    const btn = document.createElement('button');
    btn.id = 'wf-bookmark-btn';
    btn.setAttribute('aria-label','Bookmark this page');
    btn.style.cssText = 'position:fixed;bottom:64px;left:16px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#c6a36a;width:44px;height:44px;border-radius:50%;cursor:pointer;z-index:50;font-size:14px;';
    const KEY = 'wf_bookmarks';
    const saved = JSON.parse(localStorage.getItem(KEY) || '[]');
    const isSaved = () => saved.some(b => b.url === window.location.pathname);
    btn.textContent = isSaved() ? '★' : '☆';
    btn.onclick = () => {
      const url = window.location.pathname;
      const idx = saved.findIndex(b => b.url === url);
      if (idx >= 0) { saved.splice(idx,1); btn.textContent = '☆'; }
      else { saved.push({url, title: document.title, t: Date.now()}); btn.textContent = '★'; }
      localStorage.setItem(KEY, JSON.stringify(saved));
    };
    document.body.appendChild(btn);
  }

  // ============================================================
  // #257 Calculator state persistence + #258 URL share state
  // ============================================================
  function initCalcState() {
    if (!window.location.pathname.startsWith('/tools')) return;
    const KEY = 'wf_calc_' + window.location.pathname;
    const inputs = document.querySelectorAll('input[type="number"], input[type="date"], select');
    if (inputs.length === 0) return;

    // Restore from URL params first, then localStorage
    const params = new URLSearchParams(window.location.search);
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
    inputs.forEach(el => {
      const id = el.id;
      if (!id) return;
      if (params.has(id)) el.value = params.get(id);
      else if (saved[id] !== undefined) el.value = saved[id];
    });

    // Save on change
    const save = () => {
      const state = {};
      inputs.forEach(el => { if (el.id) state[el.id] = el.value; });
      localStorage.setItem(KEY, JSON.stringify(state));
    };
    inputs.forEach(el => el.addEventListener('change', save));

    // Add "share this scenario" button
    const shareBtn = document.createElement('button');
    shareBtn.textContent = '🔗 Share this scenario';
    shareBtn.style.cssText = 'position:fixed;bottom:112px;left:14px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#c6a36a;padding:8px 14px;border-radius:20px;cursor:pointer;z-index:10002;font-size:12px;';
    shareBtn.onclick = async () => {
      const url = new URL(window.location.href);
      url.search = '';
      inputs.forEach(el => { if (el.id && el.value) url.searchParams.set(el.id, el.value); });
      try {
        await navigator.clipboard.writeText(url.toString());
        shareBtn.textContent = '✓ Link copied';
        setTimeout(() => shareBtn.textContent = '🔗 Share this scenario', 2000);
      } catch(e) {
        prompt('Copy this URL:', url.toString());
      }
    };
    document.body.appendChild(shareBtn);
  }

  // ============================================================
  // #280 Exit-intent capture (offer free Move Framework eBook)
  // ============================================================
  function initExitIntent() {
    if (sessionStorage.getItem('wf_exit_shown')) return;
    if (localStorage.getItem('wf_exit_dismissed_perm')) return;
    let triggered = false;
    document.addEventListener('mouseleave', (e) => {
      if (e.clientY > 0 || triggered) return;
      triggered = true;
      sessionStorage.setItem('wf_exit_shown','1');
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9998;padding:20px;';
      overlay.innerHTML = `<div role="dialog" aria-modal="true" aria-label="Free Property Portfolio Blueprint eBook" style="background:#1a1610;border:1px solid rgba(180,140,80,0.3);border-radius:12px;padding:32px;max-width:440px;color:#f0e9d8;text-align:center;font-family:-apple-system,system-ui,sans-serif;">
        <h2 style="font-family:'Fraunces',serif;font-size:24px;margin-bottom:10px;color:#c6a36a;font-weight:400;">Before you go.</h2>
        <p style="color:#a89980;margin-bottom:20px;line-height:1.6;font-size:15px;">Free Property Portfolio Blueprint eBook. The way I diagnose every Singapore property situation. No fluff.</p>
        <input id="wf_exit_email" type="email" placeholder="your@email.com" style="width:100%;padding:12px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;margin-bottom:12px;font-size:15px;"/>
        <button id="wf_exit_send" style="background:#c6a36a;color:#0e0c08;padding:12px;border-radius:8px;border:none;cursor:pointer;font-weight:500;width:100%;font-size:15px;">Send me the eBook</button>
        <div style="color:#a89980;font-size:11px;margin-top:14px;cursor:pointer;" id="wf_exit_close">No thanks, just looking</div>
      </div>`;
      document.body.appendChild(overlay);

      var dialog = overlay.querySelector('[role="dialog"]');
      var emailInput = document.getElementById('wf_exit_email');
      emailInput.focus();

      function getFocusable() {
        return Array.prototype.slice.call(
          dialog.querySelectorAll('input, button, [href], select, textarea, [tabindex]:not([tabindex="-1"])')
        ).filter(function (el) { return !el.disabled && el.offsetParent !== null; });
      }

      function onKeydown(e) {
        if (e.key === 'Escape') {
          e.preventDefault();
          closeModal();
          return;
        }
        if (e.key === 'Tab') {
          var focusable = getFocusable();
          if (!focusable.length) return;
          var first = focusable[0], last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault(); last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
          }
        }
      }
      document.addEventListener('keydown', onKeydown);

      function closeModal() {
        document.removeEventListener('keydown', onKeydown);
        overlay.remove();
        document.body.focus();
      }

      document.getElementById('wf_exit_close').onclick = closeModal;
      document.getElementById('wf_exit_send').onclick = async () => {
        const email = document.getElementById('wf_exit_email').value.trim();
        if (!email || !/^[^@]+@[^@]+\.[^@]+$/.test(email)) return;
        try {
          await fetch('/api/lead-magnet', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, magnet:'property-portfolio-blueprint', source:'exit-intent'})});
        } catch(e){}
        dialog.innerHTML = '<h2 style="color:#c6a36a;font-family:Fraunces,serif;">✓ Sent</h2><p style="color:#a89980;margin-top:14px;">Check your inbox in 1-2min. ,  Winfred</p>';
        setTimeout(() => closeModal(), 3000);
      };
    });
  }

  // ============================================================
  // #289 Geo-targeted CTA (basic IP-based via Vercel headers)
  // ============================================================
  function initGeoCTA() {
    fetch('/api/geo').then(r => r.json()).then(d => {
      if (!d.country) return;
      const isSG = d.country === 'SG';
      document.body.dataset.geo = isSG ? 'sg' : 'foreign';
      // CSS can use [data-geo="foreign"] selectors to swap CTAs
    }).catch(()=>{});
  }

  // ============================================================
  // #332 Visitor tracking (privacy-respecting, no IP/cookie)
  // ============================================================
  function trackVisit() {
    if (!localStorage.getItem('wf_consent')) return;
    try {
      const beacon = JSON.stringify({
        path: window.location.pathname,
        ref: document.referrer.slice(0, 200),
        ts: Date.now()
      });
      if (navigator.sendBeacon) navigator.sendBeacon('/api/track-visit', new Blob([beacon], {type:'application/json'}));
    } catch(e){}
  }


  // ============================================================
  // RSS autodiscovery link (injected on all pages)
  // ============================================================
  function injectRSSLink() {
    if (document.querySelector('link[type="application/rss+xml"]')) return;
    var l = document.createElement('link');
    l.rel = 'alternate';
    l.type = 'application/rss+xml';
    l.title = 'Winfred Quek — Singapore Property Insights';
    l.href = '/feed.xml';
    document.head.appendChild(l);
  }
  // ============================================================
  // Init all on DOMContentLoaded
  // ============================================================
  // ============================================================
  // #287 Inline tool-result capture (soft, optional). Exit-intent
  // (#280) never fires on touch devices, so this is the mobile-safe
  // capture path on calculator pages. Delivers the real Portfolio
  // Blueprint eBook via the existing /api/lead-magnet rail.
  // ============================================================
  function initToolCapture() {
    var p = window.location.pathname;
    if (!(p.indexOf('/tools') === 0 || /calculator/.test(p))) return;
    var target = document.querySelector('#result, .result-panel, #results-panel, #tdsr-result, #sd-result, #absd-result');
    if (!target || target.dataset.wfCapture) return;
    target.dataset.wfCapture = '1';
    var box = document.createElement('div');
    box.style.cssText = 'margin-top:16px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);border-radius:10px;padding:16px 18px;';
    box.innerHTML = '<div style="color:#f0e9d8;font-size:14px;font-weight:500;margin-bottom:6px;">📩 Free: the Property Portfolio Blueprint</div>'
      + '<div style="color:#a89980;font-size:12px;margin-bottom:10px;line-height:1.5;">The framework behind these numbers, the way I diagnose any Singapore property situation. Optional, your result stays right here.</div>'
      + '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
      + '<input id="wf_tool_email" type="email" placeholder="your@email.com" aria-label="Email address" style="flex:1;min-width:180px;padding:10px 12px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;font-size:14px;"/>'
      + '<button id="wf_tool_send" style="background:#c6a36a;color:#0e0c08;border:none;padding:10px 18px;border-radius:6px;cursor:pointer;font-weight:500;font-size:14px;">Send it</button>'
      + '</div>';
    target.parentNode.insertBefore(box, target.nextSibling);
    box.querySelector('#wf_tool_send').onclick = async function () {
      var input = box.querySelector('#wf_tool_email');
      var email = input.value.trim();
      if (!email || !/^[^@]+@[^@]+\.[^@]+$/.test(email)) { input.style.borderColor = '#c0392b'; return; }
      try {
        await fetch('/api/lead-magnet', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email, magnet: 'property-portfolio-blueprint', source: 'tool:' + p }) });
      } catch (e) {}
      box.innerHTML = '<div style="color:#c6a36a;font-size:14px;font-weight:500;">✓ On its way. Check your inbox in 1-2 min. — Winfred</div>';
      try { window.gtag && window.gtag('event', 'tool_email_capture', { tool: p }); } catch (e) {}
    };
  }

  // ============================================================
  // Inline newsletter opt-in on articles (insights + blog). Captures
  // the warm reader before the footer, reusing the 'newsletter' magnet.
  // ============================================================
  function initArticleOptin() {
    var p = window.location.pathname;
    if (!(p.indexOf('/insights') === 0 || p.indexOf('/blog') === 0)) return;
    if (p === '/insights' || p === '/insights.html' || p === '/blog') return; // index pages
    var footer = document.querySelector('footer');
    var box = document.createElement('div');
    box.style.cssText = 'max-width:680px;margin:36px auto;background:linear-gradient(135deg,#1a1610,#221c12);border:1px solid rgba(180,140,80,0.3);border-radius:12px;padding:24px;';
    box.innerHTML = '<div style="color:#c6a36a;font-family:Fraunces,Georgia,serif;font-size:20px;margin-bottom:8px;">Get Winfred’s property insights by email</div>'
      + '<div style="color:#a89980;font-size:14px;line-height:1.6;margin-bottom:14px;">Occasional and investor-minded, the same lens I use on my own portfolio. No spam.</div>'
      + '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
      + '<input id="wf_art_email" type="email" placeholder="your@email.com" aria-label="Email address" style="flex:1;min-width:200px;padding:11px 13px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;font-size:14px;"/>'
      + '<button id="wf_art_send" style="background:#c6a36a;color:#0e0c08;border:none;padding:11px 20px;border-radius:6px;cursor:pointer;font-weight:500;font-size:14px;">Subscribe</button>'
      + '</div><div style="color:#7a6c54;font-size:11px;margin-top:10px;">Unsubscribe anytime. PDPA-aligned.</div>';
    if (footer && footer.parentNode) footer.parentNode.insertBefore(box, footer);
    else document.body.appendChild(box);
    box.querySelector('#wf_art_send').onclick = async function () {
      var input = box.querySelector('#wf_art_email');
      var email = input.value.trim();
      if (!email || !/^[^@]+@[^@]+\.[^@]+$/.test(email)) { input.style.borderColor = '#c0392b'; return; }
      try {
        await fetch('/api/lead-magnet', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email, magnet: 'newsletter', source: 'article-inline:' + p }) });
      } catch (e) {}
      box.innerHTML = '<div style="color:#c6a36a;font-family:Fraunces,Georgia,serif;font-size:18px;">✓ You are on the list. — Winfred</div>';
      try { window.gtag && window.gtag('event', 'newsletter_signup', { page: p }); } catch (e) {}
    };
  }

  // ============================================================
  // Skip-to-content link (screen reader / keyboard nav)
  // ============================================================
  function initSkipLink() {
    if (document.querySelector('.wf-skip')) return;
    var main = document.querySelector('main');
    if (main && !main.id) main.id = 'main';
    var skip = document.createElement('a');
    skip.className = 'wf-skip';
    skip.href = '#main';
    skip.textContent = 'Skip to content';
    document.body.insertBefore(skip, document.body.firstChild);
  }

  function init() {
    initSkipLink();
    initThemeToggle();
    initStickyCTA();
    initReadingProgress();
    initBookmark();
    initCalcState();
    initExitIntent();
    initToolCapture();
    initArticleOptin();
    initGeoCTA();
    trackVisit();
    initConsent(); // last so it appears on top
    injectRSSLink();
  }
  // ============================================================
  // GA4 — inject on pages that don't already have it (insight articles)
  // ============================================================
  function initGA4() {
    if (document.querySelector('script[src*="googletagmanager"]')) return;
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=G-T6C46CFKCM';
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function(){ window.dataLayer.push(arguments); };
    window.gtag('js', new Date());
    window.gtag('config', 'G-T6C46CFKCM');
  }

  // PDPA: only load GA4 if the visitor has explicitly accepted analytics.
  function initGA4IfConsented(){ if (localStorage.getItem('wf_consent') === '1') initGA4(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){ init(); initGA4IfConsented(); });
  } else {
    init(); initGA4IfConsented();
  }
})();
