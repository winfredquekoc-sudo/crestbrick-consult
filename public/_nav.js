/* Global navigation injector. Adds consistent header + footer across all pages
 * if they don't already have one. Idempotent ,  skips pages that have wf-nav class.
 */
(function(){
  'use strict';
  if (document.querySelector('.wf-nav')) return;

  // Skip on the homepage (it has its own custom hero)
  if (window.location.pathname === '/' || window.location.pathname === '/index.html') return;

  const NAV = `
    <nav class="wf-nav" style="position:sticky;top:0;background:rgba(14,12,8,0.92);backdrop-filter:blur(8px);border-bottom:1px solid rgba(180,140,80,0.15);padding:12px 16px;z-index:100;font-size:13px;display:flex;gap:18px;align-items:center;flex-wrap:wrap;">
      <a href="/" style="color:#b48c50;text-decoration:none;font-family:'Fraunces',serif;font-size:16px;letter-spacing:0.05em;font-weight:500;">Winfred Quek</a>
      <span style="flex:1;"></span>
      <a href="/tools" style="color:#a89980;text-decoration:none;">Tools</a>
      <a href="/insights" style="color:#a89980;text-decoration:none;">Insights</a>
      <a href="/services/property-portfolio-analysis" style="color:#a89980;text-decoration:none;">Services</a>
      <a href="/about" style="color:#a89980;text-decoration:none;">About</a>
      <a href="https://wa.me/6581618149" style="background:#25D366;color:#fff;padding:6px 14px;border-radius:6px;text-decoration:none;font-weight:500;font-size:12px;">WhatsApp</a>
    </nav>`;
  document.body.insertAdjacentHTML('afterbegin', NAV);

  // Footer
  const FOOTER = `
    <footer style="margin-top:60px;padding:32px 16px 24px;border-top:1px solid rgba(180,140,80,0.15);text-align:center;color:#7a6c54;font-size:12px;line-height:1.7;">
      <div style="margin-bottom:12px;">
        <a href="/tools" style="color:#a89980;text-decoration:none;margin:0 10px;">Tools</a>
        <a href="/insights" style="color:#a89980;text-decoration:none;margin:0 10px;">Insights</a>
        <a href="/glossary" style="color:#a89980;text-decoration:none;margin:0 10px;">Glossary</a>
        <a href="/resources" style="color:#a89980;text-decoration:none;margin:0 10px;">Resources</a>
        <a href="/about" style="color:#a89980;text-decoration:none;margin:0 10px;">About</a>
        <a href="/privacy" style="color:#a89980;text-decoration:none;margin:0 10px;">Privacy</a>
        <a href="/unsubscribe" style="color:#a89980;text-decoration:none;margin:0 10px;">Unsubscribe</a>
      </div>
      <div>Winfred Quek · CEA Salesperson Reg. R073319H · Crestbrick Pte Ltd</div>
      <div style="margin-top:6px;">© 2026 · winfredquek.com</div>
    </footer>`;
  document.body.insertAdjacentHTML('beforeend', FOOTER);
})();
