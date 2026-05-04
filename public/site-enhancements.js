// site-enhancements.js ,  bundles I81 (sticky filter), I83 (lazy-load), I85 (WA click tracking).
// Include once in <head> of every page: <script defer src="/site-enhancements.js"></script>

(function () {
  'use strict';

  // ── I83: lazy-load images that aren't already marked ───────────────────────
  function lazyLoadImages() {
    document.querySelectorAll('img:not([loading])').forEach(function (img) {
      // Skip above-the-fold hero/logo images
      var rect = img.getBoundingClientRect();
      if (rect.top < window.innerHeight * 0.8) return;
      img.setAttribute('loading', 'lazy');
      img.setAttribute('decoding', 'async');
    });
  }

  // ── I81: sticky filter bar on listings.html ────────────────────────────────
  function stickyFilterBar() {
    var filterRow = document.querySelector('[data-filter-bar]')
      || document.querySelector('.filter-chips')
      || document.querySelector('#listing-filters');
    if (!filterRow) return;
    // Wrap or style for sticky behavior
    var st = filterRow.style;
    st.position = 'sticky';
    st.top = '64px';
    st.zIndex = '30';
    st.background = 'var(--bg)';
    st.padding = '0.75rem 0';
    st.borderBottom = '1px solid var(--rule)';
    st.transition = 'box-shadow 0.2s';
    var lastY = 0;
    window.addEventListener('scroll', function () {
      var y = window.scrollY;
      filterRow.style.boxShadow = y > 200 ? '0 2px 8px rgba(0,0,0,0.06)' : 'none';
      lastY = y;
    }, { passive: true });
  }

  // ── I85: WhatsApp click tracking ───────────────────────────────────────────
  // Pings /api/track-click with the source page + button label.
  // Failsafe: never blocks the navigation, never throws.
  function trackWhatsAppClicks() {
    document.querySelectorAll('a[href*="wa.me"], a[href*="api.whatsapp.com"], a[href*="wa.link"]').forEach(function (a) {
      a.addEventListener('click', function () {
        var label = (a.textContent || a.getAttribute('aria-label') || 'wa-link').trim().slice(0, 80);
        var src = location.pathname + location.search;
        try {
          var beacon = JSON.stringify({ event: 'wa_click', label: label, src: src, ts: Date.now() });
          if (navigator.sendBeacon) {
            navigator.sendBeacon('/api/track-click', beacon);
          } else {
            fetch('/api/track-click', { method: 'POST', body: beacon, keepalive: true }).catch(function () {});
          }
        } catch (e) { /* noop */ }
      }, { passive: true });
    });
  }

  function init() {
    lazyLoadImages();
    stickyFilterBar();
    trackWhatsAppClicks();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
