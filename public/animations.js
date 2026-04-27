(function () {
  'use strict';

  /* ── 1. IntersectionObserver for .scroll-reveal ── */
  var revealObserver = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add('revealed');
        revealObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.10 });

  document.querySelectorAll('.scroll-reveal').forEach(function (el) {
    revealObserver.observe(el);
  });

  /* ── 2. Hero text-reveal: word-safe char splitter ── */
  /* Groups chars by word in a nowrap wrapper — prevents inline-block chars breaking mid-word */
  function splitIntoCharSpans(text, startIndex, container) {
    var idx = startIndex;
    var tokens = text.split(/(\s+)/);
    tokens.forEach(function (token) {
      if (/^\s+$/.test(token)) {
        /* whitespace between words — emit as plain text node, not inline-block */
        container.appendChild(document.createTextNode(token));
        idx += token.length;
      } else {
        var wordWrap = document.createElement('span');
        wordWrap.className = 'char-word';
        token.split('').forEach(function (ch) {
          var span = document.createElement('span');
          span.className = 'char';
          span.style.animationDelay = (idx * 0.022) + 's';
          span.textContent = ch;
          wordWrap.appendChild(span);
          idx++;
        });
        container.appendChild(wordWrap);
      }
    });
    return idx;
  }

  document.querySelectorAll('.hero-text-reveal').forEach(function (el) {
    var nodes = Array.from(el.childNodes);
    el.innerHTML = '';
    var charIdx = 0;

    nodes.forEach(function (node) {
      if (node.nodeType === Node.TEXT_NODE) {
        charIdx = splitIntoCharSpans(node.textContent, charIdx, el);
      } else {
        /* Preserve child elements (e.g. <span class="italic">) — split their text too */
        var wrapper = node.cloneNode(false);
        charIdx = splitIntoCharSpans(node.textContent, charIdx, wrapper);
        el.appendChild(wrapper);
      }
    });
  });

  /* ── 3. Parallax scroll handler (rAF-throttled, passive) ── */
  var parallaxEls = Array.from(document.querySelectorAll('[data-parallax]'));
  var rafId = null;

  if (parallaxEls.length) {
    function applyParallax() {
      var scrollY = window.scrollY;
      parallaxEls.forEach(function (el) {
        var speed = parseFloat(el.getAttribute('data-speed') || '0.3');
        var rect = el.getBoundingClientRect();
        var center = rect.top + rect.height / 2 - window.innerHeight / 2;
        el.style.transform = 'translateY(' + (center * speed * -1) + 'px)';
      });
      rafId = null;
    }

    window.addEventListener('scroll', function () {
      if (!rafId) {
        rafId = requestAnimationFrame(applyParallax);
      }
    }, { passive: true });

    applyParallax();
  }

  /* ── 4. Hover-tilt on .card-lift (subtle 3D mouse tracking) ── */
  var TILT_MAX = 2.5; /* degrees max */

  document.querySelectorAll('.card-lift').forEach(function (card) {
    card.addEventListener('mousemove', function (e) {
      var rect = card.getBoundingClientRect();
      var cx = rect.left + rect.width / 2;
      var cy = rect.top + rect.height / 2;
      var dx = (e.clientX - cx) / (rect.width / 2);
      var dy = (e.clientY - cy) / (rect.height / 2);
      var rx = dy * TILT_MAX * -1;
      var ry = dx * TILT_MAX;
      card.style.transform = 'translateY(-4px) rotateX(' + rx + 'deg) rotateY(' + ry + 'deg)';
    });

    card.addEventListener('mouseleave', function () {
      card.style.transform = '';
    });
  });

}());
