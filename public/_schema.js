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
    'image': 'https://winfredquek.com/winfred.jpg',
    'url': 'https://winfredquek.com',
    'telephone': '+6581618149',
    'email': 'winfred@winfredquek.com',
    'identifier': {'@type':'PropertyValue','propertyID':'CEA Registration No.','value':'R073319H'},
    'worksFor': {
      '@type': 'RealEstateOrganization',
      'name': 'Crestbrick Pte Ltd',
      'identifier': {'@type':'PropertyValue','propertyID':'CEA Estate Agency Licence No.','value':'L31010886H'},
      'address': {'@type':'PostalAddress','addressCountry':'SG'}
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
      'https://www.instagram.com/winfredquek',
      'https://t.me/winwithwinfred'
    ]
  };

  // Page-specific schemas
  const schemas = [agent];

  if (path.startsWith('/insights') || path.startsWith('/blog')) {
    // Article schema
    const title = document.querySelector('h1')?.textContent || document.title;
    const meta = document.querySelector('meta[name="description"]')?.content || '';
    schemas.push({
      '@context': 'https://schema.org',
      '@type': 'Article',
      'headline': title,
      'description': meta,
      'author': {'@type':'Person','name':'Winfred Quek','identifier':'R073319H'},
      'publisher': {'@type':'Organization','name':'Crestbrick Pte Ltd','logo':{'@type':'ImageObject','url':'https://winfredquek.com/logo.png'}},
      'datePublished': document.querySelector('meta[property="article:published_time"]')?.content || new Date().toISOString().split('T')[0],
      'dateModified': document.querySelector('meta[property="article:modified_time"]')?.content || new Date().toISOString().split('T')[0],
      'mainEntityOfPage': {'@type':'WebPage','@id': window.location.href},
      'speakable': {'@type':'SpeakableSpecification','cssSelector':['.quick-answer','h1','h2']}
    });
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
  const segments = path.split('/').filter(Boolean);
  if (segments.length >= 1) {
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

  // FAQ schema ,  auto-detected from h2/h3 with question marks + following p
  const faqEntries = [];
  document.querySelectorAll('h2, h3').forEach(h => {
    if (h.textContent.includes('?')) {
      const next = h.nextElementSibling;
      if (next && next.tagName === 'P') {
        faqEntries.push({
          '@type': 'Question',
          'name': h.textContent.trim(),
          'acceptedAnswer': { '@type': 'Answer', 'text': next.textContent.trim().slice(0,500) }
        });
      }
    }
  });
  if (faqEntries.length >= 2) {
    schemas.push({ '@context':'https://schema.org', '@type':'FAQPage', 'mainEntity': faqEntries });
  }

  // Inject all schemas
  schemas.forEach(s => {
    const script = document.createElement('script');
    script.type = 'application/ld+json';
    script.textContent = JSON.stringify(s);
    document.head.appendChild(script);
  });
})();
