/* ---------------------------------------------------------------------------
   track.js — first-party event collection for samuihonestly.com
   ---------------------------------------------------------------------------
   Sends page_view, pdf_download, email_signup, outbound_click and buy_click
   to /e on this same domain.

   Consent model
   -------------
   Storing an id in the visitor's browser to recognise them later needs consent
   under the ePrivacy Directive, whatever the GDPR lawful basis is. So:

     - Before a choice is made, events are held in memory only. Nothing is
       written to storage and nothing is sent.
     - Accept  -> the buffer is flushed and ids start being stored.
     - Decline -> the buffer is dropped and nothing is ever sent. The only
                  thing stored is the decision itself, so the banner does not
                  reappear on every page.

   Manual use:  window.shTrack('pdf_download', { guide: 'area-guide' });
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  var ENDPOINT     = '/e';
  var CONSENT_KEY  = 'sh_consent_v1';
  var VISITOR_KEY  = 'sh_vid';
  var SESSION_KEY  = 'sh_sid';
  var SESSION_SEEN = 'sh_sid_seen';
  var ATTR_KEY     = 'sh_attr';

  /* A session ends after 30 minutes of inactivity — the same rule GA4 uses,
     chosen so these numbers can be compared with anyone else's. */
  var IDLE_MS   = 30 * 60 * 1000;
  var MAX_QUEUE = 20;

  var consent = null;   // 'granted' | 'denied' | null (undecided)
  var queue   = [];     // events waiting for a decision
  var fired   = {};     // once-per-session guard

  /* --- storage ---------------------------------------------------------- */
  /* Private-mode browsers throw on access rather than returning null, so every
     call is wrapped. A visitor whose storage is unavailable simply is not
     tracked, which is the correct outcome rather than a broken page. */

  function get(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function set(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }
  function del(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  function uuid() {
    try {
      if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    } catch (e) { /* fall through */ }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : ((r & 0x3) | 0x8)).toString(16);
    });
  }

  /* --- identity --------------------------------------------------------- */

  function visitorId() {
    var v = get(VISITOR_KEY);
    if (!v) { v = uuid(); set(VISITOR_KEY, v); }
    return v;
  }

  function sessionId() {
    var now = Date.now();
    var sid = get(SESSION_KEY);
    var seen = parseInt(get(SESSION_SEEN) || '0', 10);

    if (!sid || !seen || (now - seen) > IDLE_MS) {
      sid = uuid();
      /* Attribution is frozen once, at session start. Read per event instead
         and an internal link with no UTM would overwrite the TikTok source
         that actually brought the visitor here. */
      set(ATTR_KEY, JSON.stringify(attribution()));
      fired = {};
    }

    set(SESSION_KEY, sid);
    set(SESSION_SEEN, String(now));
    return sid;
  }

  function attribution() {
    var p;
    try { p = new URLSearchParams(window.location.search); }
    catch (e) { p = { get: function () { return null; } }; }

    return {
      utm_source:   p.get('utm_source'),
      utm_medium:   p.get('utm_medium'),
      utm_campaign: p.get('utm_campaign'),
      utm_content:  p.get('utm_content'),
      utm_term:     p.get('utm_term'),
      referrer:     cleanReferrer(document.referrer),
      landing_path: window.location.pathname
    };
  }

  /* Query strings on a referrer can carry someone else's personal data and are
     never useful here, so only origin + path is kept. */
  function cleanReferrer(ref) {
    if (!ref) return null;
    try {
      var u = new URL(ref);
      if (u.hostname === window.location.hostname) return null;   // internal
      return u.origin + u.pathname;
    } catch (e) { return null; }
  }

  function deviceType() {
    var w = window.innerWidth || 0;
    if (w && w < 640) return 'mobile';
    if (w && w < 1024) return 'tablet';
    return 'desktop';
  }

  /* --- sending ---------------------------------------------------------- */

  function track(name, props, once) {
    if (consent === 'denied') return;

    if (once) {
      if (fired[name]) return;
      fired[name] = true;
    }

    var ev = {
      event_id:    uuid(),
      occurred_at: new Date().toISOString(),
      event_name:  name,
      page_path:   window.location.pathname,
      device_type: deviceType(),
      props:       props || {}
    };

    if (consent === 'granted') { dispatch([decorate(ev)]); return; }
    if (queue.length < MAX_QUEUE) queue.push(ev);
  }

  function decorate(ev) {
    ev.session_id = sessionId();
    ev.visitor_id = visitorId();
    try { ev.attribution = JSON.parse(get(ATTR_KEY) || '{}'); }
    catch (e) { ev.attribution = {}; }
    return ev;
  }

  function dispatch(events) {
    var body = JSON.stringify({ events: events });

    /* sendBeacon survives the page being closed — which is exactly when a
       download or an outbound click happens. fetch(keepalive) is the fallback. */
    try {
      if (navigator.sendBeacon) {
        var blob = new Blob([body], { type: 'application/json' });
        if (navigator.sendBeacon(ENDPOINT, blob)) return;
      }
    } catch (e) { /* fall through */ }

    try {
      fetch(ENDPOINT, {
        method: 'POST',
        body: body,
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
        credentials: 'omit'
      }).catch(function () { /* analytics must never surface an error */ });
    } catch (e) { /* ignore */ }
  }

  function flush() {
    if (!queue.length) return;
    var batch = queue.splice(0, MAX_QUEUE).map(decorate);
    dispatch(batch);
  }

  /* --- consent banner --------------------------------------------------- */

  function readConsent() {
    var v = get(CONSENT_KEY);
    return (v === 'granted' || v === 'denied') ? v : null;
  }

  function decide(choice) {
    consent = choice;
    set(CONSENT_KEY, choice);

    if (choice === 'granted') {
      track('consent_granted', {}, true);
      flush();
    } else {
      queue = [];
      del(VISITOR_KEY); del(SESSION_KEY); del(SESSION_SEEN); del(ATTR_KEY);
    }

    var el = document.getElementById('sh-consent');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function banner() {
    if (document.getElementById('sh-consent')) return;

    var box = document.createElement('div');
    box.id = 'sh-consent';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Analytics consent');
    box.style.cssText = [
      'position:fixed', 'left:16px', 'right:16px', 'bottom:16px', 'z-index:9999',
      'max-width:560px', 'margin:0 auto', 'padding:18px 20px',
      'background:#1d2321', 'color:rgba(244,240,232,.92)',
      'border-radius:14px', 'box-shadow:0 10px 40px rgba(0,0,0,.35)',
      'font:400 14px/1.5 "Inter Tight",system-ui,sans-serif'
    ].join(';');

    box.innerHTML =
      '<p style="margin:0 0 14px">I\'d like to store a random id in your browser ' +
      'to see which of my videos actually bring people here. No name, no email, ' +
      'no advertising, never shared. ' +
      '<a href="/privacy" style="color:#e0b872">How it works</a>.</p>' +
      '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
      '<button type="button" data-sh="yes" style="cursor:pointer;border:0;border-radius:999px;' +
      'padding:9px 20px;background:#e0b872;color:#1d2321;font:500 14px/1 inherit">Allow</button>' +
      '<button type="button" data-sh="no" style="cursor:pointer;border:1px solid rgba(244,240,232,.35);' +
      'border-radius:999px;padding:9px 20px;background:transparent;color:inherit;' +
      'font:400 14px/1 inherit">No thanks</button>' +
      '</div>';

    box.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('button[data-sh]');
      if (!b) return;
      decide(b.getAttribute('data-sh') === 'yes' ? 'granted' : 'denied');
    });

    document.body.appendChild(box);
  }

  /* --- wiring ----------------------------------------------------------- */

  function wire() {
    track('page_view', { title: document.title.slice(0, 120) });

    /* thanks.html triggers its download from a script, so no click to catch. */
    if (/^\/thanks/.test(window.location.pathname)) {
      track('pdf_download', { guide: 'koh-samui-local-list', trigger: 'auto' }, true);
    }

    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href]');
      if (a) {
        var href = a.getAttribute('href') || '';

        if (a.hasAttribute('download') || /\.pdf($|\?)/i.test(href)) {
          track('pdf_download', {
            guide: href.split('/').pop().replace(/\.pdf.*$/i, ''),
            trigger: 'click'
          });
          return;
        }

        if (/^https?:\/\//i.test(href) && href.indexOf(window.location.origin) !== 0) {
          var host = '';
          try { host = new URL(href).hostname; } catch (err) { /* ignore */ }
          track('outbound_click', { host: host });
          return;
        }
      }

      var buy = e.target.closest && e.target.closest('.paddle-buy[data-price-id]');
      if (buy) {
        track('buy_click', { price_id: buy.getAttribute('data-price-id') });
      }
    }, true);

    /* The EmailOctopus embed paints its own form into the page. Catching the
       submit is the reliable signal; the observer below is the backstop for
       the case where it posts without a native submit event. */
    var host = document.querySelector('.eo-form');
    if (host) {
      host.addEventListener('submit', function () {
        track('email_signup', { source: 'thanks_page' }, true);
      }, true);

      new MutationObserver(function () {
        if (/thank|success|check your inbox|confirm/i.test(host.textContent || '')) {
          track('email_signup', { source: 'thanks_page', trigger: 'confirmation' }, true);
        }
      }).observe(host, { childList: true, subtree: true });
    }
  }

  /* --- boot ------------------------------------------------------------- */

  window.shTrack = function (name, props) { track(name, props); };

  function boot() {
    consent = readConsent();
    wire();
    if (consent === null) banner();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
