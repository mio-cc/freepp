#!/usr/bin/env node
/*
 * min-implant bridge-load hCaptcha passive token helper (plan §3.1).
 *
 * Port of openai-paypal-main/tools/hcaptcha_passive_node.js:
 *   - loads ONLY paypalobjects hcaptchapassive_eval.html in happy-dom
 *   - official passive JS runs; we harvest parent.postMessage {token, renderData}
 *   - does NOT open paypal.com, does NOT invent tokens
 *
 * min-implant deltas vs sister:
 *   - Mac Chrome 146 default UA (align ba_authorize TLS claim)
 *   - happy-dom 20 enableJavaScriptEvaluation
 *   - optional sticky proxy via https-proxy-agent (input.proxy / HTTPS_PROXY)
 *   - local NODE_PATH / GPT_PLUS happy-dom discovery
 *
 * stdin JSON:
 *   { iframeUrl, parentUrl?, userAgent?, timeoutMs?, html?, proxy?,
 *     browserProfile?, screen?, viewport?, acceptLanguage? }
 * stdout JSON:
 *   { ok, token, renderData, error, elapsedMs, states, iframeCount, iframeSrcs }
 */
'use strict';

const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// Proxy — Node does not honor HTTPS_PROXY natively
// ---------------------------------------------------------------------------
function installProxyFromEnv(proxyOverride) {
  const proxyUrl =
    (proxyOverride && String(proxyOverride).trim()) ||
    process.env.HTTPS_PROXY ||
    process.env.HTTP_PROXY ||
    process.env.ALL_PROXY ||
    process.env.https_proxy ||
    process.env.http_proxy ||
    '';
  if (!proxyUrl) {
    console.error('[hcap-bridge] proxy=none (direct)');
    return null;
  }
  let HttpsProxyAgent;
  try {
    HttpsProxyAgent = require('https-proxy-agent').HttpsProxyAgent;
  } catch (_) {
    try {
      HttpsProxyAgent = require(path.join(__dirname, 'node_modules', 'https-proxy-agent'))
        .HttpsProxyAgent;
    } catch (e2) {
      console.error('[hcap-bridge] https-proxy-agent missing; proxy ignored:', e2.message);
      return null;
    }
  }
  const agent = new HttpsProxyAgent(proxyUrl);
  const patch = (mod, name) => {
    const origRequest = mod.request.bind(mod);
    const origGet = mod.get ? mod.get.bind(mod) : null;
    mod.request = function patchedRequest(...args) {
      try {
        if (typeof args[0] === 'string' || args[0] instanceof URL) {
          if (typeof args[1] === 'object' && args[1] !== null && typeof args[1] !== 'function') {
            const host = args[1].hostname || args[1].host || '';
            if (!host || (host !== '127.0.0.1' && host !== 'localhost')) {
              args[1] = { ...args[1], agent };
            }
          } else {
            const u = typeof args[0] === 'string' ? new URL(args[0]) : args[0];
            if (u.hostname !== '127.0.0.1' && u.hostname !== 'localhost') {
              const cb = typeof args[1] === 'function' ? args[1] : args[2];
              const opts = { agent };
              args = cb ? [args[0], opts, cb] : [args[0], opts];
            }
          }
        } else if (typeof args[0] === 'object' && args[0] !== null) {
          const host = args[0].hostname || args[0].host || '';
          if (!host || (host !== '127.0.0.1' && host !== 'localhost')) {
            args[0] = { ...args[0], agent };
          }
        }
      } catch (e) {
        console.error('[hcap-bridge] proxy patch warn', e && e.message);
      }
      return origRequest(...args);
    };
    if (origGet) {
      mod.get = function patchedGet(...args) {
        const req = mod.request(...args);
        req.end();
        return req;
      };
    }
    console.error(`[hcap-bridge] patched ${name}.request with proxy agent`);
  };
  patch(https, 'https');
  patch(http, 'http');
  try {
    const u = new URL(proxyUrl);
    console.error(`[hcap-bridge] proxy=${u.protocol}//${u.hostname}:${u.port || ''}`);
  } catch (_) {
    console.error('[hcap-bridge] proxy=set');
  }
  return agent;
}

// ---------------------------------------------------------------------------
// happy-dom
// ---------------------------------------------------------------------------
function loadHappyDOM() {
  const candidates = [
    'happy-dom',
    process.env.HAPPY_DOM_PATH || '',
    path.join(__dirname, 'node_modules', 'happy-dom'),
    'C:/Users/Administrator/Desktop/GPT_PLUS_PP纯协议版/webui/frontend/node_modules/happy-dom',
  ].filter(Boolean);
  let lastErr = null;
  for (const name of candidates) {
    try {
      return require(name);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error('happy-dom not found');
}

const happy = loadHappyDOM();
const { Window, Browser } = happy;

// Align with ba_authorize Mac Chrome 146 + curl_cffi TLS profile
const DEFAULT_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

/**
 * happy-dom often evaluates multi-block inline scripts in isolated scopes.
 * Merge them into one body script so sendMessageToParent / callbacks stay global.
 * (Ported from ba_fp_helpers/hcaptcha_passive_node.js)
 */
/**
 * Force api.js host to js.hcaptcha.com when hcaptcha.paypal.com TLS fails
 * through residential proxies, while keeping PayPal customDomains query.
 * Must rewrite BEFORE the loader IIFE runs (patch source text, not post-hoc).
 */
function forcePublicHcaptchaApiHost(html) {
  // Default OFF: PayPal passive iframe ships customDomains → hcaptcha.paypal.com.
  // Forcing js.hcaptcha.com broke asset host alignment (G11: newassets.hcaptcha.com
  // vs paypal custom). Enable only with MIN_BA_HCAP_FORCE_PUBLIC_API=1.
  if (String(process.env.MIN_BA_HCAP_FORCE_PUBLIC_API || '').trim() !== '1') {
    console.error('[hcap-bridge] forcePublicApi skipped (keep PayPal customDomains)');
    return html;
  }
  if (!html) return html;
  let out = html;
  let n = 0;
  // Core line inside getHcaptchaDomain:
  //   return 'https://' + clientDomain + '/1/api.js?onload=...'+ (customDomains?...)
  out = out.replace(
    /return\s+'https:\/\/'\s*\+\s*clientDomain\s*\+\s*'\/1\/api\.js\?onload=hCaptchaPassiveEvalCallback&render=explicit'\s*\+\s*\(customDomains\s*\?\s*'&'\s*\+\s*customDomains\s*:\s*''\)/g,
    () => {
      n += 1;
      return (
        "return 'https://js.hcaptcha.com/1/api.js?onload=hCaptchaPassiveEvalCallback&render=explicit'" +
        " + (customDomains ? '&' + customDomains : '')"
      );
    }
  );
  // Also neuter any hard-coded hcaptcha.paypal.com api.js URLs
  out = out.replace(
    /https:\/\/hcaptcha\.paypal\.com\/1\/api\.js/gi,
    () => {
      n += 1;
      return 'https://js.hcaptcha.com/1/api.js';
    }
  );
  // safeScriptSrc may still reject — ensure DEFAULT path preferred
  out = out.replace(
    /hCaptchaScript\.src\s*=\s*safeScriptSrc\(builtHcSrc,\s*DEFAULT_HC_SRC\)/g,
    () => {
      n += 1;
      return 'hCaptchaScript.src = DEFAULT_HC_SRC + (locale ? "&hl=" + encodeURIComponent(locale) : "")';
    }
  );
  console.error('[hcap-bridge] forcePublicApi rewrites=', n);
  return out;
}

function mergeInlineScripts(html) {
  const parts = [];
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  let m;
  let out = html;
  const replacements = [];
  while ((m = re.exec(html))) {
    const attrs = m[1] || '';
    const body = m[2] || '';
    if (/\bsrc\s*=/i.test(attrs)) continue;
    if (!body.trim()) continue;
    parts.push(body);
    replacements.push(m[0]);
  }
  if (parts.length <= 1) return html;
  for (const block of replacements) {
    out = out.replace(block, '<!-- merged-inline-script -->');
  }
  const promote = `
try {
  if (typeof hCaptchaPassiveEvalCallback === 'function') window.hCaptchaPassiveEvalCallback = hCaptchaPassiveEvalCallback;
  if (typeof verifyHPCallback === 'function') window.verifyHPCallback = verifyHPCallback;
  if (typeof onLoad === 'function') window.onLoad = onLoad;
  if (typeof onError === 'function') window.onError = onError;
  if (typeof checkConnection === 'function') window.checkConnection = checkConnection;
  if (typeof getLocale === 'function') window.getLocale = getLocale;
  if (typeof getHcaptchaDomain === 'function') window.getHcaptchaDomain = getHcaptchaDomain;
  if (typeof getKey === 'function') window.getKey = getKey;
  if (typeof getTargetOrigin === 'function') window.getTargetOrigin = getTargetOrigin;
  if (typeof sendMessageToParent === 'function') window.sendMessageToParent = sendMessageToParent;
} catch (__e2) {}
`;
  let bodyJoined;
  if (parts.length >= 2) {
    const head = parts.slice(0, -1).join('\n;\n');
    const tail = parts[parts.length - 1];
    bodyJoined = head + '\n;\n' + promote + '\n;\n' + tail + '\n;\n' + promote;
  } else {
    bodyJoined = parts.join('\n;\n') + '\n;\n' + promote;
  }
  const combined = '<script type="text/javascript">\n' + bodyJoined + '\n</script>';
  if (/<\/body>/i.test(out)) {
    out = out.replace(/<\/body>/i, combined + '\n</body>');
  } else {
    out = out + combined;
  }
  return out;
}

let __bridgeRewriteHtml = '';

function makeBridgeInterceptor(ResponseCtor) {
  return {
    async beforeAsyncRequest({ request, window }) {
      try {
        const u = String((request && request.url) || '');
        if (
          __bridgeRewriteHtml &&
          /hcaptchapassive(?:_eval)?\.html/i.test(u) &&
          (!request.method || request.method === 'GET')
        ) {
          const R = (window && window.Response) || ResponseCtor || globalThis.Response;
          if (R) {
            console.error('[hcap-bridge] rewrite bridge html len=', __bridgeRewriteHtml.length);
            return new R(__bridgeRewriteHtml, {
              status: 200,
              headers: { 'Content-Type': 'text/html; charset=utf-8' },
            });
          }
        }
      } catch (e) {
        console.error('[hcap-bridge] interceptor warn', e && e.message);
      }
    },
  };
}

function readStdin() {
  return fs.readFileSync(0, 'utf8');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fetchText(url, userAgent, acceptLanguage = 'en-US,en;q=0.9', redirects = 0) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const mod = u.protocol === 'http:' ? http : https;
    const req = mod.get(
      u,
      {
        headers: {
          'user-agent': userAgent || DEFAULT_UA,
          accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'accept-language': acceptLanguage,
          referer: 'https://www.paypal.com/',
        },
      },
      (res) => {
        const status = res.statusCode || 0;
        const loc = res.headers.location;
        if (status >= 300 && status < 400 && loc && redirects < 5) {
          res.resume();
          fetchText(new URL(loc, url).toString(), userAgent, acceptLanguage, redirects + 1).then(
            resolve,
            reject
          );
          return;
        }
        const chunks = [];
        res.on('data', (d) => chunks.push(Buffer.from(d)));
        res.on('end', () => {
          if (status < 200 || status >= 300) {
            reject(new Error(`GET ${url} status=${status}`));
            return;
          }
          resolve(Buffer.concat(chunks).toString('utf8'));
        });
      }
    );
    req.on('error', reject);
    req.setTimeout(30000, () => {
      req.destroy(new Error(`GET ${url} timeout`));
    });
  });
}

function define(obj, key, value) {
  try {
    Object.defineProperty(obj, key, {
      configurable: true,
      enumerable: true,
      get: typeof value === 'function' ? value : () => value,
    });
  } catch (_) {
    try {
      obj[key] = typeof value === 'function' ? value() : value;
    } catch (_) {}
  }
}

function patchCanvas(win) {
  try {
    if (!win.HTMLCanvasElement || !win.HTMLCanvasElement.prototype) return;
    win.HTMLCanvasElement.prototype.getContext = function getContext(type) {
      const ctx = {
        canvas: this,
        fillRect() {},
        clearRect() {},
        getImageData() {
          return { data: new win.Uint8ClampedArray(4) };
        },
        putImageData() {},
        createImageData() {
          return { data: new win.Uint8ClampedArray(4) };
        },
        setTransform() {},
        resetTransform() {},
        drawImage() {},
        save() {},
        restore() {},
        beginPath() {},
        closePath() {},
        moveTo() {},
        lineTo() {},
        bezierCurveTo() {},
        quadraticCurveTo() {},
        arc() {},
        rect() {},
        clip() {},
        stroke() {},
        fill() {},
        fillText() {},
        strokeText() {},
        translate() {},
        scale() {},
        rotate() {},
        measureText() {
          return { width: 42 };
        },
        transform() {},
        createLinearGradient() {
          return { addColorStop() {} };
        },
        createPattern() {
          return null;
        },
        getExtension() {
          return null;
        },
        getParameter() {
          if (type && String(type).toLowerCase().includes('webgl')) {
            return '';
          }
          return '';
        },
      };
      return ctx;
    };
    win.HTMLCanvasElement.prototype.toDataURL = function toDataURL() {
      return 'data:image/png;base64,iVBORw0KGgo=';
    };
  } catch (_) {}
}

function patchNavigator(win, userAgent, profile = {}) {
  const nav = win.navigator;
  const language = String(profile.language || 'en-US');
  const languages =
    Array.isArray(profile.languages) && profile.languages.length
      ? profile.languages
      : [language, language.split('-')[0]].filter(Boolean);
  const platform = String(profile.platform || 'MacIntel');
  const vendor = String(profile.vendor || 'Google Inc.');
  try {
    nav.userAgent = userAgent;
  } catch (_) {}
  define(nav, 'userAgent', userAgent);
  define(nav, 'appCodeName', 'Mozilla');
  define(nav, 'appName', 'Netscape');
  define(nav, 'appVersion', userAgent.replace(/^Mozilla\//, ''));
  define(nav, 'platform', platform);
  define(nav, 'vendor', vendor);
  define(nav, 'language', language);
  define(nav, 'languages', languages);
  define(nav, 'webdriver', undefined);
  define(nav, 'deviceMemory', Number(profile.device_memory || profile.deviceMemory || 8));
  define(
    nav,
    'hardwareConcurrency',
    Number(profile.hardware_concurrency || profile.hardwareConcurrency || 8)
  );
  define(nav, 'maxTouchPoints', 0);
  define(nav, 'cookieEnabled', true);
  define(nav, 'onLine', true);
  try {
    define(nav, 'plugins', [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      {
        name: 'Chrome PDF Viewer',
        filename: 'internal-pdf-viewer',
        description: 'Portable Document Format',
      },
    ]);
    define(nav, 'mimeTypes', [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ]);
  } catch (_) {}
}

function patchWindow(
  win,
  userAgent,
  parentUrl,
  parentPostMessage,
  profile = {},
  screenProfile = {},
  viewport = {}
) {
  if (!win) return;
  // Always rebind parent.postMessage (navigation may rebuild window)
  try {
    if (parentPostMessage) {
      Object.defineProperty(win, 'parent', {
        configurable: true,
        value: { postMessage: parentPostMessage },
      });
    }
  } catch (_) {}

  if (win.__pps_hcap_bridge_patched) return;
  try {
    Object.defineProperty(win, '__pps_hcap_bridge_patched', { value: true, configurable: true });
  } catch (_) {
    win.__pps_hcap_bridge_patched = true;
  }

  patchNavigator(win, userAgent, profile);
  patchCanvas(win);
  const screenWidth = Number(screenProfile.width || 1440);
  const screenHeight = Number(screenProfile.height || 900);
  const availWidth = Number(screenProfile.availWidth || screenWidth);
  const availHeight = Number(screenProfile.availHeight || Math.max(1, screenHeight - 40));
  const innerWidth = Number(viewport.width || Math.max(1000, screenWidth - 160));
  const innerHeight = Number(viewport.height || Math.max(650, screenHeight - 120));
  try {
    win.screen.width = screenWidth;
    win.screen.height = screenHeight;
    win.screen.availWidth = availWidth;
    win.screen.availHeight = availHeight;
    win.screen.colorDepth = Number(screenProfile.colorDepth || 24);
    win.screen.pixelDepth = Number(screenProfile.pixelDepth || 24);
  } catch (_) {}
  try {
    win.outerWidth = innerWidth + 16;
    win.outerHeight = innerHeight + 88;
    win.innerWidth = innerWidth;
    win.innerHeight = innerHeight;
    win.devicePixelRatio = Number(profile.device_pixel_ratio || profile.devicePixelRatio || 2);
  } catch (_) {}
  try {
    define(win.document, 'hidden', false);
    define(win.document, 'visibilityState', 'visible');
  } catch (_) {}
  try {
    Object.defineProperty(win.document, 'referrer', {
      value: parentUrl || 'https://www.paypal.com/',
      configurable: true,
    });
  } catch (_) {}
  try {
    Object.defineProperty(win.location, 'ancestorOrigins', {
      value: ['https://www.paypal.com'],
      configurable: true,
    });
  } catch (_) {}
  try {
    win.matchMedia =
      win.matchMedia ||
      function matchMedia(query) {
        return {
          matches: /landscape/.test(String(query)),
          media: query,
          onchange: null,
          addListener() {},
          removeListener() {},
          addEventListener() {},
          removeEventListener() {},
          dispatchEvent() {
            return false;
          },
        };
      };
  } catch (_) {}
  try {
    const tzOffset = Number(profile.timezone_offset_minutes ?? profile.timezoneOffsetMinutes);
    if (Number.isFinite(tzOffset)) {
      win.Date.prototype.getTimezoneOffset = function getTimezoneOffset() {
        return tzOffset;
      };
    }
    const tzName = String(profile.timezone || '');
    if (tzName && win.Intl && win.Intl.DateTimeFormat) {
      const original = win.Intl.DateTimeFormat.prototype.resolvedOptions;
      win.Intl.DateTimeFormat.prototype.resolvedOptions = function resolvedOptions() {
        const value = original.call(this);
        value.timeZone = tzName;
        return value;
      };
    }
  } catch (_) {}
  try {
    win.console = {
      log: (...args) => console.error('[hcap-bridge]', ...args),
      info: (...args) => console.error('[hcap-bridge]', ...args),
      warn: (...args) => console.error('[hcap-bridge:warn]', ...args),
      error: (...args) => console.error('[hcap-bridge:error]', ...args),
      debug: () => {},
    };
  } catch (_) {}
}

function parseMessage(data) {
  try {
    return typeof data === 'string' ? JSON.parse(data) : data;
  } catch (_) {
    return { raw: String(data || '') };
  }
}

function extractToken(msg) {
  if (!msg || typeof msg !== 'object') return '';
  if (typeof msg.token === 'string') return msg.token;
  if (msg.result && typeof msg.result === 'object' && typeof msg.result.token === 'string') {
    return msg.result.token;
  }
  if (typeof msg.result === 'string' && msg.result.length > 20) return msg.result;
  return '';
}

(async () => {
  const input = JSON.parse(readStdin() || '{}');
  const iframeUrl = String(input.iframeUrl || input.iframe_url || '').trim();
  if (!iframeUrl) throw new Error('iframeUrl is required');

  // Install proxy before any network (bridge fetch + hCaptcha API)
  if (input.proxy) {
    process.env.HTTPS_PROXY = String(input.proxy);
    process.env.HTTP_PROXY = String(input.proxy);
    process.env.ALL_PROXY = String(input.proxy);
  }
  installProxyFromEnv(input.proxy);

  const parentUrl = String(input.parentUrl || input.parent_url || 'https://www.paypal.com/').trim();
  const userAgent = String(input.userAgent || input.user_agent || DEFAULT_UA);
  const browserProfile = input.browserProfile || input.browser_profile || {};
  // Default Mac surface when profile empty
  if (!browserProfile.platform) browserProfile.platform = 'MacIntel';
  if (browserProfile.device_pixel_ratio == null && browserProfile.devicePixelRatio == null) {
    browserProfile.device_pixel_ratio = 2;
  }
  const screenProfile = input.screen || {};
  const viewport = input.viewport || {};
  const acceptLanguage = String(
    input.acceptLanguage ||
      input.accept_language ||
      `${browserProfile.language || 'en-US'},${String(browserProfile.language || 'en-US').split('-')[0]};q=0.9,en;q=0.8`
  );
  const timeoutMs = Math.max(10000, Number(input.timeoutMs || input.timeout_ms || 60000));
  const startedAt = Date.now();
  const messages = [];
  let resolvedToken = '';
  let resolvedRenderData = {};
  let terminalError = '';

  const parentPostMessage = (data, targetOrigin) => {
    const msg = parseMessage(data);
    messages.push({
      t: Date.now(),
      targetOrigin: String(targetOrigin || ''),
      msg,
    });
    if (msg && msg.log && msg.captchaState) {
      console.error('[hcap-bridge-state]', msg.captchaState);
    }
    const token = extractToken(msg);
    if (token) {
      if (token === 'NOT_REACHABLE' || token === 'RENDER_FAILURE' || token === 'EMPTY_TOKEN') {
        terminalError = token;
        console.error('[hcap-bridge] terminal', token);
      } else {
        resolvedToken = token;
        resolvedRenderData =
          (msg.renderData && typeof msg.renderData === 'object' && msg.renderData) ||
          (msg.result && msg.result.renderData) ||
          {};
        console.error('[hcap-bridge] TOKEN len', token.length);
      }
    }
  };

  let html = String(input.html || '') || (await fetchText(iframeUrl, userAgent, acceptLanguage));
  const merged = mergeInlineScripts(html);
  if (merged !== html) {
    console.error('[hcap-bridge] merged inline scripts for happy-dom global scope');
    html = merged;
  }
  // Prefer public api.js host — hcaptcha.paypal.com often TLS-fails on resi proxy
  if (String(input.forcePublicApi || input.force_public_api || '1') !== '0') {
    html = forcePublicHcaptchaApiHost(html);
    console.error('[hcap-bridge] forced js.hcaptcha.com api host');
  }
  console.error('[hcap-bridge] bridge html len=', html.length, 'url=', iframeUrl.slice(0, 120));

  const baseSettings = {
    enableJavaScriptEvaluation: true,
    suppressInsecureJavaScriptEnvironmentWarning: true,
    suppressCodeGenerationFromStringsWarning: true,
    disableJavaScriptEvaluation: false,
    disableJavaScriptFileLoading: false,
    disableCSSFileLoading: true,
    disableIframePageLoading: false,
    fetch: {
      disableSameOriginPolicy: true,
      disableStrictSSL: true,
    },
    navigation: { crossOriginPolicy: 'anyOrigin' },
    navigator: { userAgent, maxTouchPoints: 0 },
    timer: {
      maxTimeout: Math.max(timeoutMs + 15000, 90000),
      maxIntervalIterations: 500000,
    },
  };

  let rootWin = null;
  let browser = null;
  let usedStrategy = 'window-write';

  // Prefer Browser.goto so nested iframes get real contentWindow + script eval (happy-dom 20).
  if (typeof Browser === 'function') {
    try {
      usedStrategy = 'browser-goto';
      __bridgeRewriteHtml = html;
      const settings = {
        ...baseSettings,
        fetch: {
          ...baseSettings.fetch,
          interceptor: makeBridgeInterceptor(happy.Response || globalThis.Response),
        },
        navigation: {
          crossOriginPolicy: 'anyOrigin',
          beforeContentCallback: (window) => {
            try {
              // Only bind collector on the passive bridge document, not nested challenge frames.
              const href = String((window.location && window.location.href) || '');
              const isBridge = /hcaptchapassive/i.test(href) || href === iframeUrl || href.startsWith(iframeUrl.split('?')[0]);
              patchWindow(
                window,
                userAgent,
                isBridge ? parentUrl : iframeUrl,
                isBridge ? parentPostMessage : null,
                browserProfile,
                screenProfile,
                viewport
              );
              console.error(
                '[hcap-bridge] beforeContent',
                href.slice(0, 100),
                'bindCollector=',
                isBridge
              );
            } catch (e) {
              console.error('[hcap-bridge] beforeContent fail', e && e.message);
            }
          },
        },
        viewport: {
          width: Number(viewport.width || screenProfile.width || 1440),
          height: Number(viewport.height || screenProfile.height || 900),
          devicePixelRatio: Number(browserProfile.device_pixel_ratio || 2),
        },
      };
      browser = new Browser({ settings });
      const page = browser.newPage();
      rootWin = page.mainFrame.window;
      patchWindow(rootWin, userAgent, parentUrl, parentPostMessage, browserProfile, screenProfile, viewport);
      console.error('[hcap-bridge] goto', iframeUrl.slice(0, 140));
      await page.goto(iframeUrl, {
        timeout: Math.min(timeoutMs, 45000),
        referrer: parentUrl || 'https://www.paypal.com/',
      });
      rootWin = page.mainFrame.window;
      patchWindow(rootWin, userAgent, parentUrl, parentPostMessage, browserProfile, screenProfile, viewport);
    } catch (e) {
      console.error('[hcap-bridge] browser-goto fail', e && e.message);
      usedStrategy = 'window-write';
      browser = null;
      rootWin = null;
    }
  }

  if (!rootWin) {
    usedStrategy = 'window-write';
    rootWin = new Window({
      url: iframeUrl,
      width: Number(viewport.width || screenProfile.width || 1440),
      height: Number(viewport.height || screenProfile.height || 900),
      settings: baseSettings,
    });
    patchWindow(rootWin, userAgent, parentUrl, parentPostMessage, browserProfile, screenProfile, viewport);
    rootWin.document.write(html);
    rootWin.document.close();
  }

  console.error('[hcap-bridge] strategy=', usedStrategy);

  const patchTimer = setInterval(() => {
    try {
      if (!rootWin) return;
      patchWindow(rootWin, userAgent, parentUrl, parentPostMessage, browserProfile, screenProfile, viewport);
      for (const iframe of Array.from(rootWin.document.querySelectorAll('iframe'))) {
        try {
          if (iframe.contentWindow) {
            patchWindow(
              iframe.contentWindow,
              userAgent,
              iframeUrl,
              null,
              browserProfile,
              screenProfile,
              viewport
            );
          }
        } catch (_) {}
      }
    } catch (_) {}
  }, 100);

  const iframeSrcs = [];
  try {
    if (rootWin && rootWin.happyDOM && typeof rootWin.happyDOM.waitUntilComplete === 'function') {
      await rootWin.happyDOM.waitUntilComplete({ timeout: Math.min(timeoutMs, 60000) }).catch(() => {});
    } else if (browser && typeof browser.waitUntilComplete === 'function') {
      await browser.waitUntilComplete().catch(() => {});
    }
    while (Date.now() - startedAt < timeoutMs) {
      if (resolvedToken) break;
      if (terminalError === 'NOT_REACHABLE' || terminalError === 'RENDER_FAILURE') break;
      await sleep(250);
    }
    try {
      for (const iframe of Array.from((rootWin && rootWin.document.querySelectorAll('iframe')) || [])) {
        iframeSrcs.push(String(iframe.src || '').slice(0, 300));
      }
    } catch (_) {}
  } finally {
    clearInterval(patchTimer);
    __bridgeRewriteHtml = '';
    if (browser) {
      try {
        await browser.close();
      } catch (_) {}
    }
  }

  const states = messages.map((m) => m.msg && m.msg.captchaState).filter(Boolean);
  const out = {
    ok: Boolean(resolvedToken),
    token: resolvedToken,
    renderData: resolvedRenderData,
    error: resolvedToken ? '' : terminalError || 'timeout/no_token',
    elapsedMs: Date.now() - startedAt,
    states,
    iframeCount: iframeSrcs.length,
    iframeSrcs,
    messageCount: messages.length,
    helper: 'ba_hcaptcha_passive_node',
    strategy: usedStrategy,
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(resolvedToken ? 0 : 2);
})().catch((err) => {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      error: (err && (err.stack || err.message)) || String(err),
      helper: 'ba_hcaptcha_passive_node',
    })
  );
  process.exit(1);
});
