/* Client-side search over the OFAC SDN index.
   Loaded only on /search/. The index itself (search-index.js) sets
   window.__SDN_INDEX__. Pure vanilla, no dependencies, sub-50ms over 19k
   records on a mid-range laptop. */

(function(){
  "use strict";
  var INDEX = window.__SDN_INDEX__ || [];
  var PER_PAGE = 30;
  var currentResults = [];
  var currentPage = 0;

  // build a flat searchable string per record once
  var docs = INDEX.map(function(d){
    var hay = (d.n + " " + (d.a||[]).join(" ") + " " + d.t + " " + (d.p||[]).join(" ") + " uid " + d.i).toLowerCase();
    return { u:d.u, n:d.n, t:d.t, p:d.p, a:d.a, i:d.i, hay:hay };
  });

  function q(str){
    str = (str||"").trim().toLowerCase();
    if(!str) return [];
    var terms = str.split(/\s+/);
    var scored = [];
    for(var k=0;k<docs.length;k++){
      var d = docs[k]; var score = 0;
      // exact-name hit dominates (people paste full names)
      if(d.n.toLowerCase() === str) score += 1000;
      else if(d.n.toLowerCase().indexOf(str) === 0) score += 300;
      // alias exact
      if(d.a){
        for(var j=0;j<d.a.length;j++){
          if(d.a[j].toLowerCase() === str){ score += 500; break; }
        }
      }
      // uid exact match
      if(String(d.i) === str) score += 2000;
      // term frequency in the haystack
      var allHit = true;
      for(var ti=0;ti<terms.length;ti++){
        var t = terms[ti];
        var idx = d.hay.indexOf(t);
        if(idx === -1){ allHit = false; break; }
        // position-weight: hits in the name count more
        score += (d.n.toLowerCase().indexOf(t) !== -1) ? 50 : 8;
      }
      if(allHit || score >= 50) scored.push({ d:d, s:score });
    }
    scored.sort(function(a,b){ return b.s - a.s; });
    return scored.map(function(x){ return x.d; });
  }

  function applyFilters(results, type, prog){
    if(type) results = results.filter(function(d){ return d.t === type; });
    if(prog) results = results.filter(function(d){ return (d.p||[]).indexOf(prog) !== -1; });
    return results;
  }

  function renderResults(){
    var ul = document.getElementById("results");
    var head = document.getElementById("result-head");
    var count = document.getElementById("result-count");
    var pager = document.getElementById("pager");
    if(!currentResults.length){
      ul.innerHTML = "";
      head.textContent = "No matches";
      count.textContent = "Try a shorter or different query.";
      pager.innerHTML = "";
      return;
    }
    head.textContent = currentResults.length + (currentResults.length===1?" match":" matches");
    var start = currentPage * PER_PAGE;
    var slice = currentResults.slice(start, start + PER_PAGE);
    count.textContent = "Showing " + (start+1) + "–" + Math.min(start+PER_PAGE, currentResults.length)
                      + " of " + currentResults.length;
    ul.innerHTML = slice.map(function(d){
      var alias = (d.a && d.a.length) ? " · "+d.a.length+" alias"+(d.a.length>1?"es":"") : "";
      var progs = (d.p&&d.p.length) ? " · "+d.p.slice(0,2).join(", ") : "";
      return '<li><a class="row-name" href="'+d.u+'">'+esc(d.n)+'</a>'
           + '<span class="row-meta">'+esc(d.t)+progs+' · UID '+d.i+alias+'</span></li>';
    }).join("");
    renderPager();
  }

  function renderPager(){
    var pager = document.getElementById("pager");
    var pages = Math.ceil(currentResults.length / PER_PAGE);
    if(pages <= 1){ pager.innerHTML = ""; return; }
    var html = [];
    html.push('<button '+(currentPage===0?'disabled':'')+' onclick="window.__SDN_PAGE('+(currentPage-1)+')">‹ Prev</button>');
    var from = Math.max(0, currentPage-3), to = Math.min(pages, currentPage+4);
    for(var i=from;i<to;i++){
      html.push('<button class="'+(i===currentPage?'active':'')+'" onclick="window.__SDN_PAGE('+i+')">'+(i+1)+'</button>');
    }
    html.push('<button '+(currentPage>=pages-1?'disabled':'')+' onclick="window.__SDN_PAGE('+(currentPage+1)+')">Next ›</button>');
    pager.innerHTML = html.join("");
  }

  function esc(s){ return (s||"").replace(/[&<>"']/g, function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }

  // expose for inline handlers
  window.__SDN_PAGE = function(p){ currentPage = p; renderResults(); window.scrollTo({top:0,behavior:"smooth"}); };

  window.doSearch = function(e){
    if(e) e.preventDefault();
    var qEl = document.getElementById("q");
    var typeEl = document.getElementById("ftype");
    var progEl = document.getElementById("fprog");
    // also honor ?q= / ?type= / ?program= in the URL on first load
    var qs = new URLSearchParams(location.search);
    if(qEl && !qEl.value && qs.get("q")){ qEl.value = qs.get("q"); }
    var type = (typeEl && typeEl.value) || qs.get("type") || "";
    var prog = (progEl && progEl.value) || qs.get("program") || "";
    var query = qEl ? qEl.value : "";
    currentResults = applyFilters(q(query), type, prog);
    currentPage = 0;
    renderResults();
    return false;
  };

  // populate program filter from the data
  function populateFilters(){
    var progEl = document.getElementById("fprog");
    if(!progEl) return;
    var set = {};
    for(var i=0;i<INDEX.length;i++){
      var p = INDEX[i].p||[];
      for(var j=0;j<p.length;j++) set[p[j]] = (set[p[j]]||0)+1;
    }
    var arr = Object.keys(set).sort(function(a,b){ return set[b]-set[a]; });
    var html = '<option value="">All programs</option>';
    for(var k=0;k<arr.length;k++){
      html += '<option value="'+arr[k]+'">'+arr[k].replace(/-/g," ")+" ("+set[arr[k]]+")</option>";
    }
    progEl.innerHTML = html;
  }

  document.addEventListener("DOMContentLoaded", function(){
    populateFilters();
    // auto-run if query in URL
    if(location.search){ window.doSearch(); }
    // wire filter changes
    var f = document.getElementById("ftype"), p = document.getElementById("fprog");
    if(f) f.addEventListener("change", window.doSearch);
    if(p) p.addEventListener("change", window.doSearch);
  });
})();
