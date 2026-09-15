/**
 * Run a scan from your own machine, without the server.
 *   node cli.js example.com
 */
const { scanDomain } = require('./scan');

const domain = process.argv[2];
if (!domain) { console.error('Usage: node cli.js example.com'); process.exit(1); }

scanDomain(domain)
  .then(r => console.log(JSON.stringify(r, null, 2)))
  .catch(e => { console.error(e); process.exit(1); });
