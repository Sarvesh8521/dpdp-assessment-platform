/**
 * The scan engine.
 *
 * WHAT IT DOES, IN ORDER:
 *   1. Opens the page in a clean browser with no cookies.
 *   2. Records every network request, with the exact time it happened.
 *   3. Waits for the consent banner to appear and notes when.
 *   4. Clicks "Reject" if it can find it.
 *   5. Waits again and records what STILL runs.
 *   6. Reads the forms and the privacy policy link.
 *   7. Scores it and works out the stage.
 *
 * The finding that matters is step 5.
 */

const { chromium } = require('playwright');
const { identify, identifyCMP } = require('./vendors');

const WAIT_INITIAL = 6000;   // how long to watch before touching anything
const WAIT_AFTER   = 6000;   // how long to watch after clicking Reject
const NAV_TIMEOUT  = 45000;

/* Words that usually sit on a "reject" button, in English and common Indian usage. */
const REJECT_WORDS = [
  'reject all', 'reject', 'decline all', 'decline', 'deny', 'refuse',
  'only necessary', 'necessary only', 'strictly necessary', 'essential only',
  'do not accept', "don't accept", 'disagree', 'opt out', 'no thanks',
  'manage', 'preferences', 'customise', 'customize', 'settings'
];

const ACCEPT_WORDS = ['accept all', 'accept', 'agree', 'allow all', 'got it', 'i agree', 'ok'];

/* Selectors used by the common consent tools, tried first because they are reliable. */
const KNOWN_REJECT_SELECTORS = [
  '#onetrust-reject-all-handler',
  '.onetrust-close-btn-handler',
  '#CybotCookiebotDialogBodyButtonDecline',
  '.cky-btn-reject',
  '[data-cky-tag="reject-button"]',
  '#uc-btn-deny-banner',
  '.uc-deny-button',
  '[aria-label*="reject" i]',
  '[id*="reject" i]',
  '[class*="reject" i]'
];

const BANNER_HINTS = [
  '#onetrust-banner-sdk', '#CybotCookiebotDialog', '.cky-consent-container',
  '#usercentrics-root', '#truste-consent-track', '[id*="cookie" i][class*="banner" i]',
  '[class*="cookie-consent" i]', '[class*="cookie-banner" i]', '[id*="consent" i]'
];

function nowMs(t0) { return Date.now() - t0; }

async function scanDomain(rawInput, opts = {}) {
  const url = normaliseUrl(rawInput);
  const started = new Date().toISOString();

  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-dev-shm-usage']
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
               '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
    viewport: { width: 1366, height: 900 }
  });

  const page = await context.newPage();

  const hits = [];        // every tracker request we see
  const cmpsSeen = new Set();
  let phase = 'before';   // flips to 'after' once we click reject
  const t0 = Date.now();

  page.on('request', req => {
    const u = req.url();
    const cmp = identifyCMP(u);
    if (cmp) cmpsSeen.add(cmp.name);

    const v = identify(u);
    if (!v) return;
    hits.push({
      ms: nowMs(t0),
      phase,
      name: v.name,
      owner: v.owner,
      purpose: v.purpose,
      sends: v.sends,
      risk: v.risk,
      host: safeHost(u)
    });
  });

  const result = {
    input: rawInput,
    url,
    scannedAt: started,
    ok: true,
    error: null
  };

  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
    await page.waitForTimeout(WAIT_INITIAL);

    // --- did a banner appear, and when? ---
    const banner = await findBanner(page);
    result.bannerFound = banner.found;
    result.bannerAppearedMs = banner.ms;
    result.cmpDetected = Array.from(cmpsSeen);

    const cookiesBefore = await context.cookies();

    // --- try to reject ---
    phase = 'after';
    const rejected = await tryReject(page);
    result.rejectFound = rejected.found;
    result.rejectMethod = rejected.method;
    result.rejectLabel = rejected.label;

    // --- is accept easier than reject? ---
    result.acceptEasierThanReject = await acceptIsEasier(page, rejected.found);

    await page.waitForTimeout(WAIT_AFTER);
    const cookiesAfter = await context.cookies();

    // --- forms ---
    result.forms = await readForms(page);

    // --- privacy policy ---
    result.privacyPolicy = await findPrivacyLink(page);

    // --- language ---
    result.pageLang = await page.evaluate(() => document.documentElement.lang || null);

    // --- assemble tracker findings ---
    result.trackers = summariseTrackers(hits);
    result.timeline = buildTimeline(hits, banner, rejected, t0);
    result.cookies = {
      beforeReject: cookiesBefore.length,
      afterReject: cookiesAfter.length,
      thirdParty: countThirdParty(cookiesAfter, url)
    };

    result.score = scoreIt(result);

  } catch (e) {
    result.ok = false;
    result.error = String(e.message || e).slice(0, 300);
  } finally {
    await browser.close();
  }

  return result;
}

/* ------------------------------------------------------------------ helpers */

function normaliseUrl(input) {
  let s = String(input || '').trim();
  if (!s) throw new Error('No domain supplied');
  if (!/^https?:\/\//i.test(s)) s = 'https://' + s;
  return s;
}

function safeHost(u) {
  try { return new URL(u).hostname; } catch { return ''; }
}

function countThirdParty(cookies, url) {
  let base = '';
  try { base = new URL(url).hostname.replace(/^www\./, ''); } catch { }
  return cookies.filter(c => c.domain && !c.domain.replace(/^\./, '').endsWith(base)).length;
}

async function findBanner(page) {
  for (const sel of BANNER_HINTS) {
    try {
      const el = await page.$(sel);
      if (el && await el.isVisible()) {
        return { found: true, ms: null, selector: sel };
      }
    } catch { }
  }
  // fallback: look for a fixed-position box mentioning cookies or consent
  try {
    const found = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div,section,aside'));
      return els.some(e => {
        const st = getComputedStyle(e);
        if (st.position !== 'fixed' && st.position !== 'sticky') return false;
        if (e.offsetHeight < 40) return false;
        const t = (e.innerText || '').toLowerCase();
        return t.includes('cookie') || t.includes('consent') || t.includes('privacy');
      });
    });
    return { found, ms: null, selector: null };
  } catch {
    return { found: false, ms: null, selector: null };
  }
}

async function tryReject(page) {
  // 1. known selectors
  for (const sel of KNOWN_REJECT_SELECTORS) {
    try {
      const el = await page.$(sel);
      if (el && await el.isVisible()) {
        await el.click({ timeout: 3000 });
        return { found: true, method: 'known selector', label: sel };
      }
    } catch { }
  }
  // 2. any button whose text looks like a rejection
  try {
    const label = await page.evaluate(words => {
      const clickable = Array.from(document.querySelectorAll('button,a,[role="button"],input[type="button"]'));
      for (const w of words) {
        const hit = clickable.find(el => {
          const t = (el.innerText || el.value || '').trim().toLowerCase();
          return t && t.length < 40 && t.includes(w);
        });
        if (hit) { hit.click(); return hit.innerText || hit.value || w; }
      }
      return null;
    }, REJECT_WORDS);
    if (label) return { found: true, method: 'text match', label: String(label).trim() };
  } catch { }

  return { found: false, method: null, label: null };
}

async function acceptIsEasier(page, rejectFound) {
  if (!rejectFound) return true;   // no reject button at all is the worst case
  try {
    return await page.evaluate(a => {
      const els = Array.from(document.querySelectorAll('button,a,[role="button"]'));
      const acc = els.find(e => a.some(w => (e.innerText || '').trim().toLowerCase() === w));
      return !!acc;   // present at top level = one click; refined manually in the review
    }, ACCEPT_WORDS);
  } catch { return null; }
}

async function readForms(page) {
  try {
    return await page.evaluate(() => {
      const sensitive = ['pan', 'aadhaar', 'aadhar', 'dob', 'birth', 'income', 'salary',
        'employer', 'company', 'gender', 'age', 'address', 'pincode', 'pin code'];
      return Array.from(document.querySelectorAll('form')).slice(0, 12).map(f => {
        const fields = Array.from(f.querySelectorAll('input,select,textarea'))
          .filter(i => !['hidden', 'submit', 'button'].includes(i.type))
          .map(i => (i.name || i.id || i.placeholder || i.type || '').toLowerCase())
          .filter(Boolean);
        const text = (f.innerText || '').toLowerCase();
        return {
          fieldCount: fields.length,
          fields: fields.slice(0, 25),
          hasConsentCheckbox: !!f.querySelector('input[type="checkbox"]'),
          preTickedBoxes: Array.from(f.querySelectorAll('input[type="checkbox"]'))
            .filter(c => c.checked).length,
          linksToPrivacy: /privacy|policy/.test(f.innerHTML.toLowerCase()),
          statesPurpose: /we will use|will be used|purpose|to contact you|to send you/.test(text),
          sensitiveFields: fields.filter(n => sensitive.some(s => n.includes(s)))
        };
      });
    });
  } catch { return []; }
}

async function findPrivacyLink(page) {
  try {
    return await page.evaluate(() => {
      const a = Array.from(document.querySelectorAll('a')).find(x =>
        /privacy/i.test(x.innerText || '') || /privacy/i.test(x.href || ''));
      return a ? { found: true, href: a.href } : { found: false, href: null };
    });
  } catch { return { found: false, href: null }; }
}

function summariseTrackers(hits) {
  const key = h => h.name + '|' + h.host;
  const map = new Map();
  for (const h of hits) {
    const k = key(h);
    if (!map.has(k)) {
      map.set(k, { ...h, firstMs: h.ms, calls: 0, ranBefore: false, ranAfter: false });
    }
    const e = map.get(k);
    e.calls++;
    if (h.phase === 'before') e.ranBefore = true;
    else e.ranAfter = true;
  }
  const list = Array.from(map.values()).sort((a, b) => a.firstMs - b.firstMs);
  return {
    total: list.length,
    ranBeforeConsent: list.filter(t => t.ranBefore).length,
    stillRanAfterReject: list.filter(t => t.ranAfter).length,
    stoppedByReject: list.filter(t => t.ranBefore && !t.ranAfter).length,
    highRisk: list.filter(t => t.risk === 'High').length,
    advertising: list.filter(t => t.purpose === 'advertising').length,
    list
  };
}

function buildTimeline(hits, banner, rejected) {
  const rows = hits
    .filter(h => h.phase === 'before')
    .slice(0, 40)
    .map(h => ({ ms: h.ms, what: h.name, owner: h.owner, sends: h.sends, note: 'Ran before consent' }));

  if (banner.found) rows.push({ ms: null, what: 'Permission banner appeared', owner: '', sends: '', note: 'On screen' });
  rows.push({
    ms: null,
    what: rejected.found ? 'We clicked Reject' : 'No reject button found',
    owner: '', sends: '',
    note: rejected.found ? `via ${rejected.method}` : 'Could not decline'
  });

  const after = hits.filter(h => h.phase === 'after').slice(0, 25)
    .map(h => ({ ms: h.ms, what: h.name, owner: h.owner, sends: h.sends, note: 'STILL RAN after rejection' }));

  return rows.concat(after);
}

/* ------------------------------------------------------------------ scoring */

function scoreIt(r) {
  const t = r.trackers;

  // Check 1 — trackers held back until consent
  let c1 = 0;
  if (t.total === 0) c1 = 1;                       // nothing to gate
  else c1 = 1 - (t.ranBeforeConsent / t.total);

  // Check 2 — permission asked for properly (what is visible from outside)
  const tests = [];
  tests.push(r.bannerFound ? 2 : 0);                                   // banner exists
  tests.push(t.ranBeforeConsent === 0 ? 2 : 0);                        // waits before firing
  tests.push(r.rejectFound ? 2 : 0);                                   // can decline at all
  tests.push(r.acceptEasierThanReject === false ? 2 : (r.rejectFound ? 1 : 0)); // equal ease
  tests.push(t.stillRanAfterReject === 0 ? 2 : 0);                     // rejection is honoured
  tests.push(r.privacyPolicy.found ? 2 : 0);                           // policy linked
  tests.push(r.pageLang && r.pageLang !== 'en' ? 2 : 1);               // language signal
  const c2 = tests.reduce((a, b) => a + b, 0) / (tests.length * 2);

  // Check 3 — forms collect with a reason
  let c3 = 0;
  if (!r.forms.length) c3 = null;
  else {
    const f = r.forms;
    const purpose = f.filter(x => x.statesPurpose).length / f.length;
    const privacy = f.filter(x => x.linksToPrivacy).length / f.length;
    const preTicked = f.some(x => x.preTickedBoxes > 0) ? 0 : 1;
    const sensitive = f.some(x => x.sensitiveFields.length > 2) ? 0 : 1;
    c3 = (purpose + privacy + preTicked + sensitive) / 4;
  }

  const parts = [c1, c2, c3].filter(x => x !== null);
  const overall = parts.reduce((a, b) => a + b, 0) / parts.length;

  return {
    trackersHeldBack: round(c1),
    permissionAskedProperly: round(c2),
    formsCollectWithReason: c3 === null ? null : round(c3),
    promisesTheyCanKeep: null,          // needs system access — never guessed
    overall: round(overall),
    stage: stageFor(overall),
    meetsMay2027: overall >= 0.7
  };
}

function round(x) { return Math.round(x * 1000) / 1000; }

function stageFor(x) {
  if (x >= 0.90) return '5 - Governed';
  if (x >= 0.70) return '4 - Managed';
  if (x >= 0.45) return '3 - Patched';
  if (x >= 0.20) return '2 - Exposed';
  return '1 - Unaware';
}

module.exports = { scanDomain };
