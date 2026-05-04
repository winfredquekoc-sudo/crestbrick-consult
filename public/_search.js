/* #248 Site search — simple JS over a static index.
 * Loads /search-index.json (built nightly by sitemap-regen) + filters on input.
 * Inject in /search.html or as a header search bar.
 */
(function(){
  'use strict';
  let INDEX = [];
  let loaded = false;

  function ensureUI() {
    if (document.getElementById('wf-search-overlay')) return;
    const html = `
      <div id="wf-search-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9990;padding:60px 20px;">
        <div style="max-width:560px;margin:0 auto;background:#1a1610;border:1px solid rgba(180,140,80,0.3);border-radius:12px;padding:24px;">
          <input id="wf-search-input" placeholder="Search articles, tools, glossary..." style="width:100%;padding:12px;background:#0e0c08;border:1px solid rgba(180,140,80,0.3);border-radius:6px;color:#f0e9d8;font-size:16px;"/>
          <div id="wf-search-results" style="margin-top:14px;max-height:60vh;overflow-y:auto;"></div>
          <div style="margin-top:14px;text-align:right;"><button id="wf-search-close" style="background:transparent;border:1px solid rgba(180,140,80,0.3);color:#a89980;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px;">Close (Esc)</button></div>
        </div>
      </div>`;
    document.body.insertAdjacentHTML('beforeend', html);

    const overlay = document.getElementById('wf-search-overlay');
    const input = document.getElementById('wf-search-input');
    const results = document.getElementById('wf-search-results');
    const close = () => { overlay.style.display='none'; input.value=''; results.innerHTML=''; };
    document.getElementById('wf-search-close').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && overlay.style.display !== 'none') close(); });
    input.oninput = () => {
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) { results.innerHTML=''; return; }
      const matches = INDEX.filter(item => {
        return (item.title || '').toLowerCase().includes(q) ||
               (item.desc || '').toLowerCase().includes(q) ||
               (item.url || '').toLowerCase().includes(q);
      }).slice(0, 12);
      results.innerHTML = matches.length
        ? matches.map(m => `<a href="${m.url}" style="display:block;padding:12px;border-bottom:1px solid rgba(180,140,80,0.1);text-decoration:none;color:#f0e9d8;"><div style="color:#b48c50;font-size:11px;letter-spacing:0.05em;text-transform:uppercase;">${m.section || 'page'}</div><div style="font-weight:500;margin:2px 0;">${m.title}</div><div style="color:#7a6c54;font-size:13px;">${m.desc || m.url}</div></a>`).join('')
        : '<div style="padding:20px;text-align:center;color:#7a6c54;font-size:13px;">No matches. Try different keywords or <a href="https://wa.me/6581618149" style="color:#b48c50;">WA me directly</a>.</div>';
    };
  }

  async function loadIndex() {
    if (loaded) return;
    try {
      const r = await fetch('/search-index.json');
      INDEX = await r.json();
      loaded = true;
    } catch(e) {}
  }

  function open() {
    ensureUI();
    loadIndex();
    document.getElementById('wf-search-overlay').style.display='block';
    setTimeout(() => document.getElementById('wf-search-input').focus(), 50);
  }

  // Bind to Cmd/Ctrl+K + any element with [data-search-trigger]
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); open(); }
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-search-trigger]')) { e.preventDefault(); open(); }
  });

  // Optional floating search button
  if (!document.querySelector('[data-search-trigger]')) {
    const btn = document.createElement('button');
    btn.setAttribute('data-search-trigger','');
    btn.setAttribute('aria-label','Search site');
    btn.style.cssText = 'position:fixed;top:14px;right:108px;background:#1a1610;border:1px solid rgba(180,140,80,0.3);color:#b48c50;width:36px;height:36px;border-radius:50%;cursor:pointer;z-index:50;font-size:14px;';
    btn.textContent = '🔍';
    document.body.appendChild(btn);
  }
})();
