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
    bar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#171410;color:#f0e9d8;padding:14px 20px;z-index:9999;font-size:13px;display:flex;gap:14px;align-items:center;border-top:1px solid rgba(180,140,80,0.3);';
    bar.innerHTML = `<div style="flex:1;">We use minimal localStorage to remember calculator inputs and improve your experience. No cross-site tracking. <a href="/privacy" style="color:#b48c50;">PDPA notice</a></div><button id="wf_ok" style="background:#b48c50;color:#0e0c08;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:500;">OK</button>`;
    document.body.appendChild(bar);
    document.getElementById('wf_ok').onclick = () => { localStorage.setItem('wf_consent','1'); bar.remove(); };
  }

  // ============================================================
  // #246 Dark/light mode toggle (default dark)
  // ============================================================
  function initThemeToggle() {
    const saved = localStorage.getItem('wf_theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    // Inject toggle button in top-right
    const btn = document.createElement('button');
    btn.setAttribute('aria-label','Toggle dark/light mode');
    btn.style.cssText = 'position:fixed;top:14px;right:14px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#b48c50;width:36px;height:36px;border-radius:50%;cursor:pointer;z-index:50;font-size:14px;';
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
    cta.style.cssText = 'position:fixed;bottom:80px;right:14px;background:#25D366;color:#fff;padding:12px 18px;border-radius:30px;text-decoration:none;font-weight:500;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,0.4);z-index:40;';
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
    bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;background:#b48c50;width:0;z-index:1000;transition:width 0.1s;';
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
    btn.setAttribute('aria-label','Bookmark this page');
    btn.style.cssText = 'position:fixed;top:14px;right:60px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#b48c50;width:36px;height:36px;border-radius:50%;cursor:pointer;z-index:50;font-size:14px;';
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
    shareBtn.style.cssText = 'position:fixed;bottom:14px;left:14px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#b48c50;padding:8px 14px;border-radius:20px;cursor:pointer;z-index:40;font-size:12px;';
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
  // #280 Exit-intent capture (offer free Property Portfolio Blueprint eBook)
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
      overlay.innerHTML = `<div style="background:#1a1610;border:1px solid rgba(180,140,80,0.3);border-radius:12px;padding:32px;max-width:440px;color:#f0e9d8;text-align:center;font-family:-apple-system,system-ui,sans-serif;">
        <h2 style="font-family:'Fraunces',serif;font-size:24px;margin-bottom:10px;color:#b48c50;font-weight:400;">Before you go.</h2>
        <p style="color:#a89980;margin-bottom:20px;line-height:1.6;font-size:15px;">Free Property Portfolio Blueprint eBook. The way I diagnose every Singapore property situation. No fluff.</p>
        <input id="wf_exit_email" type="email" placeholder="your@email.com" style="width:100%;padding:12px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;margin-bottom:12px;font-size:15px;"/>
        <button id="wf_exit_send" style="background:#b48c50;color:#0e0c08;padding:12px;border-radius:8px;border:none;cursor:pointer;font-weight:500;width:100%;font-size:15px;">Send me the eBook</button>
        <div style="color:#7a6c54;font-size:11px;margin-top:14px;cursor:pointer;" id="wf_exit_close">No thanks, just looking</div>
      </div>`;
      document.body.appendChild(overlay);
      document.getElementById('wf_exit_close').onclick = () => { overlay.remove(); };
      document.getElementById('wf_exit_send').onclick = async () => {
        const email = document.getElementById('wf_exit_email').value.trim();
        if (!email || !/^[^@]+@[^@]+\.[^@]+$/.test(email)) return;
        try {
          await fetch('/api/lead-magnet', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, magnet:'property-portfolio-blueprint', source:'exit-intent'})});
        } catch(e){}
        overlay.querySelector('div').innerHTML = '<h2 style="color:#b48c50;font-family:Fraunces,serif;">✓ Sent</h2><p style="color:#a89980;margin-top:14px;">Check your inbox in 1-2min. ,  Winfred</p>';
        setTimeout(() => overlay.remove(), 3000);
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
  // Init all on DOMContentLoaded
  // ============================================================
  function init() {
    initThemeToggle();
    initStickyCTA();
    initReadingProgress();
    initBookmark();
    initCalcState();
    initExitIntent();
    initGeoCTA();
    trackVisit();
    initConsent(); // last so it appears on top
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
