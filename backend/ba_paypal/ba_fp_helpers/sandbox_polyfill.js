"use strict";

// Browser-API polyfills the hsw.js fingerprinting routine pokes at when
// window.hsw(jwt) runs. jsdom omits these so the WASM throws when it tries
// to read them (typical failure: "s(...)[im(...)] is not a function" where
// the resolved string is something like "getEntriesByType", "getExtension",
// "userAgentData", etc.). We give every API a benign-but-non-throwing
// implementation so the PoW path completes.
//
// Usage:
//   require('./sandbox_polyfill').install(window);

function install(win) {
    const doc = win.document;

    // -------------------------------------------------------------------
    // Performance API additions
    // -------------------------------------------------------------------
    const perf = win.performance || {};
    if (typeof perf.getEntriesByType !== "function") {
        perf.getEntriesByType = function (type) {
            // Return a single fake navigation entry — enough to satisfy
            // the bundle's "did the page load?" probe.
            if (type === "navigation") {
                return [{
                    name: win.location ? String(win.location.href) : "https://example.com/",
                    entryType: "navigation",
                    startTime: 0,
                    duration: 100,
                    initiatorType: "navigation",
                    nextHopProtocol: "h2",
                    workerStart: 0,
                    redirectStart: 0,
                    redirectEnd: 0,
                    fetchStart: 1,
                    domainLookupStart: 2,
                    domainLookupEnd: 3,
                    connectStart: 3,
                    connectEnd: 10,
                    secureConnectionStart: 5,
                    requestStart: 11,
                    responseStart: 50,
                    responseEnd: 80,
                    transferSize: 5000,
                    encodedBodySize: 4500,
                    decodedBodySize: 12000,
                    serverTiming: [],
                    unloadEventStart: 0,
                    unloadEventEnd: 0,
                    domInteractive: 60,
                    domContentLoadedEventStart: 70,
                    domContentLoadedEventEnd: 72,
                    domComplete: 90,
                    loadEventStart: 95,
                    loadEventEnd: 100,
                    type: "navigate",
                    redirectCount: 0,
                    toJSON() { return {}; },
                }];
            }
            if (type === "resource") return [];
            if (type === "paint") {
                return [
                    { name: "first-paint", entryType: "paint", startTime: 100, duration: 0 },
                    { name: "first-contentful-paint", entryType: "paint", startTime: 110, duration: 0 },
                ];
            }
            if (type === "mark" || type === "measure") return [];
            return [];
        };
    }
    if (typeof perf.getEntries !== "function") {
        perf.getEntries = function () {
            return perf.getEntriesByType("navigation").concat(perf.getEntriesByType("paint"));
        };
    }
    if (typeof perf.getEntriesByName !== "function") {
        perf.getEntriesByName = function () { return []; };
    }
    if (typeof perf.mark !== "function") perf.mark = function () {};
    if (typeof perf.measure !== "function") perf.measure = function () {};
    if (typeof perf.clearMarks !== "function") perf.clearMarks = function () {};
    if (typeof perf.clearMeasures !== "function") perf.clearMeasures = function () {};
    if (typeof perf.clearResourceTimings !== "function") perf.clearResourceTimings = function () {};
    if (typeof perf.setResourceTimingBufferSize !== "function") perf.setResourceTimingBufferSize = function () {};
    if (perf.timing == null) {
        const t0 = Date.now() - 200;
        perf.timing = {
            navigationStart: t0,
            unloadEventStart: 0, unloadEventEnd: 0,
            redirectStart: 0, redirectEnd: 0,
            fetchStart: t0 + 1,
            domainLookupStart: t0 + 2, domainLookupEnd: t0 + 3,
            connectStart: t0 + 3, connectEnd: t0 + 10,
            secureConnectionStart: t0 + 5,
            requestStart: t0 + 11,
            responseStart: t0 + 50, responseEnd: t0 + 80,
            domLoading: t0 + 55, domInteractive: t0 + 60,
            domContentLoadedEventStart: t0 + 70, domContentLoadedEventEnd: t0 + 72,
            domComplete: t0 + 90, loadEventStart: t0 + 95, loadEventEnd: t0 + 100,
        };
    }
    if (perf.navigation == null) {
        perf.navigation = { type: 0, redirectCount: 0 };
    }
    try {
        Object.defineProperty(win, "performance", { value: perf, writable: true, configurable: true });
    } catch (_) { win.performance = perf; }

    // -------------------------------------------------------------------
    // PerformanceObserver — many fingerprint paths call new PerformanceObserver(...).observe(...)
    // -------------------------------------------------------------------
    if (typeof win.PerformanceObserver !== "function") {
        function PerformanceObserver(cb) { this._cb = cb; }
        PerformanceObserver.prototype.observe = function () {};
        PerformanceObserver.prototype.disconnect = function () {};
        PerformanceObserver.prototype.takeRecords = function () { return []; };
        PerformanceObserver.supportedEntryTypes = [
            "navigation", "resource", "paint", "mark", "measure",
            "longtask", "element", "first-input", "largest-contentful-paint",
        ];
        win.PerformanceObserver = PerformanceObserver;
    }

    // -------------------------------------------------------------------
    // navigator additions
    // -------------------------------------------------------------------
    const nav = win.navigator;
    // userAgentData (Chrome only)
if (nav && nav.userAgentData == null) {
        const brands = [
            { brand: "Not=A?Brand",      version: "99" },
            { brand: "Google Chrome",     version: "151" },
            { brand: "Chromium",          version: "151" },
        ];
        try {
            Object.defineProperty(nav, "userAgentData", {
                configurable: true,
                value: {
                    brands: brands,
                    mobile: false,
                    platform: "Windows",
                    getHighEntropyValues(hints) {
                        const out = {
                            brands: brands,
                            mobile: false,
                            platform: "Windows",
                        };
                        if (Array.isArray(hints)) {
                            for (const h of hints) {
                                if (h === "platformVersion") out.platformVersion = "10.0.0";
                                else if (h === "architecture") out.architecture = "x86";
                                else if (h === "bitness") out.bitness = "64";
                                else if (h === "model") out.model = "";
                                else if (h === "uaFullVersion") out.uaFullVersion = "151.0.7922.72";
                                else if (h === "fullVersionList") out.fullVersionList = [
                                    { brand: "Not=A?Brand", version: "99.0.0.0" },
                                    { brand: "Google Chrome", version: "151.0.7922.72" },
                                    { brand: "Chromium", version: "151.0.7922.72" },
                                ];
                                else if (h === "wow64") out.wow64 = false;
                            }
                        }
                        return Promise.resolve(out);
                    },
                    toJSON() {
                        return { brands: brands, mobile: false, platform: "Windows" };
                    },
                },
            });
        } catch (_) {}
    }
    // navigator.gpu — must exist as null/undefined for fingerprint to bail gracefully
    if (nav) {
        try {
            if (!("gpu" in nav)) {
                Object.defineProperty(nav, "gpu", { value: undefined, configurable: true });
            }
        } catch (_) {}
    }
    // hardwareConcurrency, deviceMemory, etc. — jsdom already has hardwareConcurrency
    if (nav && !("deviceMemory" in nav)) {
        try {
            Object.defineProperty(nav, "deviceMemory", { value: 8, configurable: true });
        } catch (_) {}
    }
    if (nav && nav.connection == null) {
        try {
            Object.defineProperty(nav, "connection", {
                value: {
                    effectiveType: "4g",
                    rtt: 100, downlink: 10,
                    saveData: false,
                    type: "wifi",
                    addEventListener() {}, removeEventListener() {},
                    onchange: null,
                },
                configurable: true,
            });
        } catch (_) {}
    }
    if (nav && nav.permissions == null) {
        try {
            Object.defineProperty(nav, "permissions", {
                value: {
                    query(desc) {
                        return Promise.resolve({
                            state: "prompt", status: "prompt",
                            addEventListener() {}, removeEventListener() {},
                            onchange: null,
                        });
                    },
                },
                configurable: true,
            });
        } catch (_) {}
    }
    if (nav && typeof nav.javaEnabled !== "function") {
        try { nav.javaEnabled = function () { return false; }; } catch (_) {}
    }
if (nav && nav.maxTouchPoints == null) {
        try { Object.defineProperty(nav, "maxTouchPoints", { value: 0, configurable: true }); } catch (_) {}
    }
    if (nav && nav.keyboard == null) {
        try { Object.defineProperty(nav, "keyboard", { value: { getLayoutMap: () => Promise.resolve({ entries: () => new Map().entries(), get: () => undefined, has: () => false, size: 0 }) }, configurable: true }); } catch (_) {}
    }
    if (nav && nav.storage == null) {
        try { Object.defineProperty(nav, "storage", { value: { estimate: () => Promise.resolve({ usage: 0, quota: 2147483648 }), persist: () => Promise.resolve(false), persisted: () => Promise.resolve(false) }, configurable: true }); } catch (_) {}
    }
    if (nav && nav.credentials == null) {
        try { Object.defineProperty(nav, "credentials", { value: { get: () => Promise.resolve(null), store: () => Promise.resolve(), create: () => Promise.resolve(), preventSilentAccess: () => Promise.resolve() }, configurable: true }); } catch (_) {}
    }
    if (nav && nav.serial == null) {
        try { Object.defineProperty(nav, "serial", { value: { getPorts: () => Promise.resolve([]), requestPort: () => Promise.reject(new Error('NotFoundError')) }, configurable: true }); } catch (_) {}
    }
    if (nav && nav.scheduling == null) {
        try { Object.defineProperty(nav, "scheduling", { value: { isInputPending: () => false }, configurable: true }); } catch (_) {}
    }
    if (nav && nav.vendor == null) {
        try { Object.defineProperty(nav, "vendor", { value: "Google Inc.", configurable: true }); } catch (_) {}
    }
    if (nav && nav.vendorSub == null) {
        try { Object.defineProperty(nav, "vendorSub", { value: "", configurable: true }); } catch (_) {}
    }
    if (nav && nav.productSub == null) {
        try { Object.defineProperty(nav, "productSub", { value: "20030107", configurable: true }); } catch (_) {}
    }

    // -------------------------------------------------------------------
    // document.fonts (FontFaceSet API)
    // -------------------------------------------------------------------
    if (doc && doc.fonts == null) {
        const fs = new Set();
        fs.ready = Promise.resolve(fs);
        fs.status = "loaded";
        fs.check = function () { return true; };
        fs.load = function () { return Promise.resolve([]); };
        fs.addEventListener = function () {};
        fs.removeEventListener = function () {};
        try {
            Object.defineProperty(doc, "fonts", { value: fs, configurable: true });
        } catch (_) {}
    }

    // -------------------------------------------------------------------
    // OffscreenCanvas — basic stub backed by node-canvas
    // -------------------------------------------------------------------
    if (typeof win.OffscreenCanvas !== "function") {
        let createCanvas;
        try { createCanvas = require("canvas").createCanvas; } catch (_) {}
        function OffscreenCanvas(width, height) {
            this.width = width;
            this.height = height;
            if (createCanvas) this._cv = createCanvas(width, height);
        }
        OffscreenCanvas.prototype.getContext = function (type) {
            if (this._cv) return this._cv.getContext(type);
            return null;
        };
        OffscreenCanvas.prototype.convertToBlob = function () {
            return Promise.resolve(new (win.Blob || Object)([]));
        };
        OffscreenCanvas.prototype.transferToImageBitmap = function () {
            return { width: this.width, height: this.height, close() {} };
        };
        win.OffscreenCanvas = OffscreenCanvas;
    }

    // -------------------------------------------------------------------
    // WebGL — jsdom returns null for getContext('webgl'). Provide a stub
    // that returns safe defaults so the bundle can fingerprint without crash.
    // -------------------------------------------------------------------
    function makeWebGLStub() {
        const stub = {
            // Common constants the fingerprinter checks
            VENDOR: 0x1F00, RENDERER: 0x1F01, VERSION: 0x1F02, SHADING_LANGUAGE_VERSION: 0x8B8C,
            UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246,
            MAX_TEXTURE_SIZE: 0x0D33, MAX_VERTEX_ATTRIBS: 0x8869,
            MAX_VARYING_VECTORS: 0x8DFC, MAX_COMBINED_TEXTURE_IMAGE_UNITS: 0x8B4D,
            MAX_VERTEX_TEXTURE_IMAGE_UNITS: 0x8B4C, MAX_TEXTURE_IMAGE_UNITS: 0x8872,
            MAX_FRAGMENT_UNIFORM_VECTORS: 0x8DFD, MAX_VERTEX_UNIFORM_VECTORS: 0x8DFB,
            MAX_CUBE_MAP_TEXTURE_SIZE: 0x851C, MAX_RENDERBUFFER_SIZE: 0x84E8,
            MAX_VIEWPORT_DIMS: 0x0D3A, ALIASED_LINE_WIDTH_RANGE: 0x846E,
            ALIASED_POINT_SIZE_RANGE: 0x846D,
            getParameter(p) {
                switch (p) {
                    case 0x1F00: return "WebKit";
                    case 0x1F01: return "WebKit WebGL";
                    case 0x1F02: return "WebGL 1.0 (OpenGL ES 2.0 Chromium)";
                    case 0x8B8C: return "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)";
                    case 0x9245: return "Google Inc. (Intel)";
                    case 0x9246: return "ANGLE (Intel, Intel(R) HD Graphics 530 (0x0000191B) Direct3D11 vs_5_0 ps_5_0, D3D11)";
                    case 0x0D33: return 16384;
                    case 0x8869: return 16;
                    case 0x8DFC: return 30;
                    case 0x8B4D: return 32;
                    case 0x8B4C: return 16;
                    case 0x8872: return 16;
                    case 0x8DFD: return 1024;
                    case 0x8DFB: return 4096;
                    case 0x851C: return 16384;
                    case 0x84E8: return 16384;
                    case 0x0D3A: return new Int32Array([32767, 32767]);
                    case 0x846E: return new Float32Array([1, 1]);
                    case 0x846D: return new Float32Array([1, 1024]);
                    default: return 0;
                }
            },
            getSupportedExtensions() {
                return [
                    "ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_color_buffer_half_float",
                    "EXT_disjoint_timer_query", "EXT_float_blend", "EXT_frag_depth",
                    "EXT_shader_texture_lod", "EXT_texture_compression_bptc",
                    "EXT_texture_compression_rgtc", "EXT_texture_filter_anisotropic",
                    "OES_element_index_uint", "OES_fbo_render_mipmap", "OES_standard_derivatives",
                    "OES_texture_float", "OES_texture_float_linear", "OES_texture_half_float",
                    "OES_texture_half_float_linear", "OES_vertex_array_object",
                    "WEBGL_color_buffer_float", "WEBGL_compressed_texture_s3tc",
                    "WEBGL_compressed_texture_s3tc_srgb", "WEBGL_debug_renderer_info",
                    "WEBGL_debug_shaders", "WEBGL_depth_texture", "WEBGL_draw_buffers",
                    "WEBGL_lose_context",
                ];
            },
            getExtension(name) {
                if (name === "WEBGL_debug_renderer_info") {
                    return { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 };
                }
                if (name === "EXT_texture_filter_anisotropic") {
                    return { MAX_TEXTURE_MAX_ANISOTROPY_EXT: 0x84FF, TEXTURE_MAX_ANISOTROPY_EXT: 0x84FE };
                }
                return null;
            },
            getContextAttributes() {
                return {
                    alpha: true, antialias: true, depth: true, failIfMajorPerformanceCaveat: false,
                    premultipliedAlpha: true, preserveDrawingBuffer: false, stencil: false,
                    powerPreference: "default",
                };
            },
            createShader() { return {}; },
            shaderSource() {}, compileShader() {}, createProgram() { return {}; },
            attachShader() {}, linkProgram() {}, useProgram() {}, deleteShader() {},
            createBuffer() { return {}; }, bindBuffer() {}, bufferData() {}, bufferSubData() {},
            createTexture() { return {}; }, bindTexture() {}, texImage2D() {}, texParameteri() {},
            activeTexture() {}, deleteTexture() {}, deleteBuffer() {}, deleteProgram() {},
            getShaderPrecisionFormat() { return { precision: 23, rangeMin: 127, rangeMax: 127 }; },
            getShaderInfoLog() { return ""; }, getProgramInfoLog() { return ""; },
            getShaderParameter() { return true; }, getProgramParameter() { return true; },
            getAttribLocation() { return 0; }, getUniformLocation() { return {}; },
            enableVertexAttribArray() {}, vertexAttribPointer() {}, uniform1f() {}, uniform2f() {},
            uniform3f() {}, uniform4f() {}, uniform1i() {}, uniform2i() {}, uniform3i() {}, uniform4i() {},
            uniformMatrix4fv() {}, uniformMatrix3fv() {}, uniformMatrix2fv() {},
            drawArrays() {}, drawElements() {}, clear() {}, clearColor() {}, viewport() {},
            enable() {}, disable() {}, blendFunc() {}, depthFunc() {}, cullFace() {},
            readPixels(x, y, w, h, fmt, type, pixels) { /* leave pixels as zeros */ },
            finish() {}, flush() {},
            isContextLost() { return false; },
            DEPTH_TEST: 0x0B71, BLEND: 0x0BE2, CULL_FACE: 0x0B44,
            COLOR_BUFFER_BIT: 0x4000, DEPTH_BUFFER_BIT: 0x0100,
            TRIANGLES: 0x0004, FLOAT: 0x1406, UNSIGNED_BYTE: 0x1401,
            ARRAY_BUFFER: 0x8892, ELEMENT_ARRAY_BUFFER: 0x8893, STATIC_DRAW: 0x88E4,
            TEXTURE_2D: 0x0DE1, TEXTURE0: 0x84C0,
            RGBA: 0x1908, COMPILE_STATUS: 0x8B81, LINK_STATUS: 0x8B82,
            VERTEX_SHADER: 0x8B31, FRAGMENT_SHADER: 0x8B30,
            HIGH_FLOAT: 0x8DF2, MEDIUM_FLOAT: 0x8DF1, LOW_FLOAT: 0x8DF0,
            HIGH_INT: 0x8DF5, MEDIUM_INT: 0x8DF4, LOW_INT: 0x8DF3,
        };
        return stub;
    }

    // Patch getContext to return the stub for webgl / webgl2 (we keep
    // the existing canvas-backed 2d context).
    const origGetContext = win.HTMLCanvasElement.prototype.getContext;
    win.HTMLCanvasElement.prototype.getContext = function (type, attrs) {
        if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") {
            if (!this.__webglStub) this.__webglStub = makeWebGLStub();
            return this.__webglStub;
        }
        return origGetContext.call(this, type, attrs);
    };

// -------------------------------------------------------------------
    // window.matchMedia (jsdom lacks it entirely; hsw probes ~34 CSS
    // media features plus a few device queries). Truth table mirrors the
    // pinned headless-Chrome capture.
    // -------------------------------------------------------------------
    if (typeof win.matchMedia !== "function") {
        const MEDIA_TRUTH = new Set([
            "monochrome:0", "color-gamut:srgb", "any-hover:hover", "hover:hover",
            "any-pointer:fine", "pointer:fine", "display-mode:browser",
            "forced-colors:none", "prefers-color-scheme:light",
            "prefers-contrast:no-preference", "prefers-reduced-motion:no-preference",
            "prefers-reduced-transparency:no-preference",
        ]);
        function mediaMatches(query) {
            let q = String(query || "").trim();
            let negate = false;
            if (q.startsWith("not ")) { negate = true; q = q.slice(4).trim(); }
            const parts = q.split(/ and /).map(function (p) { return p.trim(); }).filter(Boolean);
            let ok = parts.length > 0;
            for (const part of parts) {
                const m = /^\((.+)\)$/.exec(part);
                const inner = m ? m[1].trim() : part;
                const kv = /^([a-zA-Z-]+)\s*:\s*(.+)$/.exec(inner);
                if (!kv) { ok = false; break; }
                const feat = kv[1].toLowerCase();
                const val = kv[2].trim();
                if (feat === "device-width") { if (val !== String((win.screen && win.screen.width) || 0) + "px") ok = false; }
                else if (feat === "device-height") { if (val !== String((win.screen && win.screen.height) || 0) + "px") ok = false; }
                else if (feat === "-webkit-device-pixel-ratio") { if (parseFloat(val) !== (win.devicePixelRatio || 1)) ok = false; }
                else if (feat === "resolution") { if (parseFloat(val) !== (win.devicePixelRatio || 1)) ok = false; }
                else if (feat === "-moz-device-pixel-ratio") { ok = false; }
                else if (!MEDIA_TRUTH.has(feat + ":" + val)) ok = false;
                if (!ok) break;
            }
            return negate ? !ok : ok;
        }
        win.matchMedia = function (q) {
            const matches = mediaMatches(q);
            return {
                media: q, matches, onchange: null,
                addListener() {}, removeListener() {},
                addEventListener() {}, removeEventListener() {},
                dispatchEvent() { return false; },
            };
        };
    }

    // -------------------------------------------------------------------
    // window.indexedDB — present in jsdom but make sure
    // -------------------------------------------------------------------
    if (typeof win.indexedDB === "undefined") {
        win.indexedDB = {
            open() {
                const req = {
                    result: null, error: null,
                    onsuccess: null, onerror: null, onupgradeneeded: null,
                    addEventListener() {}, removeEventListener() {},
                };
                setTimeout(function () {
                    if (typeof req.onsuccess === "function") req.onsuccess({ target: req });
                }, 0);
                return req;
            },
            deleteDatabase() { return {}; },
            databases() { return Promise.resolve([]); },
        };
    }

    // -------------------------------------------------------------------
    // requestIdleCallback (jsdom doesn't have it)
    // -------------------------------------------------------------------
    if (typeof win.requestIdleCallback !== "function") {
        win.requestIdleCallback = function (cb) {
            return setTimeout(function () {
                cb({ didTimeout: false, timeRemaining: function () { return 50; } });
            }, 1);
        };
        win.cancelIdleCallback = function (id) { clearTimeout(id); };
    }

    // -------------------------------------------------------------------
    // chrome.* (lots of fingerprinters look for window.chrome)
    // -------------------------------------------------------------------
    if (typeof win.chrome === "undefined") {
        win.chrome = {
            loadTimes: function () { return {}; },
            csi: function () { return {}; },
            app: { isInstalled: false, InstallState: { DISABLED: "disabled" }, RunningState: { RUNNING: "running" } },
        };
    }

    // -------------------------------------------------------------------
    // window.CSS (jsdom lacks it; aJG-gated fingerprint paths probe
    // CSS.supports). Match Chrome 146 behaviour: any well-formed
    // "property: value" declaration is supported.
    // -------------------------------------------------------------------
    if (typeof win.CSS === "undefined") {
        win.CSS = {
            escape(s) {
                return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) { return "\\" + c; });
            },
            supports(cond) {
                if (typeof cond !== "string") return false;
                const m = /^\s*([a-zA-Z-]+)\s*:/.exec(cond);
                return !!m && !/[(){}]/.test(cond);
            },
        };
    }

    // -------------------------------------------------------------------
    // Notifications
    // -------------------------------------------------------------------
    if (typeof win.Notification === "undefined") {
        function Notification() {}
        Notification.permission = "default";
        Notification.requestPermission = function () { return Promise.resolve("default"); };
        win.Notification = Notification;
    }

    // -------------------------------------------------------------------
    // BroadcastChannel
    // -------------------------------------------------------------------
    if (typeof win.BroadcastChannel === "undefined") {
        function BroadcastChannel(name) { this.name = name; }
        BroadcastChannel.prototype.postMessage = function () {};
        BroadcastChannel.prototype.close = function () {};
        BroadcastChannel.prototype.addEventListener = function () {};
        BroadcastChannel.prototype.removeEventListener = function () {};
        win.BroadcastChannel = BroadcastChannel;
    }

    // -------------------------------------------------------------------
    // navigator.serviceWorker
    // -------------------------------------------------------------------
    if (nav && nav.serviceWorker == null) {
        try {
            Object.defineProperty(nav, "serviceWorker", {
                value: {
                    controller: null, ready: new Promise(function () {}),
                    register() { return Promise.resolve({}); },
                    getRegistration() { return Promise.resolve(undefined); },
                    getRegistrations() { return Promise.resolve([]); },
                    addEventListener() {}, removeEventListener() {},
                },
                configurable: true,
            });
        } catch (_) {}
    }

    // -------------------------------------------------------------------
    // window.screen extras
    // -------------------------------------------------------------------
    const scr = win.screen;
    if (scr) {
        try {
            if (scr.availWidth == null) Object.defineProperty(scr, "availWidth", { value: 1920, configurable: true });
            if (scr.availHeight == null) Object.defineProperty(scr, "availHeight", { value: 1040, configurable: true });
            if (scr.colorDepth == null) Object.defineProperty(scr, "colorDepth", { value: 24, configurable: true });
            if (scr.pixelDepth == null) Object.defineProperty(scr, "pixelDepth", { value: 24, configurable: true });
            if (scr.availLeft == null) Object.defineProperty(scr, "availLeft", { value: 0, configurable: true });
            if (scr.availTop == null) Object.defineProperty(scr, "availTop", { value: 0, configurable: true });
        } catch (_) {}
    }

// -------------------------------------------------------------------
    // document.referrer stays '' (about:blank navigation) so hsw's
    // aDR(document.referrer) returns null and that collector payload is
    // omitted, matching headless Chrome.
    // -------------------------------------------------------------------

    // -------------------------------------------------------------------
    // window.outerWidth / outerHeight / inner sizes
    // -------------------------------------------------------------------
    try {
        if (!win.outerWidth) Object.defineProperty(win, "outerWidth", { value: 1920, configurable: true });
        if (!win.outerHeight) Object.defineProperty(win, "outerHeight", { value: 1040, configurable: true });
        if (!win.innerWidth) Object.defineProperty(win, "innerWidth", { value: 1920, configurable: true });
        if (!win.innerHeight) Object.defineProperty(win, "innerHeight", { value: 947, configurable: true });
        if (!win.devicePixelRatio) Object.defineProperty(win, "devicePixelRatio", { value: 1, configurable: true });
    } catch (_) {}

// -------------------------------------------------------------------
    // Media capability probes — hsw's media-fingerprint collector calls
    // canPlayType / MediaSource.isTypeSupported / MediaRecorder.isTypeSupported.
    // -------------------------------------------------------------------
    const MEDIA_PLAY = {
        'audio/ogg; codecs="vorbis"': "probably",
        "audio/mpeg": "probably",
        "audio/mpegurl": "maybe",
        'audio/wav; codecs="1"': "probably",
        "audio/x-m4a": "maybe",
        "audio/aac": "probably",
        'video/ogg; codecs="theora"': "",
        "video/quicktime": "",
        'video/mp4; codecs="avc1.42E01E"': "probably",
        'video/webm; codecs="vp8"': "probably",
        'video/webm; codecs="vp9"': "probably",
        "video/x-matroska": "maybe",
    };
    const MS_SUPPORT = new Set(["audio/mpeg", "audio/aac", 'video/mp4; codecs="avc1.42E01E"', 'video/webm; codecs="vp8"', 'video/webm; codecs="vp9"']);
    const MR_SUPPORT = new Set(['video/mp4; codecs="avc1.42E01E"', 'video/webm; codecs="vp8"', 'video/webm; codecs="vp9"', "video/x-matroska"]);
    try {
        if (win.HTMLMediaElement) {
            win.HTMLMediaElement.prototype.canPlayType = function (type) {
                return Object.prototype.hasOwnProperty.call(MEDIA_PLAY, type) ? MEDIA_PLAY[type] : "";
            };
        }
        if (win.HTMLVideoElement && typeof win.HTMLVideoElement.prototype.getVideoPlaybackQuality !== "function") {
            win.HTMLVideoElement.prototype.getVideoPlaybackQuality = function () {
                return { creationTime: 0, totalVideoFrames: 0, droppedVideoFrames: 0, corruptedVideoFrames: 0 };
            };
        }
    } catch (_) {}
    if (!("MediaSource" in win)) {
        win.MediaSource = function MediaSource() {};
        win.MediaSource.isTypeSupported = function (type) { return MS_SUPPORT.has(type); };
    }
    if (!("MediaRecorder" in win)) {
        win.MediaRecorder = function MediaRecorder() {};
        win.MediaRecorder.isTypeSupported = function (type) { return MR_SUPPORT.has(type); };
    }

    // -------------------------------------------------------------------
    // Window-global feature stubs — match Chrome 146 truth for the aJG
    // boolean vector and the 821166329 window-walk.
    // -------------------------------------------------------------------
    if (!("SharedWorker" in win)) win.SharedWorker = function SharedWorker() {};
    if (!("VisualViewport" in win)) win.VisualViewport = function VisualViewport() {};
    if (!("ReportingObserver" in win)) win.ReportingObserver = function ReportingObserver() {};
    if (!("RTCRtpTransceiver" in win)) win.RTCRtpTransceiver = function RTCRtpTransceiver() {};
    // Hide randomUUID from Crypto.prototype (Chrome 146 F) without breaking webcrypto
    try { win.Crypto = function Crypto() {}; } catch (_) {}
    // jsdom exposes ontouchstart on window — desktop Chrome F
    try { delete win.ontouchstart; } catch (_) {}
    try { if ("clientInformation" in win === false) Object.defineProperty(win, "clientInformation", { value: win.navigator, configurable: true }); } catch (_) {}
    try {
        const _prompt = win.prompt;
        Object.defineProperty(win, "prompt", { value: function prompt() { return typeof _prompt === "function" ? _prompt.apply(this, arguments) : undefined; }, configurable: true });
        Object.defineProperty(win.prompt, "toString", { value: function () { return "function prompt() { [native code] }"; }, configurable: true });
    } catch (_) {}
    try {
        const _close = win.close;
        Object.defineProperty(win, "close", { value: function close() { return typeof _close === "function" ? _close.apply(this, arguments) : undefined; }, configurable: true });
        Object.defineProperty(win.close, "toString", { value: function () { return "function close() { [native code] }"; }, configurable: true });
    } catch (_) {}

    // -------------------------------------------------------------------
    // hsw's 2435948857 collector probes dozens of APIs via
    // Object.getOwnPropertyDescriptor(Ctor.prototype, name) + a
    // "function x() { [native code] }" toString pattern check. Chrome
    // reports zero tampered entries; jsdom's JS-implemented accessors /
    // methods trip it. Normalize:
    //   1) move Navigator/Screen prototype accessors onto the instance
    //      (Chrome keeps them off the prototype), and
    //   2) wrap canvas helpers as concise methods (no .prototype) with a
    //      native-looking toString whitelist.
    // -------------------------------------------------------------------
    try {
        const moveToInstance = (proto, inst, props) => {
            if (!proto || !inst) return;
            for (const p of props) {
                try {
                    if (!Object.getOwnPropertyDescriptor(proto, p)) continue;
                    const d = Object.getOwnPropertyDescriptor(proto, p);
                    if (!Object.getOwnPropertyDescriptor(inst, p)) {
                        Object.defineProperty(inst, p, d);
                    }
                    delete proto[p];
                } catch (_) {}
            }
        };
        moveToInstance(win.Navigator && win.Navigator.prototype, win.navigator, [
            "userAgent", "language", "languages", "platform", "vendor", "webdriver",
            "deviceMemory", "hardwareConcurrency", "maxTouchPoints", "pdfViewerEnabled",
            "appVersion", "oscpu", "connection", "mimeTypes", "plugins", "keyboard",
            "mediaDevices", "storage", "permissions", "credentials", "serial",
        ]);
        moveToInstance(win.Screen && win.Screen.prototype, win.screen, [
            "width", "height", "availWidth", "availHeight", "colorDepth", "pixelDepth",
        ]);
        const nativeToString = (f, name) => {
            try {
                if (typeof f === "function" && !Object.prototype.hasOwnProperty.call(f, "toString")) {
                    Object.defineProperty(f, "toString", {
                        value: () => "function " + (name || f.name || "") + "() { [native code] }",
                        configurable: true,
                    });
                }
            } catch (_) {}
        };
        const wrapNoProto = (proto, p) => {
            if (!proto) return;
            try {
                const d = Object.getOwnPropertyDescriptor(proto, p);
                if (!d || typeof d.value !== "function") return;
                if (String(d.value).indexOf("[native code]") !== -1) return;
                const fn = d.value;
                const wrapped = { [p](...args) { return fn.apply(this, args); } }[p];
                nativeToString(wrapped, p);
                Object.defineProperty(proto, p, { value: wrapped, writable: true, configurable: true });
            } catch (_) {}
        };
        wrapNoProto(win.HTMLCanvasElement && win.HTMLCanvasElement.prototype, "getContext");
        wrapNoProto(win.HTMLCanvasElement && win.HTMLCanvasElement.prototype, "toDataURL");
        wrapNoProto(win.HTMLCanvasElement && win.HTMLCanvasElement.prototype, "getImageData");
        nativeToString(win.Element && win.Element.prototype && win.Element.prototype.getClientRects, "getClientRects");
        nativeToString(win.TextEncoder && win.TextEncoder.prototype && win.TextEncoder.prototype.encode, "encode");
        nativeToString(win.TextDecoder && win.TextDecoder.prototype && win.TextDecoder.prototype.decode, "decode");
        nativeToString(win.Performance && win.Performance.prototype && win.Performance.prototype.now, "now");
    } catch (_) {}

    // -------------------------------------------------------------------
    // visualViewport
    // -------------------------------------------------------------------
    if (!win.visualViewport) {
        win.visualViewport = {
            width: win.innerWidth || 1920, height: win.innerHeight || 947,
            offsetLeft: 0, offsetTop: 0, pageLeft: 0, pageTop: 0,
            scale: 1, onresize: null, onscroll: null,
            addEventListener() {}, removeEventListener() {},
        };
    }

    return win;
}

module.exports = { install };

