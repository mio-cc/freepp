#!/usr/bin/env node
/*
 * Protocol-only PayPal hCaptcha passive token helper (min-implant ba_fp_helpers).
 *
 * v3 goals vs v2:
 *  - Proxy-aware Node HTTPS (HTTPS_PROXY / HTTP_PROXY via https-proxy-agent)
 *  - Chrome146 macOS fingerprint surface (WebGL/canvas/Audio/UA-CH/chrome.runtime/fonts)
 *  - Fetch interceptor: log hCaptcha API status + response heads
 *  - Timezone aligned to exit region (input.tz or MX default)
 *  - Prefer remote paypalobjects URL (real origin) over 127.0.0.1 local serve
 *
 * stdin JSON:
 *   { iframeUrl, parentUrl?, userAgent?, timeoutMs?, html?, proxy?, tz?, region? }
 *
 * stdout JSON:
 *   { ok, token, renderData, error, states, elapsedMs, netLog, recentMessages, ... }
 */
'use strict';

const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// Proxy bootstrap — Node does NOT honor HTTPS_PROXY natively.
// ---------------------------------------------------------------------------
function installProxyFromEnv() {
  const proxyUrl =
    process.env.HTTPS_PROXY ||
    process.env.HTTP_PROXY ||
    process.env.ALL_PROXY ||
    process.env.https_proxy ||
    process.env.http_proxy ||
    '';
  if (!proxyUrl) {
    console.error('[hcap] proxy=none (direct)');
    return null;
  }
  let HttpsProxyAgent;
  try {
    // local ba_fp_helpers/node_modules first
    HttpsProxyAgent = require('https-proxy-agent').HttpsProxyAgent;
  } catch (_) {
    try {
      HttpsProxyAgent = require(path.join(__dirname, 'node_modules', 'https-proxy-agent'))
        .HttpsProxyAgent;
    } catch (e2) {
      console.error('[hcap] https-proxy-agent missing; proxy env ignored:', e2.message);
      return null;
    }
  }
  const agent = new HttpsProxyAgent(proxyUrl);
  const patch = (mod, name) => {
    const origRequest = mod.request.bind(mod);
    const origGet = mod.get ? mod.get.bind(mod) : null;
    mod.request = function patchedRequest(...args) {
      // Node signatures: request(options[, cb]) | request(url[, options][, cb])
      try {
        if (typeof args[0] === 'string' || args[0] instanceof URL) {
          if (typeof args[1] === 'object' && args[1] !== null && typeof args[1] !== 'function') {
            const host = args[1].hostname || args[1].host || '';
            if (!host || (host !== '127.0.0.1' && host !== 'localhost')) {
              args[1] = { ...args[1], agent };
            }
          } else {
            // insert options with agent
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
        console.error('[hcap] proxy patch warn', e && e.message);
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
    console.error(`[hcap] patched ${name}.request with proxy agent`);
  };
  patch(https, 'https');
  // http only for local server + rare redirects; still patch for consistency
  patch(http, 'http');
  // strip credentials from log
  try {
    const u = new URL(proxyUrl);
    console.error(`[hcap] proxy=${u.protocol}//${u.hostname}:${u.port || ''}`);
  } catch (_) {
    console.error('[hcap] proxy=set');
  }
  return agent;
}

// ---------------------------------------------------------------------------
// happy-dom load
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
const { Browser, Window } = happy;

const DEFAULT_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

const CHROME_MAJOR = '146';
const CHROME_FULL = '146.0.0.0';

// Deterministic non-trivial canvas PNG-ish payload (not the classic stub).
const CANVAS_DATA_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAASwAAACWCAYAAABkW7XSAAAE' +
  'yUlEQVR4nO3dMW7bMBRA0S9V2qX3P8wO0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL' +
  '0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL' +
  '0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL0KFL' +
  'YQ8A/wMAAP//AwD/AxnQZQAAAABJRU5ErkJggg==';

function readStdin() {
  return fs.readFileSync(0, 'utf8');
}

function sleep(ms) {
  return Promise.resolve().then(
    () =>
      new Promise((resolve) => {
        setTimeout(resolve, ms);
      })
  );
}

function define(obj, key, value) {
  try {
    Object.defineProperty(obj, key, {
      configurable: true,
      enumerable: true,
      get: typeof value === 'function' && value.length === 0 && !value.prototype ? value : () => value,
      set() {},
    });
  } catch (_) {
    try {
      obj[key] = typeof value === 'function' && value.length === 0 ? value() : value;
    } catch (_) {}
  }
}

function defineValue(obj, key, value) {
  try {
    Object.defineProperty(obj, key, {
      configurable: true,
      enumerable: true,
      writable: true,
      value,
    });
  } catch (_) {
    try {
      obj[key] = value;
    } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// Network log (shared across strategies)
// ---------------------------------------------------------------------------
const netLog = [];
function pushNet(entry) {
  netLog.push({ t: Date.now(), ...entry });
  if (netLog.length > 80) netLog.shift();
  const st = entry.status != null ? ` status=${entry.status}` : '';
  console.error(`[hcap-net] ${entry.method || 'GET'} ${String(entry.url || '').slice(0, 160)}${st}`);
}

// Optional body rewrite for bridge HTML (merged scripts) while keeping real origin URL.
let __bridgeRewriteHtml = '';

function makeInterceptor(ResponseCtor) {
  return {
    async beforeAsyncRequest({ request, window }) {
      try {
        const u = String(request.url || '');
        pushNet({ phase: 'before', method: request.method, url: u });
        if (__bridgeRewriteHtml && /hcaptchapassive_eval\.html/i.test(u) && request.method === 'GET') {
          const R = (window && window.Response) || ResponseCtor || globalThis.Response;
          if (R) {
            console.error('[hcap] interceptor rewrite bridge html len=', __bridgeRewriteHtml.length);
            return new R(__bridgeRewriteHtml, {
              status: 200,
              headers: { 'Content-Type': 'text/html; charset=utf-8' },
            });
          }
        }
      } catch (e) {
        console.error('[hcap] interceptor before fail', e && e.message);
      }
    },
    async afterAsyncResponse({ request, response }) {
      try {
        const u = String(request.url || '');
        pushNet({
          phase: 'after',
          method: request.method,
          url: u,
          status: response.status,
          bodyPreview: '',
        });
        // stash checksiteconfig JWT for manual hsw drive (clone only — never consume body)
        if (/checksiteconfig/i.test(u) && response && response.status === 200) {
          try {
            if (typeof response.clone === 'function') {
              const text = await response.clone().text();
              if (text && text[0] === '{') {
                const j = JSON.parse(text);
                if (j && j.c && j.c.req) {
                  global.__hcap_last_csc_req = j.c.req;
                  global.__hcap_last_csc_obj = j.c;
                  console.error('[hcap] stashed csc req len=', j.c.req.length, 'type=', j.c.type);
                }
              }
            }
          } catch (e) {
            console.error('[hcap] csc parse soft-fail', e && e.message);
          }
        }
      } catch (_) {}
      return response;
    },
    beforeSyncRequest({ request }) {
      try {
        pushNet({ phase: 'before-sync', method: request.method, url: String(request.url) });
      } catch (_) {}
    },
    afterSyncResponse({ request, response }) {
      try {
        pushNet({
          phase: 'after-sync',
          method: request.method,
          url: String(request.url),
          status: response.status,
        });
      } catch (_) {}
      return response;
    },
  };
}

// ---------------------------------------------------------------------------
// Fingerprint patches — Chrome146 macOS
// ---------------------------------------------------------------------------
function patchCanvas(win) {
  try {
    if (!win.HTMLCanvasElement || !win.HTMLCanvasElement.prototype) return;
    const UNMASKED_VENDOR_WEBGL = 0x9245;
    const UNMASKED_RENDERER_WEBGL = 0x9246;
    const VENDOR = 'Google Inc. (Apple)';
    const RENDERER =
      'ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)';

    const make2d = (canvas) => ({
      canvas,
      fillStyle: '#000',
      strokeStyle: '#000',
      font: '10px sans-serif',
      globalAlpha: 1,
      lineWidth: 1,
      textBaseline: 'alphabetic',
      fillRect() {},
      clearRect() {},
      getImageData(x, y, w, h) {
        const n = Math.max(4, (w || 1) * (h || 1) * 4);
        const data = new win.Uint8ClampedArray(n);
        // mild non-zero noise so fingerprint != all-zeros
        for (let i = 0; i < n; i += 4) {
          data[i] = 12 + (i % 17);
          data[i + 1] = 34 + (i % 13);
          data[i + 2] = 56 + (i % 11);
          data[i + 3] = 255;
        }
        return { data, width: w || 1, height: h || 1 };
      },
      putImageData() {},
      createImageData(w, h) {
        return { data: new win.Uint8ClampedArray(Math.max(4, (w || 1) * (h || 1) * 4)), width: w, height: h };
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
      measureText(t) {
        return { width: Math.max(1, String(t || '').length * 7.2) };
      },
      transform() {},
      createLinearGradient() {
        return { addColorStop() {} };
      },
      createRadialGradient() {
        return { addColorStop() {} };
      },
      createPattern() {
        return null;
      },
      isPointInPath() {
        return false;
      },
    });

    const makeWebGL = (canvas, is2) => {
      const params = {
        0x1f00: VENDOR, // VENDOR
        0x1f01: RENDERER, // RENDERER
        0x1f02: 'WebGL 1.0 (OpenGL ES 2.0 Chromium)', // VERSION
        0x8b8c: 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)', // SHADING_LANGUAGE_VERSION
        [UNMASKED_VENDOR_WEBGL]: VENDOR,
        [UNMASKED_RENDERER_WEBGL]: RENDERER,
        0x0d33: 16384, // MAX_TEXTURE_SIZE
        0x8869: 16, // MAX_VERTEX_ATTRIBS
        0x8b4c: 32, // MAX_VERTEX_UNIFORM_VECTORS
        0x8dfb: 16, // MAX_VARYING_VECTORS
        0x8b49: 16, // MAX_FRAGMENT_UNIFORM_VECTORS
        0x8b4d: 16, // MAX_COMBINED_TEXTURE_IMAGE_UNITS
        0x8b4f: 16, // MAX_VERTEX_TEXTURE_IMAGE_UNITS
        0x8872: 16, // MAX_TEXTURE_IMAGE_UNITS
        0x851c: 16, // MAX_CUBE_MAP_TEXTURE_SIZE
        0x0d3a: [1, 1024], // MAX_VIEWPORT_DIMS
        0x846e: 1, // ALIASED_LINE_WIDTH_RANGE [min] simplified
        0x846d: 1,
        0x0b44: true,
      };
      const extDebug = {
        UNMASKED_VENDOR_WEBGL,
        UNMASKED_RENDERER_WEBGL,
      };
      return {
        canvas,
        drawingBufferWidth: 300,
        drawingBufferHeight: 150,
        getExtension(name) {
          if (name === 'WEBGL_debug_renderer_info') return extDebug;
          if (name === 'EXT_texture_filter_anisotropic') {
            return { MAX_TEXTURE_MAX_ANISOTROPY_EXT: 0x84ff };
          }
          if (name === 'OES_texture_float') return {};
          if (name === 'OES_standard_derivatives') return {};
          if (name === 'WEBGL_lose_context') return { loseContext() {}, restoreContext() {} };
          return null;
        },
        getSupportedExtensions() {
          return [
            'WEBGL_debug_renderer_info',
            'EXT_texture_filter_anisotropic',
            'OES_texture_float',
            'OES_standard_derivatives',
            'WEBGL_lose_context',
            'OES_element_index_uint',
            'OES_vertex_array_object',
          ];
        },
        getParameter(p) {
          if (params[p] !== undefined) return params[p];
          return null;
        },
        getContextAttributes() {
          return {
            alpha: true,
            antialias: true,
            depth: true,
            failIfMajorPerformanceCaveat: false,
            powerPreference: 'default',
            premultipliedAlpha: true,
            preserveDrawingBuffer: false,
            stencil: false,
            desynchronized: false,
          };
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
        drawArrays() {},
        drawElements() {},
        viewport() {},
        clearColor() {},
        clear() {},
        enable() {},
        disable() {},
        blendFunc() {},
        depthFunc() {},
        pixelStorei() {},
        texImage2D() {},
        texParameteri() {},
        createTexture() {
          return {};
        },
        bindTexture() {},
        activeTexture() {},
        readPixels(x, y, w, h, f, t, pixels) {
          if (pixels && pixels.length) {
            for (let i = 0; i < pixels.length; i++) pixels[i] = (i * 17 + 3) & 0xff;
          }
        },
        isContextLost() {
          return false;
        },
        getError() {
          return 0;
        },
      };
    };

    win.HTMLCanvasElement.prototype.getContext = function getContext(type, attrs) {
      const t = String(type || '').toLowerCase();
      if (t === '2d') return make2d(this);
      if (t === 'webgl' || t === 'experimental-webgl' || t === 'webgl2') {
        return makeWebGL(this, t === 'webgl2');
      }
      return null;
    };
    win.HTMLCanvasElement.prototype.toDataURL = function toDataURL() {
      return CANVAS_DATA_URL;
    };
    win.HTMLCanvasElement.prototype.toBlob = function toBlob(cb) {
      if (typeof cb === 'function') {
        try {
          const bin = Buffer.from(CANVAS_DATA_URL.split(',')[1], 'base64');
          cb(new win.Blob([bin], { type: 'image/png' }));
        } catch (_) {
          cb(null);
        }
      }
    };
  } catch (_) {}
}

function patchAudio(win) {
  try {
    class FakeAnalyser {
      constructor() {
        this.fftSize = 2048;
        this.frequencyBinCount = 1024;
      }
      connect() {}
      disconnect() {}
      getFloatFrequencyData(arr) {
        if (arr) for (let i = 0; i < arr.length; i++) arr[i] = -100 + (i % 30);
      }
      getByteFrequencyData(arr) {
        if (arr) for (let i = 0; i < arr.length; i++) arr[i] = (i * 3) & 0xff;
      }
      getFloatTimeDomainData(arr) {
        if (arr) for (let i = 0; i < arr.length; i++) arr[i] = Math.sin(i / 17) * 0.1;
      }
      getByteTimeDomainData(arr) {
        if (arr) for (let i = 0; i < arr.length; i++) arr[i] = 128 + ((i * 5) & 0x1f);
      }
    }
    class FakeOscillator {
      connect() {}
      disconnect() {}
      start() {}
      stop() {}
      set frequency(v) {}
      get frequency() {
        return { value: 440 };
      }
    }
    class FakeAudioContext {
      constructor() {
        this.sampleRate = 44100;
        this.destination = { connect() {}, disconnect() {} };
        this.state = 'running';
        this.currentTime = 0;
      }
      createAnalyser() {
        return new FakeAnalyser();
      }
      createOscillator() {
        return new FakeOscillator();
      }
      createDynamicsCompressor() {
        return {
          threshold: { value: -50 },
          knee: { value: 40 },
          ratio: { value: 12 },
          attack: { value: 0 },
          release: { value: 0.25 },
          connect() {},
          disconnect() {},
        };
      }
      createGain() {
        return { gain: { value: 1 }, connect() {}, disconnect() {} };
      }
      createScriptProcessor() {
        return { connect() {}, disconnect() {}, onaudioprocess: null };
      }
      close() {
        this.state = 'closed';
        return Promise.resolve();
      }
      resume() {
        this.state = 'running';
        return Promise.resolve();
      }
    }
    class FakeOfflineAudioContext extends FakeAudioContext {
      constructor(channels, length, sampleRate) {
        super();
        this.sampleRate = sampleRate || 44100;
        this.length = length || 44100;
        this.numberOfChannels = channels || 1;
      }
      startRendering() {
        const ch = this.numberOfChannels;
        const len = this.length;
        const data = new Float32Array(len);
        for (let i = 0; i < len; i++) data[i] = Math.sin(i / 200) * 0.02;
        return Promise.resolve({
          numberOfChannels: ch,
          length: len,
          sampleRate: this.sampleRate,
          duration: len / this.sampleRate,
          getChannelData() {
            return data;
          },
        });
      }
    }
    win.AudioContext = FakeAudioContext;
    win.webkitAudioContext = FakeAudioContext;
    win.OfflineAudioContext = FakeOfflineAudioContext;
    win.webkitOfflineAudioContext = FakeOfflineAudioContext;
  } catch (_) {}
}

function patchChrome(win) {
  try {
    const runtime = {
      id: undefined,
      connect() {
        return { onMessage: { addListener() {} }, postMessage() {}, disconnect() {} };
      },
      sendMessage() {},
      getManifest() {
        return undefined;
      },
      getURL(p) {
        return p;
      },
      onMessage: { addListener() {}, removeListener() {} },
      onConnect: { addListener() {}, removeListener() {} },
    };
    win.chrome = {
      app: {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails() {
          return null;
        },
        getIsInstalled() {
          return false;
        },
      },
      runtime,
      csi() {
        return { startE: Date.now(), onloadT: Date.now(), pageT: 100, tran: 15 };
      },
      loadTimes() {
        return {
          commitLoadTime: Date.now() / 1000,
          connectionInfo: 'h2',
          finishDocumentLoadTime: Date.now() / 1000,
          finishLoadTime: Date.now() / 1000,
          firstPaintAfterLoadTime: 0,
          firstPaintTime: Date.now() / 1000,
          navigationType: 'Other',
          npnNegotiatedProtocol: 'h2',
          requestTime: Date.now() / 1000 - 0.2,
          startLoadTime: Date.now() / 1000 - 0.2,
          wasAlternateProtocolAvailable: false,
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
        };
      },
    };
  } catch (_) {}
}

function patchNavigator(win, userAgent) {
  const nav = win.navigator;
  try {
    nav.userAgent = userAgent;
  } catch (_) {}
  define(nav, 'userAgent', userAgent);
  define(nav, 'appCodeName', 'Mozilla');
  define(nav, 'appName', 'Netscape');
  define(nav, 'appVersion', userAgent.replace(/^Mozilla\//, ''));
  define(nav, 'platform', 'MacIntel');
  define(nav, 'vendor', 'Google Inc.');
  define(nav, 'vendorSub', '');
  define(nav, 'productSub', '20030107');
  define(nav, 'product', 'Gecko');
  define(nav, 'language', 'en-US');
  define(nav, 'languages', Object.freeze(['en-US', 'en']));
  // critical: webdriver must be false / undefined, not true
  try {
    Object.defineProperty(nav, 'webdriver', {
      get: () => false,
      configurable: true,
      enumerable: true,
    });
  } catch (_) {
    define(nav, 'webdriver', false);
  }
  define(nav, 'deviceMemory', 8);
  define(nav, 'hardwareConcurrency', 8);
  define(nav, 'maxTouchPoints', 0);
  define(nav, 'cookieEnabled', true);
  define(nav, 'onLine', true);
  define(nav, 'doNotTrack', null);
  define(nav, 'pdfViewerEnabled', true);

  // plugins / mimeTypes
  try {
    const pluginData = [
      {
        name: 'PDF Viewer',
        filename: 'internal-pdf-viewer',
        description: 'Portable Document Format',
        mime: 'application/pdf',
      },
      {
        name: 'Chrome PDF Viewer',
        filename: 'internal-pdf-viewer',
        description: 'Portable Document Format',
        mime: 'application/pdf',
      },
      {
        name: 'Chromium PDF Viewer',
        filename: 'internal-pdf-viewer',
        description: 'Portable Document Format',
        mime: 'application/pdf',
      },
      {
        name: 'Microsoft Edge PDF Viewer',
        filename: 'internal-pdf-viewer',
        description: 'Portable Document Format',
        mime: 'application/pdf',
      },
      {
        name: 'WebKit built-in PDF',
        filename: 'internal-pdf-viewer',
        description: 'Portable Document Format',
        mime: 'application/pdf',
      },
    ];
    const plugins = pluginData.map((p, i) => {
      const mime = {
        type: p.mime,
        suffixes: 'pdf',
        description: p.description,
        enabledPlugin: null,
      };
      const plugin = {
        name: p.name,
        filename: p.filename,
        description: p.description,
        length: 1,
        0: mime,
        item(idx) {
          return idx === 0 ? mime : null;
        },
        namedItem(n) {
          return n === p.mime ? mime : null;
        },
      };
      mime.enabledPlugin = plugin;
      return plugin;
    });
    plugins.length = pluginData.length;
    plugins.item = (i) => plugins[i] || null;
    plugins.namedItem = (n) => plugins.find((p) => p.name === n) || null;
    plugins.refresh = () => {};
    define(nav, 'plugins', plugins);

    const mimeTypes = plugins.map((p) => p[0]);
    mimeTypes.length = mimeTypes.length;
    mimeTypes.item = (i) => mimeTypes[i] || null;
    mimeTypes.namedItem = (n) => mimeTypes.find((m) => m.type === n) || null;
    define(nav, 'mimeTypes', mimeTypes);
  } catch (_) {}

  // UA-CH
  try {
    const brands = [
      { brand: 'Chromium', version: CHROME_MAJOR },
      { brand: 'Not-A.Brand', version: '24' },
      { brand: 'Google Chrome', version: CHROME_MAJOR },
    ];
    const uad = {
      brands,
      mobile: false,
      platform: 'macOS',
      getHighEntropyValues(hints) {
        const all = {
          brands,
          mobile: false,
          platform: 'macOS',
          platformVersion: '15.3.1',
          architecture: 'x86',
          bitness: '64',
          model: '',
          uaFullVersion: CHROME_FULL,
          fullVersionList: [
            { brand: 'Chromium', version: CHROME_FULL },
            { brand: 'Not-A.Brand', version: '10.0.0.0' },
            { brand: 'Google Chrome', version: CHROME_FULL },
          ],
          wow64: false,
        };
        const out = {};
        const list = Array.isArray(hints) ? hints : Object.keys(all);
        for (const h of list) {
          if (h in all) out[h] = all[h];
        }
        out.brands = brands;
        out.mobile = false;
        out.platform = 'macOS';
        return Promise.resolve(out);
      },
      toJSON() {
        return { brands, mobile: false, platform: 'macOS' };
      },
    };
    define(nav, 'userAgentData', uad);
  } catch (_) {}

  // connection
  try {
    define(nav, 'connection', {
      effectiveType: '4g',
      rtt: 50,
      downlink: 10,
      saveData: false,
      addEventListener() {},
      removeEventListener() {},
    });
  } catch (_) {}

  // permissions
  try {
    define(nav, 'permissions', {
      query() {
        return Promise.resolve({ state: 'prompt', onchange: null });
      },
    });
  } catch (_) {}

  // mediaDevices
  try {
    define(nav, 'mediaDevices', {
      enumerateDevices() {
        return Promise.resolve([
          { kind: 'audioinput', deviceId: 'default', label: '', groupId: 'g1' },
          { kind: 'audiooutput', deviceId: 'default', label: '', groupId: 'g1' },
          { kind: 'videoinput', deviceId: 'default', label: '', groupId: 'g2' },
        ]);
      },
      getUserMedia() {
        return Promise.reject(new Error('NotAllowedError'));
      },
    });
  } catch (_) {}

  // fonts (query local fonts if API present)
  try {
    define(nav, 'fonts', {
      check() {
        return true;
      },
      ready: Promise.resolve(),
      values() {
        const fonts = [
          'Arial',
          'Helvetica',
          'Times New Roman',
          'Courier New',
          'Georgia',
          'Verdana',
          'Trebuchet MS',
          'Palatino',
          'Garamond',
          'Comic Sans MS',
          'Menlo',
          'Monaco',
          'Helvetica Neue',
          'SF Pro Text',
          'SF Pro Display',
        ];
        return {
          next() {
            if (!this._i) this._i = 0;
            if (this._i >= fonts.length) return { done: true, value: undefined };
            return { done: false, value: { family: fonts[this._i++] } };
          },
          [Symbol.iterator]() {
            return this;
          },
        };
      },
    });
  } catch (_) {}
}

function shouldBindParentCollector(window) {
  // Only the PayPal passive bridge should post tokens to our collector.
  // Challenge/checkbox frames on newassets MUST keep real parent=bridge.
  try {
    const href = String((window.location && window.location.href) || '');
    if (/newassets\.hcaptcha/i.test(href)) return false;
    if (/imgs\.hcaptcha/i.test(href)) return false;
    if (/accounts\.hcaptcha/i.test(href)) return false;
    if (/\/static\/hcaptcha\.html/i.test(href)) return false;
    // default: bind (bridge / main). Explicit allow for clarity.
    if (/hcaptchapassive/i.test(href) || /paypalobjects\.com/i.test(href)) return true;
    if (window.frameElement == null) return true;
    return false;
  } catch (e) {
    console.error('[hcap] shouldBind err', e && e.message);
    return false;
  }
}

function injectCriticalGlobals(win) {
  // Capture official msgpack before hcaptcha deletes window.msgpack
  try {
    if (!win.__pps_msgpack_hooked) {
      win.__pps_msgpack_hooked = true;
      let captured = win.msgpack || null;
      try {
        Object.defineProperty(win, 'msgpack', {
          configurable: true,
          enumerable: true,
          get() {
            return captured;
          },
          set(v) {
            captured = v;
            if (v && typeof v.encode === 'function') {
              global.__hcap_msgpack = v;
              console.error('[hcap] captured official msgpack encode');
            }
          },
        });
      } catch (_) {}
    }
  } catch (_) {}

  // MUST run before any hsw/api script — every frame, every time.
  try {
    if (typeof globalThis.WebAssembly !== 'undefined') {
      // instrument once for diagnostics
      const WA = globalThis.WebAssembly;
      if (!WA.__pps_wrapped) {
        try {
          const origInst = WA.instantiate.bind(WA);
          WA.instantiate = function (...args) {
            console.error('[hcap-wasm] instantiate args', args.length);
            return origInst(...args).then(
              (r) => {
                console.error('[hcap-wasm] instantiate ok');
                return r;
              },
              (e) => {
                console.error('[hcap-wasm] instantiate fail', e && e.message);
                throw e;
              }
            );
          };
          const origComp = WA.compile.bind(WA);
          WA.compile = function (...args) {
            console.error('[hcap-wasm] compile');
            return origComp(...args);
          };
          WA.__pps_wrapped = true;
        } catch (_) {}
      }
      try {
        Object.defineProperty(win, 'WebAssembly', {
          configurable: true,
          writable: true,
          value: WA,
        });
      } catch (_) {
        win.WebAssembly = WA;
      }
    }
  } catch (_) {}
  try {
    if (typeof globalThis.BigInt !== 'undefined') win.BigInt = globalThis.BigInt;
  } catch (_) {}
  try {
    if (typeof globalThis.Atomics !== 'undefined') win.Atomics = globalThis.Atomics;
  } catch (_) {}
  try {
    if (typeof globalThis.SharedArrayBuffer !== 'undefined') {
      win.SharedArrayBuffer = globalThis.SharedArrayBuffer;
    }
  } catch (_) {}

  // crypto.subtle: happy-dom often exposes empty {} — AES-GCM for enc_get_req dies.
  try {
    const nodeCrypto = require('crypto');
    const subtle =
      (globalThis.crypto && globalThis.crypto.subtle) ||
      (nodeCrypto.webcrypto && nodeCrypto.webcrypto.subtle) ||
      null;
    const getRandomValues =
      (globalThis.crypto && globalThis.crypto.getRandomValues && globalThis.crypto.getRandomValues.bind(globalThis.crypto)) ||
      (nodeCrypto.webcrypto &&
        nodeCrypto.webcrypto.getRandomValues &&
        nodeCrypto.webcrypto.getRandomValues.bind(nodeCrypto.webcrypto)) ||
      null;
    if (subtle) {
      const cryptoObj = {
        subtle,
        getRandomValues:
          getRandomValues ||
          function (arr) {
            const b = nodeCrypto.randomBytes(arr.length);
            arr.set(b);
            return arr;
          },
      };
      try {
        Object.defineProperty(win, 'crypto', {
          configurable: true,
          writable: true,
          value: cryptoObj,
        });
      } catch (_) {
        win.crypto = cryptoObj;
      }
      try {
        if (win.Crypto) {
          /* keep */
        }
      } catch (_) {}
    }
  } catch (e) {
    console.error('[hcap] crypto.subtle inject fail', e && e.message);
  }

  // also mirror onto globalThis of the realm if distinct
  try {
    if (win.globalThis && win.globalThis !== win) {
      win.globalThis.WebAssembly = globalThis.WebAssembly;
      win.globalThis.BigInt = globalThis.BigInt;
      if (win.crypto) win.globalThis.crypto = win.crypto;
    }
  } catch (_) {}
}

function patchWindow(win, userAgent, parentUrl, parentPostMessage) {
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

  // Always re-inject WASM (iframe reloads wipe it)
  injectCriticalGlobals(win);

  if (win.__pps_hcap_patched) return;
  try {
    Object.defineProperty(win, '__pps_hcap_patched', { value: true, configurable: true });
  } catch (_) {
    win.__pps_hcap_patched = true;
  }

  patchNavigator(win, userAgent);
  patchCanvas(win);
  patchAudio(win);
  patchChrome(win);

  // Executable Worker/SharedWorker (hsw fingerprint collectors)
  try {
    let installOnWindow;
    try {
      ({ installOnWindow } = require('./worker_polyfill.js'));
    } catch (_) {
      ({ install: installOnWindow } = require('./sandbox_polyfill.js'));
    }
    installOnWindow(win);
  } catch (e) {
    console.error('[hcap] worker_polyfill fail', e && e.message);
  }

  try {
    win.screen.width = 1440;
    win.screen.height = 900;
    win.screen.availWidth = 1440;
    win.screen.availHeight = 875;
    win.screen.colorDepth = 24;
    win.screen.pixelDepth = 24;
    win.screen.availLeft = 0;
    win.screen.availTop = 0;
    win.screen.orientation = { type: 'landscape-primary', angle: 0 };
  } catch (_) {}
  try {
    win.outerWidth = 1440;
    win.outerHeight = 900;
    win.innerWidth = 1440;
    win.innerHeight = 820;
    win.devicePixelRatio = 2;
  } catch (_) {}
  try {
    define(win.document, 'hidden', false);
    define(win.document, 'visibilityState', 'visible');
    define(win.document, 'hasFocus', () => true);
  } catch (_) {}
  try {
    Object.defineProperty(win.document, 'referrer', {
      value: parentUrl || 'https://www.paypal.com/',
      configurable: true,
    });
  } catch (_) {}
  try {
    Object.defineProperty(win.location, 'ancestorOrigins', {
      value: {
        length: 1,
        0: 'https://www.paypal.com',
        item(i) {
          return i === 0 ? 'https://www.paypal.com' : null;
        },
        contains(o) {
          return o === 'https://www.paypal.com';
        },
        [Symbol.iterator]: function* () {
          yield 'https://www.paypal.com';
        },
      },
      configurable: true,
    });
  } catch (_) {}
  try {
    win.matchMedia =
      win.matchMedia ||
      function matchMedia(query) {
        const q = String(query || '');
        let matches = false;
        if (/prefers-color-scheme:\s*light/i.test(q)) matches = true;
        if (/prefers-reduced-motion:\s*no-preference/i.test(q)) matches = true;
        if (/hover:\s*hover/i.test(q)) matches = true;
        if (/pointer:\s*fine/i.test(q)) matches = true;
        if (/min-width/i.test(q)) matches = true;
        if (/landscape/i.test(q)) matches = true;
        if (/prefers-color-scheme:\s*dark/i.test(q)) matches = false;
        return {
          matches,
          media: q,
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

  // performance.now with sub-ms jitter + Chrome memory surface
  try {
    const origin = Date.now() - 1200;
    if (!win.performance) win.performance = {};
    const t0 = origin;
    win.performance.now = function now() {
      return Date.now() - t0 + Math.random() * 0.01;
    };
    win.performance.timeOrigin = t0;
    try {
      win.performance.memory = {
        jsHeapSizeLimit: 4294705152,
        totalJSHeapSize: 52000000 + Math.floor(Math.random() * 5000000),
        usedJSHeapSize: 30000000 + Math.floor(Math.random() * 8000000),
      };
    } catch (_) {}
    if (!win.performance.getEntriesByType) {
      win.performance.getEntriesByType = function () {
        return [];
      };
    }
    if (!win.performance.timing) {
      win.performance.timing = {
        navigationStart: t0,
        fetchStart: t0 + 2,
        domainLookupStart: t0 + 3,
        domainLookupEnd: t0 + 5,
        connectStart: t0 + 5,
        connectEnd: t0 + 20,
        requestStart: t0 + 21,
        responseStart: t0 + 80,
        responseEnd: t0 + 120,
        domLoading: t0 + 130,
        domInteractive: t0 + 300,
        domContentLoadedEventStart: t0 + 320,
        domContentLoadedEventEnd: t0 + 325,
        domComplete: t0 + 400,
        loadEventStart: t0 + 401,
        loadEventEnd: t0 + 405,
      };
    }
  } catch (_) {}

  // crypto.getRandomValues already in happy-dom; ensure subtle exists
  try {
    if (win.crypto && !win.crypto.subtle) {
      win.crypto.subtle = {};
    }
  } catch (_) {}

  // Notification / Permissions presence
  try {
    win.Notification = win.Notification || {
      permission: 'default',
      requestPermission() {
        return Promise.resolve('default');
      },
    };
  } catch (_) {}

  // speechSynthesis stub
  try {
    win.speechSynthesis = win.speechSynthesis || {
      getVoices() {
        return [
          { name: 'Google US English', lang: 'en-US', default: true, localService: true, voiceURI: 'Google US English' },
        ];
      },
      speaking: false,
      pending: false,
      paused: false,
      addEventListener() {},
      removeEventListener() {},
    };
  } catch (_) {}

  try {
    win.console = {
      log: (...args) => console.error('[hcap]', ...args),
      info: (...args) => console.error('[hcap]', ...args),
      warn: (...args) => console.error('[hcap:warn]', ...args),
      error: (...args) => console.error('[hcap:error]', ...args),
      debug: () => {},
      dir: () => {},
      table: () => {},
      group: () => {},
      groupEnd: () => {},
      time: () => {},
      timeEnd: () => {},
    };
  } catch (_) {}

  // WASM/BigInt already via injectCriticalGlobals (also re-run below for safety)
  injectCriticalGlobals(win);

  // Real Worker via worker_threads blob URL is hard; use a minimal message-capable stub
  // that still executes string scripts in a nested vm when possible.
  try {
    if (!win.Worker || win.__pps_fake_worker) {
      const { Worker: NodeWorker } = require('worker_threads');
      const { pathToFileURL } = require('url');
      const os = require('os');
      function BlobWorker(scriptURL, options) {
        const self = this;
        this.onmessage = null;
        this.onerror = null;
        this._listeners = { message: [], error: [] };
        let code = '';
        try {
          if (typeof scriptURL === 'string' && scriptURL.startsWith('blob:')) {
            // happy-dom blob URLs may not resolve; best-effort
            code = '';
          } else if (typeof scriptURL === 'string' && scriptURL.startsWith('data:')) {
            const idx = scriptURL.indexOf(',');
            code = decodeURIComponent(scriptURL.slice(idx + 1));
          } else {
            code = String(scriptURL || '');
          }
        } catch (_) {
          code = '';
        }
        // Fallback: empty worker that echos — better than missing constructor
        const workerCode =
          code ||
          `
          const { parentPort } = require('worker_threads');
          parentPort.on('message', (m) => parentPort.postMessage(m));
        `;
        try {
          this._w = new NodeWorker(workerCode, { eval: true });
          this._w.on('message', (data) => {
            const ev = { data };
            if (typeof self.onmessage === 'function') self.onmessage(ev);
            for (const fn of self._listeners.message) fn(ev);
          });
          this._w.on('error', (err) => {
            const ev = { message: err && err.message, error: err };
            if (typeof self.onerror === 'function') self.onerror(ev);
            for (const fn of self._listeners.error) fn(ev);
          });
        } catch (e) {
          console.error('[hcap] Worker create fail', e && e.message);
          this._w = null;
        }
      }
      BlobWorker.prototype.postMessage = function (data) {
        try {
          if (this._w) this._w.postMessage(data);
        } catch (_) {}
      };
      BlobWorker.prototype.terminate = function () {
        try {
          if (this._w) this._w.terminate();
        } catch (_) {}
      };
      BlobWorker.prototype.addEventListener = function (type, fn) {
        if (this._listeners[type]) this._listeners[type].push(fn);
      };
      BlobWorker.prototype.removeEventListener = function (type, fn) {
        if (!this._listeners[type]) return;
        this._listeners[type] = this._listeners[type].filter((f) => f !== fn);
      };
      win.Worker = BlobWorker;
      win.__pps_fake_worker = true;
    }
  } catch (e) {
    console.error('[hcap] Worker patch fail', e && e.message);
    try {
      if (!win.Worker) {
        win.Worker = function Worker() {
          this.postMessage = () => {};
          this.terminate = () => {};
          this.addEventListener = () => {};
          this.removeEventListener = () => {};
        };
      }
    } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// HTML merge (happy-dom multi-script scope isolation)
// ---------------------------------------------------------------------------
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

function browserSettings(userAgent, timeoutMs, ResponseCtor) {
  return {
    enableJavaScriptEvaluation: true,
    disableJavaScriptEvaluation: false,
    disableJavaScriptFileLoading: false,
    disableCSSFileLoading: true, // CSS not needed for passive mint
    disableIframePageLoading: false,
    suppressInsecureJavaScriptEnvironmentWarning: true,
    suppressCodeGenerationFromStringsWarning: true,
    fetch: {
      disableSameOriginPolicy: true,
      disableStrictSSL: true,
      interceptor: makeInterceptor(ResponseCtor || (happy.Response || globalThis.Response)),
      // happy-dom expects Array<{url?, headers}> — plain object throws "not iterable"
      requestHeaders: [
        {
          url: '',
          headers: {
            'sec-ch-ua': `"Chromium";v="${CHROME_MAJOR}", "Not-A.Brand";v="24", "Google Chrome";v="${CHROME_MAJOR}"`,
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'accept-language': 'en-US,en;q=0.9,es-MX;q=0.8',
          },
        },
      ],
    },
    navigation: { crossOriginPolicy: 'anyOrigin' },
    navigator: { userAgent, maxTouchPoints: 0 },
    device: {
      prefersColorScheme: 'light',
      prefersReducedMotion: 'no-preference',
      mediaType: 'screen',
      forcedColors: 'none',
    },
    viewport: { width: 1440, height: 900, devicePixelRatio: 2 },
    timer: {
      maxTimeout: Math.max(timeoutMs + 15000, 90000),
      maxIntervalTime: Math.max(timeoutMs + 15000, 90000),
      maxIntervalIterations: 500000,
    },
  };
}

function patchAllFrames(rootWin, userAgent, parentUrl, parentPostMessage) {
  try {
    patchWindow(rootWin, userAgent, parentUrl, parentPostMessage);
  } catch (_) {}
  try {
    const frames = rootWin.document && rootWin.document.querySelectorAll('iframe');
    if (!frames) return;
    for (const iframe of Array.from(frames)) {
      try {
        const cw = iframe.contentWindow;
        if (cw) {
          // Nested frames: parent is the bridge window, not our collector
          patchWindow(cw, userAgent, rootWin.location && rootWin.location.href, null);
          // recurse one level
          try {
            const nested = cw.document && cw.document.querySelectorAll('iframe');
            if (nested) {
              for (const n of Array.from(nested)) {
                try {
                  if (n.contentWindow) patchWindow(n.contentWindow, userAgent, cw.location.href, null);
                } catch (_) {}
              }
            }
          } catch (_) {}
        }
      } catch (_) {}
    }
  } catch (_) {}
}

function fetchText(url, userAgent, redirects = 0) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const mod = u.protocol === 'http:' ? http : https;
    const req = mod.get(
      {
        protocol: u.protocol,
        hostname: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        headers: {
          'user-agent': userAgent || DEFAULT_UA,
          accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'accept-language': 'en-US,en;q=0.9',
          referer: 'https://www.paypal.com/',
          'sec-ch-ua': `"Chromium";v="${CHROME_MAJOR}", "Not-A.Brand";v="24", "Google Chrome";v="${CHROME_MAJOR}"`,
          'sec-ch-ua-mobile': '?0',
          'sec-ch-ua-platform': '"macOS"',
        },
      },
      (res) => {
        const status = res.statusCode || 0;
        const loc = res.headers.location;
        if (status >= 300 && status < 400 && loc && redirects < 5) {
          res.resume();
          fetchText(new URL(loc, url).toString(), userAgent, redirects + 1).then(resolve, reject);
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

function statesReached(messages) {
  return messages.some(
    (m) => m && m.msg && String(m.msg.captchaState || '').includes('JS_LOADED')
  );
}

async function runWithBrowserNav({
  iframeUrl,
  html,
  userAgent,
  parentUrl,
  timeoutMs,
  parentPostMessage,
  strategyName,
}) {
  // Serve merged HTML at the REAL paypalobjects URL via interceptor rewrite.
  if (html) __bridgeRewriteHtml = html;
  else __bridgeRewriteHtml = '';

  const settings = browserSettings(userAgent, timeoutMs);
  settings.navigation = settings.navigation || {};
  settings.navigation.beforeContentCallback = (window) => {
    try {
      // Fires for main + every iframe document load — only place that sees the REAL frame Window.
      injectCriticalGlobals(window);
      // CRITICAL: only rebind parent.postMessage on the PayPal passive bridge frame.
      // Nested challenge/checkbox iframes must keep their real parent (the bridge),
      // otherwise hCaptcha inter-frame messaging breaks → PASSIVE_ERROR after site-setup.
      const bindCollector = shouldBindParentCollector(window);
      patchWindow(window, userAgent, parentUrl, bindCollector ? parentPostMessage : null);
      try {
        console.error(
          '[hcap] beforeContent url=',
          String((window.location && window.location.href) || '').slice(0, 100),
          'bindCollector=',
          bindCollector,
          'wasm=',
          typeof window.WebAssembly,
          'subtle=',
          !!(window.crypto && window.crypto.subtle && window.crypto.subtle.encrypt)
        );
      } catch (_) {}
    } catch (e) {
      console.error('[hcap] beforeContent patch fail', e && e.message);
    }
  };

  const browser = new Browser({ settings });
  const page = browser.newPage();
  let win = page.mainFrame.window;
  patchWindow(win, userAgent, parentUrl, parentPostMessage);

  const navUrl = iframeUrl;
  try {
    console.error('[hcap] goto', String(navUrl).slice(0, 140));
    await page.goto(navUrl, {
      timeout: Math.min(timeoutMs, 45000),
      referrer: parentUrl || 'https://www.paypal.com/',
    });
  } catch (e) {
    console.error('[hcap] goto fail', e && e.message);
  }

  win = page.mainFrame.window;
  patchAllFrames(win, userAgent, parentUrl, parentPostMessage);

  try {
    console.error(
      '[hcap] diag scripts=%s hcaptcha=%s webdriver=%s chrome=%s wasm=%s worker=%s bigint=%s iframes=%s',
      win.document.scripts.length,
      typeof win.hcaptcha,
      win.navigator && win.navigator.webdriver,
      !!(win.chrome && win.chrome.runtime),
      typeof win.WebAssembly,
      typeof win.Worker,
      typeof win.BigInt,
      win.document.querySelectorAll('iframe').length
    );
  } catch (_) {}

  let lastCscReq = '';
  // Capture checksiteconfig JWT from network for manual hsw drive if UI stalls
  try {
    const settings2 = page.context && page.context.browser && page.context.browser.settings;
    // already have interceptor on settings; stash via global
  } catch (_) {}
  global.__hcap_last_csc_req = '';

  const patchTimer = setInterval(() => {
    try {
      win = page.mainFrame.window;
      // Only re-bind collector on bridge; never patch nested parents
      injectCriticalGlobals(win);
      if (shouldBindParentCollector(win)) {
        patchWindow(win, userAgent, parentUrl, parentPostMessage);
      } else {
        injectCriticalGlobals(win);
      }
      // Drive/diagnose child frames via BrowserFrame API (real realm, not cross-origin proxy)
      try {
        const kids = page.mainFrame.childFrames || [];
        for (const f of kids) {
          try {
            const cw = f.window;
            if (!cw) continue;
            injectCriticalGlobals(cw);
            if (!cw.__pps_fp_once) {
              cw.__pps_fp_once = true;
              try {
                patchNavigator(cw, userAgent);
                patchCanvas(cw);
                patchAudio(cw);
                patchChrome(cw);
              } catch (_) {}
              console.error(
                '[hcap-frame]',
                String(cw.location.href).slice(0, 90),
                'hsw=',
                typeof cw.hsw,
                'scripts=',
                cw.document && cw.document.scripts && cw.document.scripts.length
              );
            }
            // If hsw is ready and we have a csc req, and no token yet, kick PoW + encrypted getcaptcha
            if (
              typeof cw.hsw === 'function' &&
              global.__hcap_last_csc_req &&
              !cw.__pps_hsw_kicked &&
              !global.__hcap_stop
            ) {
              cw.__pps_hsw_kicked = true;
              const req = global.__hcap_last_csc_req;
              console.error('[hcap-frame] kicking hsw proof len(req)=', req.length);
              Promise.resolve()
                .then(() => cw.hsw(req))
                .then(async (proof) => {
                  console.error(
                    '[hcap-frame] hsw proof ok len=',
                    proof && String(proof).length
                  );
                  global.__hcap_last_proof = String(proof || '');
                  // Try encrypted getcaptcha using frame's own msgpack if present
                  try {
                    const msgpack = cw.msgpack || cw.Ar || null;
                    // msgpack is deleted after capture in official code; may be gone
                    let encodeFn = null;
                    if (msgpack && typeof msgpack.encode === 'function') encodeFn = msgpack.encode.bind(msgpack);
                    if (!encodeFn && global.__hcap_msgpack && global.__hcap_msgpack.encode) {
                      encodeFn = global.__hcap_msgpack.encode.bind(global.__hcap_msgpack);
                      console.error('[hcap-frame] using captured official msgpack');
                    }
                    if (!encodeFn) {
                      try {
                        const mp = require('@msgpack/msgpack');
                        encodeFn = mp.encode;
                        console.error('[hcap-frame] using npm @msgpack/msgpack fallback');
                      } catch (_) {}
                    }
                    if (!encodeFn) {
                      console.error('[hcap-frame] no msgpack encode');
                      return;
                    }
                    const cObj = global.__hcap_last_csc_obj || { type: 'hsw', req };
                    // Dynamic sitekey / asset V / host — hardcoded V was a soft-reject bug:
                    // live frames ship ced1647… while body.v was pinned to 7d2138a… .
                    const hrefNow = String((cw.location && cw.location.href) || '');
                    const hashNow = String((cw.location && cw.location.hash) || '');
                    const sitekey =
                      (global.__hcap_sitekey && String(global.__hcap_sitekey)) ||
                      (/[?&#]sitekey=([0-9a-fA-F-]{20,})/i.exec(hrefNow) || [])[1] ||
                      (/[?&#]siteKey=([0-9a-fA-F-]{20,})/i.exec(String(iframeUrl || '')) || [])[1] ||
                      '884d15d9-b649-4bbb-8d1c-2d6f0eed75eb';
                    const host =
                      (/[?&#]host=([^&]+)/i.exec(hashNow) || [])[1]
                        ? decodeURIComponent((/[?&#]host=([^&]+)/i.exec(hashNow) || [])[1])
                        : (/[?&#]host=([^&]+)/i.exec(hrefNow) || [])[1]
                          ? decodeURIComponent((/[?&#]host=([^&]+)/i.exec(hrefNow) || [])[1])
                          : 'www.paypalobjects.com';
                    const assetV =
                      (/\/captcha\/v1\/([a-f0-9]{20,})\//i.exec(hrefNow) || [])[1] ||
                      (/\/captcha\/v1\/([a-f0-9]{20,})\//i.exec(String(global.__hcap_last_asset || '')) || [])[1] ||
                      (global.__hcap_asset_v && String(global.__hcap_asset_v)) ||
                      'ced1647459f073cc025a1281baafa600680d7f3e';
                    console.error(
                      '[hcap-frame] pack meta sitekey=',
                      sitekey.slice(0, 12),
                      'host=',
                      host,
                      'v=',
                      assetV.slice(0, 12)
                    );
                    const now = Date.now();
                    const body = {
                      v: assetV,
                      sitekey,
                      host,
                      hl: (global.__hcap_hl && String(global.__hcap_hl)) || 'pt',
                      motionData: JSON.stringify({
                        st: now - 1100,
                        dct: now - 1050,
                        mm: Array.from({ length: 80 }, (_, i) => [
                          30 + i * 4,
                          50 + (i % 23),
                          i * 11,
                        ]),
                        'mm-mp': 12.5,
                        md: [[120, 90, 600]],
                        mu: [[120, 90, 640]],
                        topLevel: {
                          st: now - 1600,
                          sc: {
                            availWidth: 1440,
                            availHeight: 875,
                            width: 1440,
                            height: 900,
                            colorDepth: 24,
                            pixelDepth: 24,
                          },
                          nv: {
                            platform: 'MacIntel',
                            language: 'en-US',
                            languages: ['en-US', 'en'],
                            hardwareConcurrency: 10,
                            deviceMemory: 8,
                            webdriver: false,
                            maxTouchPoints: 0,
                            vendor: 'Google Inc.',
                          },
                          dr: 'https://www.paypal.com/',
                          inv: true,
                          exec: true,
                        },
                        v: 1,
                      }),
                      n: String(proof),
                      pem: JSON.stringify({
                        csc: 180,
                        csch: 'api.hcaptcha.com',
                        cscrt: 45,
                        cscft: 200,
                      }),
                      pst: false,
                    };
                    const enc = await cw.hsw(1, encodeFn(body));
                    console.error(
                      '[hcap-frame] encrypt ok',
                      enc && enc.constructor && enc.constructor.name,
                      enc && (enc.byteLength || enc.length),
                      'typeof',
                      typeof enc
                    );
                    const cStr = JSON.stringify(cObj);
                    // Do NOT coerce enc to Uint8Array — keep ExtType 18 metadata.
                    const packed = encodeFn([cStr, enc]);
                    const packedBuf =
                      packed instanceof Uint8Array
                        ? packed
                        : new Uint8Array(packed.buffer || packed);
                    console.error('[hcap-frame] packed len', packedBuf.length);
                    // POST via frame fetch (same cookie jar / TLS stack as happy-dom)
                    const endpoints = [
                      'https://api.hcaptcha.com/getcaptcha/' + sitekey,
                      'https://hcaptcha.paypal.com/getcaptcha/' + sitekey,
                    ];
                    for (const ep of endpoints) {
                      try {
                        const resp = await cw.fetch(ep, {
                          method: 'POST',
                          headers: {
                            accept: 'application/json, application/octet-stream',
                            'content-type': 'application/octet-stream',
                          },
                          body: packedBuf,
                        });
                        const ct = resp.headers && resp.headers.get && resp.headers.get('content-type');
                        console.error('[hcap-frame] getcaptcha', ep.slice(0, 40), resp.status, ct);
                        if (resp.status === 200) {
                          let j = null;
                          const ctLow = String(ct || '').toLowerCase();
                          if (ctLow.includes('octet-stream') || ctLow.includes('application/msgpack')) {
                            // Binary success path: hsw(0, bytes) → msgpack.decode → token
                            try {
                              const ab = await resp.arrayBuffer();
                              const u8 = new Uint8Array(ab);
                              console.error('[hcap-frame] bin len', u8.length, 'decrypting…');
                              const plain = await cw.hsw(0, u8);
                              if (plain != null) {
                                let decodeFn = encodeFn; // same codec often has decode
                                if (global.__hcap_msgpack && global.__hcap_msgpack.decode) {
                                  decodeFn = global.__hcap_msgpack.decode.bind(global.__hcap_msgpack);
                                } else if (encodeFn && encodeFn.decode) {
                                  decodeFn = encodeFn.decode.bind(encodeFn);
                                } else {
                                  try {
                                    decodeFn = require('@msgpack/msgpack').decode;
                                  } catch (_) {
                                    decodeFn = null;
                                  }
                                }
                                // encodeFn is encode; need decode separately
                                let dec = null;
                                if (global.__hcap_msgpack && global.__hcap_msgpack.decode) {
                                  dec = global.__hcap_msgpack.decode.bind(global.__hcap_msgpack);
                                } else {
                                  try {
                                    dec = require('@msgpack/msgpack').decode;
                                  } catch (_) {}
                                }
                                if (dec) {
                                  j = dec(plain instanceof Uint8Array ? plain : new Uint8Array(plain));
                                  console.error(
                                    '[hcap-frame] decrypted keys',
                                    j && Object.keys(j)
                                  );
                                } else {
                                  console.error('[hcap-frame] no msgpack decode for binary resp');
                                }
                              }
                            } catch (de) {
                              console.error('[hcap-frame] decrypt fail', de && de.message);
                            }
                          } else {
                            try {
                              j = await resp.json();
                            } catch (_) {
                              const buf = await resp.arrayBuffer();
                              console.error('[hcap-frame] bin len', buf.byteLength);
                              // try decrypt even if CT wrong
                              try {
                                const plain = await cw.hsw(0, new Uint8Array(buf));
                                let dec = null;
                                if (global.__hcap_msgpack && global.__hcap_msgpack.decode) {
                                  dec = global.__hcap_msgpack.decode.bind(global.__hcap_msgpack);
                                } else {
                                  try {
                                    dec = require('@msgpack/msgpack').decode;
                                  } catch (_) {}
                                }
                                if (dec && plain) j = dec(plain);
                              } catch (_) {}
                            }
                          }
                          if (j) {
                            console.error(
                              '[hcap-frame] gc success=',
                              j.success,
                              'pass=',
                              j.pass,
                              'errors=',
                              JSON.stringify(j['error-codes'] || j.error_codes || []),
                              'keys=',
                              Object.keys(j)
                            );
                            try {
                              require('fs').writeFileSync(
                                require('path').join(__dirname, '..', '_gc_frame_last.json'),
                                JSON.stringify(j).slice(0, 20000)
                              );
                            } catch (_) {}
                            const tok = j.generated_pass_UUID || j.token || '';
                            if (tok) {
                              console.error('[hcap-frame] TOKEN len', tok.length);
                              parentPostMessage(
                                JSON.stringify({
                                  token: tok,
                                  renderData: {
                                    hcaptchaPassiveRenderStartTime: Date.now() - 2000,
                                    hcaptchaPassiveRenderEndTime: Date.now() - 500,
                                    hcaptchaPassiveVerificationTime: Date.now(),
                                  },
                                  source: 'hCaptchaPassiveEval',
                                }),
                                'https://www.paypal.com'
                              );
                              return;
                            }
                            if (j.c && j.c.req) {
                              global.__hcap_last_csc_req = j.c.req;
                              global.__hcap_last_csc_obj = j.c;
                              if (!cw.__pps_hsw_retries) cw.__pps_hsw_retries = 0;
                              if (cw.__pps_hsw_retries < 2) {
                                cw.__pps_hsw_retries += 1;
                                cw.__pps_hsw_kicked = false;
                              }
                            }
                          }
                        }
                      } catch (e) {
                        console.error('[hcap-frame] getcaptcha fail', e && e.message);
                      }
                    }
                  } catch (e) {
                    console.error('[hcap-frame] enc/gc fail', e && e.message);
                  }
                })
                .catch((e) => {
                  console.error('[hcap-frame] hsw fail', e && e.message);
                });
            }
          } catch (e) {
            /* ignore per-frame */
          }
        }
      } catch (_) {}
      if (global.__hcap_stop) return;
    } catch (_) {}
  }, 200);

  try {
    await page.waitUntilComplete({ timeout: Math.min(timeoutMs, 70000) }).catch(() => {});
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (global.__hcap_stop) break;
      await sleep(200);
    }
  } finally {
    clearInterval(patchTimer);
    __bridgeRewriteHtml = '';
    try {
      await browser.close();
    } catch (_) {}
  }
  return page;
}

async function runWithWindowWrite({ iframeUrl, html, userAgent, parentUrl, timeoutMs, parentPostMessage }) {
  // Prefer Browser page even for "write" path so iframe beforeContentCallback fires.
  // Plain Window() cross-origin iframes hide the real realm behind CrossOriginBrowserWindow.
  if (Browser) {
    const settings = browserSettings(userAgent, timeoutMs);
    settings.navigation = settings.navigation || {};
    settings.navigation.beforeContentCallback = (window) => {
      try {
        injectCriticalGlobals(window);
        const bindCollector = shouldBindParentCollector(window);
        patchWindow(window, userAgent, parentUrl, bindCollector ? parentPostMessage : null);
      } catch (e) {
        console.error('[hcap] write-beforeContent fail', e && e.message);
      }
    };
    // Serve merged HTML at FULL iframeUrl (must keep siteKey query) via interceptor.
    if (html) __bridgeRewriteHtml = html;
    const browser = new Browser({ settings });
    const page = browser.newPage();
    let win = page.mainFrame.window;
    patchWindow(win, userAgent, parentUrl, parentPostMessage);
    try {
      console.error('[hcap] write-path goto full url with siteKey query');
      await page.goto(iframeUrl, {
        timeout: Math.min(timeoutMs, 45000),
        referrer: parentUrl || 'https://www.paypal.com/',
      });
    } catch (e) {
      console.error('[hcap] write-path goto fail', e && e.message);
      // last resort: document.write under full URL
      try {
        win = page.mainFrame.window;
        const body = html || (await fetchText(iframeUrl, userAgent));
        win.document.open();
        win.document.write(body);
        win.document.close();
      } catch (e2) {
        console.error('[hcap] document.write fail', e2 && e2.message);
      }
    }
    win = page.mainFrame.window;
    patchWindow(win, userAgent, parentUrl, parentPostMessage);
    try {
      console.error(
        '[hcap] write-path location=',
        String(win.location.href).slice(0, 160),
        'siteKey?',
        /siteKey=/.test(String(win.location.href)),
        'hcaptcha=',
        typeof win.hcaptcha
      );
    } catch (_) {}
    const patchTimer = setInterval(() => {
      try {
        win = page.mainFrame.window;
        injectCriticalGlobals(win);
        patchWindow(win, userAgent, parentUrl, parentPostMessage);
      } catch (_) {}
    }, 120);
    try {
      await page.waitUntilComplete({ timeout: Math.min(timeoutMs, 70000) }).catch(() => {});
      const started = Date.now();
      while (Date.now() - started < timeoutMs) {
        if (global.__hcap_stop) break;
        await sleep(200);
      }
    } finally {
      clearInterval(patchTimer);
      try {
        await browser.close();
      } catch (_) {}
    }
    return;
  }

  // Fallback: classic Window (weaker iframe realm)
  const win = new Window({
    url: iframeUrl,
    width: 1440,
    height: 900,
    settings: browserSettings(userAgent, timeoutMs),
  });
  patchWindow(win, userAgent, parentUrl, parentPostMessage);
  const body = html || (await fetchText(iframeUrl, userAgent));
  win.document.write(body);
  win.document.close();
  patchAllFrames(win, userAgent, parentUrl, parentPostMessage);

  const patchTimer = setInterval(() => {
    try {
      patchAllFrames(win, userAgent, parentUrl, parentPostMessage);
    } catch (_) {}
  }, 120);

  try {
    await win.happyDOM.waitUntilComplete({ timeout: Math.min(timeoutMs, 70000) }).catch(() => {});
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (global.__hcap_stop) break;
      await sleep(200);
    }
  } finally {
    clearInterval(patchTimer);
    try {
      win.happyDOM.abort();
    } catch (_) {}
    try {
      win.close();
    } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
(async () => {
  const input = JSON.parse(readStdin() || '{}');
  const iframeUrl = String(input.iframeUrl || input.iframe_url || '').trim();
  if (!iframeUrl) throw new Error('iframeUrl is required');
  const parentUrl = String(input.parentUrl || input.parent_url || 'https://www.paypal.com/').trim();
  const userAgent = String(input.userAgent || input.user_agent || DEFAULT_UA);
  const timeoutMs = Math.max(10000, Number(input.timeoutMs || input.timeout_ms || 60000));

  // Proxy: only use explicit input.proxy OR MIN_BA_HCAP_FORCE_ENV_PROXY=1.
  // System Clash HTTPS_PROXY often breaks local/relay assumptions; default off unless asked.
  if (input.proxy) {
    process.env.HTTPS_PROXY = String(input.proxy);
    process.env.HTTP_PROXY = String(input.proxy);
    process.env.ALL_PROXY = String(input.proxy);
    installProxyFromEnv();
  } else if (process.env.MIN_BA_HCAP_FORCE_ENV_PROXY === '1') {
    installProxyFromEnv();
  } else {
    // strip inherited proxy so happy-dom/node hit network directly (or we pass proxy later)
    delete process.env.HTTPS_PROXY;
    delete process.env.HTTP_PROXY;
    delete process.env.ALL_PROXY;
    delete process.env.https_proxy;
    delete process.env.http_proxy;
    console.error('[hcap] proxy=none (direct; pass input.proxy for sticky exit)');
  }

  // Timezone: match exit region
  const region = String(input.region || process.env.MIN_BA_HCAP_REGION || 'MX').toUpperCase();
  const tzMap = {
    MX: 'America/Mexico_City',
    BR: 'America/Sao_Paulo',
    US: 'America/New_York',
    GB: 'Europe/London',
  };
  const tz = String(input.tz || process.env.TZ || tzMap[region] || 'America/Mexico_City');
  try {
    process.env.TZ = tz;
    console.error('[hcap] TZ=', tz, 'region=', region);
  } catch (_) {}

  const startedAt = Date.now();
  const messages = [];
  let resolvedToken = '';
  let resolvedRenderData = {};
  let terminalError = '';
  let lastPassiveError = false;

  const parentPostMessage = (data, targetOrigin) => {
    const msg = parseMessage(data);
    messages.push({
      t: Date.now(),
      targetOrigin: String(targetOrigin || ''),
      msg,
    });
    if (msg && msg.log && msg.captchaState) {
      console.error('[hcap-state]', msg.captchaState);
      if (String(msg.captchaState).includes('PASSIVE_ERROR')) {
        lastPassiveError = true;
        // stop this strategy; outer may still try other strategies
        global.__hcap_stop = true;
      }
      if (String(msg.captchaState).includes('PASSIVE_SOLVED')) {
        // rare: state-only success without token field
      }
    }
    // dump interesting hCaptcha internal messages (full-ish for debugging)
    if (msg && (msg.label || msg.contents || msg.error || msg.event || msg.data || msg.type)) {
      let contentsPreview = undefined;
      try {
        if (typeof msg.contents === 'string') contentsPreview = msg.contents.slice(0, 400);
        else if (Array.isArray(msg.contents)) contentsPreview = msg.contents.slice(0, 10);
        else if (msg.contents && typeof msg.contents === 'object')
          contentsPreview = JSON.stringify(msg.contents).slice(0, 400);
      } catch (_) {}
      console.error(
        '[hcap-msg]',
        JSON.stringify({
          label: msg.label,
          id: msg.id,
          event: msg.event,
          type: msg.type,
          error: msg.error,
          contents: contentsPreview,
          keys: msg && typeof msg === 'object' ? Object.keys(msg).slice(0, 20) : [],
        })
      );
    }
    const token = extractToken(msg);
    if (token) {
      if (token === 'NOT_REACHABLE' || token === 'RENDER_FAILURE' || token === 'EMPTY_TOKEN') {
        terminalError = token;
        global.__hcap_stop = true;
      } else {
        resolvedToken = token;
        resolvedRenderData =
          (msg.renderData && typeof msg.renderData === 'object' && msg.renderData) ||
          (msg.result && msg.result.renderData) ||
          {};
        global.__hcap_stop = true;
      }
    }
  };

  let html = String(input.html || '');
  if (!html) {
    try {
      html = await fetchText(iframeUrl, userAgent);
      console.error('[hcap] fetched bridge html len=', html.length);
    } catch (e) {
      console.error('[hcap] fetch iframe soft-fail', e && e.message);
      html = '';
    }
  }
  const mergedHtml = html ? mergeInlineScripts(html) : '';
  if (mergedHtml && mergedHtml !== html) {
    console.error('[hcap] merged inline scripts for happy-dom global scope');
  }

  const remain = () => Math.max(12000, timeoutMs - (Date.now() - startedAt));

  // Strategy v3.2: ONE primary full-budget path (remote+merged). Avoid splitting
  // budget across strategies that re-run checksiteconfig after hsw already started.
  if (!resolvedToken && !terminalError && Browser) {
    global.__hcap_stop = false;
    lastPassiveError = false;
    try {
      console.error('[hcap] strategy=remote-merged-interceptor (solo)');
      await runWithBrowserNav({
        iframeUrl,
        html: mergedHtml || html || '',
        userAgent,
        parentUrl,
        timeoutMs: Math.max(remain(), timeoutMs),
        parentPostMessage,
        strategyName: 'remote-merged',
      });
    } catch (e) {
      console.error('[hcap] remote-merged fail', e && e.message);
    }
  }

  // Fallback only if primary never reached JS_LOADED
  const reachedJs = statesReached(messages);
  if (
    !resolvedToken &&
    !terminalError &&
    !reachedJs &&
    (mergedHtml || html)
  ) {
    global.__hcap_stop = false;
    try {
      console.error('[hcap] strategy=window-write fallback');
      await runWithWindowWrite({
        iframeUrl,
        html: mergedHtml || html,
        userAgent,
        parentUrl,
        timeoutMs: Math.min(remain(), 60000),
        parentPostMessage,
      });
    } catch (e) {
      console.error('[hcap] window-write fail', e && e.message);
    }
  }

  const states = messages.map((m) => m.msg && m.msg.captchaState).filter(Boolean);
  const out = {
    ok: Boolean(resolvedToken),
    token: resolvedToken,
    renderData: resolvedRenderData,
    error: resolvedToken
      ? ''
      : terminalError || (lastPassiveError ? 'PASSIVE_ERROR' : 'timeout/no_token'),
    elapsedMs: Date.now() - startedAt,
    states,
    messageCount: messages.length,
    netLog: netLog.slice(-40),
    tz,
    region,
    recentMessages: messages.slice(-12).map((m) => ({
      t: m.t,
      keys: m.msg && typeof m.msg === 'object' ? Object.keys(m.msg) : [],
      captchaState: m.msg && m.msg.captchaState,
      tokenLen: extractToken(m.msg).length,
      errorish: extractToken(m.msg),
      label: m.msg && m.msg.label,
      id: m.msg && m.msg.id,
      // shallow contents for challenge type detection
      contentsType: m.msg && m.msg.contents && typeof m.msg.contents,
      contentsKeys:
        m.msg && m.msg.contents && typeof m.msg.contents === 'object'
          ? Object.keys(m.msg.contents).slice(0, 20)
          : undefined,
      contentsHead:
        m.msg && typeof m.msg.contents === 'string' ? m.msg.contents.slice(0, 200) : undefined,
    })),
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(resolvedToken ? 0 : 2);
})().catch((err) => {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      error: (err && (err.stack || err.message)) || String(err),
      netLog,
    })
  );
  process.exit(1);
});
