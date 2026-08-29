/* ---------------------------------------------------------------------------
   Paddle checkout — samuihonestly.com
   ---------------------------------------------------------------------------
   Load at the end of <body>, after Paddle.js:

     <script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script>
     <script src="/assets/paddle-checkout.js"></script>

   Markup contract:

     Price (auto-localised, tax included):
       <span class="paddle-price" data-price-id="pri_...">€5</span>
       Whatever you type inside stays visible if Paddle is slow or blocked,
       so the page never shows an empty price.

     Buy button:
       <button class="paddle-buy" data-price-id="pri_...">Get the guide</button>
--------------------------------------------------------------------------- */
(function () {
  'use strict';

  /* --- CONFIG ------------------------------------------------------------ */
  /* Paddle > Developer tools > Authentication > Client-side tokens.
     Safe in public code: it can only read prices and open checkouts. */
  var TOKEN       = 'live_1dde6b801c4262715d4f06350a8';
  var SUCCESS_URL = 'https://samuihonestly.com/thank-you';
  /* ----------------------------------------------------------------------- */

  if (!window.Paddle) {
    console.error('[paddle] paddle.js did not load — buy buttons will not work.');
    return;
  }

  /* Live is Paddle.js's default environment, so no Environment.set() call.
     For sandbox testing, add: Paddle.Environment.set('sandbox'); */
  Paddle.Initialize({ token: TOKEN });

  var idOf = function (el) { return (el.getAttribute('data-price-id') || '').trim(); };

  /* --- 1. Localised prices ----------------------------------------------- */
  /* Paddle returns each price already formatted for the visitor's country and
     currency, tax included. Never do maths on these or re-format them. */
  var labels = [].slice.call(document.querySelectorAll('.paddle-price[data-price-id]'));

  if (labels.length) {
    var unique = [];
    labels.forEach(function (el) {
      var id = idOf(el);
      if (id && unique.indexOf(id) === -1) unique.push(id);
    });

    Paddle.PricePreview({
      items: unique.map(function (id) { return { priceId: id, quantity: 1 }; })
    }).then(function (res) {
      var byId = {};
      res.data.details.lineItems.forEach(function (li) {
        byId[li.price.id] = li.formattedTotals.total;
      });
      labels.forEach(function (el) {
        var t = byId[idOf(el)];
        if (t) el.textContent = t;
      });
      document.documentElement.classList.add('paddle-priced');
    }).catch(function (err) {
      /* Leave the hard-coded euro prices in place. Nothing breaks. */
      console.warn('[paddle] price preview failed, keeping fallback prices', err);
    });
  }

  /* --- 2. Buy buttons ----------------------------------------------------- */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.paddle-buy[data-price-id]');
    if (!btn) return;
    e.preventDefault();

    var id = idOf(btn);
    if (!id) return;

    Paddle.Checkout.open({
      items: [{ priceId: id, quantity: 1 }],
      settings: {
        displayMode:      'overlay',
        variant:          'one-page',
        theme:            'light',
        successUrl:       SUCCESS_URL,
        showAddDiscounts: true,
        allowLogout:      false
      }
    });
  });
})();
