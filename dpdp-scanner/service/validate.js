/**
 * QC AND VALIDATION LAYER
 *
 * Runs after every scan. Its job is to stop the tool asserting things it
 * cannot support.
 *
 * The governing rule:
 *   A finding is only reported as "Observed" or "Not observed" if the
 *   evidence supports it at HIGH confidence. Everything else becomes
 *   "Cannot determine". The tool is allowed to say "I don't know".
 *   It is not allowed to guess.
 *
 * Six checks, in order. If any run-validity check fails, nothing is emitted.
 *
 *   A. Run validity      — was this scan usable at all?
 *   B. Determinism       — does a second run agree with the first?
 *   C. Confidence        — how was each finding derived?
 *   D. Consistency       — do the findings contradict each other?
 *   E. Sanity bounds     — are the numbers plausible?
 *   F. Verdict mapping   — confidence becomes verdict, never the other way round
 *
 * Plus G: a sampling flag so a human checks a share of runs and we can
 * measure whether the tool is actually right.
 */

const HIGH = 'high', MED = 'medium', LOW = 'low';

/* Substring matches that are known to be unreliable. A hit on one of these
   alone is never HIGH confidence. Extend this list as false positives are found. */
const WEAK_MATCHES = ['segment', 'intercom', 'clarity', 'amplitude', 'criteo', 'moat'];

/* Text that indicates we were served a block page rather than the site. */
const BLOCK_SIGNS = [
  'attention required', 'cloudflare', 'access denied', 'are you a robot',
  'verify you are human', 'captcha', 'request blocked', 'unusual traffic',
  'ddos protection', 'checking your browser'
];

/* ------------------------------------------------------------------ A */
function checkRunValidity(scan) {
  const fails = [];

  if (!scan.ok) fails.push('Scan threw an error: ' + (scan.error || 'unknown'));
  if (scan.httpStatus && scan.httpStatus >= 400) fails.push('HTTP ' + scan.httpStatus);

  const text = (scan.pageText || '').toLowerCase();
  if (text) {
    const hit = BLOCK_SIGNS.find(sign => text.includes(sign));
    if (hit && text.length < 3000) {
      fails.push('Served a bot-protection page, not the site (matched: ' + hit + ')');
    }
    if (text.length < 200) fails.push('Almost no rendered content — page may not have loaded');
  } else {
    fails.push('No page text captured — cannot confirm the page rendered');
  }

  if (scan.domNodeCount !== undefined && scan.domNodeCount < 50) {
    fails.push('DOM is too small to be a real page');
  }
  if (scan.durationMs !== undefined && scan.durationMs < 3000) {
    fails.push('Scan finished implausibly fast — page probably did not execute');
  }

  return { valid: fails.length === 0, failures: fails };
}

/* ------------------------------------------------------------------ B */
/**
 * Compare two independent runs of the same URL.
 * Trackers seen in both = confirmed. Seen in only one = unstable, downgraded.
 */
function checkDeterminism(runA, runB) {
  const names = r => new Set(((r.trackers && r.trackers.list) || []).map(t => t.name));
  const a = names(runA), b = names(runB);

  const both = [...a].filter(n => b.has(n));
  const union = new Set([...a, ...b]);
  const agreement = union.size === 0 ? 1 : both.length / union.size;

  const onlyOnce = [...union].filter(n => !(a.has(n) && b.has(n)));

  return {
    agreement: Math.round(agreement * 100) / 100,
    stable: agreement >= 0.9,
    confirmed: both,
    unstable: onlyOnce,
    note: agreement >= 0.9
      ? 'Runs agree. Tracker set is stable.'
      : 'Runs disagree. Only trackers seen in both runs are reported; the rest are marked cannot determine.'
  };
}

/* ------------------------------------------------------------------ C */
function scoreTracker(t) {
  const reasons = [];
  let conf = HIGH;

  if (WEAK_MATCHES.some(w => (t.matchedOn || '').includes(w))) {
    conf = MED;
    reasons.push('Matched on a substring known to produce false positives');
  }
  if (!t.host) { conf = LOW; reasons.push('No host recorded'); }
  if (t.calls === 1 && t.firstMs > 8000) {
    conf = MED;
    reasons.push('Single late call — may be unrelated to page load');
  }
  return { confidence: conf, reasons };
}

function scoreConsentSignal(t) {
  /* Google Consent Mode encodes state in the gcs / gcd parameters.
     If we can read it, we know whether the request ran with consent denied. */
  if (t.consentSignal === 'denied') {
    return { confidence: HIGH, state: 'denied', note: 'Request made with consent signals denied. Not reported as a failure.' };
  }
  if (t.consentSignal === 'granted') {
    return { confidence: HIGH, state: 'granted', note: 'Request made with consent signals granted.' };
  }
  if (t.carriesIdentifier === true) {
    return { confidence: HIGH, state: 'identifier-present', note: 'Request carried an identifier.' };
  }
  if (t.carriesIdentifier === false) {
    return { confidence: MED, state: 'no-identifier-seen', note: 'No identifier detected, but detection is not exhaustive.' };
  }
  return { confidence: LOW, state: 'unknown', note: 'Consent state could not be read. Reported as cannot determine.' };
}

function scoreBanner(scan) {
  if (scan.bannerSelector) {
    return { confidence: HIGH, reasons: ['Matched a known consent platform selector'] };
  }
  if (scan.bannerFound) {
    return { confidence: MED, reasons: ['Found by layout heuristic, not by a known selector'] };
  }
  if ((scan.cmpDetected || []).length > 0) {
    return { confidence: LOW, reasons: ['A consent platform was loaded but no banner was seen — may render on a delay or by geography'] };
  }
  return { confidence: HIGH, reasons: ['No consent platform loaded and no banner element found'] };
}

function scoreReject(scan) {
  if (scan.rejectMethod === 'known selector') {
    return { confidence: HIGH, reasons: ['Clicked a known consent platform reject control'] };
  }
  if (scan.rejectMethod === 'text match') {
    return { confidence: MED, reasons: ['Clicked a control matched on button text — verify by hand'] };
  }
  if (scan.bannerFound) {
    return { confidence: LOW, reasons: ['A banner was present but no reject control was found. May exist behind a settings screen.'] };
  }
  return { confidence: MED, reasons: ['No banner and no reject control found'] };
}

/* ------------------------------------------------------------------ D */
function checkConsistency(scan) {
  const flags = [];
  const t = scan.trackers || {};

  if ((scan.cmpDetected || []).length > 0 && !scan.bannerFound) {
    flags.push({
      severity: 'review',
      issue: 'A consent platform loaded but no banner was detected.',
      likely: 'Banner renders on a delay, only in some regions, or only for new sessions.',
      action: 'Human check before reporting anything about the banner.'
    });
  }

  if (scan.bannerFound && !scan.rejectFound) {
    flags.push({
      severity: 'review',
      issue: 'A banner was found but no way to decline.',
      likely: 'This is either a real finding or our detection missed a settings screen.',
      action: 'Must be verified by hand. Do not report without it.'
    });
  }

  if (scan.rejectFound && t.stoppedByReject === 0 && t.total > 0) {
    const readable = (t.list || []).some(x => x.consentSignal);
    if (!readable) {
      flags.push({
        severity: 'downgrade',
        issue: 'Rejection appeared to change nothing, and no consent signal was readable.',
        likely: 'Either the site ignores rejection, or our click did not register.',
        action: 'Report as cannot determine until verified.'
      });
    }
  }

  if (t.total === 0 && (scan.cmpDetected || []).length > 0) {
    flags.push({
      severity: 'downgrade',
      issue: 'Zero trackers detected on a site running a consent platform.',
      likely: 'Requests were blocked, or the vendor list needs extending.',
      action: 'Treat the tracker count as unreliable.'
    });
  }

  if (scan.cookies && scan.cookies.beforeReject === 0 && t.total > 0) {
    flags.push({
      severity: 'review',
      issue: 'Trackers fired but no cookies were set.',
      likely: 'Cookieless or consent-denied mode — which would be correct behaviour.',
      action: 'Do not describe this as a problem.'
    });
  }

  return flags;
}

/* ------------------------------------------------------------------ E */
function checkSanity(scan) {
  const flags = [];
  const t = scan.trackers || {};

  if (t.total > 80) flags.push('Unusually high tracker count (' + t.total + ') — verify before reporting');
  if (t.total === 0) flags.push('Zero trackers found — unusual for a commercial site. Treat as unreliable.');
  if (t.ranBeforeConsent > t.total) flags.push('Internal inconsistency: more pre-consent trackers than total trackers');
  if ((scan.forms || []).length === 0 && (scan.pageText || '').length > 5000) {
    flags.push('No forms found on a substantial page — form detection may have failed');
  }
  return flags;
}

/* ------------------------------------------------------------------ F */
/**
 * The only place a verdict is produced. Confidence in, verdict out.
 * LOW confidence can never produce "Not observed".
 */
function verdictFrom(confidence, evidencePresent) {
  if (confidence === LOW) return 'Cannot determine';
  if (confidence === MED) return evidencePresent ? 'Observed' : 'Cannot determine';
  return evidencePresent ? 'Observed' : 'Not observed';
}

/* ------------------------------------------------------------------ G */
function samplingFlag(url, rate = 0.1) {
  /* Deterministic per URL so the same site is always in or out of the sample. */
  let h = 0;
  for (const ch of String(url)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return (h % 100) < rate * 100;
}

/* ================================================================== MAIN */
function validate(scan, secondRun) {
  const report = {
    url: scan.url,
    scannedAt: scan.scannedAt,
    runValid: true,
    blockers: [],
    determinism: null,
    consistencyFlags: [],
    sanityFlags: [],
    findings: {},
    needsHumanReview: false,
    sampledForReview: samplingFlag(scan.url),
    emit: true
  };

  /* A */
  const validity = checkRunValidity(scan);
  if (!validity.valid) {
    report.runValid = false;
    report.blockers = validity.failures;
    report.emit = false;
    report.summary = 'Run rejected. No findings emitted.';
    return report;
  }

  /* B */
  let usableTrackers = (scan.trackers && scan.trackers.list) || [];
  if (secondRun) {
    report.determinism = checkDeterminism(scan, secondRun);
    if (!report.determinism.stable) {
      const ok = new Set(report.determinism.confirmed);
      usableTrackers = usableTrackers.filter(t => ok.has(t.name));
      report.needsHumanReview = true;
    }
  } else {
    report.determinism = { agreement: null, stable: null, note: 'Single run. Determinism not checked — run twice for reportable output.' };
    report.needsHumanReview = true;
  }

  /* C */
  report.findings.trackers = usableTrackers.map(t => {
    const conf = scoreTracker(t);
    const signal = scoreConsentSignal(t);
    return {
      name: t.name,
      host: t.host,
      firstMs: t.firstMs,
      detectionConfidence: conf.confidence,
      detectionNotes: conf.reasons,
      consentState: signal.state,
      consentConfidence: signal.confidence,
      consentNote: signal.note,
      reportable: conf.confidence !== LOW
    };
  });

  const banner = scoreBanner(scan);
  report.findings.banner = {
    found: scan.bannerFound,
    confidence: banner.confidence,
    notes: banner.reasons,
    verdict: verdictFrom(banner.confidence, !!scan.bannerFound)
  };

  const reject = scoreReject(scan);
  report.findings.rejectControl = {
    found: scan.rejectFound,
    method: scan.rejectMethod,
    confidence: reject.confidence,
    notes: reject.reasons,
    verdict: verdictFrom(reject.confidence, !!scan.rejectFound)
  };

  /* D and E */
  report.consistencyFlags = checkConsistency(scan);
  report.sanityFlags = checkSanity(scan);

  if (report.consistencyFlags.some(f => f.severity === 'downgrade')) {
    report.findings.rejectControl.verdict = 'Cannot determine';
    report.needsHumanReview = true;
  }
  if (report.consistencyFlags.some(f => f.severity === 'review')) report.needsHumanReview = true;
  if (report.sanityFlags.length) report.needsHumanReview = true;

  /* counts, using only reportable findings */
  const rep = report.findings.trackers.filter(t => t.reportable);
  report.counts = {
    trackersReportable: rep.length,
    trackersDropped: report.findings.trackers.length - rep.length,
    consentDenied: rep.filter(t => t.consentState === 'denied').length,
    carryingIdentifier: rep.filter(t => t.consentState === 'identifier-present').length,
    consentStateUnknown: rep.filter(t => t.consentState === 'unknown').length
  };

  report.summary = report.needsHumanReview
    ? 'Findings produced, but this scan must be checked by a person before it goes to a client.'
    : 'Findings produced and passed every automated check.';

  return report;
}

module.exports = {
  validate, checkRunValidity, checkDeterminism, checkConsistency,
  checkSanity, verdictFrom, samplingFlag
};
