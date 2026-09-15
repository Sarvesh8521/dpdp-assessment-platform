/**
 * REGRESSION HARNESS
 *
 * Run this before every deployment and weekly against live sites.
 *
 *   node qc.js fixtures     — synthetic pages with known answers
 *   node qc.js live         — real sites, checks stability not correctness
 *   node qc.js all
 *
 * WHY FIXTURES MATTER
 * You cannot measure accuracy against a real website, because nobody knows
 * the right answer. You can measure it against a page you built yourself.
 * These fixtures are the only place the tool's correctness is actually proven.
 */

const http = require('http');
const { scanDomain } = require('./scan');
const { validate, checkDeterminism } = require('./validate');

/* ------------------------------------------------------------------ */
/* Synthetic pages with known-correct answers.                          */
/* Serve locally, scan, and assert.                                     */
/* ------------------------------------------------------------------ */

const FIXTURES = {
  '/clean': {
    expect: { trackers: 0, banner: false, reject: false },
    html: `<html><head><title>Clean</title></head><body>
      <h1>No tracking at all</h1><p>${'Filler content. '.repeat(60)}</p>
      </body></html>`
  },

  '/tracker-no-banner': {
    expect: { trackersMin: 1, banner: false, reject: false },
    html: `<html><head>
      <script src="https://www.googletagmanager.com/gtag/js?id=G-TEST"></script>
      </head><body><h1>Tracks with no banner</h1>
      <p>${'Filler content. '.repeat(60)}</p></body></html>`
  },

  '/banner-with-reject': {
    expect: { banner: true, reject: true, rejectMethod: 'known selector' },
    html: `<html><body>
      <div id="onetrust-banner-sdk" style="position:fixed;bottom:0;height:120px">
        <p>We use cookies</p>
        <button id="onetrust-accept-btn-handler">Accept All</button>
        <button id="onetrust-reject-all-handler">Reject All</button>
      </div>
      <h1>Banner present</h1><p>${'Filler content. '.repeat(60)}</p>
      </body></html>`
  },

  '/banner-accept-only': {
    expect: { banner: true, reject: false },
    html: `<html><body>
      <div id="cookie-banner-x" style="position:fixed;bottom:0;height:120px">
        <p>We use cookies to improve your experience</p>
        <button>Accept</button>
      </div>
      <h1>No way to decline</h1><p>${'Filler content. '.repeat(60)}</p>
      </body></html>`
  },

  '/form-pre-ticked': {
    expect: { formsMin: 1, preTicked: true },
    html: `<html><body><h1>Form</h1>
      <form><input name="email" type="email"><input name="mobile">
      <label><input type="checkbox" checked> Send me marketing</label>
      <button type="submit">Go</button></form>
      <p>${'Filler content. '.repeat(60)}</p></body></html>`
  },

  '/sensitive-form': {
    expect: { formsMin: 1, sensitiveMin: 2 },
    html: `<html><body><h1>Apply</h1>
      <form><input name="name"><input name="pan"><input name="income_band">
      <input name="dob" type="date"><input name="employer">
      <button type="submit">Submit</button></form>
      <p>${'Filler content. '.repeat(60)}</p></body></html>`
  },

  '/blocked': {
    expect: { runInvalid: true },
    html: `<html><body><h1>Attention Required! | Cloudflare</h1>
      <p>Checking your browser before accessing.</p></body></html>`
  },

  '/empty': {
    expect: { runInvalid: true },
    html: `<html><body></body></html>`
  }
};

function startFixtureServer(port = 8099) {
  return new Promise(resolve => {
    const server = http.createServer((req, res) => {
      const f = FIXTURES[req.url];
      if (!f) { res.writeHead(404); res.end('not found'); return; }
      res.writeHead(200, { 'content-type': 'text/html' });
      res.end(f.html);
    });
    server.listen(port, () => resolve(server));
  });
}

function assertFixture(path, scan, report) {
  const e = FIXTURES[path].expect;
  const fails = [];
  const t = scan.trackers || {};

  if (e.runInvalid) {
    if (report.runValid) fails.push('Expected the run to be rejected, but it was accepted');
    return fails;
  }
  if (!report.runValid) {
    fails.push('Run was rejected: ' + report.blockers.join('; '));
    return fails;
  }

  if (e.trackers !== undefined && t.total !== e.trackers)
    fails.push(`trackers: expected ${e.trackers}, got ${t.total}`);
  if (e.trackersMin !== undefined && t.total < e.trackersMin)
    fails.push(`trackers: expected at least ${e.trackersMin}, got ${t.total}`);
  if (e.banner !== undefined && !!scan.bannerFound !== e.banner)
    fails.push(`banner: expected ${e.banner}, got ${!!scan.bannerFound}`);
  if (e.reject !== undefined && !!scan.rejectFound !== e.reject)
    fails.push(`reject: expected ${e.reject}, got ${!!scan.rejectFound}`);
  if (e.rejectMethod && scan.rejectMethod !== e.rejectMethod)
    fails.push(`rejectMethod: expected ${e.rejectMethod}, got ${scan.rejectMethod}`);
  if (e.formsMin !== undefined && (scan.forms || []).length < e.formsMin)
    fails.push(`forms: expected at least ${e.formsMin}, got ${(scan.forms || []).length}`);
  if (e.preTicked !== undefined) {
    const got = (scan.forms || []).some(f => f.preTickedBoxes > 0);
    if (got !== e.preTicked) fails.push(`preTicked: expected ${e.preTicked}, got ${got}`);
  }
  if (e.sensitiveMin !== undefined) {
    const max = Math.max(0, ...(scan.forms || []).map(f => (f.sensitiveFields || []).length));
    if (max < e.sensitiveMin) fails.push(`sensitive fields: expected at least ${e.sensitiveMin}, got ${max}`);
  }
  return fails;
}

async function runFixtures() {
  console.log('\n=== FIXTURE TESTS — correctness against known answers ===\n');
  const server = await startFixtureServer();
  let pass = 0, fail = 0;

  for (const path of Object.keys(FIXTURES)) {
    const url = `http://localhost:8099${path}`;
    try {
      const scan = await scanDomain(url);
      const report = validate(scan);
      const fails = assertFixture(path, scan, report);
      if (fails.length === 0) { console.log(`  PASS  ${path}`); pass++; }
      else { console.log(`  FAIL  ${path}`); fails.forEach(f => console.log(`          ${f}`)); fail++; }
    } catch (e) {
      console.log(`  ERROR ${path} — ${e.message}`); fail++;
    }
  }

  server.close();
  console.log(`\n  ${pass} passed, ${fail} failed\n`);
  return fail === 0;
}

/* ------------------------------------------------------------------ */
/* Live stability. Cannot check correctness — only that we get the      */
/* same answer twice and that nothing looks absurd.                     */
/* ------------------------------------------------------------------ */

const LIVE = (process.env.QC_LIVE_SITES || 'example.com').split(',').map(s => s.trim());

async function runLive() {
  console.log('\n=== LIVE STABILITY — same answer twice? ===\n');
  let unstable = 0;

  for (const site of LIVE) {
    try {
      const a = await scanDomain(site);
      const b = await scanDomain(site);
      const report = validate(a, b);

      if (!report.runValid) {
        console.log(`  SKIP  ${site} — run rejected: ${report.blockers.join('; ')}`);
        continue;
      }
      const d = report.determinism;
      const tag = d.stable ? 'STABLE' : 'UNSTABLE';
      if (!d.stable) unstable++;
      console.log(`  ${tag}  ${site}  agreement ${d.agreement}  trackers ${report.counts.trackersReportable}` +
        (report.needsHumanReview ? '  [needs review]' : ''));
      if (!d.stable) console.log(`          inconsistent: ${d.unstable.join(', ')}`);
      report.sanityFlags.forEach(f => console.log(`          sanity: ${f}`));
      report.consistencyFlags.forEach(f => console.log(`          ${f.severity}: ${f.issue}`));
    } catch (e) {
      console.log(`  ERROR ${site} — ${e.message}`);
    }
  }
  console.log(`\n  ${unstable} unstable of ${LIVE.length}\n`);
  return unstable === 0;
}

/* ------------------------------------------------------------------ */

async function main() {
  const mode = process.argv[2] || 'all';
  let ok = true;
  if (mode === 'fixtures' || mode === 'all') ok = await runFixtures() && ok;
  if (mode === 'live' || mode === 'all') ok = await runLive() && ok;
  console.log(ok ? 'QC PASSED\n' : 'QC FAILED — do not deploy\n');
  process.exit(ok ? 0 : 1);
}

if (require.main === module) main();
module.exports = { FIXTURES, runFixtures, runLive };
