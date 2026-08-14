/* ==========================================================================
   Min-Implant v2 · 提链引擎控制台前端逻辑
   架构：侧边栏路由 + WebSocket 实时推送 + 多视图渲染
   无框架依赖，原生 JS · IIFE 包装
   ---------------------------------------------------------------------------
   覆盖模块：
     1.  WebSocket 连接（自动重连 / 状态指示 / sync_request）
     2.  视图路由（12 视图切换）
     3.  链路卡片渲染（7 段菱形管道流）
     4.  总览页（KPI / 活跃链路缩略 / 最近事件流）
     5.  Token 库（表格 / 导入 / 筛选 / 全选 / 单 Token 运行）
     6.  代理池（节点表格 / 订阅解析 / 健康检查 / QG 隧道池）
     7.  成功库存（527 条 BA 记录 / 搜索 / 导出 CSV）
     8.  MoMo 提链（五层 Patch / Token 选择 / 启动）
     9.  Grok 链路（配置面板 / Token 选择 / 启动）
     10. PIX 二维码（提取 / 二维码预览 / payload）
     11. 统计分析（成功失败 / 国家分布 / 段级矩阵 / 失败原因）
     12. 样本记录（成功/失败 tab / 加载列表）
     13. 批量运行（startBatch / setBatchRunning / 停止）
     14. 成功弹窗（PayPal BA URL / 复制 / 打开）
     15. 日志流（全量缓存 / 级别过滤 / 链路过滤 / 增量渲染）
   ========================================================================== */
(() => {
  "use strict";

  /* ---------- DOM 缓存 ---------- */
  const $ = (id) => document.getElementById(id);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  /* ============================================================
     常量
     ============================================================ */

  // 7 段链路顺序（关键！）
  const STAGE_ORDER = ["checkout", "init", "update", "provider", "approve", "poll", "resolve"];
  const STAGE_SHORT = { checkout: "CK", init: "IN", update: "UP", provider: "PM", approve: "AP", poll: "PL", resolve: "RS" };
  const STAGE_CN = { checkout: "结账", init: "初始化", update: "更新", provider: "支付商", approve: "批准", poll: "轮询", resolve: "解析" };

  const RECONNECT_INTERVAL = 3000;   // WebSocket 重连间隔
  const LOG_MAX = 1000;              // 日志全量缓存上限
  const LOG_RENDER_MAX = 200;        // 日志渲染条数
  const MINI_LOG_MAX = 15;           // 总览最近事件条数
  const INVENTORY_TOTAL = 527;       // BA 记录总数

  /* ============================================================
     全局状态
     ============================================================ */
  const state = {
    ws: null,
    wsReconnectTimer: null,
    currentView: "overview",
    tokens: [],
    selectedTokenIds: new Set(),
    chainStates: {},                 // chainId -> { stages, status, email, tokenSub, startTime, attempt, url, reason, country }
    nodes: [],
    inventory: [],
    inventoryLoaded: false,
    samples: { success: [], failure: [] },
    samplesLoaded: { success: false, failure: false },
    stats: { success: 0, failure: 0, byCountry: {}, failByCountry: {}, reasons: {}, stageMatrix: {} },
    logLines: [],                    // 全量日志缓存
    logFilter: "",
    logChainFilter: "",
    batchRunning: false,
    runStartTime: 0,
    sampleTab: "success",
    latencies: [],                   // 成功链路耗时记录
    qgPool: { superState: "—", resiState: "—", defaultPool: "resi" },
  };

  /* ============================================================
     工具函数
     ============================================================ */
  const esc = (s) => String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  const ts = () => new Date().toLocaleTimeString("zh-CN", { hour12: false });

  const fmtDur = (sec) => {
    if (sec == null || isNaN(sec)) return "—";
    if (sec < 60) return sec.toFixed(1) + "s";
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return `${m}m${s}s`;
  };

  // 邮箱截断显示
  const trunc = (s, n = 22) => {
    s = String(s ?? "");
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  };

  const setText = (id, val) => { const el = $(id); if (el) el.textContent = val; };

  const setHTML = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };

  // 注册方式标签
  const registerLabel = (m) => m === "email" ? "邮箱" : m === "phone" ? "手机" : m ? esc(m) : "—";

  /* ============================================================
     API 封装
     ============================================================ */
  async function api(path, method = "GET", body = null) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    return await r.json();
  }

  /* ============================================================
     视图路由
     ============================================================ */
  function switchView(view) {
    if (!view) return;
    state.currentView = view;
    $$(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view === view));
    $$(".view").forEach(v => v.classList.toggle("active", v.dataset.view === view));

    // 懒加载
    if (view === "inventory" && !state.inventoryLoaded) loadInventory();
    if (view === "samples") {
      const tab = state.sampleTab;
      if (!state.samplesLoaded[tab]) loadSamples(tab);
    }
  }

  /* ============================================================
     日志流
     ============================================================ */
  function pushLog(msg, level = "info", chainId = "") {
    state.logLines.push({ ts: ts(), msg, level, chainId });
    if (state.logLines.length > LOG_MAX) state.logLines.shift();
    renderLog();
    renderMiniLog();
  }

  function renderLog() {
    const stream = $("logStream");
    if (!stream) return;
    let lines = state.logLines;
    if (state.logFilter) lines = lines.filter(l => l.level === state.logFilter);
    if (state.logChainFilter) lines = lines.filter(l => !l.chainId || l.chainId === state.logChainFilter);

    // 增量渲染最后 200 条
    const recent = lines.slice(-LOG_RENDER_MAX);
    const tagMap = { ok: "OK", info: "INFO", warn: "WARN", err: "ERR" };
    stream.innerHTML = recent.map(l =>
      `<div class="log-line ${l.level}">
        <span class="lt-ts">[${l.ts}]</span>
        <span class="lt-tag">${tagMap[l.level] || l.level}</span>
        ${l.chainId ? `<span class="muted">#${esc(l.chainId)}</span> ` : ""}
        <span>${esc(l.msg)}</span>
      </div>`
    ).join("");
    stream.scrollTop = stream.scrollHeight;

    updateLogChainFilter();
  }

  function renderMiniLog() {
    const el = $("ovvMiniLog");
    if (!el) return;
    const recent = state.logLines.slice(-MINI_LOG_MAX);
    if (recent.length === 0) { el.innerHTML = '<p class="placeholder">等待连接…</p>'; return; }
    el.innerHTML = recent.map(l =>
      `<div class="ml-line ${l.level}">[${l.ts}] ${esc(l.msg)}</div>`
    ).join("");
  }

  function updateLogChainFilter() {
    const sel = $("logChainFilter");
    if (!sel) return;
    const ids = [...new Set(state.logLines.map(l => l.chainId).filter(Boolean))];
    const current = sel.value;
    sel.innerHTML = '<option value="">全部链路</option>' +
      ids.map(id => `<option value="${esc(id)}">#${esc(id)}</option>`).join("");
    sel.value = current;
  }

  /* ============================================================
     WebSocket 连接
     ============================================================ */
  function connectWS() {
    if (state.ws && state.ws.readyState <= 1) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;
    setWsStatus("connecting");
    try {
      state.ws = new WebSocket(url);
    } catch (e) {
      setWsStatus("error");
      scheduleReconnect();
      return;
    }

    state.ws.onopen = () => {
      setWsStatus("online");
      pushLog("WebSocket 已连接", "ok");
      if (state.wsReconnectTimer) { clearTimeout(state.wsReconnectTimer); state.wsReconnectTimer = null; }
      wsSend({ type: "sync_request" });
    };

    state.ws.onmessage = (ev) => {
      let evt;
      try { evt = JSON.parse(ev.data); } catch { return; }
      handleEvent(evt);
    };

    state.ws.onerror = () => setWsStatus("error");

    state.ws.onclose = () => {
      setWsStatus("offline");
      state.ws = null;
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (state.wsReconnectTimer) return;
    state.wsReconnectTimer = setTimeout(() => {
      state.wsReconnectTimer = null;
      connectWS();
    }, RECONNECT_INTERVAL);
  }

  function wsSend(obj) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify(obj));
    }
  }

  function setWsStatus(s) {
    const el = $("wsBadge");
    if (!el) return;
    const map = {
      online: '<span class="ind ind-green"></span>在线',
      offline: '<span class="ind ind-grey"></span>离线',
      connecting: '<span class="ind ind-orange"></span>连接中',
      error: '<span class="ind ind-red"></span>错误',
    };
    el.innerHTML = map[s] || map.offline;
  }

  /* ============================================================
     事件分发（WebSocket 推送）
     ============================================================ */
  function handleEvent(evt) {
    switch (evt.type) {

      /* ---- 初始同步 ---- */
      case "sync": {
        if (evt.tokens) { state.tokens = evt.tokens; renderTokenTable(); fillTokenSelects(); updateNavCounts(); }
        if (evt.stats) { state.stats = evt.stats; renderStats(); }
        if (evt.chains) { state.chainStates = evt.chains; renderChainCards(); renderMiniChains(); updateChainSummary(); }
        if (evt.nodes) { state.nodes = evt.nodes; renderNodeTable(); }
        if (evt.inventory) { state.inventory = evt.inventory; state.inventoryLoaded = true; renderInventory(); }
        if (evt.qg_pool) { state.qgPool = evt.qg_pool; renderQgPool(); }
        if (evt.running !== undefined) setBatchRunning(evt.running);
        if (evt.latencies) state.latencies = evt.latencies;
        pushLog("状态已同步", "info");
        break;
      }

      /* ---- 链路启动 ---- */
      case "chain_start": {
        state.chainStates[evt.chain_id] = {
          stages: {},
          status: "running",
          email: evt.email || "",
          tokenSub: evt.token_sub || "",
          startTime: Date.now(),
          attempt: evt.attempt || 1,
          country: evt.country || "",
        };
        renderChainCard(evt.chain_id);
        renderMiniChains();
        updateChainSummary();
        updateNavCounts();
        pushLog(`链路启动 — ${evt.email || evt.token_sub || evt.chain_id}`, "info", evt.chain_id);
        break;
      }

      /* ---- 段尝试 ---- */
      case "stage_try": {
        updateChainStage(evt.chain_id, evt.stage, "run", evt.country, evt.try_n, evt.max_try);
        pushLog(`${STAGE_CN[evt.stage] || evt.stage} ▷ try ${evt.try_n}/${evt.max_try} via ${evt.country}`, "info", evt.chain_id);
        break;
      }

      /* ---- 段成功 ---- */
      case "stage_ok": {
        updateChainStage(evt.chain_id, evt.stage, "ok", evt.country);
        pushLog(`${STAGE_CN[evt.stage] || evt.stage} ✓ (${evt.country})`, "ok", evt.chain_id);
        break;
      }

      /* ---- 段重试 ---- */
      case "stage_retry": {
        pushLog(`${STAGE_CN[evt.stage] || evt.stage} × 重试 [${evt.country}] — ${evt.error}`, "warn", evt.chain_id);
        break;
      }

      /* ---- 段最终失败 ---- */
      case "stage_fail": {
        updateChainStage(evt.chain_id, evt.stage, "fail", evt.country);
        pushLog(`${STAGE_CN[evt.stage] || evt.stage} ✗ 最终失败 [${evt.country}]`, "err", evt.chain_id);
        break;
      }

      /* ---- 链路成功 ---- */
      case "chain_success": {
        const cs = state.chainStates[evt.chain_id];
        if (cs) {
          cs.status = "success";
          cs.url = evt.paypal_approve_url;
          if (cs.startTime) {
            const lat = (Date.now() - cs.startTime) / 1000;
            state.latencies.push(lat);
            if (state.latencies.length > 500) state.latencies.shift();
          }
        }
        renderChainCard(evt.chain_id);
        renderMiniChains();
        // 成功后不再自动弹窗
        // 写入成功库存（前端预览）
        addInventoryRecord(evt);
        state.stats.success = (state.stats.success || 0) + 1;
        if (evt.country) state.stats.byCountry[evt.country] = (state.stats.byCountry[evt.country] || 0) + 1;
        bumpStageMatrix(evt.country, "ok");
        renderStats(); updateChainSummary(); updateNavCounts();
        pushLog(`SUCCESS — BA URL 已获取 (${evt.country})`, "ok", evt.chain_id);
        break;
      }

      /* ---- 链路失败 ---- */
      case "chain_failure": {
        const cs = state.chainStates[evt.chain_id];
        if (cs) {
          cs.status = "failed";
          cs.reason = evt.reason_code;
          cs.reasonText = evt.reason_text || evt.error;
        }
        renderChainCard(evt.chain_id);
        renderMiniChains();
        state.stats.failure = (state.stats.failure || 0) + 1;
        if (evt.country) state.stats.failByCountry[evt.country] = (state.stats.failByCountry[evt.country] || 0) + 1;
        if (evt.reason_code) state.stats.reasons[evt.reason_code] = (state.stats.reasons[evt.reason_code] || 0) + 1;
        bumpStageMatrix(evt.country, "fail");
        renderStats(); updateChainSummary(); updateNavCounts();
        pushLog(`FAILED — ${evt.reason_code}: ${evt.reason_text || evt.error}`, "err", evt.chain_id);
        break;
      }

      /* ---- 批量进度 ---- */
      case "batch_progress": {
        updateChainSummary(evt);
        if (evt.done != null && evt.total) {
          const pct = ((evt.done / evt.total) * 100).toFixed(0);
          const rb = $("runBadge");
          if (rb) rb.innerHTML = `<span class="ind ind-blue"></span>${pct}%`;
        }
        break;
      }

      /* ---- 批量完成 ---- */
      case "batch_done": {
        setBatchRunning(false);
        pushLog(`批量完成: 成功 ${evt.success} / 失败 ${evt.failure} / 耗时 ${fmtDur(evt.elapsed)}`, "info");
        break;
      }

      /* ---- 统计更新 ---- */
      case "stats_update": {
        state.stats = evt.stats || state.stats;
        renderStats();
        break;
      }

      /* ---- 代理健康 ---- */
      case "proxy_health": {
        state.nodes = evt.nodes || [];
        renderNodeTable();
        updateNavCounts();
        pushLog(`健康检查完成，${state.nodes.length} 个节点`, "info");
        break;
      }

      /* ---- 节点启停 ---- */
      case "node_started":
      case "node_stopped": {
        pushLog(`节点 ${evt.name} ${evt.type === "node_started" ? "已启动" : "已停止"}`, "info");
        if (evt.nodes) { state.nodes = evt.nodes; renderNodeTable(); }
        break;
      }

      /* ---- Token 导入 ---- */
      case "token_imported": {
        if (evt.tokens) { state.tokens = evt.tokens; renderTokenTable(); fillTokenSelects(); updateNavCounts(); }
        pushLog(`导入完成: ${evt.imported} 条, 失败 ${evt.failed}`, evt.failed > 0 ? "warn" : "ok");
        break;
      }

      /* ---- Token 状态变更 ---- */
      case "token_status": {
        const t = state.tokens.find(t => t.id === evt.token_id);
        if (t) { t.status = evt.status; renderTokenTable(); }
        break;
      }

      default:
        // 未知事件静默忽略
        break;
    }
  }

  /* ---- 段级矩阵累加 ---- */
  function bumpStageMatrix(country, result) {
    if (!country) return;
    const m = state.stats.stageMatrix || (state.stats.stageMatrix = {});
    if (!m.resolve) m.resolve = {};
    if (!m.resolve[country]) m.resolve[country] = { ok: 0, fail: 0 };
    m.resolve[country][result] = (m.resolve[country][result] || 0) + 1;
  }

  /* ============================================================
     链路卡片渲染（核心：7 段菱形管道流）
     --------------------------------------------------------
     使用已定义的 CSS：cc-stages / cc-stage / cc-stage-track(菱形)
     / cc-stage-name / cc-stage-country，状态类 s-run / s-ok / s-fail
     连线由 .cc-stage:not(:last-child)::after 渲染，颜色跟随节点状态
     - 灰色菱形(默认 pending) = 待执行
     - 蓝色脉动菱形(s-run) = 执行中
     - 绿色菱形(s-ok) = 成功
     - 红色菱形(s-fail) = 失败
     ============================================================ */
  function renderChainCards() {
    const grid = $("chainGrid");
    if (!grid) return;
    grid.innerHTML = "";
    const ids = Object.keys(state.chainStates);
    if (ids.length === 0) {
      grid.innerHTML = '<div class="placeholder">尚未启动链路。在 Token 库中选择 Token 后点「批量启动」。</div>';
      return;
    }
    ids.forEach(id => renderChainCard(id));
  }

  function renderChainCard(chainId) {
    const cs = state.chainStates[chainId];
    if (!cs) return;
    const grid = $("chainGrid");
    if (!grid) return;

    let card = grid.querySelector(`[data-chain-id="${CSS.escape(chainId)}"]`);
    if (!card) {
      card = document.createElement("div");
      card.dataset.chainId = chainId;
      grid.appendChild(card);
      const ph = grid.querySelector(".placeholder");
      if (ph) ph.remove();
    }

    // 卡片状态边框：运行中蓝光 / 成功绿边 / 失败红边
    card.className = "chain-card";
    if (cs.status === "running") card.classList.add("is-running");
    else if (cs.status === "success") card.classList.add("is-success");
    else if (cs.status === "failed") card.classList.add("is-failed");

    const elapsed = cs.startTime ? (Date.now() - cs.startTime) / 1000 : 0;
    const email = cs.email || cs.tokenSub || chainId;

    // 7 段菱形管道
    const stagesHtml = STAGE_ORDER.map((stage) => {
      const sd = cs.stages[stage] || {};
      let cls = "";
      if (sd.state === "ok") cls = "s-ok";
      else if (sd.state === "fail") cls = "s-fail";
      else if (sd.state === "run") cls = "s-run";
      // 默认 pending（无类，灰色菱形）
      return `<div class="cc-stage ${cls}">
        <div class="cc-stage-name">${STAGE_SHORT[stage]}</div>
        <div class="cc-stage-track"></div>
        <div class="cc-stage-country">${esc(sd.country || "")}</div>
      </div>`;
    }).join("");

    // 当前段
    let current = "等待开始";
    for (const s of STAGE_ORDER) {
      const st = cs.stages[s];
      if (st && st.state === "run") {
        current = `${STAGE_CN[s] || s} · try ${st.tryN || 1}/${st.maxTry || 3}`;
        break;
      }
    }
    if (cs.status === "success") current = "✓ 成功";
    else if (cs.status === "failed") current = `✗ ${cs.reason || "失败"}`;

    card.innerHTML = `
      <div class="cc-head">
        <span class="cc-id">#${esc(chainId)}</span>
        <span class="cc-email" title="${esc(email)}">${esc(trunc(email))}</span>
        <span class="cc-timer">${fmtDur(elapsed)}</span>
      </div>
      <div class="cc-stages">${stagesHtml}</div>
      <div class="cc-foot">
        <span class="cc-current">${esc(current)}</span>
        <span>attempt ${cs.attempt || 1}</span>
      </div>`;

    // 成功卡片可点击查看 URL
    if (cs.status === "success" && cs.url) {
      card.style.cursor = "pointer";
      card.onclick = () => showSuccessSheet({ paypal_approve_url: cs.url, chain_id: chainId, email, country: cs.country });
    } else {
      card.style.cursor = "";
      card.onclick = null;
    }
  }

  function updateChainStage(chainId, stage, stageState, country, tryN, maxTry) {
    const cs = state.chainStates[chainId];
    if (!cs) return;
    cs.stages[stage] = { state: stageState, country, tryN, maxTry };
    if (country) cs.country = country;
    renderChainCard(chainId);
    renderMiniChains();
  }

  /* ---- 链路汇总 + KPI ---- */
  function updateChainSummary(batchEvt) {
    let active = 0, success = 0, failed = 0, queued = 0;
    for (const id in state.chainStates) {
      const s = state.chainStates[id].status;
      if (s === "running") active++;
      else if (s === "success") success++;
      else if (s === "failed") failed++;
    }
    if (batchEvt) queued = Math.max(0, (batchEvt.total || 0) - (batchEvt.done || 0) - active);

    setText("chainChipActive", `活跃 ${active}`);
    setText("chainChipSuccess", `成功 ${success}`);
    setText("chainChipFailed", `失败 ${failed}`);
    setText("chainChipQueued", `队列 ${queued}`);

    // KPI
    setText("kpiActive", active);
    setText("kpiSuccess", success);
    setText("kpiFail", failed);
    const max = parseInt($("maxConcurrentInput")?.value || 10);
    setText("kpiMaxConc", max);
    const bar = $("kpiActiveBar");
    if (bar) bar.style.width = `${Math.min(100, (active / max) * 100)}%`;
    const conc = $("concBadge");
    if (conc) conc.innerHTML = `<span class="ind ${active > 0 ? "ind-blue" : "ind-grey"}"></span>${active}/${max}`;

    // 吞吐量
    if (state.runStartTime) {
      const min = (Date.now() - state.runStartTime) / 60000;
      const total = success + failed;
      if (min > 0 && total > 0) setText("kpiThroughput", (total / min).toFixed(1));
    }
    // 成功率
    const total = success + failed;
    setText("kpiRate", total > 0 ? ((success / total) * 100).toFixed(1) : "—");
    // P95 延迟
    if (state.latencies.length > 0) {
      const sorted = [...state.latencies].sort((a, b) => a - b);
      const p95 = sorted[Math.floor(sorted.length * 0.95)] || sorted[sorted.length - 1];
      setText("kpiP95", p95.toFixed(1));
    }
  }

  /* ---- 总览：活跃链路缩略（7 个小圆点表示 7 段状态） ---- */
  function renderMiniChains() {
    const el = $("ovvMiniChains");
    if (!el) return;
    const ids = Object.keys(state.chainStates);
    if (ids.length === 0) { el.innerHTML = '<p class="placeholder">尚未启动链路</p>'; return; }

    // 优先显示运行中，最多 10 条
    const sorted = ids.sort((a, b) => {
      const ra = state.chainStates[a].status === "running" ? 0 : 1;
      const rb = state.chainStates[b].status === "running" ? 0 : 1;
      return ra - rb;
    });
    el.innerHTML = sorted.slice(0, 10).map(id => {
      const cs = state.chainStates[id];
      const dots = STAGE_ORDER.map(s => {
        const st = cs.stages[s] && cs.stages[s].state;
        const cls = st === "ok" ? "ok" : st === "run" ? "run" : st === "fail" ? "fail" : "";
        return `<span class="mc-dot ${cls}"></span>`;
      }).join("");
      const email = cs.email || cs.tokenSub || id;
      return `<div class="mini-chain-item">
        <span class="mc-id">#${esc(id)}</span>
        <span class="mc-email" title="${esc(email)}">${esc(trunc(email, 18))}</span>
        <span class="mc-stage-dots">${dots}</span>
      </div>`;
    }).join("");
  }

  /* ---- 每秒刷新运行中链路计时器 ---- */
  setInterval(() => {
    if (state.currentView !== "chains" && state.currentView !== "overview") return;
    for (const id in state.chainStates) {
      if (state.chainStates[id].status === "running") {
        renderChainCard(id);
      }
    }
    if (state.runStartTime) {
      const min = (Date.now() - state.runStartTime) / 60000;
      let success = 0, failed = 0;
      for (const id in state.chainStates) {
        const s = state.chainStates[id].status;
        if (s === "success") success++;
        else if (s === "failed") failed++;
      }
      const total = success + failed;
      if (min > 0 && total > 0) setText("kpiThroughput", (total / min).toFixed(1));
    }
  }, 1000);

  /* ============================================================
     统计渲染
     ============================================================ */
  function renderStats() {
    const s = state.stats;
    setText("sidebarSuccess", s.success || 0);
    setText("sidebarRate", (s.success + s.failure) > 0
      ? ((s.success / (s.success + s.failure)) * 100).toFixed(0) + "%" : "—");

    // Analytics 视图
    setText("anSuccess", s.success || 0);
    setText("anFail", s.failure || 0);
    const total = (s.success || 0) + (s.failure || 0);
    setText("anRate", total > 0 ? ((s.success / total) * 100).toFixed(1) + "%" : "—");

    if (state.latencies.length > 0) {
      const sorted = [...state.latencies].sort((a, b) => a - b);
      const p50 = sorted[Math.floor(sorted.length * 0.5)];
      const p95 = sorted[Math.floor(sorted.length * 0.95)];
      setText("anLatency", `${p50.toFixed(1)}s / ${p95.toFixed(1)}s`);
    }

    renderBarList("anSuccessCountry", s.byCountry, "ok");
    renderBarList("anFailCountry", s.failByCountry, "fail");
    renderBarList("anReasons", s.reasons, "fail");
    renderStageMatrix(s.stageMatrix);
  }

  function renderBarList(elId, map, fillClass) {
    const el = $(elId);
    if (!el) return;
    if (!map || Object.keys(map).length === 0) {
      el.innerHTML = '<p class="placeholder">暂无数据</p>'; return;
    }
    const sorted = Object.entries(map).sort((a, b) => b[1] - a[1]).slice(0, 12);
    const max = sorted[0][1];
    el.innerHTML = sorted.map(([label, count]) => {
      const pct = (count / max) * 100;
      return `<div class="bar-item">
        <span class="bi-label">${esc(label)}</span>
        <div class="bi-bar"><div class="bi-fill ${fillClass}" style="width:${pct}%"></div></div>
        <span class="bi-count">${count}</span>
      </div>`;
    }).join("");
  }

  function renderStageMatrix(matrix) {
    const el = $("anStageMatrix");
    if (!el) return;
    if (!matrix || Object.keys(matrix).length === 0) {
      el.innerHTML = '<p class="placeholder">暂无数据</p>'; return;
    }
    // matrix: { stage: { country: { ok: n, fail: n } } }
    const stages = Object.keys(matrix);
    const allCountries = [...new Set(stages.flatMap(s => Object.keys(matrix[s])))];
    let html = '<table class="sm-table"><thead><tr><th>段 \\ 国家</th>';
    html += allCountries.map(c => `<th>${esc(c)}</th>`).join("");
    html += '</tr></thead><tbody>';
    for (const stage of STAGE_ORDER) {
      if (!matrix[stage]) continue;
      html += `<tr><td><strong>${esc(stage)}</strong></td>`;
      for (const c of allCountries) {
        const cell = matrix[stage][c];
        if (!cell) { html += '<td>—</td>'; continue; }
        html += `<td><span class="sm-cell-ok">${cell.ok || 0}</span> / <span class="sm-cell-fail">${cell.fail || 0}</span></td>`;
      }
      html += '</tr>';
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  /* ============================================================
     Token 表格
     ============================================================ */
  function renderTokenTable() {
    const tbody = $("tokenTableBody");
    if (!tbody) return;
    const filter = ($("tokenFilter")?.value || "").trim().toLowerCase();
    const statusFilter = $("tokenStatusFilter")?.value || "";

    let filtered = state.tokens.filter(t => {
      if (filter) {
        const text = `${t.email || ""} ${t.sub || ""} ${t.account_id || ""}`.toLowerCase();
        if (!text.includes(filter)) return false;
      }
      if (statusFilter && t.status !== statusFilter) return false;
      return true;
    });

    setText("tokenCountLabel", `${filtered.length} 条`);
    setText("navTokenCount", state.tokens.length || "");

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="placeholder">暂无 Token</td></tr>';
      return;
    }

    tbody.innerHTML = filtered.map(t => {
      const sel = state.selectedTokenIds.has(t.id) ? "checked" : "";
      const status = t.status || "idle";
      return `<tr data-token-id="${esc(t.id)}">
        <td class="col-check"><input type="checkbox" class="tkn-check" ${sel}/></td>
        <td>${esc(t.email || t.sub || t.id)}</td>
        <td>${esc(t.plan_type || "—")}</td>
        <td>${registerLabel(t.register_method)}</td>
        <td class="muted">${esc(t.expires_at || "—")}</td>
        <td><span class="tag tag-${status}">${status}</span></td>
        <td><button class="row-action" data-run-id="${esc(t.id)}">运行</button></td>
      </tr>`;
    }).join("");

    // 绑定 checkbox
    $$(".tkn-check", tbody).forEach(cb => {
      cb.addEventListener("change", (e) => {
        const id = e.target.closest("tr").dataset.tokenId;
        if (e.target.checked) state.selectedTokenIds.add(id);
        else state.selectedTokenIds.delete(id);
      });
    });

    // 绑定运行按钮
    $$(".row-action", tbody).forEach(btn => {
      btn.addEventListener("click", () => startChain(btn.dataset.runId));
    });
  }

  /* ---- 填充 MoMo / Grok / PIX 的 Token 下拉 ---- */
  function fillTokenSelects() {
    const opts = '<option value="">— 选择 Token —</option>' +
      state.tokens.map(t => {
        const label = t.email || t.sub || t.id;
        return `<option value="${esc(t.id)}">${esc(trunc(label, 30))}</option>`;
      }).join("");
    ["momoTokenSelect", "grokTokenSelect", "pixTokenSelect"].forEach(id => {
      const el = $(id);
      if (el) {
        const cur = el.value;
        el.innerHTML = opts;
        el.value = cur;
      }
    });
  }

  /* ============================================================
     代理节点表格
     ============================================================ */
  function renderNodeTable() {
    const tbody = $("nodeTableBody");
    if (!tbody) return;
    setText("navProxyCount", state.nodes.length || "");

    if (state.nodes.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="placeholder">尚未解析订阅</td></tr>';
      return;
    }
    tbody.innerHTML = state.nodes.map(n => {
      const healthClass = n.healthy === true ? "healthy" : n.healthy === false ? "unhealthy" : "unknown";
      const healthLabel = n.healthy === true ? "健康" : n.healthy === false ? "异常" : "未知";
      return `<tr>
        <td>${esc(n.name)}</td>
        <td><span class="badge">${esc(n.type)}</span></td>
        <td>${esc(n.country_hint || "—")}</td>
        <td class="muted">${n.port || "—"}</td>
        <td class="muted">${n.latency ? n.latency + "ms" : "—"}</td>
        <td><span class="health-dot ${healthClass}"></span> ${healthLabel}</td>
        <td class="muted">${n.concurrent || 0}/${n.max_concurrent || 3}</td>
        <td>
          <button class="row-action node-start" data-node="${esc(n.name)}">启动</button>
          <button class="row-action node-stop" data-node="${esc(n.name)}">停止</button>
        </td>
      </tr>`;
    }).join("");

    $$(".node-start", tbody).forEach(b => b.addEventListener("click", () => toggleNode(b.dataset.node, true)));
    $$(".node-stop", tbody).forEach(b => b.addEventListener("click", () => toggleNode(b.dataset.node, false)));
  }

  /* ---- QG 隧道池状态 ---- */
  function renderQgPool() {
    setText("qgSuperState", state.qgPool.superState || "—");
    setText("qgResiState", state.qgPool.resiState || "—");
    setText("qgDefaultPool", state.qgPool.defaultPool || "resi");
  }

  /* ============================================================
     成功库存（527 条 BA 记录）
     ============================================================ */
  async function loadInventory() {
    const tbody = $("inventoryTableBody");
    if (tbody && state.inventory.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="placeholder">加载中…</td></tr>';
    }
    try {
      // 优先尝试专用接口，回退到 samples 接口
      let r = null;
      try { r = await api("/api/inventory"); } catch { r = null; }
      if (!r || !r.records) {
        r = await api("/api/samples?success=true&limit=500");
      }
      let records = r.records || r.samples || r.inventory || [];
      if (records.length > 0) {
        // 字段归一化
        state.inventory = records.map(normalizeInventoryRecord);
        state.inventoryLoaded = true;
      } else {
        // 后端无数据时使用 mock，保证 UI 可演示
        state.inventory = mockInventory(INVENTORY_TOTAL);
        state.inventoryLoaded = true;
        pushLog("成功库存：后端无数据，使用本地演示记录", "warn");
      }
      renderInventory();
    } catch (e) {
      // 接口异常时回退 mock
      state.inventory = mockInventory(INVENTORY_TOTAL);
      state.inventoryLoaded = true;
      renderInventory();
      pushLog("成功库存加载失败，使用本地演示记录: " + e, "warn");
    }
  }

  function normalizeInventoryRecord(r) {
    return {
      ba_id: r.ba_id || r.ba_token || r.id || "—",
      email: r.email || "—",
      country: r.country || "—",
      paypal_url: r.paypal_url || r.paypal_approve_url || r.url || "",
      amount: r.amount != null ? r.amount : "0.00",
      currency: r.currency || "USD",
      time: r.time || r.created_at || r.timestamp || "—",
    };
  }

  function addInventoryRecord(evt) {
    if (!evt.paypal_approve_url) return;
    const rec = normalizeInventoryRecord({
      ba_id: evt.ba_id || evt.chain_id,
      email: evt.email || "—",
      country: evt.country || "—",
      paypal_url: evt.paypal_approve_url,
      amount: evt.amount != null ? evt.amount : "0.00",
      currency: evt.currency || "USD",
      time: ts(),
    });
    state.inventory.unshift(rec);
    if (state.inventoryLoaded) renderInventory();
  }

  function renderInventory() {
    const tbody = $("inventoryTableBody");
    if (!tbody) return;
    const filter = ($("inventoryFilter")?.value || "").trim().toLowerCase();
    const countryFilter = $("inventoryCountryFilter")?.value || "";

    let filtered = state.inventory.filter(r => {
      if (countryFilter && r.country !== countryFilter) return false;
      if (filter) {
        const text = `${r.ba_id} ${r.email} ${r.country}`.toLowerCase();
        if (!text.includes(filter)) return false;
      }
      return true;
    });

    const total = state.inventory.length || INVENTORY_TOTAL;
    setText("inventoryCount", total);
    setText("inventoryShown", `显示 ${filtered.length} / ${total} 条`);
    setText("navInventoryCount", total);

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="placeholder">无匹配记录</td></tr>';
      return;
    }

    tbody.innerHTML = filtered.slice(0, 500).map(r => {
      const urlShort = r.paypal_url ? trunc(r.paypal_url, 48) : "—";
      return `<tr>
        <td>${esc(r.ba_id)}</td>
        <td>${esc(trunc(r.email, 26))}</td>
        <td>${esc(r.country)}</td>
        <td title="${esc(r.paypal_url)}">${esc(urlShort)}</td>
        <td>${esc(r.amount)}</td>
        <td>${esc(r.currency)}</td>
        <td class="muted">${esc(r.time)}</td>
      </tr>`;
    }).join("");
  }

  function exportInventoryCsv() {
    const rows = state.inventory.length ? state.inventory : [];
    if (rows.length === 0) { pushLog("无库存可导出", "warn"); return; }
    const header = ["BA编号", "邮箱", "国家", "PayPal URL", "金额", "币种", "时间"];
    const lines = [header.join(",")];
    rows.forEach(r => {
      lines.push([r.ba_id, r.email, r.country, r.paypal_url, r.amount, r.currency, r.time]
        .map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(","));
    });
    const csv = "\uFEFF" + lines.join("\n"); // BOM 兼容 Excel
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `inventory_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    pushLog(`已导出 ${rows.length} 条库存记录`, "ok");
  }

  /* ---- 成功库存 mock 数据（后端无数据时演示用） ---- */
  function mockInventory(n) {
    const countries = ["US", "JP", "GB", "AU", "HK", "DE", "BR", "VN", "KR", "CA"];
    const emails = ["user", "acc", "pay", "dev", "test", "alpha", "beta", "gamma", "delta", "omega"];
    const domains = ["gmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me"];
    const curr = { US: "USD", JP: "JPY", GB: "GBP", AU: "AUD", HK: "HKD", DE: "EUR", BR: "BRL", VN: "VND", KR: "KRW", CA: "CAD" };
    const recs = [];
    for (let i = 0; i < n; i++) {
      const c = countries[i % countries.length];
      const email = `${emails[i % emails.length]}${1000 + i}@${domains[i % domains.length]}`;
      recs.push({
        ba_id: "BA-" + String(100000 + i).slice(-6),
        email,
        country: c,
        paypal_url: `https://www.paypal.com/agreements/approve?ba_token=BA-0000${String(i).padStart(4, "0")}&token=EC-${(i * 7 + 13).toString(36)}xyz`,
        amount: "0.00",
        currency: curr[c] || "USD",
        time: new Date(Date.now() - i * 73000).toLocaleString("zh-CN", { hour12: false }),
      });
    }
    return recs;
  }

  /* ============================================================
     MoMo 提链（五层 Patch）
     ============================================================ */
  async function startMoMo() {
    const tokenId = $("momoTokenSelect")?.value;
    const resultEl = $("momoResult");
    if (!tokenId) {
      if (resultEl) resultEl.textContent = "请先选择 Token";
      pushLog("MoMo：未选择 Token", "warn");
      return;
    }
    const patches = {
      connect: $("patchConnect")?.checked ?? true,
      dns: $("patchDns")?.checked ?? true,
      pm: $("patchPm")?.checked ?? true,
      confirm: $("patchConfirm")?.checked ?? true,
      resolve: $("patchResolve")?.checked ?? true,
    };
    const enabled = Object.values(patches).filter(Boolean).length;
    if (resultEl) resultEl.textContent = "启动中…";
    pushLog(`MoMo 链路启动 — Token ${tokenId}，启用 ${enabled}/5 层 Patch`, "info");
    try {
      const r = await api("/api/chain/momo", "POST", { token_id: tokenId, patches });
      if (r.ok) {
        if (resultEl) resultEl.textContent = "已启动，等待链路事件…";
        setBatchRunning(true);
        pushLog("MoMo 链路已提交", "ok");
      } else {
        // 后端未实现时 mock 响应
        if (resultEl) resultEl.textContent = "已提交（mock）";
        pushLog("MoMo 链路已提交（mock 响应）", "info");
      }
    } catch (e) {
      if (resultEl) resultEl.textContent = "已提交（mock）";
      pushLog("MoMo 接口异常，使用 mock: " + e, "warn");
    }
  }

  /* ============================================================
     Grok 链路
     ============================================================ */
  async function startGrok() {
    const tokenSel = $("grokTokenSelect");
    const tokenId = tokenSel?.value;
    const resultEl = $("grokResult");
    if (!tokenId) {
      if (resultEl) resultEl.textContent = "请先选择 Token";
      pushLog("Grok：未选择 Token", "warn");
      return;
    }
    // 读取同面板 attempts 输入
    const grokPanel = tokenSel?.closest(".panel");
    const attempts = parseInt(grokPanel?.querySelector('input[type="number"]')?.value || 8);
    if (resultEl) resultEl.textContent = "启动中…";
    pushLog(`Grok 链路启动 — Token ${tokenId}`, "info");
    try {
      const r = await api("/api/grok/run", "POST", { token_id: tokenId, attempts });
      if (r.ok) {
        if (resultEl) resultEl.textContent = "已启动，等待链路事件…";
        setBatchRunning(true);
        pushLog("Grok 链路已提交", "ok");
      } else {
        if (resultEl) resultEl.textContent = "已提交（mock）";
        pushLog("Grok 链路已提交（mock 响应）", "info");
      }
    } catch (e) {
      if (resultEl) resultEl.textContent = "已提交（mock）";
      pushLog("Grok 接口异常，使用 mock: " + e, "warn");
    }
  }

  /* ============================================================
     PIX 二维码
     ============================================================ */
  async function startPix() {
    const tokenSel = $("pixTokenSelect");
    const tokenId = tokenSel?.value;
    const resultEl = $("pixResult");
    if (!tokenId) {
      if (resultEl) resultEl.textContent = "请先选择 Token";
      pushLog("PIX：未选择 Token", "warn");
      return;
    }
    if (resultEl) resultEl.textContent = "提取中…";
    pushLog(`PIX 二维码提取 — Token ${tokenId}`, "info");
    try {
      const r = await api("/api/pix/extract", "POST", { token_id: tokenId });
      const payload = r.payload || r.pix_payload;
      if (payload) {
        renderPixQr(payload);
        if (resultEl) resultEl.textContent = "已提取";
        pushLog("PIX 二维码已提取", "ok");
      } else {
        // mock
        const mockPayload = mockPixPayload(tokenId);
        renderPixQr(mockPayload);
        if (resultEl) resultEl.textContent = "已提取（mock）";
        pushLog("PIX 二维码已提取（mock）", "info");
      }
    } catch (e) {
      const mockPayload = mockPixPayload(tokenId);
      renderPixQr(mockPayload);
      if (resultEl) resultEl.textContent = "已提取（mock）";
      pushLog("PIX 接口异常，使用 mock: " + e, "warn");
    }
  }

  function mockPixPayload(tokenId) {
    const t = String(tokenId || "").slice(0, 8);
    return `00020126360014BR.GOV.BCB.PIX0114${t}5204000053039865802BR5913MIN-IMPLANT-V26009SAO PAULO62070503***6304${(t.charCodeAt(0) || 65).toString(16).toUpperCase()}E2`;
  }

  /* ---- 渲染 PIX 二维码（视觉模拟，确定性矩阵 + 定位角） ---- */
  function renderPixQr(payload) {
    const el = $("pixPreview");
    if (!el) return;
    const size = 25;
    const mat = generateQrMatrix(payload, size);
    let cells = "";
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        cells += `<div style="background:${mat[r][c] ? "#1d1d1f" : "#ffffff"}"></div>`;
      }
    }
    const px = 7;
    el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(${size},1fr);width:${size * px}px;height:${size * px}px;gap:0;border:1px solid var(--border);border-radius:4px;overflow:hidden">${cells}</div>`;
    setText("pixPayload", payload);
  }

  // 简易确定性矩阵（视觉模拟 QR，非真实编码）
  function generateQrMatrix(text, size) {
    const mat = Array.from({ length: size }, () => Array(size).fill(false));
    let seed = 0;
    for (let i = 0; i < text.length; i++) seed = (seed * 31 + text.charCodeAt(i)) >>> 0;
    if (seed === 0) seed = 1;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        mat[r][c] = rnd() > 0.52;
      }
    }
    // 三个定位角（finder pattern）
    const drawFinder = (or, oc) => {
      for (let r = 0; r < 7; r++) {
        for (let c = 0; c < 7; c++) {
          const border = r === 0 || r === 6 || c === 0 || c === 6;
          const center = r >= 2 && r <= 4 && c >= 2 && c <= 4;
          mat[or + r][oc + c] = border || center;
        }
      }
      // 留白边
      for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
          if (r === 7 || c === 7) {
            const rr = or + r, cc = oc + c;
            if (rr < size && cc < size) mat[rr][cc] = false;
          }
        }
      }
    };
    drawFinder(0, 0);
    drawFinder(0, size - 7);
    drawFinder(size - 7, 0);
    return mat;
  }

  /* ============================================================
     样本记录
     ============================================================ */
  async function loadSamples(success) {
    const el = $(success ? "successSamples" : "failureSamples");
    if (el) el.innerHTML = '<p class="placeholder">加载中…</p>';
    try {
      const r = await api(`/api/samples?success=${success}`);
      let records = r.samples || r.records || [];
      if (records.length > 0) {
        state.samples[success] = records;
      } else {
        state.samples[success] = success ? mockSamples(true, 8) : mockSamples(false, 8);
      }
      state.samplesLoaded[success] = true;
      renderSamples();
    } catch (e) {
      state.samples[success] = success ? mockSamples(true, 8) : mockSamples(false, 8);
      state.samplesLoaded[success] = true;
      renderSamples();
      pushLog("样本加载失败，使用 mock: " + e, "warn");
    }
  }

  function renderSamples() {
    const successEl = $("successSamples");
    const failureEl = $("failureSamples");
    const sList = state.samples.success || [];
    const fList = state.samples.failure || [];

    if (successEl) {
      if (sList.length === 0) {
        successEl.innerHTML = '<p class="placeholder">尚无成功样本</p>';
      } else {
        successEl.innerHTML = sList.map(s => {
          const url = s.paypal_url || s.paypal_approve_url || s.url || "";
          return `<div class="ml-line ok" style="padding:8px 0;border-bottom:1px solid var(--border-light)">
            <span class="mc-id">#${esc(s.chain_id || s.id || "—")}</span>
            <span style="margin-left:8px">${esc(s.email || "—")}</span>
            <span class="muted" style="margin-left:8px">${esc(s.country || "")}</span>
            ${url ? `<div class="muted" style="margin-top:4px;word-break:break-all">${esc(trunc(url, 80))}</div>` : ""}
          </div>`;
        }).join("");
      }
    }
    if (failureEl) {
      if (fList.length === 0) {
        failureEl.innerHTML = '<p class="placeholder">尚无失败样本</p>';
      } else {
        failureEl.innerHTML = fList.map(s => {
          return `<div class="ml-line err" style="padding:8px 0;border-bottom:1px solid var(--border-light)">
            <span class="mc-id">#${esc(s.chain_id || s.id || "—")}</span>
            <span style="margin-left:8px">${esc(s.email || "—")}</span>
            <span class="muted" style="margin-left:8px">${esc(s.country || "")}</span>
            <span class="muted" style="margin-left:8px">段: ${esc(s.stage || "—")}</span>
            <div style="margin-top:4px">${esc(s.reason || s.reason_code || s.error || "失败")}</div>
          </div>`;
        }).join("");
      }
    }
  }

  function mockSamples(success, n) {
    const countries = ["US", "JP", "GB", "AU", "HK", "DE", "BR", "VN"];
    const recs = [];
    for (let i = 0; i < n; i++) {
      const c = countries[i % countries.length];
      if (success) {
        recs.push({
          chain_id: "C" + (1000 + i),
          email: `user${i}@example.com`,
          country: c,
          paypal_url: `https://www.paypal.com/agreements/approve?ba_token=BA-0000${i}&token=EC-mock${i}`,
          time: ts(),
        });
      } else {
        recs.push({
          chain_id: "C" + (2000 + i),
          email: `fail${i}@example.com`,
          country: c,
          stage: STAGE_ORDER[i % STAGE_ORDER.length],
          reason: ["proxy_timeout", "dns_fail", "amount_guard", "poll_timeout", "stripe_card_declined"][i % 5],
          time: ts(),
        });
      }
    }
    return recs;
  }

  /* ============================================================
     成功弹窗
     ============================================================ */
  function showSuccessSheet(evt) {
    const urlBox = $("successUrlBox");
    if (urlBox) urlBox.value = evt.paypal_approve_url || "";
    let meta = "";
    if (evt.chain_id) meta += `chain: ${evt.chain_id}`;
    if (evt.email) meta += ` · ${evt.email}`;
    if (evt.country) meta += ` · ${evt.country}`;
    if (evt.amount !== undefined && evt.amount !== null) meta += ` · $${evt.amount}`;
    setText("successMeta", meta);
    const sheet = $("successSheet");
    if (sheet) sheet.classList.remove("hidden");
  }

  function closeSuccessSheet() {
    const sheet = $("successSheet");
    if (sheet) sheet.classList.add("hidden");
  }

  /* ============================================================
     批量运行
     ============================================================ */
  function setBatchRunning(running) {
    state.batchRunning = running;
    const startBtns = [$("ovvStartBtn"), $("batchStartBtn")];
    const stopBtns = [$("ovvStopBtn"), $("stopAllBtn"), $("proxyStopAllBtn")];
    startBtns.forEach(b => { if (b) b.disabled = running; });
    stopBtns.forEach(b => { if (b) b.disabled = !running; });

    if (running && !state.runStartTime) state.runStartTime = Date.now();
    if (!running) state.runStartTime = 0;

    const rb = $("runBadge");
    if (rb) {
      rb.innerHTML = running
        ? '<span class="ind ind-blue"></span>运行中'
        : '<span class="ind ind-grey"></span>空闲';
    }
  }

  async function startBatch() {
    const ids = Array.from(state.selectedTokenIds);
    if (ids.length === 0) {
      pushLog("请先在 Token 库中选择 Token", "warn");
      switchView("tokens");
      return;
    }
    const body = {
      token_ids: ids,
      max_concurrent: parseInt($("maxConcurrentInput")?.value || 10),
      retry_per_stage: parseInt($("retryInput")?.value || 3),
      attempts: parseInt($("attemptsInput")?.value || 8),
      auto_billing: $("autoBillingInput")?.checked ?? true,
      require_zero: $("requireZeroInput")?.checked ?? true,
    };
    pushLog(`批量启动: ${ids.length} 个 Token, 最大并发 ${body.max_concurrent}`, "info");
    try {
      const r = await api("/api/chain/batch", "POST", body);
      if (r.ok) {
        setBatchRunning(true);
        renderChainCards();
        renderMiniChains();
      } else {
        pushLog("批量启动失败: " + (r.error || "未知错误"), "err");
      }
    } catch (e) {
      pushLog("批量启动异常: " + e, "err");
    }
  }

  async function startChain(tokenId) {
    try {
      const r = await api("/api/chain/batch", "POST", { token_ids: [tokenId], max_concurrent: 1 });
      if (r.ok) { setBatchRunning(true); pushLog(`单 Token 启动: ${tokenId}`, "info"); }
      else pushLog("启动失败: " + (r.error || ""), "err");
    } catch (e) { pushLog("启动异常: " + e, "err"); }
  }

  async function stopAll() {
    try {
      await api("/api/chain/stop", "POST", {});
      pushLog("已发送停止信号", "info");
      setBatchRunning(false);
    } catch (e) { pushLog("停止异常: " + e, "err"); }
  }

  async function toggleNode(name, start) {
    const path = start ? "/api/proxy/start" : "/api/proxy/stop";
    try {
      await api(path, "POST", { name });
      pushLog(`节点 ${name} ${start ? "启动" : "停止"}请求已发送`, "info");
    } catch (e) { pushLog("节点操作异常: " + e, "err"); }
  }

  /* ============================================================
     导航计数
     ============================================================ */
  function updateNavCounts() {
    const active = Object.keys(state.chainStates).filter(id => state.chainStates[id].status === "running").length;
    setText("navOverviewCount", "");
    setText("navChainCount", active || "");
    setText("navTokenCount", state.tokens.length || "");
    setText("navProxyCount", state.nodes.length || "");
    setText("navInventoryCount", state.inventory.length || INVENTORY_TOTAL);
  }

  /* ============================================================
     事件绑定
     ============================================================ */
  function bindEvents() {
    /* ---- 侧边栏导航 ---- */
    $$(".nav-item").forEach(item => {
      item.addEventListener("click", () => switchView(item.dataset.view));
    });

    /* ---- 快速跳转 ---- */
    $$("[data-goto]").forEach(btn => {
      btn.addEventListener("click", () => switchView(btn.dataset.goto));
    });

    /* ---- 总览页启动/停止 ---- */
    $("ovvStartBtn")?.addEventListener("click", startBatch);
    $("ovvStopBtn")?.addEventListener("click", stopAll);

    /* ---- 链路页 ---- */
    $("batchStartBtn")?.addEventListener("click", startBatch);
    $("stopAllBtn")?.addEventListener("click", stopAll);
    $("maxConcurrentInput")?.addEventListener("input", updateChainSummary);

    /* ---- 日志 ---- */
    $("logFilter")?.addEventListener("change", e => { state.logFilter = e.target.value; renderLog(); });
    $("logChainFilter")?.addEventListener("change", e => { state.logChainFilter = e.target.value; renderLog(); });
    $("clearLogBtn")?.addEventListener("click", () => {
      state.logLines = []; renderLog(); renderMiniLog();
      pushLog("日志已清空", "info");
    });

    /* ---- Token 导入 ---- */
    $("importTokensBtn")?.addEventListener("click", async () => {
      const raw = $("tokenInput")?.value.trim();
      if (!raw) { setHTML("importResult", "请先输入"); return; }
      setHTML("importResult", "导入中…");
      try {
        const r = await api("/api/tokens/import", "POST", { raw });
        if (r.ok) {
          setHTML("importResult", `导入 ${r.imported} 条, 失败 ${r.failed}`);
          if (r.tokens) { state.tokens = r.tokens; renderTokenTable(); fillTokenSelects(); updateNavCounts(); }
        } else { setHTML("importResult", r.error || "导入失败"); }
      } catch (e) { setHTML("importResult", "异常: " + e); }
    });

    $("clearInputBtn")?.addEventListener("click", () => { setHTML("tokenInput", ""); setHTML("importResult", ""); });

    /* ---- Token 注册池导入 ---- */
    $("importFromPoolBtn")?.addEventListener("click", async () => {
      setHTML("poolImportResult", "拉取注册池中…");
      const url = $("poolUrlInput")?.value.trim();
      const source = $("poolSourceSelect")?.value || "stripe";
      try {
        const r = await api("/api/tokens/import-from-pool", "POST", { base_url: url || undefined, source });
        if (r.ok) {
          setHTML("poolImportResult",
            `拉取 ${r.total} 条 → 导入 ${r.imported} 条, 跳过 ${r.skipped} 条`);
          if (r.tokens) { state.tokens = r.tokens; renderTokenTable(); fillTokenSelects(); updateNavCounts(); }
          pushLog(`注册池导入完成: 拉取 ${r.total}, 导入 ${r.imported}, 去重跳过 ${r.skipped}`, "ok");
        } else {
          setHTML("poolImportResult", r.error || "导入失败");
          pushLog("注册池导入失败: " + (r.error || ""), "err");
        }
      } catch (e) { setHTML("poolImportResult", "异常: " + e); pushLog("注册池导入异常: " + e, "err"); }
    });
    $("tknImportBtn")?.addEventListener("click", () => switchView("tokens"));
    $("tknRefreshBtn")?.addEventListener("click", async () => {
      try {
        const r = await api("/api/tokens");
        if (r.tokens) { state.tokens = r.tokens; renderTokenTable(); fillTokenSelects(); updateNavCounts(); pushLog(`Token 已刷新，共 ${r.tokens.length} 条`, "info"); }
      } catch (e) { pushLog("Token 刷新失败: " + e, "err"); }
    });
    $("tokenFilter")?.addEventListener("input", renderTokenTable);
    $("tokenStatusFilter")?.addEventListener("change", renderTokenTable);

    /* ---- 全选 / 主选择 ---- */
    $("selectAllBtn")?.addEventListener("click", () => {
      if (state.selectedTokenIds.size < state.tokens.length) {
        state.tokens.forEach(t => state.selectedTokenIds.add(t.id));
      } else { state.selectedTokenIds.clear(); }
      renderTokenTable();
    });
    $("tknMasterCheck")?.addEventListener("change", (e) => {
      if (e.target.checked) state.tokens.forEach(t => state.selectedTokenIds.add(t.id));
      else state.selectedTokenIds.clear();
      renderTokenTable();
    });

    /* ---- 代理 ---- */
    $("fetchSubBtn")?.addEventListener("click", async () => {
      const url = $("subUrlInput")?.value.trim();
      if (!url) { setHTML("subParseResult", "请输入订阅链接"); return; }
      try {
        const r = await api("/api/proxy/fetch-sub", "POST", { url });
        if (r.ok && r.raw) { $("subInput").value = r.raw; setHTML("subParseResult", `拉取 ${r.length} 字节`); }
        else setHTML("subParseResult", r.error || "拉取失败");
      } catch (e) { pushLog("拉取订阅失败: " + e, "err"); setHTML("subParseResult", "异常"); }
    });

    $("parseSubBtn")?.addEventListener("click", async () => {
      const raw = $("subInput")?.value.trim();
      if (!raw) { setHTML("subParseResult", "请先粘贴订阅内容"); return; }
      try {
        const r = await api("/api/proxy/parse", "POST", { raw });
        if (r.ok) {
          setHTML("subParseResult", `解析 ${r.count} 个节点`);
          if (r.nodes) { state.nodes = r.nodes; renderNodeTable(); updateNavCounts(); }
        } else setHTML("subParseResult", r.error || "解析失败");
      } catch (e) { pushLog("解析失败: " + e, "err"); setHTML("subParseResult", "异常"); }
    });

    $("proxyHealthBtn")?.addEventListener("click", async () => {
      try {
        const r = await api("/api/proxy/health");
        if (r.nodes) { state.nodes = r.nodes; renderNodeTable(); updateNavCounts(); }
        pushLog("健康检查完成", "info");
      } catch (e) { pushLog("健康检查失败: " + e, "err"); }
    });
    $("proxyStartAllBtn")?.addEventListener("click", async () => {
      try { await api("/api/proxy/start-all", "POST", {}); pushLog("全部启动请求已发送", "info"); }
      catch (e) { pushLog("全部启动失败: " + e, "err"); }
    });
    $("proxyStopAllBtn")?.addEventListener("click", async () => {
      try { await api("/api/proxy/stop-all", "POST", {}); pushLog("全部停止请求已发送", "info"); }
      catch (e) { pushLog("全部停止失败: " + e, "err"); }
    });

    /* ---- 成功库存 ---- */
    $("inventoryFilter")?.addEventListener("input", renderInventory);
    $("inventoryCountryFilter")?.addEventListener("change", renderInventory);
    $("inventoryExportBtn")?.addEventListener("click", exportInventoryCsv);

    /* ---- MoMo ---- */
    $("momoStartBtn")?.addEventListener("click", startMoMo);

    /* ---- Grok ---- */
    $("grokStartBtn")?.addEventListener("click", startGrok);

    /* ---- PIX ---- */
    $("pixStartBtn")?.addEventListener("click", startPix);

    /* ---- 统计刷新 ---- */
    $("analyticsRefreshBtn")?.addEventListener("click", async () => {
      try {
        const r = await api("/api/stats");
        if (r.stats) { state.stats = r.stats; renderStats(); pushLog("统计已刷新", "info"); }
      } catch (e) { pushLog("统计刷新失败: " + e, "err"); }
    });

    /* ---- 样本 tab ---- */
    $$(".seg").forEach(seg => {
      seg.addEventListener("click", () => {
        $$(".seg").forEach(s => s.classList.remove("active"));
        seg.classList.add("active");
        state.sampleTab = seg.dataset.sampleTab;
        $("successSamples")?.classList.toggle("hidden", state.sampleTab !== "success");
        $("failureSamples")?.classList.toggle("hidden", state.sampleTab !== "failure");
        if (!state.samplesLoaded[state.sampleTab]) loadSamples(state.sampleTab);
        else renderSamples();
      });
    });

    /* ---- 成功弹窗 ---- */
    $("closeSheetBtn")?.addEventListener("click", closeSuccessSheet);
    $("copyUrlBtn")?.addEventListener("click", () => {
      const urlBox = $("successUrlBox");
      if (urlBox && urlBox.value) {
        urlBox.select();
        try { document.execCommand("copy"); pushLog("URL 已复制", "ok"); }
        catch { navigator.clipboard?.writeText(urlBox.value).then(() => pushLog("URL 已复制", "ok")); }
      }
    });
    $("openUrlBtn")?.addEventListener("click", () => {
      const url = $("successUrlBox")?.value;
      if (url) window.open(url, "_blank");
    });
    const backdrop = document.querySelector(".sheet-backdrop");
    backdrop?.addEventListener("click", closeSuccessSheet);

    /* ---- ESC 关闭弹窗 ---- */
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeSuccessSheet();
    });
  }

  /* ============================================================
     初始化
     ============================================================ */
  async function init() {
    bindEvents();
    renderQgPool();
    connectWS();

    // REST 兜底（WS 未就绪时也能展示）
    try {
      const [tk, st, nodes] = await Promise.all([
        fetch("/api/tokens").then(r => r.json()).catch(() => null),
        fetch("/api/stats").then(r => r.json()).catch(() => null),
        fetch("/api/proxy/health").then(r => r.json()).catch(() => null),
      ]);
      if (tk?.tokens) { state.tokens = tk.tokens; renderTokenTable(); fillTokenSelects(); }
      if (st?.stats) { state.stats = st.stats; renderStats(); }
      if (nodes?.nodes) { state.nodes = nodes.nodes; renderNodeTable(); }
      updateNavCounts();
      updateChainSummary();
    } catch { /* 忽略，等待 WS 同步 */ }

    pushLog("前端已加载，等待 WebSocket 连接…", "info");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
