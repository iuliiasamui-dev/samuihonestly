/* ---------------------------------------------------------------------------
   samuihonestly — event collector (Phase 3)
   ---------------------------------------------------------------------------
   Runs on the same Worker that serves the site, so events are posted
   same-origin: no CORS, no third-party domain, nothing for an ad-blocker to
   recognise and drop.

   Routing: static assets are matched first by the platform. Only requests that
   match no file reach this code. /e is the collector; everything else is
   handed back to the asset server so the site behaves exactly as before.
--------------------------------------------------------------------------- */

const MAX_BODY_BYTES = 32 * 1024;
const MAX_BATCH = 20;

/* An allow-list, not a block-list. A typo in a page's tracking call should
   show up as a reject you can see, not as a new event name nobody defined. */
const ALLOWED_EVENTS = new Set([
  'page_view',
  'pdf_download',
  'email_signup',
  'outbound_click',
  'buy_click',
  'consent_granted'
]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/e') {
      if (request.method === 'POST') return collect(request, env);
      if (request.method === 'GET' || request.method === 'HEAD') {
        return json({ ok: true, service: 'collector' });
      }
      return new Response('Method Not Allowed', {
        status: 405,
        headers: { Allow: 'POST, GET' }
      });
    }

    return env.ASSETS.fetch(request);
  }
};

async function collect(request, env) {
  const raw = await request.text();

  if (raw.length > MAX_BODY_BYTES) {
    return json({ error: 'payload too large' }, 413);
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    await reject(env, 'invalid_json', raw.slice(0, 2000));
    return json({ error: 'invalid json' }, 400);
  }

  const events = Array.isArray(payload && payload.events)
    ? payload.events
    : [payload];

  if (!events.length) return new Response(null, { status: 204 });
  if (events.length > MAX_BATCH) {
    return json({ error: 'batch too large' }, 413);
  }

  /* Server-side enrichment. One timestamp for the whole batch so a batch is
     obviously a batch when you look at the raw table. */
  const cf = request.cf || {};
  const receivedAt = new Date().toISOString();
  const ingestDay = receivedAt.slice(0, 10);
  const country = typeof cf.country === 'string' ? cf.country.slice(0, 2) : null;

  const insert = env.DB.prepare(
    `INSERT OR IGNORE INTO raw_events (
       event_id, received_at, occurred_at, ingest_day, event_name,
       visitor_id, session_id, page_path, landing_path, referrer,
       utm_source, utm_medium, utm_campaign, utm_content, utm_term,
       country, device_type, props
     ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18)`
  );

  const rejectInsert = env.DB.prepare(
    `INSERT INTO raw_event_rejects (rejected_at, reason, payload) VALUES (?1,?2,?3)`
  );

  const statements = [];

  for (const ev of events) {
    const problem = validate(ev);
    if (problem) {
      statements.push(
        rejectInsert.bind(receivedAt, problem, JSON.stringify(ev).slice(0, 2000))
      );
      continue;
    }

    const attr = (ev && ev.attribution) || {};

    statements.push(
      insert.bind(
        str(ev.event_id, 64),
        receivedAt,
        str(ev.occurred_at, 32),
        ingestDay,
        str(ev.event_name, 40),
        str(ev.visitor_id, 64),
        str(ev.session_id, 64),
        str(ev.page_path, 300),
        str(attr.landing_path, 300),
        str(attr.referrer, 500),
        str(attr.utm_source, 100),
        str(attr.utm_medium, 100),
        str(attr.utm_campaign, 150),
        str(attr.utm_content, 150),
        str(attr.utm_term, 150),
        country,
        str(ev.device_type, 16),
        ev.props ? JSON.stringify(ev.props).slice(0, 2000) : null
      )
    );
  }

  try {
    if (statements.length) await env.DB.batch(statements);
  } catch (err) {
    console.error('[collector] write failed', err && err.message);
    return json({ error: 'write failed' }, 500);
  }

  return new Response(null, { status: 204 });
}

/* Returns null when the event is fine, otherwise a short reason string that
   lands in raw_event_rejects. Reasons are deliberately coarse — they are for
   spotting a broken deploy, not for debugging one visitor. */
function validate(ev) {
  if (!ev || typeof ev !== 'object') return 'not_an_object';
  if (!str(ev.event_id, 64)) return 'missing_event_id';
  if (!str(ev.event_name, 40)) return 'missing_event_name';
  if (!ALLOWED_EVENTS.has(ev.event_name)) return 'unknown_event_name';
  if (!str(ev.session_id, 64)) return 'missing_session_id';
  return null;
}

function str(value, max) {
  return typeof value === 'string' && value.length ? value.slice(0, max) : null;
}

async function reject(env, reason, payload) {
  try {
    await env.DB.prepare(
      `INSERT INTO raw_event_rejects (rejected_at, reason, payload) VALUES (?1,?2,?3)`
    )
      .bind(new Date().toISOString(), reason, payload)
      .run();
  } catch (err) {
    console.error('[collector] reject write failed', err && err.message);
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}
