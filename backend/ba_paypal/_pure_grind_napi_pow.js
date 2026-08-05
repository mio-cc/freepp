#!/usr/bin/env node
/**
 * Pure happy-dom PoW with real @napi-rs/canvas raster (line Y depth experiment).
 * Builds on window_force path + real 2d canvas pixels for toDataURL/getImageData.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const nc = require('crypto');
const ROOT = __dirname;

// Delegate: load base pow, but first patch require path by wrapping after browser create.
// Simpler: spawn-compatible stdin protocol identical to window_force_pow.
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const req = String(input.req || '').trim();
const hswPath = input.hswPath || path.join(ROOT, '_hsw_happy_dom_ctl.js');
const hswCode = fs.readFileSync(hswPath, 'utf8');
const UA0 =
  input.userAgent ||
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36';

let createCanvas, loadImage;
try {
  const napi = require(path.join(ROOT, 'ba_fp_helpers', 'node_modules', '@napi-rs/canvas'));
  createCanvas = napi.createCanvas;
  loadImage = napi.loadImage;
} catch (e) {
  process.stdout.write(JSON.stringify({ ok: false, error: 'napi canvas missing: ' + e.message }));
  process.exit(1);
}

// Process scrub (same as window_force)
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
  } catch (_) {}
})();

const HOST = {};
const FORCED = {};
const GATE_MAP = {};
const STATS = { tdu: 0, gid: 0, getContext: [], fillText: 0 };

function wrapImports(imports) {
  if (!imports || !imports.a) return imports;
  const a = imports.a;
  for (const name of Object.keys(a)) {
    const fn = a[name];
    if (typeof fn !== 'function') continue;
    let src = '';
    try {
      src = Function.prototype.toString.call(fn);
    } catch (_) {}
    const m = src.match(/instanceof\s+([A-Za-z0-9_$.]+)/);
    if (m) GATE_MAP[name] = m[1];
  }
  const wrapped = {};
  for (const name of Object.keys(a)) {
    const fn = a[name];
    if (typeof fn !== 'function') {
      wrapped[name] = fn;
      continue;
    }
    const gateType = GATE_MAP[name] || null;
    wrapped[name] = function () {
      HOST[name] = (HOST[name] || 0) + 1;
      if (gateType === 'Window') {
        FORCED.Window = (FORCED.Window || 0) + 1;
        if (FORCED.Window <= 20000) return 1;
      }
      if (gateType === 'HTMLCanvasElement' || gateType === 'CanvasRenderingContext2D') {
        FORCED[gateType] = (FORCED[gateType] || 0) + 1;
        if (FORCED[gateType] <= 500) return 1;
      }
      return fn.apply(this, arguments);
    };
  }
  return Object.assign({}, imports, { a: wrapped });
}

const origInst = WebAssembly.instantiate.bind(WebAssembly);
WebAssembly.instantiate = async function (src, imports) {
  return origInst(src, wrapImports(imports));
};

function patchRealCanvas(w) {
  // Real Skia-backed canvas via napi for 2d path
  const store = new WeakMap();
  function ensureBacking(el) {
    let b = store.get(el);
    if (!b) {
      const ww = Number(el.width) || 300;
      const hh = Number(el.height) || 150;
      const c = createCanvas(ww, hh);
      b = { canvas: c, w: ww, h: hh };
      store.set(el, b);
    } else if (b.w !== (Number(el.width) || b.w) || b.h !== (Number(el.height) || b.h)) {
      const ww = Number(el.width) || b.w;
      const hh = Number(el.height) || b.h;
      b.canvas = createCanvas(ww, hh);
      b.w = ww;
      b.h = hh;
    }
    return b;
  }

  const proto = w.HTMLCanvasElement && w.HTMLCanvasElement.prototype;
  if (!proto) return;

  const _getContext = proto.getContext;
  proto.getContext = function (type, attrs) {
    const t = String(type || '').toLowerCase();
    STATS.getContext.push({ type: t, w: this.width, h: this.height });
    if (t === '2d') {
      const b = ensureBacking(this);
      const ctx = b.canvas.getContext('2d');
      // wrap fillText/getImageData for stats
      const _ft = ctx.fillText.bind(ctx);
      ctx.fillText = function (...a) {
        STATS.fillText++;
        return _ft(...a);
      };
      const _gid = ctx.getImageData.bind(ctx);
      ctx.getImageData = function (...a) {
        STATS.gid++;
        return _gid(...a);
      };
      // expose chrome-like props happy-dom may expect
      try {
        ctx.canvas = this;
      } catch (_) {}
      return ctx;
    }
    // webgl: still stub but with realistic getParameter table
    if (t.includes('webgl')) {
      const UNMASKED_VENDOR = 0x9245;
      const UNMASKED_RENDERER = 0x9246;
      return {
        canvas: this,
        drawingBufferWidth: Number(this.width) || 300,
        drawingBufferHeight: Number(this.height) || 150,
        getExtension(name) {
          if (name === 'WEBGL_debug_renderer_info') {
            return { UNMASKED_VENDOR_WEBGL: UNMASKED_VENDOR, UNMASKED_RENDERER_WEBGL: UNMASKED_RENDERER };
          }
          if (name === 'EXT_texture_filter_anisotropic') return {};
          return {};
        },
        getSupportedExtensions() {
          return [
            'ANGLE_instanced_arrays',
            'EXT_blend_minmax',
            'EXT_color_buffer_half_float',
            'EXT_disjoint_timer_query',
            'EXT_float_blend',
            'EXT_frag_depth',
            'EXT_shader_texture_lod',
            'EXT_texture_compression_bptc',
            'EXT_texture_compression_rgtc',
            'EXT_texture_filter_anisotropic',
            'OES_element_index_uint',
            'OES_fbo_render_mipmap',
            'OES_standard_derivatives',
            'OES_texture_float',
            'OES_texture_float_linear',
            'OES_texture_half_float',
            'OES_texture_half_float_linear',
            'OES_vertex_array_object',
            'WEBGL_color_buffer_float',
            'WEBGL_compressed_texture_s3tc',
            'WEBGL_compressed_texture_s3tc_srgb',
            'WEBGL_debug_renderer_info',
            'WEBGL_debug_shaders',
            'WEBGL_depth_texture',
            'WEBGL_draw_buffers',
            'WEBGL_lose_context',
            'WEBGL_multi_draw',
          ];
        },
        getParameter(p) {
          if (p === UNMASKED_VENDOR) return 'Google Inc. (Intel)';
          if (p === UNMASKED_RENDERER) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)';
          if (p === 0x1f00) return 'WebKit'; // VENDOR
          if (p === 0x1f01) return 'WebKit WebGL'; // RENDERER
          if (p === 0x1f02) return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
          if (p === 0x8b8c) return 16; // MAX_VERTEX_ATTRIBS
          if (p === 0x8869) return 16;
          if (p === 0x0d33) return 16384; // MAX_TEXTURE_SIZE
          if (p === 0x851c) return 16384;
          if (p === 0x846e) return new Float32Array([1, 1]);
          return null;
        },
        getShaderPrecisionFormat() {
          return { rangeMin: 127, rangeMax: 127, precision: 23 };
        },
        createBuffer() {
          return {};
        },
        bindBuffer() {},
        bufferData() {},
        createProgram() {
          return {};
        },
        createShader() {
          return {};
        },
        shaderSource() {},
        compileShader() {},
        attachShader() {},
        linkProgram() {},
        useProgram() {},
        getAttribLocation() {
          return 0;
        },
        getUniformLocation() {
          return {};
        },
        enableVertexAttribArray() {},
        vertexAttribPointer() {},
        uniform1f() {},
        uniform2f() {},
        drawArrays() {},
        clearColor() {},
        clear() {},
        viewport() {},
        scissor() {},
        enable() {},
        disable() {},
        blendFunc() {},
        readPixels(x, y, w, h, f, ty, pixels) {
          if (pixels && pixels.length) {
            for (let i = 0; i < pixels.length; i++) pixels[i] = (i * 17 + 31) & 255;
          }
        },
        getContextAttributes() {
          return { alpha: true, antialias: true, depth: true, failIfMajorPerformanceCaveat: false, powerPreference: 'default', premultipliedAlpha: true, preserveDrawingBuffer: false, stencil: false, desynchronized: false, xrCompatible: false };
        },
        isContextLost() {
          return false;
        },
      };
    }
    if (typeof _getContext === 'function') {
      try {
        return _getContext.call(this, type, attrs);
      } catch (_) {
        return null;
      }
    }
    return null;
  };

  proto.toDataURL = function (type, quality) {
    STATS.tdu++;
    try {
      const b = ensureBacking(this);
      // if dimensions mismatch, resize
      if (b.w !== (Number(this.width) || b.w) || b.h !== (Number(this.height) || b.h)) {
        const ww = Number(this.width) || 300;
        const hh = Number(this.height) || 150;
        const nc2 = createCanvas(ww, hh);
        const c2 = nc2.getContext('2d');
        c2.drawImage(b.canvas, 0, 0);
        b.canvas = nc2;
        b.w = ww;
        b.h = hh;
      }
      // paint a deterministic "chrome-like" fingerprint if blank-ish
      const ctx = b.canvas.getContext('2d');
      ctx.textBaseline = 'top';
      ctx.font = '14px Arial';
      ctx.fillStyle = '#f60';
      ctx.fillRect(0, 0, 20, 20);
      ctx.fillStyle = '#069';
      ctx.fillText('Cwm fjordbank glyphs vext quiz, 😃', 2, 2);
      return b.canvas.toDataURL(type || 'image/png', quality);
    } catch (e) {
      return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    }
  };
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

  for (const k of ['process', 'Buffer', 'require', 'module', 'exports', 'global', '__dirname', '__filename', 'setImmediate', 'clearImmediate']) {
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

  // ─── REAL monotonic clock: hsw fingerprints performance.now() into n;
  // happy-dom's virtual clock is a dead time-axis → server soft-rejects. ───
  try {
    const NS = process.hrtime.bigint;
    const T0 = NS.call(process);
    const perfW = w.performance || {};
    if (typeof perfW.now !== 'function' || /happy/i.test(String(perfW.constructor || ''))) {
      Object.defineProperty(w, 'performance', {
        configurable: true,
        value: {
          ...perfW,
          timeOrigin: Date.now() - 1200,
          now() {
            const ms = Number(NS.call(process) - T0) / 1e6;
            return ms + Math.random() * 0.0005;
          },
          timing: perfW.timing || {
            navigationStart: Date.now() - 1200,
            fetchStart: Date.now() - 1199,
            domContentLoadedEventEnd: Date.now() - 400,
            loadEventEnd: Date.now() - 380,
          },
          getEntriesByType: (t) =>
            t === 'navigation'
              ? [{ entryType: 'navigation', name: w.location ? String(w.location.href) : 'https://newassets.hcaptcha.com/', duration: 1200, transferSize: 5000, type: 'navigate' }]
              : [],
          getEntries: () => [],
          mark: () => {},
          measure: () => {},
          clearMarks: () => {},
          clearMeasures: () => {},
        },
      });
    }
    if (typeof w.PerformanceObserver !== 'function') {
      function PerformanceObserver() {}
      PerformanceObserver.prototype.observe = () => {};
      PerformanceObserver.prototype.disconnect = () => {};
      PerformanceObserver.prototype.takeRecords = () => [];
      PerformanceObserver.supportedEntryTypes = ['navigation', 'resource', 'paint', 'longtask', 'element', 'largest-contentful-paint'];
      w.PerformanceObserver = PerformanceObserver;
    }
  } catch (_) {}

  // ─── userAgentData (Chrome-only, hsw fingerprint probe) ───
  try {
    const nav = w.navigator;
    if (nav && nav.userAgentData == null) {
      const brands = [
        { brand: 'Not_A Brand', version: '24' },
        { brand: 'Chromium', version: '146' },
        { brand: 'Google Chrome', version: '146' },
      ];
      Object.defineProperty(nav, 'userAgentData', {
        configurable: true,
        value: {
          brands,
          mobile: false,
          platform: 'MacIntel',
          getHighEntropyValues(hints) {
            const out = { brands, mobile: false, platform: 'MacIntel' };
            for (const h of hints || []) {
              if (h === 'platformVersion') out.platformVersion = '15.0.0';
              else if (h === 'architecture') out.architecture = 'arm';
              else if (h === 'bitness') out.bitness = '64';
              else if (h === 'model') out.model = '';
              else if (h === 'uaFullVersion') out.uaFullVersion = '146.0.0.0';
              else if (h === 'fullVersionList') out.fullVersionList = brands.map((b) => ({ brand: b.brand, version: b.version + '.0.0.0' }));
              else if (h === 'wow64') out.wow64 = false;
            }
            return Promise.resolve(out);
          },
          toJSON() {
            return { brands, mobile: false, platform: 'MacIntel' };
          },
        },
      });
    }
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
      if (w.crypto && w.crypto.constructor) w.crypto.constructor.constructor = globalThis.__SafeFunction;
    }
  } catch (_) {}

  // Device profile alignment + rAF VSync (Power-to-Device Ratio hypothesis)
  let appliedProfile = null;
  try {
    const dp = require(path.join(ROOT, 'ba_fp_helpers', 'hsw_pow_device_profile.js'));
    const profileName =
      input.deviceProfile || input.profile || process.env.MIN_BA_POW_DEVICE_PROFILE || 'mid_mac_intel';
    appliedProfile = dp.applyDeviceProfile(w, profileName);
    if (input.userAgent) {
      Object.defineProperty(w.navigator, 'userAgent', { get: () => UA0, configurable: true });
    }
  } catch (_) {
    const nav = w.navigator;
    try {
      Object.defineProperty(nav, 'userAgent', { get: () => UA0, configurable: true });
      Object.defineProperty(nav, 'platform', { get: () => 'MacIntel', configurable: true });
      Object.defineProperty(nav, 'vendor', { get: () => 'Google Inc.', configurable: true });
      Object.defineProperty(nav, 'language', { get: () => 'pt-BR', configurable: true });
      Object.defineProperty(nav, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'], configurable: true });
      Object.defineProperty(nav, 'webdriver', { get: () => false, configurable: true });
      Object.defineProperty(nav, 'hardwareConcurrency', { get: () => 4, configurable: true });
      Object.defineProperty(nav, 'deviceMemory', { get: () => 4, configurable: true });
      Object.defineProperty(nav, 'maxTouchPoints', { get: () => 0, configurable: true });
    } catch (_) {}
  }

  w.chrome = {
    runtime: { id: undefined, connect: undefined, sendMessage: undefined },
    loadTimes() {
      return { connectionInfo: 'h2', wasFetchedViaSpdy: true, wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2' };
    },
    csi() {
      return { startE: Date.now() - 1200, onloadT: Date.now() - 400, pageT: 800, tran: 15 };
    },
    app: { isInstalled: false },
  };
  try {
    Object.defineProperty(w, 'screen', {
      configurable: true,
      value: {
        width: 1920,
        height: 1080,
        availWidth: 1920,
        availHeight: 1040,
        colorDepth: 24,
        pixelDepth: 24,
        availLeft: 0,
        availTop: 0,
        orientation: { type: 'landscape-primary', angle: 0 },
      },
    });
  } catch (_) {}

  if (!w.OfflineAudioContext) {
    w.OfflineAudioContext = function (ch, len, rate) {
      this.length = len;
      this.sampleRate = rate || 44100;
      this.destination = {};
      this.createOscillator = () => ({
        connect() {},
        start() {},
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
          getChannelData: () => {
            const a = new Float32Array(len || 5000);
            for (let i = 0; i < a.length; i++) a[i] = Math.sin(i * 0.01) * 0.1;
            return a;
          },
          length: len || 5000,
          sampleRate: rate || 44100,
        });
    };
    w.webkitOfflineAudioContext = w.OfflineAudioContext;
  }

  patchRealCanvas(w);

  // OffscreenCanvas light polyfill using napi
  if (!w.OffscreenCanvas) {
    w.OffscreenCanvas = function (width, height) {
      this.width = width || 1;
      this.height = height || 1;
      const c = createCanvas(this.width, this.height);
      this.getContext = function (type) {
        if (String(type).toLowerCase() === '2d') return c.getContext('2d');
        // webgl stub same as above via HTMLCanvas path — return minimal
        return null;
      };
      this.convertToBlob = async function () {
        const buf = c.toBuffer('image/png');
        return new Blob([buf], { type: 'image/png' });
      };
      this.transferToImageBitmap = function () {
        return {};
      };
    };
  }

  const keepAlive = setInterval(() => {}, 1000);
  const t0 = Date.now();
  const s = w.document.createElement('script');
  s.textContent = hswCode;
  w.document.body.appendChild(s);
  await new Promise((r) => setTimeout(r, 50));
  if (typeof w.hsw !== 'function') throw new Error('no hsw');

  const n = await Promise.race([
    Promise.resolve(w.hsw(req)).then((x) => String(x)),
    new Promise((_, rej) => setTimeout(() => rej(new Error('hsw timeout')), 60000)),
  ]);
  const host_sum = Object.values(HOST).reduce((a, b) => a + b, 0);
  process.stdout.write(
    JSON.stringify({
      ok: true,
      n,
      n_len: n.length,
      host_sum,
      host_unique: Object.keys(HOST).length,
      forced: FORCED,
      stats: STATS,
      ms: Date.now() - t0,
      mode: 'napi_canvas',
      deviceProfile: (appliedProfile && appliedProfile.id) || null,
    })
  );
  clearInterval(keepAlive);
  await browser.close().catch(() => {});
})().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)) }));
  process.exit(1);
});
