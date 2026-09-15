# DPDP Readiness Scanner

A tool that opens a website in a real browser, clicks "Reject" on the permission
banner, and records everything that tracks the visitor anyway.

Built for use across Interactive Avenues and sister agency brands.

---

## What it does, in plain words

You paste a list of website addresses into a Google Sheet and click a button.
For each one, the tool:

1. Opens the site in a clean browser with no cookies.
2. Writes down every tracking code that runs, and the exact moment it ran.
3. Finds the permission banner and clicks **Reject**.
4. Writes down everything that **still** runs afterwards.
5. Reads the forms and looks for a privacy policy link.
6. Scores it and gives you a stage from 1 to 5.

It needs nothing from the client. It only sees what any visitor's browser sees.

---

## Why it is not built entirely in Google Sheets

Google Sheets can fetch a web page, but it cannot run the JavaScript on it.

Almost every tracker on a modern site is loaded *by* JavaScript, usually through
Google Tag Manager, after the page arrives. A Sheets-only tool would look at a
site carrying 27 trackers, see the 2 or 3 written directly into the page, and
report it as clean.

That is worse than having no tool. So the Sheet stays as the front end, and a
small service does the browser work behind it.

---

## The two pieces

```
Google Sheet                    Scan service
(your teams use this)           (runs once, nobody looks at it)

 Domains sheet   ──── POST ────▶  Opens a real browser
 Results sheet   ◀─── JSON ────   Records what fires
 Trackers sheet                   Scores it
```

| Folder | What it is |
|---|---|
| `service/` | The scanner. Node.js and Playwright. Runs on Google Cloud Run. |
| `sheet/Code.gs` | The Google Sheets front end. Paste into Apps Script. |

---

## Part 1 — put the service online

You need a Google Cloud project and the `gcloud` command line tool.

```bash
cd service

# Pick any long random string. You will need it again in Part 2.
SECRET=$(openssl rand -hex 24)
echo "Your key is: $SECRET"     # write this down

gcloud run deploy dpdp-scanner \
  --source . \
  --region asia-south1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 1 \
  --max-instances 5 \
  --set-env-vars SCAN_SECRET=$SECRET,MAX_CONCURRENT=2 \
  --no-allow-unauthenticated
```

Two notes on those settings:

- **2 GB memory** — a real browser needs it. Less than this and scans fail
  silently on heavy sites.
- **`--no-allow-unauthenticated`** — the service is not open to the internet.
  If you would rather let the Sheet call it directly with just the shared key,
  change this to `--allow-unauthenticated`. Simpler to set up, slightly weaker.

The command prints a URL. Your scan endpoint is that URL with `/scan` on the end.

### Test it before going further

```bash
curl -X POST https://YOUR-URL/scan \
  -H "content-type: application/json" \
  -H "x-scan-key: YOUR_SECRET" \
  -d '{"domain":"example.com"}'
```

You should get JSON back within about 30 seconds.

### Or run it on a laptop first

```bash
cd service
npm install
npx playwright install chromium
node cli.js example.com
```

This is the fastest way to see whether the findings are any good before
spending money on hosting.

---

## Part 2 — set up the Sheet

1. Create a new Google Sheet. Name it something obvious.
2. **Extensions → Apps Script**. Delete what is there. Paste in `sheet/Code.gs`. Save.
3. **Project Settings → Script Properties → Add script property**, twice:

   | Property | Value |
   |---|---|
   | `SCAN_URL` | `https://YOUR-URL/scan` |
   | `SCAN_KEY` | the secret from Part 1 |

4. Go back to the Sheet and reload the page. A **DPDP Scan** menu appears.
5. **DPDP Scan → Set up the sheets.** This builds the four tabs.
6. **DPDP Scan → Test the connection.** You should see `{"ok":true}`.

---

## Part 3 — using it

1. Paste website addresses into column A of the **Domains** sheet.
   Brand names in column B if you want them.
2. **DPDP Scan → Scan all pending.**
3. Results appear on the **Results** sheet, one row per site.
   Every individual tracker appears on the **Trackers found** sheet.

To scan just one site, click its row and use **Scan selected row**.

### How long it takes

About 25 to 40 seconds per website. Sheets will stop a script after 6 minutes,
so scan in batches of roughly 8 to 10. Run it again for the next batch — sites
already marked `Done` are skipped.

---

## Reading the results

| Column | What it means |
|---|---|
| **STAGE** | 1 to 5. This is the headline. Coloured automatically. |
| **Ran before agreement** | Trackers that fired before the visitor clicked anything. |
| **Still ran after Reject** | Trackers that ignored the rejection. **This is the finding that matters.** |
| **Stopped by Reject** | Trackers the banner actually controls. |
| **Promises they can keep** | Always blank. Cannot be checked from outside. |

The last row is deliberate. The tool never guesses at what it cannot see.
Those gaps are the reason to sell the full review.

---

## Adding a tracker the tool does not know

Open `service/vendors.js`, copy any line in the `VENDORS` list, change the
values, redeploy. Nothing else needs touching.

```js
{ match: 'newtracker.com', name: 'New Tracker', owner: 'Someone',
  purpose: 'advertising', sends: 'Page, browser ID', risk: 'High' },
```

Keep `match` lowercase. It is checked against the whole request address.

---

## What this tool cannot do

Be honest about this with clients. It is in the report template too.

- It cannot tell whether opting out reaches the email or sales systems.
- It cannot see whether a dated record of consent is kept.
- It cannot see retention rules or whether anything is ever deleted.
- It cannot check mobile apps.
- It cannot see anything behind a login.
- It cannot read contracts with dealers, agencies or vendors.

Roughly half the full review still needs a person and system access.

---

## Things to be careful about

**Scanning sites you do not work on.** Loading a public page is what any browser
does, so a single scan is unremarkable. Scanning hundreds of sites, or scanning
and then cold-emailing the findings, is a different matter and will read as
intrusive. Use it in meetings and on your own clients first.

**Some sites will block it.** Sites behind Cloudflare bot protection may refuse
the scan. The row will come back `Failed`. That is not a finding about their
privacy position — do not report it as one.

**The reject button is found by guessing.** The tool knows the common consent
tools by name and otherwise looks for buttons with words like "Reject" or
"Decline". Unusual designs will defeat it. When `Reject button found? = No`,
have a person check by hand before putting it in a client report.

**Results are a moment in time.** Sites change. A scan from last month is not
evidence about this month.

**This is not legal advice.** Every output must say so.

---

## Rough running cost

Cloud Run charges only while a scan is running. At around 30 seconds each:

| Scans a month | Approximate cost |
|---|---|
| 100 | under Rs. 500 |
| 1,000 | Rs. 2,000 to 4,000 |
| 10,000 | Rs. 20,000 to 40,000 |

Small enough that cost should not be a factor in the decision.

---

## Where this goes next

The same service, run on a weekly schedule against clients who pay for the
monthly service, becomes an alert: *a new untagged tracker appeared on your
site on Tuesday.*

That turns the monthly service from "we do work for you" into "we watch this
for you" — which is easier to sell, harder to cancel, and not priced against
agency day rates.

---

## Quality control

The scanner ships with a validation layer and a regression harness. Both are in
`service/`.

**`validate.js`** runs after every scan. Its job is to stop the tool asserting
things it cannot support. It checks run validity, determinism across two runs,
confidence per finding, internal contradictions and sanity bounds — then maps
confidence to verdict. A LOW confidence finding can never produce "Not observed";
it becomes "Cannot determine".

**`qc.js`** is the regression harness.

```bash
node qc.js fixtures   # correctness against synthetic pages with known answers
node qc.js live       # stability on real sites — same answer twice?
node qc.js all
```

Run `fixtures` before every deploy and `live` weekly. Set the live list with:

```bash
export QC_LIVE_SITES="example.com,anotherclient.com"
```

Fixtures are the only place correctness is actually proven, because nobody knows
the right answer for a real website. Add a fixture every time a false positive is
found in the field — that is how the tool stops repeating mistakes.

**Sampling.** `validate.js` flags roughly 10% of scans for manual review,
deterministically by URL. Check those by hand and track the agreement rate over
time. If humans are correcting more than about 10% of findings, the tool is not
ready for client use on that finding type.

---

## Phase 2

See `PHASE2_SPEC.md`. Read Section 1 first — it covers the data processor
obligations that must be settled before any code is written.
