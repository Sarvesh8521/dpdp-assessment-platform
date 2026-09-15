/**
 * DPDP READINESS INDEX — Google Sheets front end
 * Interactive Avenues
 *
 * WHAT THIS DOES
 *   You paste a list of website addresses into the "Domains" sheet and click
 *   "DPDP Scan > Scan all". It sends each one to the scan service, waits for
 *   the answer, and writes the results back into the sheet.
 *
 * SETUP — do this once
 *   1. Extensions > Apps Script, paste this file in, save.
 *   2. Project Settings > Script Properties, add two properties:
 *        SCAN_URL   = https://your-service-url/scan
 *        SCAN_KEY   = the same secret you set on the service
 *   3. Reload the sheet. A "DPDP Scan" menu appears.
 *
 * WHO CAN USE IT
 *   Anyone you share the Sheet with. They never see the key or the service.
 */

var SHEET_DOMAINS = 'Domains';
var SHEET_RESULTS = 'Results';
var SHEET_TRACKERS = 'Trackers found';
var SHEET_LOG = 'Log';

/* ------------------------------------------------------------------ menu */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('DPDP Scan')
    .addItem('Set up the sheets', 'setupSheets')
    .addSeparator()
    .addItem('Scan all pending', 'scanAllPending')
    .addItem('Scan selected row', 'scanSelectedRow')
    .addSeparator()
    .addItem('Clear results', 'clearResults')
    .addItem('Test the connection', 'testConnection')
    .addToUi();
}

/* --------------------------------------------------------------- set up */

function setupSheets() {
  var ss = SpreadsheetApp.getActive();

  var d = getOrCreate(ss, SHEET_DOMAINS);
  d.clear();
  d.getRange('A1:D1').setValues([['Website address', 'Client / brand', 'Status', 'Last scanned']]);
  d.getRange('A1:D1').setFontWeight('bold').setBackground('#1F3864').setFontColor('#FFFFFF');
  d.getRange('A2:A4').setValues([['example.com'], ['www.samplebrand.in'], ['']]);
  d.setColumnWidth(1, 260); d.setColumnWidth(2, 180);
  d.setColumnWidth(3, 110); d.setColumnWidth(4, 160);
  d.setFrozenRows(1);

  var r = getOrCreate(ss, SHEET_RESULTS);
  r.clear();
  var headers = [
    'Website', 'Client / brand', 'Scanned on', 'STAGE', 'Overall %',
    'Trackers found', 'Ran before agreement', 'Still ran after Reject',
    'Stopped by Reject', 'High risk', 'Advertising trackers',
    'Banner found?', 'Reject button found?', 'Consent tool',
    'Privacy policy linked?', 'Forms found', 'Forms stating a purpose',
    'Forms with pre-ticked boxes', 'Third-party cookies',
    'Trackers held back %', 'Permission asked properly %', 'Forms with a reason %',
    'Meets May 2027?', 'Notes'
  ];
  r.getRange(1, 1, 1, headers.length).setValues([headers]);
  r.getRange(1, 1, 1, headers.length)
    .setFontWeight('bold').setBackground('#1F3864').setFontColor('#FFFFFF')
    .setWrap(true).setVerticalAlignment('middle');
  r.setRowHeight(1, 46);
  r.setFrozenRows(1);
  r.setColumnWidth(1, 220); r.setColumnWidth(4, 130);

  var t = getOrCreate(ss, SHEET_TRACKERS);
  t.clear();
  var th = ['Website', 'Tracker', 'Belongs to', 'What it does', 'What it sends',
            'First seen (ms)', 'Ran before agreement', 'Still ran after Reject', 'Risk'];
  t.getRange(1, 1, 1, th.length).setValues([th]);
  t.getRange(1, 1, 1, th.length).setFontWeight('bold')
    .setBackground('#1F3864').setFontColor('#FFFFFF');
  t.setFrozenRows(1);
  t.setColumnWidth(1, 200); t.setColumnWidth(2, 180); t.setColumnWidth(5, 220);

  var l = getOrCreate(ss, SHEET_LOG);
  l.clear();
  l.getRange('A1:C1').setValues([['When', 'Website', 'What happened']]);
  l.getRange('A1:C1').setFontWeight('bold').setBackground('#1F3864').setFontColor('#FFFFFF');
  l.setFrozenRows(1);

  SpreadsheetApp.getUi().alert('Sheets are ready. Paste your website addresses into the Domains sheet.');
}

function getOrCreate(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

/* -------------------------------------------------------------- scanning */

function scanAllPending() {
  var ss = SpreadsheetApp.getActive();
  var d = ss.getSheetByName(SHEET_DOMAINS);
  if (!d) { alert('Run "Set up the sheets" first.'); return; }

  var last = d.getLastRow();
  if (last < 2) { alert('No website addresses found.'); return; }

  var rows = d.getRange(2, 1, last - 1, 4).getValues();
  var done = 0, failed = 0;

  for (var i = 0; i < rows.length; i++) {
    var domain = String(rows[i][0] || '').trim();
    var brand = String(rows[i][1] || '').trim();
    var status = String(rows[i][2] || '').trim();
    if (!domain) continue;
    if (status === 'Done') continue;

    d.getRange(i + 2, 3).setValue('Scanning...');
    SpreadsheetApp.flush();

    var res = callService(domain);
    if (res && res.ok !== false) {
      writeResult(res, brand);
      d.getRange(i + 2, 3).setValue('Done');
      d.getRange(i + 2, 4).setValue(new Date());
      log(domain, 'Scanned — ' + (res.score ? res.score.stage : 'no score'));
      done++;
    } else {
      d.getRange(i + 2, 3).setValue('Failed');
      log(domain, 'Failed — ' + (res && res.error ? res.error : 'no response'));
      failed++;
    }
    SpreadsheetApp.flush();
  }

  alert('Finished. ' + done + ' scanned, ' + failed + ' failed.');
}

function scanSelectedRow() {
  var ss = SpreadsheetApp.getActive();
  var sheet = ss.getActiveSheet();
  if (sheet.getName() !== SHEET_DOMAINS) { alert('Select a row on the Domains sheet first.'); return; }

  var row = sheet.getActiveRange().getRow();
  if (row < 2) { alert('Select a row below the header.'); return; }

  var domain = String(sheet.getRange(row, 1).getValue() || '').trim();
  var brand = String(sheet.getRange(row, 2).getValue() || '').trim();
  if (!domain) { alert('That row has no website address.'); return; }

  sheet.getRange(row, 3).setValue('Scanning...');
  SpreadsheetApp.flush();

  var res = callService(domain);
  if (res && res.ok !== false) {
    writeResult(res, brand);
    sheet.getRange(row, 3).setValue('Done');
    sheet.getRange(row, 4).setValue(new Date());
    alert(domain + '\n\nStage: ' + res.score.stage +
          '\nTrackers: ' + res.trackers.total +
          '\nRan before agreement: ' + res.trackers.ranBeforeConsent);
  } else {
    sheet.getRange(row, 3).setValue('Failed');
    alert('Scan failed: ' + (res && res.error ? res.error : 'no response'));
  }
}

/* ------------------------------------------------------------ the service */

function callService(domain) {
  var props = PropertiesService.getScriptProperties();
  var url = props.getProperty('SCAN_URL');
  var key = props.getProperty('SCAN_KEY');

  if (!url || !key) {
    return { ok: false, error: 'SCAN_URL or SCAN_KEY not set in Script Properties' };
  }

  try {
    var resp = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'x-scan-key': key },
      payload: JSON.stringify({ domain: domain }),
      muteHttpExceptions: true
    });
    var code = resp.getResponseCode();
    var body = resp.getContentText();
    if (code !== 200) return { ok: false, error: 'HTTP ' + code + ' — ' + body.slice(0, 200) };
    return JSON.parse(body);
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 200) };
  }
}

/* -------------------------------------------------------------- writing */

function writeResult(r, brand) {
  var ss = SpreadsheetApp.getActive();
  var res = ss.getSheetByName(SHEET_RESULTS);
  var t = r.trackers || {};
  var s = r.score || {};
  var forms = r.forms || [];

  var row = [
    r.url,
    brand,
    new Date(r.scannedAt),
    s.stage || '',
    s.overall,
    t.total, t.ranBeforeConsent, t.stillRanAfterReject, t.stoppedByReject,
    t.highRisk, t.advertising,
    r.bannerFound ? 'Yes' : 'No',
    r.rejectFound ? 'Yes' : 'No',
    (r.cmpDetected || []).join(', ') || 'None found',
    r.privacyPolicy && r.privacyPolicy.found ? 'Yes' : 'No',
    forms.length,
    forms.filter(function (f) { return f.statesPurpose; }).length,
    forms.filter(function (f) { return f.preTickedBoxes > 0; }).length,
    r.cookies ? r.cookies.thirdParty : '',
    s.trackersHeldBack, s.permissionAskedProperly, s.formsCollectWithReason,
    s.meetsMay2027 ? 'On course' : 'Not on course',
    r.error || ''
  ];

  res.appendRow(row);
  var n = res.getLastRow();

  // colour the stage cell
  var stage = String(s.stage || '');
  var colour = stage.indexOf('1') === 0 || stage.indexOf('2') === 0 ? '#E8631A'
             : stage.indexOf('3') === 0 ? '#B8860B' : '#1B7F5A';
  res.getRange(n, 4).setFontColor('#FFFFFF').setBackground(colour).setFontWeight('bold');

  // percentages
  res.getRange(n, 5).setNumberFormat('0.0%');
  res.getRange(n, 20, 1, 3).setNumberFormat('0.0%');

  writeTrackers(r);
}

function writeTrackers(r) {
  var ss = SpreadsheetApp.getActive();
  var t = ss.getSheetByName(SHEET_TRACKERS);
  var list = (r.trackers && r.trackers.list) || [];
  if (!list.length) return;

  var rows = list.map(function (x) {
    return [
      r.url, x.name, x.owner, x.purpose, x.sends,
      x.firstMs,
      x.ranBefore ? 'Yes' : 'No',
      x.ranAfter ? 'Yes' : 'No',
      x.risk
    ];
  });
  t.getRange(t.getLastRow() + 1, 1, rows.length, 9).setValues(rows);
}

/* --------------------------------------------------------------- extras */

function clearResults() {
  var ui = SpreadsheetApp.getUi();
  var ok = ui.alert('Clear all results?', 'This deletes everything on the Results and Trackers sheets.',
                    ui.ButtonSet.YES_NO);
  if (ok !== ui.Button.YES) return;

  var ss = SpreadsheetApp.getActive();
  [SHEET_RESULTS, SHEET_TRACKERS].forEach(function (n) {
    var sh = ss.getSheetByName(n);
    if (sh && sh.getLastRow() > 1) {
      sh.getRange(2, 1, sh.getLastRow() - 1, sh.getLastColumn()).clear();
    }
  });

  var d = ss.getSheetByName(SHEET_DOMAINS);
  if (d && d.getLastRow() > 1) d.getRange(2, 3, d.getLastRow() - 1, 2).clearContent();
}

function testConnection() {
  var props = PropertiesService.getScriptProperties();
  var url = props.getProperty('SCAN_URL');
  if (!url) { alert('SCAN_URL is not set in Script Properties.'); return; }
  try {
    var health = url.replace(/\/scan$/, '/health');
    var resp = UrlFetchApp.fetch(health, { muteHttpExceptions: true });
    alert('Service replied with HTTP ' + resp.getResponseCode() + '\n\n' +
          resp.getContentText().slice(0, 300));
  } catch (e) {
    alert('Could not reach the service:\n' + e);
  }
}

function log(domain, message) {
  var l = SpreadsheetApp.getActive().getSheetByName(SHEET_LOG);
  if (l) l.appendRow([new Date(), domain, message]);
}

function alert(msg) {
  SpreadsheetApp.getUi().alert(msg);
}
