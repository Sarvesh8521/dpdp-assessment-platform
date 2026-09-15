/**
 * Tracker signatures.
 *
 * Each entry matches a network request by hostname or URL fragment and tells us
 * who it belongs to, what it is for, and what it typically sends.
 *
 * TO ADD A NEW TRACKER: copy any block below and change the values. No other
 * file needs editing. Keep `match` lowercase.
 */

const VENDORS = [
  // ---- Advertising and retargeting (highest risk under DPDP) ----
  { match: 'facebook.net',            name: 'Meta Pixel',              owner: 'Meta',      purpose: 'advertising', sends: 'Page, device, browser ID', risk: 'High' },
  { match: 'facebook.com/tr',         name: 'Meta Pixel',              owner: 'Meta',      purpose: 'advertising', sends: 'Page, device, browser ID', risk: 'High' },
  { match: 'googleadservices.com',    name: 'Google Ads conversion',   owner: 'Google',    purpose: 'advertising', sends: 'Page, click ID',          risk: 'High' },
  { match: 'googlesyndication.com',   name: 'Google Ads remarketing',  owner: 'Google',    purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'doubleclick.net',         name: 'Google DoubleClick',      owner: 'Google',    purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'criteo',                  name: 'Criteo retargeting',      owner: 'Criteo',    purpose: 'advertising', sends: 'Page, product viewed',    risk: 'High' },
  { match: 'taboola',                 name: 'Taboola',                 owner: 'Taboola',   purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'outbrain',                name: 'Outbrain',                owner: 'Outbrain',  purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'ads.linkedin.com',        name: 'LinkedIn Ads',            owner: 'Microsoft', purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'snap.licdn.com',          name: 'LinkedIn Insight',        owner: 'Microsoft', purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'bat.bing.com',            name: 'Microsoft Ads UET',       owner: 'Microsoft', purpose: 'advertising', sends: 'Page, click ID',          risk: 'High' },
  { match: 'analytics.twitter.com',   name: 'X (Twitter) Pixel',       owner: 'X',         purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'ads.tiktok.com',          name: 'TikTok Pixel',            owner: 'TikTok',    purpose: 'advertising', sends: 'Page, device',            risk: 'High' },
  { match: 'analytics.tiktok.com',    name: 'TikTok Analytics',        owner: 'TikTok',    purpose: 'advertising', sends: 'Page, device',            risk: 'High' },
  { match: 'sc-static.net',           name: 'Snap Pixel',              owner: 'Snap',      purpose: 'advertising', sends: 'Page, device',            risk: 'High' },
  { match: 'amazon-adsystem.com',     name: 'Amazon Ads',              owner: 'Amazon',    purpose: 'advertising', sends: 'Page, browser ID',        risk: 'High' },
  { match: 'moatads.com',             name: 'Moat',                    owner: 'Oracle',    purpose: 'advertising', sends: 'Page, viewability',       risk: 'Medium' },

  // ---- Analytics ----
  { match: 'google-analytics.com',    name: 'Google Analytics',        owner: 'Google',    purpose: 'analytics',   sends: 'Page, device, location',  risk: 'High' },
  { match: 'analytics.google.com',    name: 'Google Analytics 4',      owner: 'Google',    purpose: 'analytics',   sends: 'Page, device, location',  risk: 'High' },
  { match: 'googletagmanager.com/gtag', name: 'Google gtag',           owner: 'Google',    purpose: 'analytics',   sends: 'Page, device, location',  risk: 'High' },
  { match: 'clarity.ms',              name: 'Microsoft Clarity',       owner: 'Microsoft', purpose: 'analytics',   sends: 'Screen recording',        risk: 'High' },
  { match: 'hotjar',                  name: 'Hotjar',                  owner: 'Contentsquare', purpose: 'analytics', sends: 'Screen recording',      risk: 'High' },
  { match: 'mixpanel',                name: 'Mixpanel',                owner: 'Mixpanel',  purpose: 'analytics',   sends: 'Page, events',            risk: 'Medium' },
  { match: 'amplitude',               name: 'Amplitude',               owner: 'Amplitude', purpose: 'analytics',   sends: 'Page, events',            risk: 'Medium' },
  { match: 'segment.com',             name: 'Segment',                 owner: 'Twilio',    purpose: 'analytics',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'segment.io',              name: 'Segment',                 owner: 'Twilio',    purpose: 'analytics',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'fullstory',               name: 'FullStory',               owner: 'FullStory', purpose: 'analytics',   sends: 'Screen recording',        risk: 'High' },
  { match: 'quantserve',              name: 'Quantcast Measure',       owner: 'Quantcast', purpose: 'analytics',   sends: 'Page, browser ID',        risk: 'Medium' },
  { match: 'scorecardresearch',       name: 'Comscore',                owner: 'Comscore',  purpose: 'analytics',   sends: 'Page, browser ID',        risk: 'Medium' },

  // ---- Marketing and CRM platforms (common in India) ----
  { match: 'clevertap',               name: 'CleverTap',               owner: 'CleverTap', purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'moengage',                name: 'MoEngage',                owner: 'MoEngage',  purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'webengage',               name: 'WebEngage',               owner: 'WebEngage', purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'netcorecloud',            name: 'Netcore',                 owner: 'Netcore',   purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'hubspot',                 name: 'HubSpot',                 owner: 'HubSpot',   purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'pardot',                  name: 'Salesforce Pardot',       owner: 'Salesforce',purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'marketo',                 name: 'Adobe Marketo',           owner: 'Adobe',     purpose: 'marketing',   sends: 'Page, events, identity',  risk: 'High' },
  { match: 'demdex.net',              name: 'Adobe Audience Manager',  owner: 'Adobe',     purpose: 'marketing',   sends: 'Page, browser ID',        risk: 'High' },
  { match: 'omtrdc.net',              name: 'Adobe Analytics',         owner: 'Adobe',     purpose: 'analytics',   sends: 'Page, device',            risk: 'High' },
  { match: 'leadsquared',             name: 'LeadSquared',             owner: 'LeadSquared', purpose: 'marketing', sends: 'Page, events, identity',  risk: 'High' },
  { match: 'freshchat',               name: 'Freshchat',               owner: 'Freshworks',purpose: 'support',     sends: 'Page, chat identity',     risk: 'Medium' },
  { match: 'intercom',                name: 'Intercom',                owner: 'Intercom',  purpose: 'support',     sends: 'Page, chat identity',     risk: 'Medium' },
  { match: 'haptik',                  name: 'Haptik',                  owner: 'Jio',       purpose: 'support',     sends: 'Page, chat identity',     risk: 'Medium' },

  // ---- Tag managers (containers — they load the others) ----
  { match: 'googletagmanager.com/gtm', name: 'Google Tag Manager',     owner: 'Google',    purpose: 'container',   sends: 'Loads other trackers',    risk: 'Medium' },
  { match: 'assets.adobedtm.com',     name: 'Adobe Launch',            owner: 'Adobe',     purpose: 'container',   sends: 'Loads other trackers',    risk: 'Medium' },
  { match: 'tealiumiq',               name: 'Tealium',                 owner: 'Tealium',   purpose: 'container',   sends: 'Loads other trackers',    risk: 'Medium' }
];

/**
 * Consent tools. If we find one of these, the site has at least tried.
 */
const CMPS = [
  { match: 'onetrust',        name: 'OneTrust' },
  { match: 'cookielaw.org',   name: 'OneTrust' },
  { match: 'cookieyes',       name: 'CookieYes' },
  { match: 'cookiebot',       name: 'Cookiebot' },
  { match: 'usercentrics',    name: 'Usercentrics' },
  { match: 'trustarc',        name: 'TrustArc' },
  { match: 'quantcast.mgr',   name: 'Quantcast Choice' },
  { match: 'sourcepoint',     name: 'Sourcepoint' },
  { match: 'termly',          name: 'Termly' },
  { match: 'iubenda',         name: 'Iubenda' },
  { match: 'securiti',        name: 'Securiti' },
  { match: 'consentmanager',  name: 'ConsentManager' }
];

function identify(url) {
  const u = (url || '').toLowerCase();
  for (const v of VENDORS) {
    if (u.includes(v.match)) return v;
  }
  return null;
}

function identifyCMP(url) {
  const u = (url || '').toLowerCase();
  for (const c of CMPS) {
    if (u.includes(c.match)) return c;
  }
  return null;
}

module.exports = { VENDORS, CMPS, identify, identifyCMP };
