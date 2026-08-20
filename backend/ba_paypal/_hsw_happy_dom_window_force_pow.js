#!/usr/bin/env node
/**
 * Pure happy-dom hsw(req) with name-rotation-safe instanceof / typeof gates.
 * stdin JSON: { req, hswPath, userAgent?, forceMode? }
 * forceMode: none | window | window+perf | window+object | all-safe
 * stdout JSON: { ok, n, n_len, host_sum, host_unique, host_top, forced, fails, ... }
 */
'use strict';
const fs = require('fs');
const path = require('path');
const nc = require('crypto');
const ROOT = __dirname;

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const req = String(input.req || '').trim();
const hswPath = input.hswPath || path.join(ROOT, '_hsw_happy_dom_ctl.js');
const hswCode = fs.readFileSync(hswPath, 'utf8');
// Modes:
//   none | window | window+perf | window+object | all-safe | window+buffers
// window+buffers = Window + ArrayBuffer/Uint8Array/DOMStringList/Error (NO Performance* — those hang)
// window+perf HANGS (PerformanceNavigationTiming force) — do not use for e2e
const FORCE_MODE = input.forceMode || process.env.HSW_FORCE_MODE || 'window';
const CAPTURE_SEQ = !!input.captureSeq || process.env.HSW_CAPTURE_SEQ === '1';
const SEQ_LIMIT = Number(input.seqLimit || 800);

// hsw sandbox escape: Function("return process") — return fake process, don't mutate Node.
(function scrubProcessLeak() {
  const fakeProcess = {
    browser: true,
    version: '',
    versions: {},
    platform: 'win32',
    arch: 'x64',
    title: 'chrome',
    execPath: '',
    argv0: 'chrome',
    argv: ['chrome'],
    pid: 1,
    ppid: 0,
    env: {},
    cwd: () => 'C:\\',
    nextTick: (fn, ...a) => queueMicrotask(() => fn(...a)),
    binding: () => {
      throw new Error('process.binding is not supported');
    },
  };
  try {
    const RF = Function;
    const wrapped = function (...args) {
      const body = String(args[args.length - 1] || '');
      if (/return\s+process/.test(body)) {
        return function () {
          return fakeProcess;
        };
      }
      return RF(...args);
    };
    wrapped.prototype = RF.prototype;
    Object.setPrototypeOf(wrapped, RF);
    globalThis.__SafeFunction = wrapped;
    globalThis.__RealFunction = RF;
    globalThis.__FakeProcess = fakeProcess;
  } catch (_) {}
})();

const HOST = {};
const FORCED = {};
const GATE_MAP = {};
const FAIL_SAMPLES = [];
const SEQ = [];

function sumRet(v) {
  if (v === null) return 'null';
  if (v === undefined) return 'undef';
  const t = typeof v;
  if (t === 'boolean' || t === 'number') return v;
  if (t === 'bigint') return 'bigint:' + String(v);
  if (t === 'string') return 'str:' + v.slice(0, 80);
  if (t === 'function') return 'fn';
  if (t === 'object') {
    try {
      if (Array.isArray(v)) return 'arr' + v.length;
      return 'obj:' + ((v.constructor && v.constructor.name) || '?');
    } catch (e) {
      return 'obj';
    }
  }
  return t;
}

function wrapImports(imports) {
  if (!imports || !imports.a) return imports;
  const a = imports.a;
  const TYPEOF_OBJECT = new Set();
  for (const name of Object.keys(a)) {
    const fn = a[name];
    if (typeof fn !== 'function') continue;
    let src = '';
    try {
      src = Function.prototype.toString.call(fn);
    } catch (_) {}
    const m = src.match(/instanceof\s+([A-Za-z0-9_$.]+)/);
    if (m) GATE_MAP[name] = m[1];
    if (
      /typeof\s+\w+\s*===\s*\w+\(\d+\)\s*&&\s*null/.test(src) ||
      /typeof\s+\w+\s*===\s*\w+\(\d+\)\s*&&\s*\w+\s*!==\s*null/.test(src) ||
      /null\s*!==\s*\w+\s*&&\s*typeof/.test(src) ||
      (/typeof\s+\w+\s*===\s*[^)&|]{0,40}object/.test(src) && /null/.test(src))
    ) {
      TYPEOF_OBJECT.add(name);
    }
  }
  globalThis.__typeof_object = Array.from(TYPEOF_OBJECT);

  const forceWindow = FORCE_MODE !== 'none';
  const forcePerf =
    FORCE_MODE === 'window+perf' || FORCE_MODE === 'all-safe';
  const forceObject =
    FORCE_MODE === 'window+object' ||
    FORCE_MODE === 'window+perf' ||
    FORCE_MODE === 'all-safe';
  const forceBuffers =
    FORCE_MODE === 'window+buffers' ||
    FORCE_MODE === 'all-safe' ||
    FORCE_MODE === 'window+object';

  const BUFFER_TYPES = new Set([
    'ArrayBuffer',
    'Uint8Array',
    'DOMStringList',
    'Error',
  ]);
  const PERF_TYPES = new Set([
    'PerformanceNavigationTiming',
    'PerformanceResourceTiming',
  ]);

  const wrapped = {};
  for (const name of Object.keys(a)) {
    const fn = a[name];
    if (typeof fn !== 'function') {
      wrapped[name] = fn;
      continue;
    }
    const gateType = GATE_MAP[name] || null;
    const isTypeofObj = TYPEOF_OBJECT.has(name);
    let src0 = '';
    try {
      src0 = Function.prototype.toString.call(fn);
    } catch (_) {}
    wrapped[name] = function () {
      HOST[name] = (HOST[name] || 0) + 1;

      if (gateType === 'Window' && forceWindow) {
        FORCED.Window = (FORCED.Window || 0) + 1;
        if (FORCED.Window <= 20000) {
          if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
            SEQ.push({ k: name, ret: true, forced: true, src_snip: src0.slice(0, 90) });
          }
          return 1;
        }
      }
      if (
        forceWindow &&
        (gateType === 'HTMLCanvasElement' || gateType === 'CanvasRenderingContext2D')
      ) {
        FORCED[gateType] = (FORCED[gateType] || 0) + 1;
        if (FORCED[gateType] <= 200) {
          if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
            SEQ.push({ k: name, ret: true, forced: true, src_snip: src0.slice(0, 90) });
          }
          return 1;
        }
      }
      // Performance* force HANGS even at low cap — never force under window mode.
      // Real class polyfill above is the only safe path.
      if (forcePerf && gateType && PERF_TYPES.has(gateType)) {
        FORCED[gateType] = (FORCED[gateType] || 0) + 1;
        if (FORCED[gateType] <= 2) {
          if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
            SEQ.push({ k: name, ret: true, forced: true, src_snip: src0.slice(0, 90) });
          }
          return 1;
        }
      }
      if (forceBuffers && gateType && BUFFER_TYPES.has(gateType)) {
        FORCED[gateType] = (FORCED[gateType] || 0) + 1;
        if (FORCED[gateType] <= 5000) {
          if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
            SEQ.push({ k: name, ret: true, forced: true, src_snip: src0.slice(0, 90) });
          }
          return 1;
        }
      }
      if (gateType === 'Object' && forceObject) {
        FORCED.Object = (FORCED.Object || 0) + 1;
        if (FORCED.Object <= 50000) {
          if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
            SEQ.push({ k: name, ret: true, forced: true, src_snip: src0.slice(0, 90) });
          }
          return 1;
        }
      }
      if (isTypeofObj && (forceObject || forceWindow)) {
        FORCED.typeof_object = (FORCED.typeof_object || 0) + 1;
        if (FORCED.typeof_object <= 50000) {
          if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
            SEQ.push({ k: name, ret: true, forced: true, src_snip: src0.slice(0, 90) });
          }
          return 1;
        }
      }

      let ret;
      try {
        ret = fn.apply(this, arguments);
      } catch (e) {
        if (gateType && FAIL_SAMPLES.length < 30) {
          FAIL_SAMPLES.push({ gate: gateType, name, err: String(e.message || e) });
        }
        throw e;
      }
      if (gateType && !ret && FAIL_SAMPLES.length < 40) {
        FAIL_SAMPLES.push({ gate: gateType, name, ret: !!ret, i: HOST[name] });
      }
      // Align performance.now-like floats to Chrome early-mid range (~200–2500ms)
      if (typeof ret === 'number' && !Number.isInteger(ret) && ret > 0 && ret < 100000) {
        // pure often too young (~50–150); chrome early samples ~200–400
        if (ret < 180) {
          ret = 220 + (ret % 80) + Math.random();
          FORCED.timing_boost = (FORCED.timing_boost || 0) + 1;
        } else if (ret > 500) {
          ret = 200 + (ret % 400) + Math.random();
          FORCED.timing_clamp = (FORCED.timing_clamp || 0) + 1;
        }
      }
      if (CAPTURE_SEQ && SEQ.length < SEQ_LIMIT) {
        SEQ.push({
          k: name,
          ret: sumRet(ret),
          src_snip: src0.slice(0, 90).replace(/\s+/g, ' '),
        });
      }
      return ret;
    };
  }
  return Object.assign({}, imports, { a: wrapped });
}

const origInst = WebAssembly.instantiate.bind(WebAssembly);
WebAssembly.instantiate = async function (src, imports) {
  return origInst(src, wrapImports(imports));
};
const OrigInstance = WebAssembly.Instance;
function HookedInstance(module, imports) {
  return new OrigInstance(module, wrapImports(imports));
}
HookedInstance.prototype = OrigInstance.prototype;
Object.setPrototypeOf(HookedInstance, OrigInstance);
try {
  WebAssembly.Instance = HookedInstance;
} catch (_) {}

function def(obj, key, val) {
  try {
    Object.defineProperty(obj, key, {
      configurable: true,
      enumerable: true,
      get: () => val,
    });
  } catch (_) {
    try {
      obj[key] = val;
    } catch (_) {}
  }
}

(async () => {
  let happy;
  try {
    happy = require(path.join(ROOT, 'ba_fp_helpers', 'node_modules', 'happy-dom'));
  } catch (_) {
    happy = require(
      'C:/Users/Administrator/Desktop/GPT_PLUS_PP纯协议版/webui/frontend/node_modules/happy-dom'
    );
  }
  const { Browser } = happy;
  const UA0 =
    input.userAgent ||
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36';
  const browser = new Browser({
    settings: {
      enableJavaScriptEvaluation: true,
      suppressInsecureJavaScriptEnvironmentWarning: true,
      suppressCodeGenerationFromStringsWarning: true,
      disableCSSFileLoading: true,
      timer: { maxTimeout: 120000, maxIntervalIterations: 1e7 },
      fetch: { disableSameOriginPolicy: true, disableStrictSSL: true },
      navigator: { userAgent: UA0 },
      device: { prefersColorScheme: 'light', mediaType: 'screen' },
    },
  });
  const page = browser.newPage();
  try {
    await page.goto('https://newassets.hcaptcha.com/', { timeout: 8000 });
  } catch (_) {}
  const w = page.mainFrame.window;

  for (const k of [
    'process',
    'Buffer',
    'require',
    'module',
    'exports',
    'global',
    '__dirname',
    '__filename',
    'setImmediate',
    'clearImmediate',
  ]) {
    try {
      delete w[k];
    } catch (_) {}
    try {
      Object.defineProperty(w, k, {
        configurable: true,
        get() {
          return undefined;
        },
        set() {},
      });
    } catch (_) {}
  }

  const Win = w.constructor;
  try {
    Object.defineProperty(w, 'Window', { configurable: true, writable: true, value: Win });
  } catch (_) {}

  w.WebAssembly = globalThis.WebAssembly;
  w.BigInt = globalThis.BigInt;
  if (globalThis.Atomics) w.Atomics = globalThis.Atomics;
  if (globalThis.SharedArrayBuffer) w.SharedArrayBuffer = globalThis.SharedArrayBuffer;
  w.crypto = {
    subtle: nc.webcrypto.subtle,
    getRandomValues: (a) => {
      a.set(nc.randomBytes(a.length));
      return a;
    },
  };
  try {
    if (globalThis.__SafeFunction) {
      w.Function = globalThis.__SafeFunction;
      const c = w.crypto;
      if (c && c.constructor) c.constructor.constructor = globalThis.__SafeFunction;
    }
  } catch (_) {}

  try {
    const fts = Function.prototype.toString;
    Function.prototype.toString = function () {
      try {
        const s = fts.call(this);
        if (/HappyDOM|happy-dom|node:|\\Users\\|workbuddy/i.test(s)) {
          return 'function () { [native code] }';
        }
        return s;
      } catch (_) {
        return 'function () { [native code] }';
      }
    };
  } catch (_) {}

  try {
    const RO = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function () {
      const o = RO.apply(this, arguments);
      return Object.assign({}, o, {
        locale: 'zh-CN',
        timeZone: 'Asia/Shanghai',
        calendar: 'gregory',
        numberingSystem: 'latn',
      });
    };
  } catch (_) {}

  // Device profile alignment (Power-to-Device) + real-time performance.now + rAF VSync.
  // Default mid_mac_intel (not M1 Pro over-claim). Override: input.deviceProfile / MIN_BA_POW_DEVICE_PROFILE.
  let appliedProfile = null;
  try {
    const dp = require(path.join(ROOT, 'ba_fp_helpers', 'hsw_pow_device_profile.js'));
    const profileName =
      input.deviceProfile || input.profile || process.env.MIN_BA_POW_DEVICE_PROFILE || 'mid_mac_intel';
    appliedProfile = dp.applyDeviceProfile(w, profileName);
    // Caller UA wins when provided
    if (input.userAgent) {
      Object.defineProperty(w.navigator, 'userAgent', {
        get: () => UA0,
        configurable: true,
      });
    }
  } catch (_) {
    // Fallback: under-claim 4c/4GB rather than M1 Pro
    try {
      const t0 = Date.now();
      const base = 800 + Math.random() * 400;
      Object.defineProperty(w.performance, 'now', {
        configurable: true,
        value: function () {
          return base + (Date.now() - t0);
        },
      });
    } catch (_) {}
    const nav = w.navigator;
    try {
      Object.defineProperty(nav, 'userAgent', { get: () => UA0, configurable: true });
      Object.defineProperty(nav, 'platform', { get: () => 'MacIntel', configurable: true });
      Object.defineProperty(nav, 'vendor', { get: () => 'Google Inc.', configurable: true });
      Object.defineProperty(nav, 'language', { get: () => 'pt-BR', configurable: true });
      Object.defineProperty(nav, 'languages', {
        get: () => ['pt-BR', 'pt', 'en-US', 'en'],
        configurable: true,
      });
      Object.defineProperty(nav, 'webdriver', { get: () => false, configurable: true });
      Object.defineProperty(nav, 'hardwareConcurrency', { get: () => 4, configurable: true });
      Object.defineProperty(nav, 'deviceMemory', { get: () => 4, configurable: true });
      Object.defineProperty(nav, 'maxTouchPoints', { get: () => 0, configurable: true });
    } catch (_) {}
  }

  try {
    const pluginData = [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    ];
    const plugins = pluginData.map((p) => {
      const mime = {
        type: 'application/pdf',
        suffixes: 'pdf',
        description: 'Portable Document Format',
        enabledPlugin: null,
      };
      const plug = {
        ...p,
        length: 1,
        item: (j) => (j === 0 ? mime : null),
        namedItem: () => mime,
        0: mime,
      };
      mime.enabledPlugin = plug;
      return plug;
    });
    plugins.item = (i) => plugins[i] || null;
    plugins.namedItem = (n) => plugins.find((p) => p.name === n) || null;
    plugins.refresh = () => {};
    Object.defineProperty(nav, 'plugins', { get: () => plugins, configurable: true });
    const mimes = [
      { type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: plugins[0] },
    ];
    mimes.item = (i) => mimes[i] || null;
    mimes.namedItem = (n) => mimes.find((m) => m.type === n) || null;
    Object.defineProperty(nav, 'mimeTypes', { get: () => mimes, configurable: true });
  } catch (_) {}

  w.chrome = {
    runtime: {
      OnInstalledReason: {},
      OnRestartRequiredReason: {},
      PlatformArch: {},
      PlatformNaclArch: {},
      PlatformOs: {},
      RequestUpdateCheckStatus: {},
      id: undefined,
      connect: undefined,
      sendMessage: undefined,
    },
    loadTimes() {
      return {
        connectionInfo: 'h2',
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true,
        npnNegotiatedProtocol: 'h2',
      };
    },
    csi() {
      return { startE: Date.now() - 1200, onloadT: Date.now() - 400, pageT: 800, tran: 15 };
    },
    app: { isInstalled: false, getDetails: () => null, getIsInstalled: () => false },
  };
  // Prefer device-profile screen if applied; else default mid laptop
  const sc0 = (appliedProfile && appliedProfile.screen) || {
    width: 1680,
    height: 1050,
    availWidth: 1680,
    availHeight: 1025,
    colorDepth: 24,
    pixelDepth: 24,
  };
  def(w, 'screen', {
    width: sc0.width || 1680,
    height: sc0.height || 1050,
    availWidth: sc0.availWidth || sc0.width || 1680,
    availHeight: sc0.availHeight || (sc0.height || 1050) - 25,
    colorDepth: sc0.colorDepth || 24,
    pixelDepth: sc0.pixelDepth || 24,
    availLeft: 0,
    availTop: 0,
    orientation: { type: 'landscape-primary', angle: 0 },
  });
  w.matchMedia = function (q) {
    const s = String(q || '');
    return {
      matches:
        s.includes('prefers-color-scheme: light') ||
        s.includes('pointer: fine') ||
        s.includes('hover: hover') ||
        s.includes('min-width'),
      media: s,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    };
  };
  if (!w.Notification) {
    w.Notification = { permission: 'denied', requestPermission: async () => 'denied' };
  } else {
    try {
      Object.defineProperty(w.Notification, 'permission', {
        get: () => 'denied',
        configurable: true,
      });
    } catch (_) {}
  }
  if (!w.OfflineAudioContext) {
    w.OfflineAudioContext = function (ch, len, rate) {
      this.length = len;
      this.sampleRate = rate || 44100;
      this.destination = {};
      this.createOscillator = () => ({
        connect() {},
        start() {},
        stop() {},
        frequency: { value: 1 },
        type: 'triangle',
      });
      this.createDynamicsCompressor = () => ({
        connect() {},
        threshold: { value: -50 },
        knee: { value: 40 },
        ratio: { value: 12 },
        attack: { value: 0 },
        release: { value: 0.25 },
      });
      this.startRendering = () =>
        Promise.resolve({
          getChannelData: () => new Float32Array(len || 5000),
          length: len || 5000,
          sampleRate: rate || 44100,
        });
    };
    w.webkitOfflineAudioContext = w.OfflineAudioContext;
  }
  if (!w.RTCPeerConnection) {
    w.RTCPeerConnection = function () {
      return {
        createDataChannel() {
          return {};
        },
        createOffer() {
          return Promise.resolve({ type: 'offer', sdp: 'v=0\r\n' });
        },
        setLocalDescription() {
          return Promise.resolve();
        },
        close() {},
        addEventListener() {},
      };
    };
  }
  try {
    w.webkitRTCPeerConnection = w.webkitRTCPeerConnection || w.RTCPeerConnection;
  } catch (_) {}

  // Real Performance* classes so instanceof works WITHOUT force (force hangs)
  try {
    if (typeof w.PerformanceEntry !== 'function') {
      class PerformanceEntry {
        constructor() {
          this.name = '';
          this.entryType = '';
          this.startTime = 0;
          this.duration = 0;
        }
        toJSON() {
          return this;
        }
      }
      w.PerformanceEntry = PerformanceEntry;
    }
    if (typeof w.PerformanceNavigationTiming !== 'function') {
      class PerformanceNavigationTiming extends w.PerformanceEntry {
        constructor() {
          super();
          this.entryType = 'navigation';
          this.name = (w.location && w.location.href) || 'https://newassets.hcaptcha.com/';
          this.startTime = 0;
          this.duration = 1200;
          this.initiatorType = 'navigation';
          this.nextHopProtocol = 'h2';
          this.workerStart = 0;
          this.redirectStart = 0;
          this.redirectEnd = 0;
          this.fetchStart = 1;
          this.domainLookupStart = 2;
          this.domainLookupEnd = 3;
          this.connectStart = 3;
          this.connectEnd = 10;
          this.secureConnectionStart = 5;
          this.requestStart = 12;
          this.responseStart = 40;
          this.responseEnd = 80;
          this.domInteractive = 400;
          this.domContentLoadedEventStart = 420;
          this.domContentLoadedEventEnd = 430;
          this.domComplete = 1100;
          this.loadEventStart = 1110;
          this.loadEventEnd = 1120;
          this.type = 'navigate';
          this.redirectCount = 0;
          this.transferSize = 12000;
          this.encodedBodySize = 8000;
          this.decodedBodySize = 8000;
          this.finalResponseHeadersStart = 35;
        }
      }
      w.PerformanceNavigationTiming = PerformanceNavigationTiming;
    }
    if (typeof w.PerformanceResourceTiming !== 'function') {
      class PerformanceResourceTiming extends w.PerformanceEntry {
        constructor(name) {
          super();
          this.entryType = 'resource';
          this.name = name || '';
          this.initiatorType = 'script';
          this.nextHopProtocol = 'h2';
          this.duration = 50;
          this.transferSize = 1000;
          this.encodedBodySize = 800;
          this.decodedBodySize = 800;
        }
      }
      w.PerformanceResourceTiming = PerformanceResourceTiming;
    }
    if (typeof w.PerformancePaintTiming !== 'function') {
      class PerformancePaintTiming extends w.PerformanceEntry {
        constructor(name, t) {
          super();
          this.entryType = 'paint';
          this.name = name;
          this.startTime = t;
        }
      }
      w.PerformancePaintTiming = PerformancePaintTiming;
    }
    const perf = w.performance || {};
    const navEntry = new w.PerformanceNavigationTiming();
    const _getEntriesByType = perf.getEntriesByType
      ? perf.getEntriesByType.bind(perf)
      : () => [];
    perf.getEntriesByType = function (t) {
      if (String(t) === 'navigation') return [navEntry];
      if (String(t) === 'resource') {
        return [
          new w.PerformanceResourceTiming('https://newassets.hcaptcha.com/c/x/hsw.js'),
        ];
      }
      if (String(t) === 'paint') {
        return [
          new w.PerformancePaintTiming('first-paint', 120),
          new w.PerformancePaintTiming('first-contentful-paint', 140),
        ];
      }
      try {
        return _getEntriesByType(t);
      } catch (_) {
        return [];
      }
    };
    perf.getEntries = function () {
      return [navEntry];
    };
    perf.timeOrigin = perf.timeOrigin || Date.now() - 2500;
    const _now = perf.now ? perf.now.bind(perf) : () => Date.now() % 1e5;
    const t0 = Date.now();
    perf.now = function () {
      // Chrome-like mid-range ms since navigation
      return 200 + (Date.now() - t0) + Math.random();
    };
    w.performance = perf;
  } catch (_) {}
  try {
    if (w.document && !w.document.ancestorOrigins) {
      Object.defineProperty(w.document, 'ancestorOrigins', {
        get: () => {
          const a = ['https://www.paypalobjects.com', 'https://www.paypal.com'];
          a.item = (i) => a[i] || null;
          a.contains = (x) => a.indexOf(x) >= 0;
          return a;
        },
        configurable: true,
      });
    }
  } catch (_) {}
  try {
    if (w.location && !w.location.ancestorOrigins) {
      Object.defineProperty(w.location, 'ancestorOrigins', {
        get: () => {
          const a = ['https://www.paypalobjects.com'];
          a.item = (i) => a[i] || null;
          return a;
        },
        configurable: true,
      });
    }
  } catch (_) {}
  try {
    if (!w.Intl) w.Intl = globalThis.Intl;
    const RDTF = w.Intl && w.Intl.DateTimeFormat;
    if (RDTF) {
      const _ro = RDTF.prototype.resolvedOptions;
      RDTF.prototype.resolvedOptions = function () {
        const o = _ro.call(this);
        return Object.assign({}, o, {
          locale: o.locale || 'en-US',
          timeZone: o.timeZone || 'America/Sao_Paulo',
          calendar: o.calendar || 'gregory',
          numberingSystem: o.numberingSystem || 'latn',
        });
      };
    }
  } catch (_) {}

  // Ensure typed-array / buffer constructors visible on window
  try {
    w.ArrayBuffer = ArrayBuffer;
    w.Uint8Array = Uint8Array;
    w.Float32Array = Float32Array;
    w.DataView = DataView;
  } catch (_) {}

  const pre = {
    self_is_window: w === w.self || w.self === w,
    win_is_window: typeof w.Window === 'function' && w instanceof w.Window,
    window_name: w.constructor && w.constructor.name,
    ctor: w.constructor && w.constructor.name,
  };

  // Keep Node event loop alive while happy-dom timers may be unref'd.
  const keepAlive = setInterval(() => {}, 1000);

  const s = w.document.createElement('script');
  s.textContent = hswCode;
  w.document.body.appendChild(s);
  // allow microtasks / wasm init
  await new Promise((r) => setTimeout(r, 50));
  if (typeof w.hsw !== 'function') throw new Error('no hsw after inject');

  const n = await Promise.race([
    Promise.resolve(w.hsw(req)).then((x) => String(x)),
    new Promise((_, rej) =>
      setTimeout(() => rej(new Error('hsw timeout 60s host=' + Object.keys(HOST).length)), 60000)
    ),
  ]);
  const host_sum = Object.values(HOST).reduce((a, b) => a + b, 0);
  const host_top = Object.entries(HOST)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 40);
  const out = {
    ok: true,
    n,
    n_len: n.length,
    host_sum,
    host_unique: Object.keys(HOST).length,
    host_top,
    host_all: HOST,
    forced_j: FORCED.Window || 0,
    forced: FORCED,
    window_gates: Object.entries(GATE_MAP).map(([k, v]) => `${k}:${v}`),
    typeof_object_hosts: globalThis.__typeof_object || [],
    fails: FAIL_SAMPLES,
    pre,
    forceMode: FORCE_MODE,
    deviceProfile: (appliedProfile && appliedProfile.id) || null,
    hardwareConcurrency: (appliedProfile && appliedProfile.hardwareConcurrency) || null,
    keysAdded: 0,
  };
  if (CAPTURE_SEQ) out.seq = SEQ;
  process.stdout.write(JSON.stringify(out));
  clearInterval(keepAlive);
  await browser.close().catch(() => {});
  process.exit(0);
})().catch((e) => {
  try {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        error: String(e && (e.stack || e)),
        forceMode: FORCE_MODE,
        host_sum: Object.values(HOST).reduce((a, b) => a + b, 0),
        host_unique: Object.keys(HOST).length,
        forced: FORCED,
        fails: FAIL_SAMPLES.slice(0, 10),
      })
    );
  } catch (_) {}
  process.exit(1);
});
