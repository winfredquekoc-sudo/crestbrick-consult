(function(){
  "use strict";
  var SG = {lng:103.85007, lat:1.28967, zoom:15.5};
  var TZ_OFFSET = 8*60; // SGT minutes east of UTC (no DST in SG)
  var MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // ---- map ----
  var map = new maplibregl.Map({
    container:'map',
    style:'https://tiles.openfreemap.org/styles/positron',
    center:[SG.lng, SG.lat], zoom:SG.zoom, pitch:50, bearing:0, maxPitch:70, antialias:true
  });
  map.addControl(new maplibregl.NavigationControl({visualizePitch:true}), 'bottom-right');
  map.addControl(new maplibregl.AttributionControl({compact:true}), 'bottom-left');

  // ---- deck.gl overlay with sun shadows ----
  var ambient = new deck.AmbientLight({color:[255,255,255], intensity:1.0});
  // one light instance, mutate its timestamp (rebuilding the effect per tick tears down shadow buffers)
  var sun = new deck._SunLight({timestamp:Date.now(), color:[255,250,235], intensity:1.35, _shadow:true});
  var lighting = new deck.LightingEffect({ambientLight:ambient, sun:sun});
  lighting.shadowColor = [0.06, 0.09, 0.15, 0.55];   // 0..1 range; larger values clamp to white
  var overlay = new deck.MapboxOverlay({interleaved:true, effects:[lighting], layers:[]});
  map.addControl(overlay);

  var buildings = {type:'FeatureCollection', features:[]};
  var pin = null;

  function buildingLayer(){
    return new deck.GeoJsonLayer({
      id:'buildings', data:buildings, extruded:true, filled:true, stroked:false,
      getElevation:function(f){ return f.properties.h || 15; },
      getFillColor:function(f){ return f.properties.src==='est' ? [206,208,214] : [228,230,235]; },
      material:{ambient:0.55, diffuse:0.7, shininess:24, specularColor:[70,74,82]}
    });
  }
  function pinLayer(){
    if(!pin) return null;
    return new deck.ScatterplotLayer({id:'pin', data:[pin], getPosition:function(d){return d;},
      getFillColor:[201,162,75], getRadius:6, radiusUnits:'pixels', getLineColor:[26,20,8], lineWidthUnits:'pixels', getLineWidth:2, stroked:true});
  }
  // ---- ground shadows as swept footprint polygons (ShadeMap-style) ----
  // deck.gl's GL shadow only lands on other deck layers, never on the MapLibre base, so
  // we compute each building's shadow footprint from sun altitude/azimuth and draw it.
  function hull(pts){
    pts=pts.slice().sort(function(a,b){return a[0]-b[0]||a[1]-b[1];});
    if(pts.length<3) return pts;
    function cross(o,a,b){return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0]);}
    var lower=[],upper=[],i;
    for(i=0;i<pts.length;i++){ while(lower.length>=2&&cross(lower[lower.length-2],lower[lower.length-1],pts[i])<=0) lower.pop(); lower.push(pts[i]); }
    for(i=pts.length-1;i>=0;i--){ while(upper.length>=2&&cross(upper[upper.length-2],upper[upper.length-1],pts[i])<=0) upper.pop(); upper.push(pts[i]); }
    upper.pop(); lower.pop(); return lower.concat(upper);
  }
  // exact swept shadow: union of the footprint, the far cap, and one quad per edge.
  // (A convex hull over-covers L/U-shaped blocks into blobs; the union is the true shape.)
  // The union is ~100x slower than the hull, so the hull draws instantly while the
  // slider moves and the exact union replaces it once the slider settles.
  function sweep(ring, dlng, dlat, exact){
    if(exact && window.polygonClipping){
      var cap=ring.map(function(p){return [p[0]+dlng,p[1]+dlat];});
      var polys=[[ring],[cap]];
      for(var i=0;i<ring.length-1;i++){
        var a=ring[i], b=ring[i+1];
        polys.push([[a,b,[b[0]+dlng,b[1]+dlat],[a[0]+dlng,a[1]+dlat],a]]);
      }
      try{ return polygonClipping.union.apply(null, polys); }catch(e){}
    }
    var pts=[]; ring.forEach(function(p){pts.push([p[0],p[1]]); pts.push([p[0]+dlng,p[1]+dlat]);});
    return [[hull(pts)]];
  }
  var EXACT_MAX=5000;                               // ~0.25ms/building: above this the settle-time union is too slow
  function shadowPolys(exact){
    var ts=selectedTsMs(), c=map.getCenter();
    var pos=SunCalc.getPosition(new Date(ts), c.lat, c.lng);
    if(pos.altitude<=0.03){                         // sun down: no cast shadows
      window.__sunDbg={n:0, alt:pos.altitude*180/Math.PI, az:((pos.azimuth*180/Math.PI)+180+360)%360, b:buildings.features.length, exact:!!exact};
      return [];
    }
    var perM=1/Math.tan(pos.altitude);              // shadow length per metre of height
    // SunCalc azimuth is from SOUTH toward WEST; the shadow points AWAY from the sun.
    var ex=Math.sin(pos.azimuth), ny=Math.cos(pos.azimuth);
    var low=pos.altitude<0.21;                      // below ~12°: long, faint shadows
    var alpha=low?85:130, maxLen=low?900:1400, out=[];
    // only buildings whose shadow can reach the viewport (tiles cover far more than the screen)
    var bb=map.getBounds(), pad=maxLen/111000;
    var w=bb.getWest()-pad, e=bb.getEast()+pad, s=bb.getSouth()-pad, n=bb.getNorth()+pad;
    var vis=buildings.features.filter(function(f){
      var p=f.geometry.coordinates[0]&&f.geometry.coordinates[0][0];
      return p && p[0]>=w && p[0]<=e && p[1]>=s && p[1]<=n;
    });
    var useExact=!!exact && vis.length<=EXACT_MAX;
    vis.forEach(function(f){
      var h=f.properties.h||15, len=Math.min(h*perM, maxLen);
      var ring=f.geometry.coordinates[0]; if(!ring||ring.length<4) return;
      var mLng=111320*Math.cos(ring[0][1]*Math.PI/180), mLat=110540;
      var mp=sweep(ring, ex*len/mLng, ny*len/mLat, useExact);
      mp.forEach(function(poly){ if(poly&&poly[0]&&poly[0].length>=3) out.push({polygon:poly, a:alpha}); });
    });
    window.__sunDbg={n:out.length, alt:pos.altitude*180/Math.PI, az:((pos.azimuth*180/Math.PI)+180+360)%360, b:buildings.features.length, vis:vis.length, exact:useExact};
    return out;
  }
  function shadowLayer(exact){
    return new deck.PolygonLayer({id:'shadows', data:shadowPolys(exact), getPolygon:function(d){return d.polygon;},
      filled:true, stroked:false, extruded:false, getFillColor:function(d){return [16,24,40,d.a];},
      parameters:{depthTest:false}});
  }
  var refineTimer=null;
  function render(exact){
    overlay.setProps({layers:[shadowLayer(exact), buildingLayer(), pinLayer()].filter(Boolean)});
    clearTimeout(refineTimer);
    if(!exact) refineTimer=setTimeout(function(){ render(true); }, 250);
  }

  // ---- OSM buildings via Overpass for the current view ----
  var fetchTimer=null, lastKey='';
  function metersToH(t){
    if(t.height){ var m=parseFloat(t.height); if(!isNaN(m)){ if(/ft/i.test(t.height)) m*=0.3048; return {h:m,src:'height'}; } }
    if(t['building:levels']){ var l=parseFloat(t['building:levels']); if(!isNaN(l)) return {h:l*3.0,src:'levels'}; }
    var b=t.building||'yes';   // Singapore defaults: HDB and condo blocks are tall, landed is low
    if(/^(apartments|residential|dormitory)$/.test(b)) return {h:36,src:'est'};
    if(/^(house|detached|semidetached_house|terrace|bungalow)$/.test(b)) return {h:8,src:'est'};
    if(/^(commercial|office|hotel)$/.test(b)) return {h:30,src:'est'};
    if(/^(industrial|warehouse|retail|school|hospital)$/.test(b)) return {h:12,src:'est'};
    return {h:15,src:'est'};
  }
  // Buildings come straight from the map's own vector tiles (OpenMapTiles 'building'
  // layer): no external API at runtime, so it works whenever the map does. Heights use
  // OSM render_height where recorded; otherwise a Singapore-typical 30 m block is assumed.
  function loadBuildings(){
    if(map.getZoom() < 14.5){ buildings={type:'FeatureCollection',features:[]}; render();
      setHint('Zoom in to load buildings and shadows.'); return; }
    var c=map.getCenter(), key=c.lat.toFixed(3)+','+c.lng.toFixed(3)+','+map.getZoom().toFixed(1);
    if(key===lastKey) return;
    var sources=map.getStyle().sources, srcId=null;
    for(var s in sources){ if(sources[s].type==='vector'){ srcId=s; break; } }
    if(!srcId) return;
    var feats=map.querySourceFeatures(srcId,{sourceLayer:'building'});
    if(!feats.length) return;                        // tiles not in yet; the next idle retries
    lastKey=key;
    var seen={}, out=[], known=0;
    feats.forEach(function(f){
      var g=f.geometry; if(!g) return;
      var polys=g.type==='Polygon'?[g.coordinates]:(g.type==='MultiPolygon'?g.coordinates:[]);
      var p=f.properties||{}, rh=parseFloat(p.render_height), h, src;
      if(!isNaN(rh)&&rh>0){ h=rh; src='tile'; known++; } else { h=30; src='est'; }
      polys.forEach(function(coords){
        if(!coords||!coords[0]||coords[0].length<4) return;
        var id=(f.id!=null?f.id:'')+':'+coords[0][0][0].toFixed(6)+','+coords[0][0][1].toFixed(6);
        if(seen[id]) return; seen[id]=1;
        out.push({type:'Feature',properties:{h:h,src:src},geometry:{type:'Polygon',coordinates:coords}});
      });
    });
    buildings={type:'FeatureCollection',features:out}; render();
    setHint('');
  }
  map.on('idle', loadBuildings);                     // fires once the view's tiles are all in
  map.on('moveend',function(){ clearTimeout(fetchTimer); fetchTimer=setTimeout(loadBuildings, 300); });

  // ---- time model: selected date + minutes-of-day (SGT) -> epoch ms ----
  var curDate = new Date();
  function selectedTsMs(){
    var mins = parseInt(document.getElementById('time').value,10);
    // build a UTC instant for the chosen SGT wall-clock time on curDate
    var y=curDate.getFullYear(), mo=curDate.getMonth(), da=curDate.getDate();
    var utcMins = mins - TZ_OFFSET;
    return Date.UTC(y,mo,da,0,0,0) + utcMins*60000;
  }
  function fmtHM(mins){ var h=Math.floor(mins/60), m=mins%60; return (h<10?'0':'')+h+':'+(m<10?'0':'')+m; }
  function fmtAMPM(mins){ var h=Math.floor(mins/60), m=mins%60; var ap=h<12?'am':'pm'; var h12=((h+11)%12)+1; return h12+':'+(m<10?'0':'')+m+' '+ap; }
  function compass(azRad){ var d=(azRad*180/Math.PI+180)%360; var dirs=['N','NE','E','SE','S','SW','W','NW']; return dirs[Math.round(d/45)%8]; }

  function setHint(text){ var h=document.getElementById('hint'); if(!h) return; h.textContent=text||''; h.hidden=!text; }

  function updateSun(){
    var mins=parseInt(document.getElementById('time').value,10);
    document.getElementById('tclock').textContent=fmtHM(mins);
    document.getElementById('tampm').textContent=fmtAMPM(mins);
    var ts=selectedTsMs();
    sun.timestamp=ts; map.triggerRepaint();
    // sun readout for the pin if placed, else the map centre
    var c=pin?{lat:pin[1],lng:pin[0]}:map.getCenter();
    var pos=SunCalc.getPosition(new Date(ts), c.lat, c.lng);
    var times=SunCalc.getTimes(new Date(selectedTsMsForNoon()), c.lat, c.lng);
    var altDeg=pos.altitude*180/Math.PI;
    document.getElementById('alt').textContent= altDeg<0 ? 'Below horizon' : altDeg.toFixed(0)+'°';
    document.getElementById('az').textContent= altDeg<0 ? 'Night' : compass(pos.azimuth)+' '+Math.round(((pos.azimuth*180/Math.PI)+180+360)%360)+'°';
    document.getElementById('sr').textContent=sgtClock(times.sunrise);
    document.getElementById('ss').textContent=sgtClock(times.sunset);
    render();   // recompute shadow footprints for the new sun position
  }
  function selectedTsMsForNoon(){ var y=curDate.getFullYear(),mo=curDate.getMonth(),da=curDate.getDate(); return Date.UTC(y,mo,da,0,0,0)+(12*60-TZ_OFFSET)*60000; }
  function sgtClock(d){ if(!d||isNaN(d)) return '—'; var m=Math.round((d.getTime()-Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),d.getUTCDate())) /60000)+TZ_OFFSET; m=((m%1440)+1440)%1440; return fmtHM(m); }
  function sgtMinutesOfDay(d){ if(!d||isNaN(d)) return null; var m=Math.round((d.getTime()-Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),d.getUTCDate()))/60000)+TZ_OFFSET; return ((m%1440)+1440)%1440; }

  // ---- controls ----
  var timeEl=document.getElementById('time');
  timeEl.addEventListener('input',updateSun);
  var monthEl=document.getElementById('month');
  function setMonth(m){ monthEl.value=m; document.getElementById('mlab').textContent=MONTHS[m-1]; curDate=new Date(new Date().getFullYear(), m-1, 15); }
  setMonth(new Date().getMonth()+1);
  monthEl.addEventListener('input',function(){ setMonth(parseInt(monthEl.value,10)); updateSun(); });
  document.getElementById('now').addEventListener('click',function(){
    var n=new Date(); setMonth(n.getMonth()+1);
    // now in SGT minutes
    var sgtNowMin=(Math.floor(n.getTime()/60000)+TZ_OFFSET)%1440;
    timeEl.value=((sgtNowMin%1440)+1440)%1440; updateSun();
  });

  // play through the day
  var playing=null;
  document.getElementById('play').addEventListener('click',function(){
    var btn=this;
    if(playing){ clearInterval(playing); playing=null; btn.textContent='▶ Play day'; return; }
    btn.textContent='❚❚ Pause';
    playing=setInterval(function(){
      var v=(parseInt(timeEl.value,10)+10); if(v>1439){ v=300; } // loop 5am
      timeEl.value=v; updateSun();
    },160);
  });

  // ==================================================================
  // Lead tool layer: CTA strip, WhatsApp links, sun summary, FAQ toggle
  // ==================================================================
  var state = { address:null, lat:null, lng:null, hasResult:false, firstSearchPulsed:false };

  function escapeHtml(s){
    return String(s).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function waMessage(kind){
    if(kind==='generic'){
      return "Hi Winfred, I found you on the Sun Facing Checker and would like a free valuation report.";
    }
    var label = state.address ? state.address : 'this address';
    var coordSuffix = state.address ? '' : ' ('+state.lat.toFixed(5)+', '+state.lng.toFixed(5)+')';
    return "Hi Winfred, I checked the sun facing for "+label+coordSuffix+" on your Sun Facing Checker and would like a free valuation report on it.";
  }
  function waLink(kind){ return 'https://wa.me/6581618149?text='+encodeURIComponent(waMessage(kind)); }
  function updateWaLinks(url){
    var f=document.getElementById('wa-float'); if(f) f.href=url;
    var m=document.getElementById('wa-mini'); if(m) m.href=url;
  }
  function pulseWaOnce(){
    if(state.firstSearchPulsed) return;
    state.firstSearchPulsed=true;
    ['wa-float','wa-mini'].forEach(function(id){
      var el=document.getElementById(id); if(!el) return;
      el.classList.add('wa-pulsing');
      setTimeout(function(){ el.classList.remove('wa-pulsing'); }, 2200);
    });
  }
  function renderCTA(){
    var el=document.getElementById('cta'); if(!el) return;
    var kind = state.hasResult ? 'valuation' : 'generic';
    var link = waLink(kind);
    if(!state.hasResult){
      el.innerHTML =
        '<p>Buying or selling a property? Message Winfred, a property agent you can trust, for a free valuation report.</p>'+
        '<a class="btn" id="ctaBtn" href="'+link+'" target="_blank" rel="noopener">WhatsApp Winfred</a>'+
        '<a class="calllink" href="https://calendly.com/winfredquekoc" target="_blank" rel="noopener">Or book a free 30 minute call</a>';
    } else {
      var label = state.address ? state.address : 'this address';
      el.innerHTML =
        '<p>Thinking of buying or selling at '+escapeHtml(label)+'? Message Winfred, a property agent you can trust, for a free valuation report on it.</p>'+
        '<a class="btn" id="ctaBtn" href="'+link+'" target="_blank" rel="noopener">Get free valuation report</a>'+
        '<a class="calllink" href="https://calendly.com/winfredquekoc" target="_blank" rel="noopener">Or book a free 30 minute call</a>';
    }
    updateWaLinks(link);
    var btn=document.getElementById('ctaBtn');
    if(btn) btn.addEventListener('click', function(){
      try{
        navigator.sendBeacon('/api/sun-facing', JSON.stringify({
          kind:'valuation_click', address:state.address, lat:state.lat, lng:state.lng,
          month: parseInt(monthEl.value,10), time: parseInt(timeEl.value,10)
        }));
      }catch(e){}
    });
  }
  function setResult(addressOrNull, lat, lng, pulseOnce){
    state.address=addressOrNull; state.lat=lat; state.lng=lng; state.hasResult=true;
    renderCTA();
    if(pulseOnce) pulseWaOnce();
  }

  // ---- geocode (OneMap, free, SG-specific) ----
  function geocode(){
    var q=document.getElementById('q').value.trim(); if(!q) return;
    fetch('https://www.onemap.gov.sg/api/common/elastic/search?searchVal='+encodeURIComponent(q)+'&returnGeom=Y&getAddrDetails=Y&pageNum=1')
      .then(function(r){return r.json();})
      .then(function(d){
        if(d.results&&d.results.length){
          var r0=d.results[0], lat=parseFloat(r0.LATITUDE), lng=parseFloat(r0.LONGITUDE);
          pin=[lng,lat]; map.flyTo({center:[lng,lat],zoom:16.5,pitch:55}); render();
          setHint(r0.ADDRESS||q);
          setResult(r0.ADDRESS||q, lat, lng, true);
        } else { setHint('No match found for "'+q+'". Try a postal code or block + street.'); }
      })
      .catch(function(){ setHint('Address search is unavailable right now; drag the map instead.'); });
  }
  document.getElementById('go').addEventListener('click',geocode);
  document.getElementById('q').addEventListener('keydown',function(e){ if(e.key==='Enter') geocode(); });

  // click to drop a pin
  map.on('click',function(e){
    pin=[e.lngLat.lng,e.lngLat.lat]; render();
    setResult(null, e.lngLat.lat, e.lngLat.lng, false);
  });

  // ---- sun summary: 12 month sun and shadow profile for the pinned point ----
  function pointInRing(pt, ring){
    var x=pt[0], y=pt[1], inside=false;
    for(var i=0,j=ring.length-1;i<ring.length;j=i++){
      var xi=ring[i][0], yi=ring[i][1], xj=ring[j][0], yj=ring[j][1];
      var intersect=((yi>y)!==(yj>y)) && (x < (xj-xi)*(y-yi)/(yj-yi)+xi);
      if(intersect) inside=!inside;
    }
    return inside;
  }
  function findContainingBuildingIndex(pt, feats){
    for(var i=0;i<feats.length;i++){
      var ring=feats[i].geometry.coordinates[0];
      if(ring && ring.length>=4 && pointInRing(pt, ring)) return i;
    }
    return -1;
  }
  function nearbyBuildings(pt, feats, excludeIdx, maxLenM){
    var mLng=111320*Math.cos(pt[1]*Math.PI/180), mLat=110540;
    var padLng=(maxLenM+120)/mLng, padLat=(maxLenM+120)/mLat; // +120m building size buffer
    var out=[];
    for(var i=0;i<feats.length;i++){
      if(i===excludeIdx) continue;
      var ring=feats[i].geometry.coordinates[0]; if(!ring||ring.length<4) continue;
      var p0=ring[0];
      if(Math.abs(p0[0]-pt[0])>padLng || Math.abs(p0[1]-pt[1])>padLat) continue;
      // centroid + radius in metres relative to the pin, so each sample can reject
      // buildings whose shadow cannot reach the pin without building a hull
      var sx=0, sy=0, n=ring.length-1, k;
      for(k=0;k<n;k++){ sx+=(ring[k][0]-pt[0])*mLng; sy+=(ring[k][1]-pt[1])*mLat; }
      var cx=sx/n, cy=sy/n, r=0;
      for(k=0;k<n;k++){ var ddx=(ring[k][0]-pt[0])*mLng-cx, ddy=(ring[k][1]-pt[1])*mLat-cy; r=Math.max(r, Math.sqrt(ddx*ddx+ddy*ddy)); }
      out.push({f:feats[i], cx:cx, cy:cy, r:r});
    }
    return out;
  }
  // fast hull mode only (never the exact union) — this runs ~300 times per summary,
  // and the reach/direction pre-check below skips >99% of buildings before any hull
  function pinShaded(tsMs, pt, candidates){
    var pos=SunCalc.getPosition(new Date(tsMs), pt[1], pt[0]);
    if(pos.altitude<=0.03) return {shaded:false, altitude:pos.altitude, azimuth:pos.azimuth};
    var perM=1/Math.tan(pos.altitude);
    var ex=Math.sin(pos.azimuth), ny=Math.cos(pos.azimuth);
    var low=pos.altitude<0.21, maxLen=low?900:1400;
    var mLng=111320*Math.cos(pt[1]*Math.PI/180), mLat=110540;
    for(var i=0;i<candidates.length;i++){
      var c=candidates[i], f=c.f, h=f.properties.h||15, len=Math.min(h*perM, maxLen);
      // pin relative to the building centroid, projected along the shadow direction
      var px=-c.cx, py=-c.cy, t=px*ex+py*ny, u=Math.abs(px*ny-py*ex);
      if(t<-c.r || t>len+c.r || u>c.r) continue;
      var ring=f.geometry.coordinates[0]; if(!ring||ring.length<4) continue;
      var dlng=ex*len/mLng, dlat=ny*len/mLat;
      var swept=sweep(ring, dlng, dlat, false);
      var hullRing = swept && swept[0] && swept[0][0];
      if(hullRing && hullRing.length>=3 && pointInRing(pt, hullRing)) return {shaded:true, altitude:pos.altitude, azimuth:pos.azimuth};
    }
    return {shaded:false, altitude:pos.altitude, azimuth:pos.azimuth};
  }
  function computeMonthRow(monthNum, pt, candidates, buildingsLoaded){
    var year=new Date().getFullYear();
    var noonUtcMs = Date.UTC(year, monthNum-1, 15, 0,0,0) + (12*60-TZ_OFFSET)*60000;
    var times = SunCalc.getTimes(new Date(noonUtcMs), pt[1], pt[0]);
    var noonPos = SunCalc.getPosition(new Date(noonUtcMs), pt[1], pt[0]);
    var noonCompassDeg = ((noonPos.azimuth*180/Math.PI)+180+360)%360;
    var side = (noonCompassDeg<90 || noonCompassDeg>270) ? 'north' : 'south';

    function tsAt(hh,mm){ return Date.UTC(year,monthNum-1,15,0,0,0) + (hh*60+mm-TZ_OFFSET)*60000; }
    var morningPos = SunCalc.getPosition(new Date(tsAt(9,0)), pt[1], pt[0]);
    var afternoonPos = SunCalc.getPosition(new Date(tsAt(16,0)), pt[1], pt[0]);
    var morningDir = morningPos.altitude>0 ? compass(morningPos.azimuth) : '—';
    var afternoonDir = afternoonPos.altitude>0 ? compass(afternoonPos.azimuth) : '—';

    var shadedCount=0, westCount=0;
    if(buildingsLoaded){
      var sunriseMin=sgtMinutesOfDay(times.sunrise), sunsetMin=sgtMinutesOfDay(times.sunset);
      if(sunriseMin!=null && sunsetMin!=null){
        sunriseMin=Math.ceil(sunriseMin/30)*30; sunsetMin=Math.floor(sunsetMin/30)*30;
        for(var m=sunriseMin; m<=sunsetMin; m+=30){
          var ts=Date.UTC(year,monthNum-1,15,0,0,0)+(m-TZ_OFFSET)*60000;
          var r=pinShaded(ts, pt, candidates);
          if(r.shaded) shadedCount++;
          else if(m>=840){ // 14:00 SGT onward
            var compassDeg=((r.azimuth*180/Math.PI)+180+360)%360;
            var westDist=Math.min(Math.abs(compassDeg-270), 360-Math.abs(compassDeg-270));
            var altDeg=r.altitude*180/Math.PI;
            if(westDist<=45 && altDeg>5) westCount++;
          }
        }
      }
    }
    return {
      month:monthNum, sunrise:sgtClock(times.sunrise), sunset:sgtClock(times.sunset), side:side,
      morningDir:morningDir, afternoonDir:afternoonDir,
      westHours: buildingsLoaded? +(westCount*0.5).toFixed(1) : null,
      shadedHours: buildingsLoaded? +(shadedCount*0.5).toFixed(1) : null
    };
  }
  function longestRun(vals, predicate){
    var best=null, bestLen=0;
    for(var start=0; start<12; start++){
      if(!predicate(vals[start])) continue;
      var len=0, i=start;
      while(len<12 && predicate(vals[i%12])){ len++; i++; }
      if(len>bestLen){ bestLen=len; best={start:start, len:len}; }
    }
    return best;
  }
  function rangeLabel(run){
    if(!run) return null;
    var s=MONTHS[run.start], e=MONTHS[(run.start+run.len-1)%12];
    return run.len===1 ? s : s+' to '+e;
  }
  function renderSummaryCard(rows, buildingsLoaded, pt){
    var addrLabel = state.address || 'this address';
    var sides = rows.map(function(r){return r.side;});
    var northRun = longestRun(sides, function(v){return v==='north';});
    var southRun = longestRun(sides, function(v){return v==='south';});
    var s1parts=[];
    if(northRun) s1parts.push('north of '+addrLabel+' from '+rangeLabel(northRun));
    if(southRun) s1parts.push('south from '+rangeLabel(southRun));
    var sentence1 = s1parts.length ? 'The sun passes '+s1parts.join(' and ')+'.' : 'The sun position could not be determined for this point.';

    var sentence2, sentence3;
    if(!buildingsLoaded){
      sentence2 = 'Buildings were not loaded for this view, so afternoon west sun hours could not be calculated. Drag the map over the address to load buildings, then run the summary again.';
      sentence3 = 'Shaded hours from neighbouring buildings could not be calculated for the same reason.';
    } else {
      var westVals = rows.map(function(r){return r.westHours||0;});
      var maxW=Math.max.apply(null, westVals), minW=Math.min.apply(null, westVals);
      var maxRow=rows[westVals.indexOf(maxW)], minRow=rows[westVals.indexOf(minW)];
      sentence2 = 'Afternoon west sun reaches this spot for about '+maxW+' hours a day in '+MONTHS[maxRow.month-1]+' and '+minW+' hours in '+MONTHS[minRow.month-1]+'.';

      var shadedVals = rows.map(function(r){return r.shadedHours||0;});
      var maxS=Math.max.apply(null, shadedVals);
      if(maxS<=0){
        sentence3 = 'Neighbouring buildings do not appear to shade this spot at any time of year, based on the buildings currently loaded.';
      } else {
        var runS = longestRun(shadedVals, function(v){return v>=maxS-0.01;});
        var sum=0, cnt=0;
        for(var i=0;i<runS.len;i++){ sum+=shadedVals[(runS.start+i)%12]; cnt++; }
        var avg=+(sum/cnt).toFixed(1);
        sentence3 = 'Neighbouring buildings shade this spot for about '+avg+' hours a day in '+rangeLabel(runS)+'.';
      }
    }
    var noteLine = 'Shadows use OpenStreetMap building heights. Where a height is not recorded a typical block is assumed, so treat these figures as a strong guide, not a survey.';

    var html = '<div class="summarysentences">';
    html += '<p>'+escapeHtml(sentence1)+'</p>';
    html += '<p>'+escapeHtml(sentence2)+'</p>';
    html += '<p>'+escapeHtml(sentence3)+'</p>';
    html += '<p class="summarymsg">'+escapeHtml(noteLine)+'</p>';
    html += '</div>';
    html += '<div style="overflow-x:auto"><table><thead><tr><th>Month</th><th>Sunrise</th><th>Sunset</th><th>Sun side</th><th>West sun hrs</th><th>Shaded hrs</th></tr></thead><tbody>';
    rows.forEach(function(r){
      html += '<tr><td>'+MONTHS[r.month-1]+'</td><td>'+r.sunrise+'</td><td>'+r.sunset+'</td><td>'+r.side+'</td><td>'+(r.westHours==null?'—':r.westHours)+'</td><td>'+(r.shadedHours==null?'—':r.shadedHours)+'</td></tr>';
    });
    html += '</tbody></table></div>';
    html += '<form id="summaryEmailForm" class="summaryemail" autocomplete="off">'+
      '<input type="email" id="summaryEmail" placeholder="Your email" required>'+
      '<input type="text" name="website" id="summaryHoneypot" class="hp" tabindex="-1" autocomplete="off">'+
      '<button type="submit" class="btn">Email me this summary</button>'+
      '</form>'+
      '<p class="summarymsg" id="summaryEmailStatus"></p>';

    var card=document.getElementById('summaryCard');
    card.innerHTML = html; card.hidden=false;
    if(window.matchMedia && window.matchMedia('(max-width:640px)').matches && panelEl){
      sheetState='expanded';
      panelEl.classList.remove('collapsed'); panelEl.classList.add('expanded');
      setTimeout(function(){ card.scrollIntoView({behavior:'smooth', block:'nearest'}); }, 50);
    }

    document.getElementById('summaryEmailForm').addEventListener('submit', function(ev){
      ev.preventDefault();
      var formBtn=this.querySelector('button[type=submit]');
      if(formBtn.disabled) return;
      formBtn.disabled=true; formBtn.textContent='Sending…';
      var statusEl=document.getElementById('summaryEmailStatus');
      statusEl.textContent='';
      var email=document.getElementById('summaryEmail').value.trim();
      var honeypot=document.getElementById('summaryHoneypot').value;
      fetch('/api/sun-facing', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          kind:'summary_email', email:email, address: state.address, lat: pt[1], lng: pt[0],
          summary:{months:rows, sentences:[sentence1,sentence2,sentence3]},
          source:'sun-facing-checker', website: honeypot
        })
      }).then(function(res){
        if(!res.ok) throw new Error('bad status');
        statusEl.textContent='Sent, check your inbox.';
        formBtn.textContent='Email me this summary'; formBtn.disabled=false;
      }).catch(function(){
        statusEl.textContent='Could not send right now, please try again.';
        formBtn.textContent='Email me this summary'; formBtn.disabled=false;
      });
    });
  }
  var BUILD_WAIT_MS=8000, BUILD_POLL_MS=300;
  function runSummary(){
    var btn=document.getElementById('summaryBtn');
    if(btn.disabled) return;
    if(state.lat==null || state.lng==null){
      var card=document.getElementById('summaryCard');
      card.innerHTML='<p>Search an address or tap the map to set a spot first.</p>';
      card.hidden=false;
      return;
    }
    var pt=[state.lng, state.lat];
    btn.disabled=true;
    function compute(){
      var feats = buildings.features;
      var buildingsLoaded = feats.length>0;
      var containingIdx = buildingsLoaded ? findContainingBuildingIndex(pt, feats) : -1;
      var candidates = buildingsLoaded ? nearbyBuildings(pt, feats, containingIdx, 1400) : [];
      btn.textContent='Working…';
      var rows=[], mIdx=1;
      function step(){
        if(mIdx>12){ finish(); return; }
        rows.push(computeMonthRow(mIdx, pt, candidates, buildingsLoaded));
        mIdx++;
        setTimeout(step,0);
      }
      function finish(){
        btn.textContent='Sun summary'; btn.disabled=false;
        renderSummaryCard(rows, buildingsLoaded, pt);
      }
      step();
    }
    function waitForBuildings(deadline){
      if(buildings.features.length>0 || Date.now()>=deadline){ compute(); return; }
      setTimeout(function(){ waitForBuildings(deadline); }, BUILD_POLL_MS);
    }
    btn.textContent='Loading buildings…';
    if(map.getZoom() < 14.5){
      map.flyTo({center:pt, zoom:16.5});
    }
    if(buildings.features.length>0 && map.getZoom() >= 14.5){ compute(); }
    else { waitForBuildings(Date.now()+BUILD_WAIT_MS); }
  }
  document.getElementById('summaryBtn').addEventListener('click', runSummary);

  // ---- mobile bottom sheet ----
  var panelEl=document.getElementById('panel'), sheetState='mid';
  document.getElementById('sheetHandle').addEventListener('click', function(){
    sheetState = sheetState==='expanded' ? 'collapsed' : 'expanded';
    panelEl.classList.remove('collapsed','expanded');
    if(sheetState!=='mid') panelEl.classList.add(sheetState);
  });

  renderCTA();
  map.on('load',function(){ loadBuildings(); updateSun(); });
})();
