/**
 * The service that Google Sheets talks to.
 *
 * One endpoint: POST /scan  { "domain": "example.com" }
 * Returns JSON with the findings and the stage.
 *
 * Protected by a shared secret so only your Sheet can use it.
 */

const express = require('express');
const { scanDomain } = require('./scan');

const app = express();
app.use(express.json({ limit: '256kb' }));

const SECRET = process.env.SCAN_SECRET || '';
const PORT = process.env.PORT || 8080;

/* --- basic protection ------------------------------------------------- */
app.use((req, res, next) => {
  if (req.path === '/health') return next();
  if (!SECRET) return res.status(500).json({ error: 'SCAN_SECRET not configured on the server' });
  if (req.get('x-scan-key') !== SECRET) return res.status(401).json({ error: 'Bad or missing key' });
  next();
});

/* --- simple queue so we never run more than N browsers at once -------- */
const MAX_CONCURRENT = Number(process.env.MAX_CONCURRENT || 2);
let running = 0;
const queue = [];

function withSlot(fn) {
  return new Promise((resolve, reject) => {
    const task = async () => {
      running++;
      try { resolve(await fn()); }
      catch (e) { reject(e); }
      finally {
        running--;
        const next = queue.shift();
        if (next) next();
      }
    };
    if (running < MAX_CONCURRENT) task();
    else queue.push(task);
  });
}

/* --- routes ------------------------------------------------------------ */

app.get('/health', (_req, res) => res.json({ ok: true, running, queued: queue.length }));

app.post('/scan', async (req, res) => {
  const domain = (req.body && req.body.domain || '').trim();
  if (!domain) return res.status(400).json({ error: 'Send { "domain": "example.com" }' });

  // Refuse anything that is obviously not a public website.
  if (/^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(domain)) {
    return res.status(400).json({ error: 'Private addresses are not scanned' });
  }

  try {
    const result = await withSlot(() => scanDomain(domain));
    res.json(result);
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e).slice(0, 300) });
  }
});

app.listen(PORT, () => console.log(`DPDP scan service listening on ${PORT}`));
