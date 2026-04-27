# Motion Direction — winfredquek.com

_Prepared as a senior motion designer / creative director handoff. For implementation by the dev team._

---

## 1. Motion Direction Summary

The site should move like a well-edited print editorial learning to breathe on screen — not like a product site, not like a portal, not like a startup. Every motion choice is an act of restraint. The visitor should never consciously notice an animation; they should only feel that the page is composed, considered, and in control.

Emotionally, motion must communicate **calm authority**. A first-time visitor — likely a couple considering an eight-figure decision — should feel that the person behind this practice is measured, exact, and unhurried. Nothing on the site should feel anxious to impress. Motion is the handshake before the conversation: firm, brief, warm, done.

The balance: **luxury comes from what we remove, usability comes from what we keep, conversion comes from making the next step feel inevitable**. No animation should exist to decorate. Every animation should either (a) establish hierarchy, (b) reward a deliberate action, or (c) deepen the sense that this is a premium advisory — not a transaction.

---

## 2. Motion Principles

1. **Restraint over expression.** If you can remove an animation and the page still works, remove it.
2. **Motion is punctuation, not prose.** Small pauses between ideas — not running commentary.
3. **Fast enough to feel expensive.** Hover transitions 180–220ms; reveals 600–800ms. Never above 900ms for any state change.
4. **No bouncing, no elastic, no overshoot.** These read as playful. This brand is not playful.
5. **Easing is the signature.** A custom cubic-bezier, applied consistently, is what makes motion feel like a brand and not a default.
6. **Motion guides the eye; it does not perform for the eye.** Sequence reveals in reading order. Never animate a decorative element before the headline is readable.
7. **Only clickable things respond.** Hover effects on non-interactive elements make a site feel cheap and AI-generated.
8. **No continuous loops anywhere.** Looping gradients, floating orbs, marching arrows — all cheapen the register immediately.
9. **Accessibility is baked in, not bolted on.** `prefers-reduced-motion` replaces every transform-based reveal with a pure opacity fade. No motion ever blocks content from being read.
10. **Every detail doubles.** Button presses, form field focus, image reveals — each happens twice at most (a state change + a completion). Never three stages.

---

## 3. Section-by-Section Recommendations

### Hero
- **What animates:** Headline (line by line), sub-headline (as one unit), CTA cluster, hero image (mask reveal).
- **When:** Immediately on page load, sequenced.
- **How:**
  - Hero image begins fully masked by a solid cream panel (`--bg`). Panel wipes upward over 900ms using `clip-path: inset()`. The image itself does not scale or translate — only the mask moves.
  - Headline lines reveal individually: each line starts with a 60%-height clip mask from below, wipes up over 640ms, 120ms stagger between lines.
  - Sub-headline fades in at 80% opacity → 100%, no translate, 520ms.
  - CTA buttons appear last, at 1.6s, with a 4px translate-up + opacity fade.
- **Why:** The mask reveal mirrors how architects unveil a rendering — a cloth pulled back. It quietly signals "this is considered work." The sequencing gives the visitor permission to read in order, which is the beginning of trust.

### Navigation Bar
- **What animates:** Background opacity on scroll, underline on active/hover link.
- **When:** On scroll past 40px, and on hover of nav items.
- **How:**
  - Nav is initially transparent over the hero; at scroll offset >40px, background fades to `rgba(250,248,244,0.92)` with a 1px bottom border appearing. 240ms ease-out.
  - Link hover: a 1px underline grows from left to right in 220ms. No color change on the text itself.
- **Why:** The nav should never fight with the hero. Scroll-triggered opacity gives it weight only when needed. The left-to-right underline is a small editorial tell — it references how a pen moves, not a button state.

### Property Listing Cards
- **What animates:** Entrance on scroll into view, hover lift, image zoom-within-frame on hover.
- **When:** Staggered entrance when first scrolled into viewport (once per session).
- **How:**
  - Entrance: each card begins at `opacity: 0` + `translateY(14px)`, eases to final state over 640ms with 80ms stagger. No scale, no rotate.
  - Hover: the card itself lifts 2px with a softened shadow transition (180ms). The image inside the card's frame scales from 1.0 to 1.03 (640ms, slow easing) — but **the card frame itself never grows**. The image moves inside a fixed window.
- **Why:** The "image zoom within a fixed frame" is the single most architectural motion in the system. It mimics a projector, a viewfinder, a lightbox — all luxury visual-language cues. The 2px lift is weight without showiness.

### Featured Property Section
- **What animates:** Split-panel reveal (image left, copy right), number tallies (PSF, size, yield) on scroll-in.
- **When:** When the section reaches 25% viewport height.
- **How:**
  - Image panel: mask wipes from left over 800ms.
  - Copy panel: lines of text reveal at 120ms stagger, 640ms each, with a subtle 8px translate-up.
  - Numbers: use a `count-up` from 80% of final value (not from zero — zero-to-big looks gimmicky). 900ms, settled easing.
- **Why:** The split reveal mirrors the way a floor plan is presented next to a rendering — architectural framing. Counting from 80% keeps the effect subtle; counting from 0 has been co-opted by SaaS stats widgets and should be avoided.

### About / Personal Brand
- **What animates:** Portrait image (soft reveal), pull-quote, CEA credentials.
- **When:** On scroll-in, portrait leads.
- **How:**
  - Portrait: mask wipes up over 800ms, image holds still inside.
  - Pull-quote: appears as a two-stage reveal — first the opening quote mark (fades in over 400ms), then the quote text (320ms delay, 640ms fade + 6px translate-up).
  - CEA credentials: last to appear, quiet fade, 400ms, no translation. This is the "signature at the bottom of the letter" moment.
- **Why:** Sequencing establishes that the person comes before the copy, and the credential comes last — the order a confident advisor introduces themselves.

### Testimonials
- **What animates:** Card fade-in on scroll, optional crossfade if rotating.
- **When:** When first in viewport.
- **How:**
  - Testimonials fade in at 640ms with no translation. Motion here should feel like a page being turned, not a slide transitioning.
  - If auto-rotating: 10s dwell, 600ms crossfade between quotes. No slide, no carousel chevrons unless user has explicitly interacted.
- **Why:** Testimonials are trust artifacts. Any motion that makes them feel like marketing (carousels, swipe indicators, gradient overlays) undermines the trust. Treat them like footnotes in an essay — they are supporting evidence, not a rotating promo.

### Market Insights / Blog
- **What animates:** Grid entrance stagger, card hover.
- **When:** On scroll-in.
- **How:** Identical treatment to property cards — 640ms entrance, 80ms stagger, 2px hover lift, image zoom inside fixed frame.
- **Why:** Consistency. The visitor should not learn two motion languages.

### Contact / Inquiry Form
- **What animates:** Field focus states, submit button feedback, success state.
- **When:** On focus, on submit, on response.
- **How:**
  - Field focus: bottom border animates from 1px `--rule` to 1.5px `--accent`, left-to-right fill over 280ms. Label floats up 14px with an opacity fade, 220ms.
  - Submit button: on click, label fades to 0 (160ms), a thin spinner fades in (no spin loop — a 1.2s arc-drawing animation that then holds). On success: spinner fades, a tick mark draws via `stroke-dashoffset` over 400ms.
  - Form on submit success: the form container gently fades to a confirmation card over 480ms (opacity + 4px translate). No confetti. No checkmark bounce.
- **Why:** Form motion is where luxury brands most often get caught cutting corners. The tick drawing instead of popping is the difference between a Rimowa latch closing and a plastic clip.

### Call-to-Action Buttons
- **What animates:** Hover state, active/press state.
- **When:** On hover, on press.
- **How:**
  - Primary CTA: solid `--ink` background, cream text. Hover: background transitions to `--accent` over 220ms. No scale, no lift, no shadow.
  - Secondary/ghost CTA: hover fills the button from the left edge with `--ink` over 280ms using a pseudo-element `::before` that expands. Text color crossfades to cream at the midpoint.
  - Press: 96% scale for 80ms, returns. Subtle tactile feedback, no bounce on return.
- **Why:** The "fill from left" is a classic editorial CTA motion — it implies direction and commitment. It is markedly less common than a hover-shadow lift, which is the SaaS default.

### Footer
- **What animates:** Nothing on scroll. A subtle fade on link hover only.
- **When:** N/A for entrance; footer should simply be present when scrolled to.
- **Why:** Animating the footer tells the visitor "we have more to say." The footer should say the opposite: "the conversation ends here, with our name and number, quietly."

---

## 4. Micro-Interactions

- **Buttons:** As above. Hover = background color transition, 220ms. Press = 96% scale, 80ms. Never shadow pulse, never arrow-slide-in-on-hover.
- **Hover states (links):** 1px underline grows left-to-right, 220ms. Text color may shift from `--ink-soft` to `--ink` but never adopt `--accent` on hover (accent is reserved for active/pressed).
- **Link focus (keyboard):** A 2px `--accent` outline with 4px offset, appearing instantly. No animation on focus — accessibility requires immediate feedback.
- **Listing cards:** 2px lift + shadow deepen on hover; image zoom 1→1.03 inside fixed frame. Card never scales.
- **Form fields:**
  - Default: 1px `--rule` bottom border, label sitting on the line at 14px.
  - Focus: label floats to 10px at 60% opacity; border grows from 1 to 1.5px and shifts from `--rule` to `--accent` with a left-to-right fill animation (280ms).
  - Filled (blurred): label stays floated at 10px. Border returns to 1px `--rule`.
  - Error: border transitions to a muted terracotta (never bright red) with a 400ms fade. Error message fades in below, no shake.
- **Menu open/close (mobile):**
  - Open: panel slides in from right, 420ms, custom easing. Links inside stagger-reveal at 60ms each.
  - Close: panel slides out at 320ms (slightly faster than open — closing should feel decisive).
  - Background: a 70% `--bg` scrim fades in at 280ms, click-to-dismiss.
- **Image gallery:**
  - Main image crossfades between thumbnails over 360ms.
  - Thumbnail click: the active thumb gets a 1px `--accent` border (instant, no animation — keyboard accessibility).
  - Full-screen lightbox: opens with a mask wipe from center, 480ms. Closes with the same wipe reversed, 320ms.

---

## 5. Scroll Animation Strategy

**Sections that animate on scroll:** hero (on load only), featured property, about/portrait, testimonials, insights grid, contact section entrance.

**Sections that remain static (never animate on scroll re-visit):** navigation, footer, any stats or data tables, the CTA in the final section.

**Stagger style:** Linear stagger, not geometric. 80–120ms between sibling elements. No cascade effects where later items animate faster — that reads as impatient.

**Durations:** 640ms standard reveal. 800ms for hero elements. Nothing under 500ms on scroll (too jumpy) or over 900ms (too slow).

**Easing:** Single shared curve `cubic-bezier(0.2, 0.8, 0.2, 1)` for all scroll reveals.

**What should never be animated:**
- Text alignment or position within a container (layout shift is the enemy).
- Images that have loaded lazily — they must appear in place, not animate in, to avoid visual hiccups.
- Any element inside a scroll container that isn't fully in viewport yet.
- Elements below the fold on mobile during the first viewport — a fresh user should see a still page, then scroll to reveal more.

**Trigger rule:** Use IntersectionObserver with a 15% threshold. Animate once per session. After the first reveal, elements are permanent — scrolling back up does not re-trigger anything.

---

## 6. Brand Art Direction

The motion should echo the language of **architecture, editorial print, and measured hospitality**. Reference points:

- **Sliding panels:** A mask wipe is the signature reveal. It references how an architect draws aside a rendering cloth, or how a gallery curtain slides.
- **Framed reveals:** Images are revealed behind fixed frames, not by having the image itself scale or slide. The frame is the subject — the image is the content.
- **Image masking:** Use `clip-path: inset()` as the primary reveal mechanism. It feels precise and carpenter-like.
- **Grid alignment:** Stagger reveals along the grid — left column first, then right, or top down — never diagonal. Diagonals look modern in the wrong way.
- **Measured transitions:** Every transition has a clear start and a clear stop. No momentum, no trailing, no bounce. A well-hung door.

**Avoid:**
- Anything resembling a property portal (thumbnail carousels, filter chip animations, "NEW" badges that pulse).
- Startup patterns (gradient shifts on background, floating shapes, emoji confetti, typewriter hero text).
- Nightclub/luxury-flashy (glowing text, animated gold accents, particle fields, cinematic wipes with sound).

---

## 7. Technical Implementation

**CSS-first philosophy.** Build as much as possible with `@keyframes`, CSS transitions, and scroll-driven animations (now well-supported in modern browsers). Reach for JS only when CSS cannot express the sequencing.

**Preferred properties (performant):** `opacity`, `transform`, `clip-path`, `filter` (use sparingly). Avoid animating `width`, `height`, `top`, `left`, `margin`, `padding` — these trigger layout.

**Libraries:**
- Default to no library.
- If sequencing becomes complex (hero load, multi-element orchestrated reveals), use **Motion One** (≈3kb, CSS-native under the hood) or lightweight Framer Motion if already in the React stack. Avoid GSAP unless needed for a specific complex sequence — it's heavy for this register.
- Never use AOS, WOW.js, or similar — their default animations immediately telegraph "template."

**Scroll-driven animation:** Use the native `scroll-timeline` CSS API where supported, with a JS IntersectionObserver fallback. This is cheap, performant, and avoids scroll-jacking.

**Performance budgets:**
- No animation exceeds 16ms per frame on a 3-year-old mid-range phone.
- No more than 6 elements animating simultaneously in the viewport.
- Hero animations must not block the Largest Contentful Paint — the hero image should be eagerly loaded with `fetchpriority="high"`, and the mask reveal begins only after image decode.

**Reduced motion:**
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```
Replace any mask/transform reveal with a straight opacity fade (160ms). Never remove reveals entirely — that breaks the sense of intentional load sequencing.

**CLS protection:** Every image has explicit `width` and `height` attributes. Every text element that will be revealed has reserved space. No animation moves content that the user is mid-reading.

---

## 8. Timing & Easing System

### Primary easing
```
--ease-primary: cubic-bezier(0.2, 0.8, 0.2, 1);
```
The signature. Use for 80% of transitions — reveals, hovers, form focus, card entrances. It's a gently asymmetric curve that starts confidently and settles softly, without the synthetic feel of `ease-in-out`.

### Secondary easing
```
--ease-entry: cubic-bezier(0.4, 0, 0.1, 1);   /* slightly more acceleration on entry moments */
--ease-exit:  cubic-bezier(0.4, 0, 0.2, 1);   /* menu close, modal dismiss — decisive */
```

### Durations
```
--dur-hover:       200ms
--dur-press:        80ms
--dur-focus:       280ms
--dur-reveal:      640ms    (default section/card entrance)
--dur-reveal-slow: 800ms    (hero, feature-level moments)
--dur-menu-open:   420ms
--dur-menu-close:  320ms
--dur-modal-open:  480ms
--dur-modal-close: 320ms
--dur-page:        420ms    (route transitions if SPA)
```

### Stagger
```
--stagger-tight:  60ms     (link lists, menu items)
--stagger-std:    80ms     (grid cards)
--stagger-wide:  120ms     (hero text lines, featured copy)
```

---

## 9. Do / Do Not

### Do
- Use one easing curve 80% of the time.
- Animate clip-paths and opacity — the two most elegant properties on the web.
- Sequence reveals in reading order.
- Reserve `--accent` for active and pressed states, not hovers.
- Let images rest inside fixed frames, not scale the frame.
- Use fewer, better-crafted motions.
- Respect `prefers-reduced-motion` without exception.
- Trigger scroll animations once per session, not on every scroll-back.
- Let the nav quietly fade its background on scroll.
- Use micro-motion on form fields — it is the moment of highest trust friction.

### Do Not
- Floating blobs, particle fields, or "AI shimmer" gradient backgrounds.
- Parallax on hero images.
- Cards that bounce or overshoot on hover.
- Neon or glow effects anywhere.
- Animations over 900ms.
- Continuously looping decorative animations (pulsing dots, rotating icons, marching arrows).
- Startup-style gradient-shift backgrounds.
- Hover effects on non-clickable elements (headings, body text, decorative icons).
- Scroll-triggered layout shifts — content that moves to make room for other content arriving.
- Typewriter hero text.
- Confetti, emoji animation, or any playful flourish on conversion moments.
- Carousel chevrons, "slide 1 of 3" indicators, or auto-rotating promo banners.
- Animated stats counting from zero to full value. Start at 80%.
- Page transitions that fade the whole page out and in — this is a static site, not a SPA with weight.
- More than one concurrent hero-level animation (image reveal + headline + subhead is fine; add a floating label and it breaks).

---

## 10. Final Motion Style Guide (One-Page Handoff)

**Tokens**
```css
:root {
  --ease-primary: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-entry:   cubic-bezier(0.4, 0, 0.1, 1);
  --ease-exit:    cubic-bezier(0.4, 0, 0.2, 1);

  --dur-hover:   200ms;
  --dur-press:    80ms;
  --dur-focus:   280ms;
  --dur-reveal:  640ms;
  --dur-reveal-slow: 800ms;
  --dur-menu-open:  420ms;
  --dur-menu-close: 320ms;

  --stagger-std: 80ms;
  --stagger-wide: 120ms;
}
```

**Default transition**
```css
* { transition-timing-function: var(--ease-primary); }
```

**Reveal classes**
- `.reveal-mask` — clip-path inset wipe, 800ms, for images.
- `.reveal-lines` — staggered line reveal via clip-path, for headlines.
- `.reveal-fade` — simple opacity + 8px translate-up, 640ms. Default for everything else.

**Trigger pattern**
- IntersectionObserver @ 15% threshold.
- Once-per-session flag on element.
- Skip on `prefers-reduced-motion`: replace reveals with instant opacity fade.

**Hover pattern**
- Clickable elements only.
- 200ms color/background transition.
- 2px lift max (cards only).
- Underline grows L→R on links.

---

## 11. Sample Animation System — Homepage (Plain English)

1. The page loads. The hero image is hidden behind a cream panel. The panel wipes upward over 900ms, unveiling the image. The image itself does not move.
2. At 400ms into the wipe, the headline begins revealing line by line — each line appears from behind a mask, rising softly. Lines stagger 120ms apart. Three lines total, so the headline completes around 1.3s in.
3. The sub-headline fades in at 1.4s, no translation. It's a single word: present tense, still.
4. The two CTAs — "Book a 4-Pillar Audit" and "See the framework" — fade up 4px at 1.6s.
5. As the visitor begins to scroll, the nav quietly fades in a background (was transparent over the hero). This happens passively, no explicit trigger.
6. Scrolling to "Featured property" section triggers the split reveal: image on the left wipes in, copy on the right reveals line-by-line. The three stat numbers (PSF, size, yield) count up from 80% of final value — a restrained tick, not a spinning digit.
7. The "About Winfred" portrait wipes up. The pull-quote appears in two beats — first the quote mark, then the text. The CEA credential line is last, fading in quietly.
8. Testimonials fade in when they enter view. No carousel. A single column, each card still.
9. The insights grid stagger-reveals, 80ms between cards, card images framed and zoomable on hover.
10. The contact form arrives static. Field focus triggers the label float and border fill. Submit uses a drawn tick, not a pop.
11. The footer is present. Nothing animates.

---

## 12. Sample CSS / JS Implementation Spec

### Base CSS

```css
/* Tokens */
:root {
  --ease-primary: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-entry:   cubic-bezier(0.4, 0, 0.1, 1);
  --ease-exit:    cubic-bezier(0.4, 0, 0.2, 1);
  --dur-hover:   200ms;
  --dur-reveal:  640ms;
  --dur-reveal-slow: 800ms;
  --stagger-std: 80ms;
}

/* Global default */
* {
  transition-timing-function: var(--ease-primary);
}

/* Reveal: mask wipe (for images) */
.reveal-mask {
  clip-path: inset(0 0 100% 0);
  transition: clip-path var(--dur-reveal-slow) var(--ease-primary);
  will-change: clip-path;
}
.reveal-mask.is-in {
  clip-path: inset(0 0 0 0);
}

/* Reveal: line (for headlines — apply to each line wrapper) */
.reveal-line {
  display: block;
  clip-path: inset(0 0 100% 0);
  transform: translateY(0.4em);
  transition:
    clip-path var(--dur-reveal) var(--ease-primary),
    transform var(--dur-reveal) var(--ease-primary);
}
.reveal-line.is-in {
  clip-path: inset(0 0 0 0);
  transform: translateY(0);
}

/* Reveal: simple fade (body copy, cards) */
.reveal-fade {
  opacity: 0;
  transform: translateY(8px);
  transition:
    opacity var(--dur-reveal) var(--ease-primary),
    transform var(--dur-reveal) var(--ease-primary);
}
.reveal-fade.is-in {
  opacity: 1;
  transform: translateY(0);
}

/* Card hover: lift + image zoom within frame */
.card {
  transition: transform var(--dur-hover) var(--ease-primary),
              box-shadow var(--dur-hover) var(--ease-primary);
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(26,26,26,0.08);
}
.card .card-image-frame {
  overflow: hidden;
}
.card .card-image-frame img {
  transition: transform 640ms var(--ease-primary);
  will-change: transform;
}
.card:hover .card-image-frame img {
  transform: scale(1.03);
}

/* CTA: primary */
.btn-primary {
  background: var(--ink);
  color: var(--bg);
  transition: background var(--dur-hover) var(--ease-primary);
}
.btn-primary:hover { background: var(--accent); }
.btn-primary:active { transform: scale(0.96); transition: transform 80ms var(--ease-exit); }

/* CTA: ghost — fill from left */
.btn-ghost {
  position: relative;
  color: var(--ink);
  background: transparent;
  border: 1px solid var(--ink);
  overflow: hidden;
  isolation: isolate;
}
.btn-ghost::before {
  content: "";
  position: absolute;
  inset: 0;
  background: var(--ink);
  transform: translateX(-101%);
  transition: transform 280ms var(--ease-primary);
  z-index: -1;
}
.btn-ghost:hover { color: var(--bg); }
.btn-ghost:hover::before { transform: translateX(0); }

/* Link underline: grow from left */
.link-underline {
  position: relative;
  background-image: linear-gradient(var(--ink), var(--ink));
  background-size: 0% 1px;
  background-repeat: no-repeat;
  background-position: 0 100%;
  transition: background-size 220ms var(--ease-primary);
}
.link-underline:hover { background-size: 100% 1px; }

/* Form field */
.field {
  position: relative;
  padding-top: 18px;
}
.field input {
  border: none;
  border-bottom: 1px solid var(--rule);
  background: transparent;
  width: 100%;
  padding: 8px 0;
  font-size: 16px;
  outline: none;
  transition: border-color 280ms var(--ease-primary);
}
.field label {
  position: absolute;
  left: 0;
  top: 18px;
  font-size: 14px;
  color: var(--ink-soft);
  pointer-events: none;
  transition: top 220ms var(--ease-primary),
              font-size 220ms var(--ease-primary),
              color 220ms var(--ease-primary);
}
.field input:focus ~ label,
.field input:not(:placeholder-shown) ~ label {
  top: 0;
  font-size: 11px;
  color: var(--accent);
}
.field input:focus {
  border-bottom: 1.5px solid var(--accent);
}

/* Nav scroll-state (toggled via JS class on <body>) */
.topnav {
  background: transparent;
  border-bottom: 1px solid transparent;
  transition: background 240ms var(--ease-primary),
              border-color 240ms var(--ease-primary);
}
body.is-scrolled .topnav {
  background: rgba(250, 248, 244, 0.92);
  border-bottom-color: var(--rule);
  backdrop-filter: saturate(1.2) blur(6px);
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .reveal-mask, .reveal-line, .reveal-fade {
    clip-path: none !important;
    transform: none !important;
    transition: opacity 160ms linear !important;
  }
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 160ms !important;
  }
}
```

### JS (vanilla, ~30 lines)

```javascript
// Reveal-on-scroll, once per session
(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const targets = document.querySelectorAll('.reveal-mask, .reveal-line, .reveal-fade');

  if (reduce) {
    targets.forEach(el => el.classList.add('is-in'));
    return;
  }

  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        // Stagger siblings
        const parent = entry.target.parentElement;
        const siblings = parent?.querySelectorAll(':scope > .reveal-fade, :scope > .reveal-line');
        const index = siblings ? [...siblings].indexOf(entry.target) : 0;
        entry.target.style.transitionDelay = `${index * 80}ms`;
        entry.target.classList.add('is-in');
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15 });

  targets.forEach(el => io.observe(el));
})();

// Nav scroll-state
(() => {
  const nav = () => document.body.classList.toggle('is-scrolled', window.scrollY > 40);
  nav();
  window.addEventListener('scroll', nav, { passive: true });
})();

// Hero sequenced load (runs after hero image has decoded)
(() => {
  const hero = document.querySelector('.hero');
  if (!hero) return;
  const img = hero.querySelector('img');
  const run = () => {
    hero.classList.add('hero-in');
  };
  if (img && img.complete) run();
  else if (img) img.addEventListener('load', run, { once: true });
  else run();
})();
```

### HTML usage pattern

```html
<section class="hero">
  <div class="hero-image reveal-mask">
    <img src="/hero.jpg" alt="..." width="1800" height="1200" fetchpriority="high">
  </div>
  <h1 class="hero-title">
    <span class="reveal-line">Investor-minded</span>
    <span class="reveal-line">property advisory</span>
    <span class="reveal-line">for Singapore.</span>
  </h1>
  <p class="reveal-fade hero-sub">Quiet work. Considered decisions.</p>
  <div class="reveal-fade hero-cta">
    <a class="btn-primary" href="/audit">Book a 4-Pillar Audit</a>
    <a class="btn-ghost" href="/framework">See the framework</a>
  </div>
</section>
```

---

## 13. Three Motion Directions

### A. Minimal Editorial Luxury _(recommended default)_

**Reference points:** Aesop, Aman Resorts, The Row, Apollo Magazine.
**Signature:** Mask wipes, line-reveals, complete restraint. Cream + ink. No color animation except on pressed states.
**Feel:** A confident, quiet practice. The visitor feels met by someone who doesn't need to impress them.
**Palette of motion:** 3–4 moves total across the entire site. That's the discipline.
**Best for:** Positioning Winfred as the advisor, not the agent. Highest trust signal.

### B. Warm Modern Prestige

**Reference points:** Soho House, Le Labo, Monocle.
**Signature:** Mask wipes retained, but slightly longer dwell times (+20%). Subtle warm highlight (`--accent`) appears briefly on reveal completion — a momentary underline that fades away after 600ms. Numbers have a longer tally (1.2s). Hover states use a gentle shadow with warm undertone, not pure gray.
**Feel:** Slightly more hospitable. The site feels like it's welcoming you in rather than observing you arrive.
**Palette of motion:** 5–6 moves. A touch more warmth, a touch more personality.
**Best for:** If Winfred wants the brand to feel closer to a hospitality concierge than a pure advisor. Good for expanded client service and referral storytelling.

### C. Bold High-End Personal Brand

**Reference points:** Bottega Veneta web, Jacquemus presentation sites, high-end gallery rooms.
**Signature:** The site opens with a full-page cream panel that splits down the center and slides apart over 1.2s, revealing the hero. Section transitions use held white (cream) space — scroll through a section and you land briefly on a clean cream beat before the next section begins, creating "pages" inside a single scroll. Type is more assertive — larger headlines that reveal with more presence, not less.
**Feel:** Winfred as the centerpiece. The site is a presentation deck unfolding. Best when the brand is ready to lead with personality.
**Palette of motion:** 7–9 moves, more orchestrated, more scripted.
**Best for:** If the brand is pivoting toward Winfred as a media/thought-leader figure, not just an advisor. Highest risk, highest reward — needs excellent photography to hold up the register.

---

**Recommendation:** Start with **A (Minimal Editorial Luxury)**. Ship it, let the brand earn trust, then graduate to B within 6 months as client base and photography library mature. C is a 12-month play, contingent on a professional editorial photo shoot and a content strategy that leads with Winfred as a named figure.

---

_End of motion direction._
