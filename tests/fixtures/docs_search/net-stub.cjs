'use strict';
// Test double for the network, preloaded with `node --require` so the live
// search probe runs hermetically: every URL it asks for is answered from
// memory, so nothing is ever fetched from nastechresearch.github.io or from
// Algolia.  The shapes below are the measured ones -- the Pages HTML that
// carries an empty `<span class="DocSearch-Button-Keys"></span>`, the hashed
// bundle that carries the deployed `algolia:{...}` config, and a query answer
// with enough on-site hits to satisfy MIN_HITS.
const { Response } = globalThis;

const DOCS_URL = (process.env.DOCS_URL || '').replace(/\/$/, '');
const BUNDLE = '/nastech-agent/docs/assets/js/main.c19ee7d3.js';
const PAGE =
  '<html><body><button class="DocSearch DocSearch-Button" aria-label="Search">' +
  '<span class="DocSearch-Button-Keys"></span></button>' +
  `<script src="${BUNDLE}"></script></body></html>`;
const JS =
  'window.docusaurus=1;' +
  'algolia:{"appId":"testapp","apiKey":"testkey","indexName":"hermes docs","contextualSearch":true};';

function answer(url) {
  const u = String(url);
  if (u.includes('-dsn.algolia.net')) {
    const hits = [{ url: `${DOCS_URL}/telegram` }];
    const body = { results: [{ nbHits: 402, hits }, { nbHits: 402, hits }, { nbHits: 402, hits }] };
    return new Response(JSON.stringify(body), { status: 200 });
  }
  if (u.endsWith(BUNDLE)) return new Response(JS, { status: 200 });
  return new Response(PAGE, { status: 200 });
}

globalThis.fetch = async (url) => answer(url);
