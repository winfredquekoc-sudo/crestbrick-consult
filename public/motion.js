/* ===========================================================================
   Motion Direction B — Minimal Editorial Luxury (UNIFIED)
   Vanilla JS. No dependencies. Runs once per session.

   - Reveal-on-scroll for .reveal-mask / .reveal-line / .reveal-fade
   - Nav scroll-state: adds .is-scrolled to <body> past 40px
   - Hero sequenced load: staggers .reveal-line + .reveal-fade after image decodes
   - Hero rotator: crossfade stacked headshots every 6s (index.html)
   - Respects prefers-reduced-motion (everything collapses to instant fade)

   Consolidated from former animations.js + motion.js pair on 2026-04-20.
   Dropped (Direction B bans, see /docs/motion-direction.md "Do Not"):
     - [data-parallax] scroll-coupled translate (hero parallax banned)
     - .card-lift 3D tilt (SaaS gimmick)
     - .hero-text-reveal per-character stagger (typewriter banned)
     - .scroll-reveal IntersectionObserver (redundant with .reveal-fade)
   =========================================================================== */
(function () {
  'use strict';

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- 1. Reveal-on-scroll (once per element) ----------
  function initReveal() {
    var targets = document.querySelectorAll('.reveal-mask, .reveal-line, .reveal-fade');
    if (!targets.length) return;

    // Reduced motion: reveal everything instantly with an opacity fade handled by CSS.
    if (reduce) {
      targets.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }

    if (!('IntersectionObserver' in window)) {
      targets.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        // Hero elements are handled by the hero sequencer; skip them here.
        if (el.closest('.hero-sequence')) {
          io.unobserve(el);
          return;
        }
        // Stagger siblings by index among revealable peers
        var parent = el.parentElement;
        var siblings = parent ? parent.querySelectorAll(':scope > .reveal-fade, :scope > .reveal-line, :scope > .reveal-mask') : null;
        var index = 0;
        if (siblings && siblings.length) {
          for (var i = 0; i < siblings.length; i++) {
            if (siblings[i] === el) { index = i; break; }
          }
        }
        el.style.transitionDelay = (index * 80) + 'ms';
        el.classList.add('is-in');
        io.unobserve(el);
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -5% 0px' });

    targets.forEach(function (el) { io.observe(el); });
  }

  // ---------- 2. Nav scroll-state ----------
  function initNavScroll() {
    var update = function () {
      if (window.scrollY > 40) {
        document.body.classList.add('is-scrolled');
      } else {
        document.body.classList.remove('is-scrolled');
      }
    };
    update();
    window.addEventListener('scroll', update, { passive: true });
  }

  // ---------- 3. Hero sequenced load ----------
  // Opt-in via .hero-sequence on the hero section. Elements inside fire a
  // deterministic cascade: mask wipes first, then headline lines (120ms stagger),
  // then subhead + CTA cluster fade up.
  function initHeroSequence() {
    var heroes = document.querySelectorAll('.hero-sequence');
    if (!heroes.length) return;

    heroes.forEach(function (hero) {
      var run = function () {
        // Mask first (no delay)
        hero.querySelectorAll('.reveal-mask').forEach(function (el) {
          if (reduce) { el.classList.add('is-in'); return; }
          el.style.transitionDelay = '0ms';
          requestAnimationFrame(function () {
            requestAnimationFrame(function () { el.classList.add('is-in'); });
          });
        });

        // Lines: 400ms after mask starts, 120ms stagger
        var lines = hero.querySelectorAll('.reveal-line');
        lines.forEach(function (el, idx) {
          if (reduce) { el.classList.add('is-in'); return; }
          var delay = 400 + idx * 120;
          el.style.transitionDelay = delay + 'ms';
          requestAnimationFrame(function () {
            requestAnimationFrame(function () { el.classList.add('is-in'); });
          });
        });

        // Fades (subhead + CTA cluster): after last line
        var fades = hero.querySelectorAll('.reveal-fade');
        var baseDelay = 400 + Math.max(lines.length, 1) * 120 + 120;
        fades.forEach(function (el, idx) {
          if (reduce) { el.classList.add('is-in'); return; }
          el.style.transitionDelay = (baseDelay + idx * 140) + 'ms';
          requestAnimationFrame(function () {
            requestAnimationFrame(function () { el.classList.add('is-in'); });
          });
        });
      };

      // Wait for the first hero image to decode, so reveal never beats LCP.
      var img = hero.querySelector('img');
      if (!img) { run(); return; }
      if (img.complete && img.naturalWidth > 0) { run(); return; }
      var done = false;
      var go = function () { if (!done) { done = true; run(); } };
      img.addEventListener('load', go, { once: true });
      img.addEventListener('error', go, { once: true });
      // Hard fallback so we never leave the hero hidden.
      setTimeout(go, 1200);
    });
  }

  // ---------- 4. Hero rotator — crossfade stacked imgs every 6s ----------
  // Used on index.html only. Skipped under reduced motion.
  function initHeroRotator() {
    if (reduce) return;
    var rotator = document.querySelector('.hero-rotator');
    if (!rotator) return;
    var imgs = rotator.querySelectorAll('img');
    if (imgs.length < 2) return;
    var idx = 0;
    setInterval(function () {
      imgs[idx].classList.remove('is-active');
      idx = (idx + 1) % imgs.length;
      imgs[idx].classList.add('is-active');
    }, 6000);
  }

  // ---------- Boot ----------
  function boot() {
    initNavScroll();
    initHeroSequence();
    initReveal();
    initHeroRotator();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
