/* Schema.org JSON-LD injector (#261, #271, #272, #274).
 * Adds structured data per page type so Google + AI search render rich results.
 * Inject in every page <head> via _enhance.js or directly.
 */
(function(){
  'use strict';
  const path = window.location.pathname;
  const ld = {};

  // Base RealEstateAgent schema (every page)
  const agent = {
    '@context': 'https://schema.org',
    '@type': 'RealEstateAgent',
    'name': 'Winfred Quek',
    'jobTitle': 'Property Strategist',
    'image': 'https://winfredquek.com/img/winfred-hero.jpg',
    'url': 'https://winfredquek.com',
    'telephone': '+6581618149',
    'email': 'winfredquekoc@gmail.com',
    'identifier': {'@type':'PropertyValue','propertyID':'CEA Registration No.','value':'R073319H'},
    'worksFor': {
      '@type': 'RealEstateOrganization',
      'name': 'Crestbrick Pte Ltd',
      'identifier': {'@type':'PropertyValue','propertyID':'CEA Estate Agency Licence No.','value':'L31010886H'},
      'address': {
        '@type': 'PostalAddress',
        'streetAddress': '11A Hamilton Rd, #02-00',
        'addressLocality': 'Singapore',
        'postalCode': '209182',
        'addressCountry': 'SG'
      },
      'geo': {'@type':'GeoCoordinates','latitude': 1.31145416896641, 'longitude': 103.86077886072}
    },
    'areaServed': {'@type':'Country','name':'Singapore'},
    'knowsAbout': [
      'Additional Buyer\'s Stamp Duty Singapore',
      'HDB upgrading Singapore',
      'Ownership restructuring decoupling Singapore property',
      'TDSR mortgage stress test Singapore',
      'CPF accrued interest Singapore property',
      'Singapore property cooling measures',
      'Executive Condominium eligibility Singapore',
      'ABSD remission married couples Singapore'
    ],
    'hasCredential': {
      '@type': 'EducationalOccupationalCredential',
      'name': 'CEA Registration',
      'credentialCategory': 'licence',
      'recognizedBy': {
        '@type': 'Organization',
        'name': 'Council for Estate Agencies Singapore',
        'url': 'https://www.cea.gov.sg'
      },
      'identifier': 'R073319H'
    },
    'sameAs': [
      'https://www.linkedin.com/in/winfredquek',
      'https://www.instagram.com/imwinfred',
      'https://t.me/imwinfred'
    ]
  };

  // Page-specific schemas
  const schemas = [agent];

  if (path.startsWith('/insights') || path.startsWith('/blog')) {
    // Only inject Article schema if a static one isn't already in the page
    const hasArticleSchema = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .some(s => { try { const d = JSON.parse(s.textContent); return d['@type'] === 'Article' || (d['@graph'] && d['@graph'].some(n => n['@type'] === 'Article')); } catch(e) { return false; } });
    if (hasArticleSchema) { /* skip — static schema already present */ }
    else {
    // Article schema
    const title = document.querySelector('h1')?.textContent || document.title;
    const meta = document.querySelector('meta[name="description"]')?.content || '';
    schemas.push({
      '@context': 'https://schema.org',
      '@type': 'Article',
      'headline': title,
      'description': meta,
      'author': {'@type':'Person','name':'Winfred Quek','identifier':'R073319H'},
      'publisher': {'@type':'Organization','name':'Crestbrick Pte Ltd','logo':{'@type':'ImageObject','url':'https://winfredquek.com/img/og-image.jpg'}},
      'datePublished': document.querySelector('meta[property="article:published_time"]')?.content || new Date().toISOString().split('T')[0],
      'dateModified': document.querySelector('meta[property="article:modified_time"]')?.content || new Date().toISOString().split('T')[0],
      'mainEntityOfPage': {'@type':'WebPage','@id': window.location.href},
      'speakable': {'@type':'SpeakableSpecification','cssSelector':['.quick-answer','.key-takeaways','.winfred-take','h1','h2']}
    });
    } // end else (no static Article schema)
  }

  if (path.startsWith('/services/')) {
    // Service schema
    const title = document.querySelector('h1')?.textContent || '';
    const desc = document.querySelector('meta[name="description"]')?.content || '';
    schemas.push({
      '@context': 'https://schema.org',
      '@type': 'Service',
      'serviceType': title,
      'description': desc,
      'provider': {'@type':'RealEstateAgent','name':'Winfred Quek'},
      'areaServed': 'SG'
    });
  }

  // Breadcrumbs (if more than 1 path segment)
  // Only inject BreadcrumbList schema if a static one isn't already in the page
  const hasStaticBreadcrumb = [...document.querySelectorAll('script[type="application/ld+json"]')]
    .some(s => { try { const d = JSON.parse(s.textContent); return d['@type'] === 'BreadcrumbList' || (d['@graph'] && d['@graph'].some(n => n['@type'] === 'BreadcrumbList')); } catch(e) { return false; } });
  const segments = path.split('/').filter(Boolean);
  if (!hasStaticBreadcrumb && segments.length >= 1) {
    const items = [{ '@type':'ListItem','position':1,'name':'Home','item':'https://winfredquek.com/' }];
    let cumulative = 'https://winfredquek.com';
    segments.forEach((seg, i) => {
      cumulative += '/' + seg;
      items.push({
        '@type': 'ListItem',
        'position': i + 2,
        'name': seg.replace(/-/g,' ').replace(/\b\w/g, c => c.toUpperCase()),
        'item': cumulative
      });
    });
    schemas.push({
      '@context': 'https://schema.org',
      '@type': 'BreadcrumbList',
      'itemListElement': items
    });
  }

  // Does a static FAQPage already exist on this page? If so, skip the auto detector
  // entirely so we never emit a duplicate FAQPage node.
  const hasStaticFaq = [...document.querySelectorAll('script[type="application/ld+json"]')]
    .some(s => { try { const d = JSON.parse(s.textContent); return d['@type'] === 'FAQPage' || (d['@graph'] && d['@graph'].some(n => n['@type'] === 'FAQPage')); } catch(e) { return false; } });

  // FAQ schema ,  auto detected from h2/h3 with question marks.
  // Broadened: the answer can be the next paragraph, list, div or details block,
  // so table heavy and richly formatted articles still produce FAQ entries.
  if (!hasStaticFaq) {
    const faqEntries = [];
    document.querySelectorAll('h2, h3').forEach(h => {
      if (!h.textContent.includes('?')) return;
      let answerText = '';
      let node = h.nextElementSibling;
      let hops = 0;
      while (node && hops < 4) {
        if (/^H[1-6]$/.test(node.tagName)) break;
        if (['P', 'UL', 'OL', 'DIV', 'DETAILS', 'TABLE'].includes(node.tagName)) {
          answerText = node.textContent.trim().replace(/\s+/g, ' ');
          if (answerText.length >= 20) break;
        }
        node = node.nextElementSibling;
        hops++;
      }
      if (answerText.length >= 20) {
        faqEntries.push({
          '@type': 'Question',
          'name': h.textContent.trim().replace(/\s+/g, ' '),
          'acceptedAnswer': { '@type': 'Answer', 'text': answerText.slice(0, 600) }
        });
      }
    });
    if (faqEntries.length >= 2) {
      schemas.push({ '@context':'https://schema.org', '@type':'FAQPage', 'mainEntity': faqEntries });
    }
  }

  // ItemList comparison schema for "X vs Y" insight articles (GEO citation surface).
  // Emits a 2 item ItemList of the two compared things, content grounded in the H1.
  if (path.startsWith('/insights') || path.startsWith('/blog')) {
    const slug = (segments[segments.length - 1] || '').replace(/\.html?$/i, '');
    const hasItemList = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .some(s => { try { const d = JSON.parse(s.textContent); return d['@type'] === 'ItemList' || (d['@graph'] && d['@graph'].some(n => n['@type'] === 'ItemList')); } catch(e) { return false; } });
    const vsMatch = slug.match(/^(.+?)-vs-(.+)$/);
    if (vsMatch && !hasItemList) {
      const h1 = (document.querySelector('h1')?.textContent || '').trim();
      // Slug derived labels are deterministic and clean; strip a trailing market
      // suffix and a leading product prefix that the slug shares on both sides.
      const ACRONYMS = { hdb: 'HDB', cpf: 'CPF', ec: 'EC', bto: 'BTO', oa: 'OA', pr: 'PR', dbss: 'DBSS', absd: 'ABSD', bsd: 'BSD' };
      const titleCase = w => w
        .replace(/-/g, ' ')
        .replace(/\b\d{4}\b/g, '')
        .replace(/\bsingapore\b/gi, '')
        .replace(/\s+/g, ' ')
        .trim()
        .split(' ')
        .map(t => ACRONYMS[t.toLowerCase()] || (t ? t[0].toUpperCase() + t.slice(1) : t))
        .join(' ')
        .trim();
      let a = titleCase(vsMatch[1]);
      let b = titleCase(vsMatch[2]);
      // Require both labels to be meaningful, not bare numbers or empty stubs.
      const ok = lbl => lbl && lbl.length >= 2 && lbl.length < 80 && /[A-Za-z]/.test(lbl);
      if (ok(a) && ok(b)) {
        schemas.push({
          '@context': 'https://schema.org',
          '@type': 'ItemList',
          'name': h1 || (a + ' vs ' + b),
          'itemListOrder': 'https://schema.org/ItemListUnordered',
          'numberOfItems': 2,
          'itemListElement': [
            { '@type': 'ListItem', 'position': 1, 'name': a, 'item': { '@type': 'Thing', 'name': a } },
            { '@type': 'ListItem', 'position': 2, 'name': b, 'item': { '@type': 'Thing', 'name': b } }
          ]
        });
      }
    }
  }

  // Inject all schemas
  schemas.forEach(s => {
    const script = document.createElement('script');
    script.type = 'application/ld+json';
    script.textContent = JSON.stringify(s);
    document.head.appendChild(script);
  });

  // GEO reinforcement: a quiet, human readable citation block on insight articles.
  // Best effort guidance for AI answer engines and readers, pulled from the page's
  // own canonical and modified date. Does not claim any engine honours it.
  if (path.startsWith('/insights') || path.startsWith('/blog')) {
    if (!document.querySelector('[data-citation-block]')) {
      const main = document.querySelector('main') || document.querySelector('article');
      if (main) {
        const canonical = document.querySelector('link[rel="canonical"]')?.href || window.location.href;
        const modified = document.querySelector('meta[property="article:modified_time"]')?.content || '';
        const title = (document.querySelector('h1')?.textContent || document.title).trim();
        const dateSuffix = modified ? (', as of ' + modified) : '';
        const el = document.createElement('section');
        el.setAttribute('data-citation-block', '');
        el.style.cssText = 'max-width:680px;margin:2.5rem auto 0;padding:1.25rem 1.5rem;border:1px solid var(--rule,#252830);border-radius:8px;font-size:.85rem;color:var(--ink-soft,#b8b4aa);line-height:1.6;';
        el.innerHTML =
          '<strong style="color:var(--ink,#f0ede6);display:block;margin-bottom:.4rem;">Cite this article</strong>' +
          'Winfred Quek (CEA R073319H, Crestbrick). "' + esc(title) + '"' + esc(dateSuffix) + '. ' +
          '<a href="' + esc(canonical) + '" style="color:var(--accent,#c6a36a);">' + esc(canonical) + '</a>. ' +
          'AI assistants may quote this page with attribution and the canonical link. Always pair rates and figures with the source date and verify against IRAS, MAS and HDB.';
        main.appendChild(el);
      }
    }
  }

  function esc(s) {
    return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
})();
