/**
 * Delta India Universal AI Trading Add-on - Browser Dashboard Controller
 * Conforms to trading-bot-mark-1.vercel.app and TradeHull Masterclass (EP 1-7)
 */
const BASE = "http://127.0.0.1:8000";

let isBackendConnected = false;
let currentPendingAction = null;
let currentActiveSymbol = "BTCUSD";
let activePreset = "balanced";

// DOM Elements - Header & Market Strip
const connIndicator = document.getElementById("conn-indicator");
const connText = document.getElementById("conn-text");
const envPill = document.getElementById("env-pill");
const headerBalance = document.getElementById("header-balance");
const marketSymbol = document.getElementById("market-symbol");
const marketPrice = document.getElementById("market-price");
const marketBidAsk = document.getElementById("market-bid-ask");
const marketSpread = document.getElementById("market-spread");
const marketFreshness = document.getElementById("market-freshness");
const footerTime = document.getElementById("footer-time");

// Modal Elements
const modal = document.getElementById("modal-confirm");
const modalTitle = document.getElementById("modal-title");
const modalDesc = document.getElementById("modal-desc");
const modalProceed = document.getElementById("modal-proceed");
const modalCancel = document.getElementById("modal-cancel");

// Generic API Fetch Wrapper
async function api(path, options = {}) {
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || body.message || `HTTP ${res.status}`);
    setConnected(true);
    return body;
  } catch (err) {
    if (err.message.includes("Failed to fetch") || err.message.includes("NetworkError")) {
      setConnected(false);
    }
    throw err;
  }
}

function setConnected(connected) {
  isBackendConnected = connected;
  if (connected) {
    connIndicator.className = "status-indicator dot-green";
    connText.textContent = "Backend Connected";
  } else {
    connIndicator.className = "status-indicator dot-red";
    connText.textContent = "Backend Offline";
  }
}

// Tab Switching
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
    btn.classList.add("active");
    const target = document.getElementById(btn.getAttribute("data-tab"));
    if (target) target.classList.add("active");

    // Auto-refresh scanner if clicked
    if (btn.getAttribute("data-tab") === "tab-scanner") {
      fetchMarketScanner();
    }
    // Auto-refresh client IP if clicked
    if (btn.getAttribute("data-tab") === "tab-credentials") {
      fetchClientIP();
      loadEnvConfig();
    }
  });
});

// Preset Strategy Selection (Mark-1: Balanced Alpha, Scalper, Fortress)
const PRESETS = {
  balanced: {
    name: "Balanced Alpha",
    levelsAbove: 5,
    levelsBelow: 5,
    gridGap: 100,
    leverage: 10,
    coverFees: true,
    desc: "10 Rungs (±5) · $100 gap · 10x leverage · Fee-Covered"
  },
  scalper: {
    name: "High-Frequency Scalper",
    levelsAbove: 8,
    levelsBelow: 8,
    gridGap: 50,
    leverage: 15,
    coverFees: true,
    desc: "16 Rungs (±8) · $50 gap · 15x leverage · Micro-Turns"
  },
  fortress: {
    name: "Conservative Fortress",
    levelsAbove: 4,
    levelsBelow: 4,
    gridGap: 200,
    leverage: 5,
    coverFees: true,
    desc: "8 Rungs (±4) · $200 gap · 5x leverage · Low Drawdown"
  }
};

document.querySelectorAll(".preset-card").forEach(card => {
  card.addEventListener("click", async () => {
    document.querySelectorAll(".preset-card").forEach(c => c.classList.remove("active"));
    card.classList.add("active");
    const pKey = card.getAttribute("data-preset");
    activePreset = pKey;
    const p = PRESETS[pKey];

    // Populate backtest inputs
    document.getElementById("bt-levels-above").value = p.levelsAbove;
    document.getElementById("bt-levels-below").value = p.levelsBelow;
    document.getElementById("bt-grid-gap").value = p.gridGap;
    document.getElementById("bt-cover-fees").checked = p.coverFees;

    try {
      const sym = currentActiveSymbol || "BTCUSD";
      await api("/api/grids", {
        method: "POST",
        body: JSON.stringify({
          symbol: sym,
          levelsAbove: p.levelsAbove,
          levelsBelow: p.levelsBelow,
          gridGap: p.gridGap,
          leverage: p.leverage,
          coverFees: p.coverFees,
          contractsPerLevel: 1
        })
      });
      alert(`⚡ Preset '${p.name}' Applied!\n\n${p.desc}\n\nGrid levels updated. Click 'Start Grid' to activate.`);
      refreshAll();
    } catch (e) {
      console.log("Preset apply error:", e);
    }
  });
});

// Periodic Refresh Loop
async function refreshAll() {
  footerTime.textContent = new Date().toUTCString().slice(17, 25) + " UTC";
  const sym = currentActiveSymbol || "BTCUSD";
  try {
    const [statusData, tickerData, levelsData, tradesData, riskData] = await Promise.allSettled([
      api("/api/status"),
      api(`/api/market/ticker/${sym}`),
      api("/api/grid/levels"),
      api("/api/trades?limit=10"),
      api(`/api/risk/status?symbol=${encodeURIComponent(sym)}`)
    ]);

    if (statusData.status === "fulfilled") renderStatus(statusData.value);
    if (tickerData.status === "fulfilled") renderTicker(tickerData.value);
    if (levelsData.status === "fulfilled") renderGridLevels(levelsData.value);
    if (tradesData.status === "fulfilled") renderTrades(tradesData.value);
    if (riskData.status === "fulfilled") renderRisk(riskData.value);
  } catch (e) {
    setConnected(false);
  }
}

function renderStatus(data) {
  envPill.textContent = data.mode;
  if (data.mode.includes("TESTNET")) {
    envPill.className = "pill pill-testnet";
  } else {
    envPill.className = "pill pill-paper";
  }

  // Account & Summary
  const acct = data.account || {};
  const realizedPnl = acct.realized_pnl || 0;
  const unrealizedPnl = acct.unrealized_pnl || 0;
  const totalFees = acct.total_fees_paid || 0;
  const netEquity = acct.equity || acct.balance || 10000.0 + realizedPnl;

  if (headerBalance) {
    headerBalance.textContent = `$${netEquity.toFixed(2)}`;
  }
  document.getElementById("grid-realized-pnl").textContent = `$${realizedPnl.toFixed(2)}`;
  document.getElementById("pos-realized-pnl").textContent = `$${realizedPnl.toFixed(2)}`;
  document.getElementById("pos-unrealized-pnl").textContent = `$${unrealizedPnl.toFixed(2)}`;
  document.getElementById("pos-fees").textContent = `$${totalFees.toFixed(2)}`;
  document.getElementById("grid-open-orders").textContent = data.orders_count || 0;

  // Positions
  const posTbody = document.getElementById("positions-tbody");
  const positions = data.positions || {};
  const posEntries = Object.entries(positions).filter(([_, size]) => size !== 0);
  if (posEntries.length === 0) {
    posTbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No open positions.</td></tr>`;
  } else {
    posTbody.innerHTML = posEntries.map(([sym, size]) => `
      <tr>
        <td><strong>${sym}</strong></td>
        <td class="${size > 0 ? 'side-buy' : 'side-sell'}">${size > 0 ? 'LONG' : 'SHORT'}</td>
        <td>${Math.abs(size)}</td>
        <td>Market</td>
      </tr>
    `).join("");
  }

  // Update Prominent Live Trade & Position Status Banner
  const tradeBox = document.getElementById("live-trade-status-box");
  const tradeIndicator = document.getElementById("live-trade-indicator");
  const tradeTitle = document.getElementById("live-trade-title");
  const tradeDesc = document.getElementById("live-trade-desc");
  const tradePnl = document.getElementById("live-trade-pnl");
  const tradeMode = document.getElementById("live-trade-mode");

  if (tradeBox) {
    const isPaper = data.mode === "PAPER";
    if (tradeMode) tradeMode.textContent = isPaper ? "Mode: Paper (Simulated)" : "Mode: Delta Live (Testnet)";

    if (posEntries.length > 0) {
      const [posSym, posQty] = posEntries[0];
      const isLong = posQty > 0;
      tradeBox.style.borderColor = isLong ? "#10b981" : "#ef4444";
      tradeBox.style.background = isLong ? "rgba(16, 185, 129, 0.08)" : "rgba(239, 68, 68, 0.08)";
      if (tradeIndicator) {
        tradeIndicator.style.background = isLong ? "#10b981" : "#ef4444";
        tradeIndicator.style.boxShadow = isLong ? "0 0 8px #10b981" : "0 0 8px #ef4444";
      }
      if (tradeTitle) tradeTitle.textContent = `⚡ Active Trade: ${isLong ? 'BUY / LONG' : 'SELL / SHORT'} ${Math.abs(posQty)} ct ${posSym}`;
      if (tradeDesc) tradeDesc.textContent = `Realized: $${realizedPnl.toFixed(2)} | Paired profit exit counter-orders placed on ladder`;
      if (tradePnl) {
        tradePnl.textContent = `Unrealized: ${unrealizedPnl >= 0 ? '+' : ''}$${unrealizedPnl.toFixed(2)}`;
        tradePnl.style.color = unrealizedPnl >= 0 ? "#10b981" : "#ef4444";
      }
    } else {
      const isGridActive = Boolean(data.grid_strategy_active || data.grid_active);
      if (isGridActive) {
        tradeBox.style.borderColor = "#10b981";
        tradeBox.style.background = "rgba(16, 185, 129, 0.06)";
        if (tradeIndicator) {
          tradeIndicator.style.background = "#10b981";
          tradeIndicator.style.boxShadow = "0 0 8px #10b981";
        }
        if (tradeTitle) tradeTitle.textContent = "🤖 Bot Status: ACTIVE & MONITORING";
        if (tradeDesc) tradeDesc.textContent = "Grid is active! Bot is monitoring market to auto-buy dips & auto-sell rallies.";
      } else {
        tradeBox.style.borderColor = "#334155";
        tradeBox.style.background = "#0f172a";
        if (tradeIndicator) {
          tradeIndicator.style.background = "#64748b";
          tradeIndicator.style.boxShadow = "none";
        }
        if (tradeTitle) tradeTitle.textContent = "🤖 Bot Status: IDLE (Click Start Grid)";
        if (tradeDesc) tradeDesc.textContent = "Position: 0 contracts (Flat). Ready for automated dip entry or instant trade.";
      }
      if (tradePnl) {
        tradePnl.textContent = `Realized: $${realizedPnl.toFixed(2)}`;
        tradePnl.style.color = "#94a3b8";
      }
    }
  }
}

function renderTicker(t) {
  if (marketSymbol) marketSymbol.textContent = t.symbol || currentActiveSymbol;
  marketPrice.textContent = `$${(t.last_price || 0).toFixed(2)}`;
  marketBidAsk.textContent = `$${(t.best_bid || 0).toFixed(1)} / $${(t.best_ask || 0).toFixed(1)}`;
  marketSpread.textContent = `${(t.spread_pct * 100 || 0).toFixed(3)}%`;
  marketFreshness.textContent = t.is_stale ? "STALE" : "LIVE";
  marketFreshness.style.color = t.is_stale ? "var(--red)" : "var(--green)";
  updateTradeEstimation();
}

function renderGridLevels(levels) {
  const tbody = document.getElementById("grid-levels-tbody");
  if (!levels || levels.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center;">No grid levels configured. Click '⚡ Auto-Calibrate' to generate.</td></tr>`;
    return;
  }

  let activeCount = 0;
  const sortedLevels = [...levels].sort((a, b) => b.price - a.price);

  tbody.innerHTML = sortedLevels.map(lvl => {
    let sideClass = "side-buy";
    if (lvl.target_side === "SELL") sideClass = "side-sell";
    if (lvl.target_side === "HOLD") sideClass = "text-muted";

    let badgeClass = "badge-vacant";
    if (lvl.status === "ENTRY_OPEN") { badgeClass = "badge-open"; activeCount++; }
    if (lvl.status === "EXIT_OPEN") { badgeClass = "badge-exit-open"; activeCount++; }

    return `
      <tr>
        <td>#${lvl.level_id}</td>
        <td class="${sideClass}">${lvl.target_side}</td>
        <td><strong>$${(lvl.price || 0).toFixed(1)}</strong></td>
        <td><span class="status-badge ${badgeClass}">${lvl.status}</span></td>
        <td>${lvl.paired_price ? `$${lvl.paired_price.toFixed(1)}` : '--'}</td>
        <td>${lvl.cycle_count || 0}</td>
      </tr>
    `;
  }).join("");

  document.getElementById("grid-active-levels").textContent = `${activeCount} / ${levels.length}`;
  if (levels.length > 0) {
    const prices = levels.map(l => l.price);
    const mid = (Math.max(...prices) + Math.min(...prices)) / 2;
    document.getElementById("grid-center-val").textContent = `$${mid.toFixed(0)}`;
  }
}

function renderTrades(trades) {
  const tbody = document.getElementById("trades-tbody");
  if (!trades || trades.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No trade executions yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = trades.map(tr => `
    <tr>
      <td>${tr.timestamp ? tr.timestamp.slice(11, 19) : '--'}</td>
      <td class="${tr.side === 'BUY' ? 'side-buy' : 'side-sell'}">${tr.side}</td>
      <td>${tr.quantity}</td>
      <td>$${(tr.price || 0).toFixed(2)}</td>
      <td>$${(tr.fee || 0).toFixed(4)}</td>
      <td style="color: ${tr.realized_pnl >= 0 ? 'var(--green)' : 'var(--red)'}; font-weight: 600;">
        ${tr.realized_pnl >= 0 ? '+' : ''}${(tr.realized_pnl || 0).toFixed(2)}
      </td>
    </tr>
  `).join("");
}

function renderRisk(r) {
  document.getElementById("risk-daily-val").textContent = `$${Math.abs(r.daily_realized_pnl || 0).toFixed(0)} / $1000`;
  const dailyPct = Math.min(100, (Math.abs(r.daily_realized_pnl || 0) / 1000) * 100);
  document.getElementById("bar-daily").style.width = `${dailyPct}%`;
  document.getElementById("bar-daily").style.backgroundColor = dailyPct > 80 ? "var(--red)" : "var(--accent)";

  const posQty = Math.abs(r.current_position || 0);
  document.getElementById("risk-pos-val").textContent = `${posQty} / 10 contracts`;
  document.getElementById("bar-pos").style.width = `${Math.min(100, (posQty / 10) * 100)}%`;

  document.getElementById("risk-orders-val").textContent = `${r.open_orders_count || 0} / 20`;
  document.getElementById("bar-orders").style.width = `${Math.min(100, ((r.open_orders_count || 0) / 20) * 100)}%`;

  const cbStatus = document.getElementById("circuit-status");
  if (r.kill_switch_active) {
    cbStatus.textContent = `KILL SWITCH ACTIVE (${r.breach_reason || 'Emergency lock'})`;
    cbStatus.className = "circuit-status tripped";
  } else {
    cbStatus.textContent = `NORMAL (${r.consecutive_errors} consecutive errors)`;
    cbStatus.className = "circuit-status normal";
  }
}

// Investment & Margin Calculation
function updateTradeEstimation() {
  const qtyInput = document.getElementById("trade-qty-input");
  const estPill = document.getElementById("trade-est-pill");
  if (!qtyInput || !estPill) return;

  const qty = parseInt(qtyInput.value) || 1;
  const sym = currentActiveSymbol || "BTCUSD";
  const p = parseFloat(marketPrice.textContent.replace(/[^0-9.]/g, "")) || 82000;
  const mult = sym.includes("BTC") ? 0.001 : (sym.includes("ETH") ? 0.01 : 1.0);
  const notional = qty * p * mult;
  const margin = notional / 10.0;
  estPill.textContent = `Est: ~$${margin.toFixed(2)} margin (10x) / ~$${notional.toFixed(2)} notional`;
}

const tradeQtyInput = document.getElementById("trade-qty-input");
if (tradeQtyInput) {
  tradeQtyInput.addEventListener("input", updateTradeEstimation);
}

// Instant Trade Buttons (Market BUY / SELL)
document.getElementById("btn-instant-trade").addEventListener("click", async () => {
  const sym = currentActiveSymbol || "BTCUSD";
  const qty = parseInt(document.getElementById("trade-qty-input")?.value) || 1;
  try {
    const res = await api(`/api/trade/market?symbol=${encodeURIComponent(sym)}&side=BUY&quantity=${qty}`, { method: "POST" });
    if (res.warning) {
      alert(`⚡ BUY Order Executed in Paper Simulation!\n\n${res.warning}\n\nQuantity: ${res.quantity} contract(s)\nFill Price: $${res.fill_price}\nEstimated Margin: ~$${res.margin_usd}`);
    } else {
      alert(`⚡ BUY Order Executed!\n\nSide: ${res.side}\nSymbol: ${res.symbol}\nQuantity: ${res.quantity} contract(s)\nFill Price: $${res.fill_price}\nEstimated Margin: ~$${res.margin_usd}\nMode: ${res.mode}\nOrder ID: ${res.client_order_id}`);
    }
    refreshAll();
  } catch (e) {
    alert(`Trade Execution Notice:\n\n${e.message}\n\n👉 Tip: Toggle to Paper Trading in the top pill to test instantly!`);
  }
});

const btnInstantSell = document.getElementById("btn-instant-sell");
if (btnInstantSell) {
  btnInstantSell.addEventListener("click", async () => {
    const sym = currentActiveSymbol || "BTCUSD";
    const qty = parseInt(document.getElementById("trade-qty-input")?.value) || 1;
    try {
      const res = await api(`/api/trade/market?symbol=${encodeURIComponent(sym)}&side=SELL&quantity=${qty}`, { method: "POST" });
      if (res.warning) {
        alert(`⚡ SELL Order Executed in Paper Simulation!\n\n${res.warning}\n\nQuantity: ${res.quantity} contract(s)\nFill Price: $${res.fill_price}\nEstimated Margin: ~$${res.margin_usd}`);
      } else {
        alert(`⚡ SELL Order Executed!\n\nSide: ${res.side}\nSymbol: ${res.symbol}\nQuantity: ${res.quantity} contract(s)\nFill Price: $${res.fill_price}\nEstimated Margin: ~$${res.margin_usd}\nMode: ${res.mode}\nOrder ID: ${res.client_order_id}`);
      }
      refreshAll();
    } catch (e) {
      alert(`Trade Execution Notice:\n\n${e.message}\n\n👉 Tip: Toggle to Paper Trading in the top pill to test instantly!`);
    }
  });
}

// Start Grid Button (with clear feedback on Seed Trade)
document.getElementById("btn-start").addEventListener("click", async () => {
  const sym = currentActiveSymbol || "BTCUSD";
  try {
    const res = await api(`/api/grid/start?symbol=${encodeURIComponent(sym)}&auto_center=true`, { method: "POST" });
    await refreshAll();

    if (res.seed_trade_executed) {
      alert(`🚀 Grid Strategy Started & Seed Buy Order FILLED for ${sym}!\n\nCenter Price: $${res.center_price}\nStatus: ACTIVE & MONITORING\n\nInitial Buy order was filled at market price and paired counter-exit sell order was placed on the ladder. Bot is now actively monitoring the market!`);
    } else if (res.current_position && res.current_position > 0) {
      alert(`🚀 Grid Strategy Active for ${sym}!\n\nCenter Price: $${res.center_price}\nActive Position: Holding ${res.current_position} contract(s).\n\nBot is running! Counter-exit limit sell orders are placed on the ladder to lock in profit, and dip buy orders are waiting below market price.`);
    } else if (res.seed_error) {
      alert(`⚠️ Grid Strategy Started, but initial buy order was skipped:\n\nReason: ${res.seed_error}\n\n👉 Switch to Paper Trading mode in the top header pill for instant execution.`);
    } else {
      alert(`🚀 Grid Strategy Started for ${sym}!\n\nCenter: $${res.center_price}\nStatus: ACTIVE & MONITORING\nBot is now watching for market dips to auto-buy and rallies to auto-sell.`);
    }
  } catch (e) {
    alert(`Grid Start Error: ${e.message}`);
  }
});

// Auto-Calibrate Grid
document.getElementById("btn-auto-calibrate").addEventListener("click", async () => {
  const sym = currentActiveSymbol || "BTCUSD";
  try {
    const res = await api(`/api/grid/auto-configure?symbol=${encodeURIComponent(sym)}`, { method: "POST" });
    const cfg = res.config;
    alert(`⚡ Grid auto-calibrated around live market price $${res.live_price}!\n\nCenter: $${cfg.center_price}\nLower: $${cfg.lower_price}\nUpper: $${cfg.upper_price}\nLevels: ${cfg.levels_count}\nQty per Level: ${cfg.quantity_per_level} contracts`);
    refreshAll();
  } catch (e) {
    alert(`Auto-calibrate error: ${e.message}`);
  }
});

document.getElementById("btn-pause").addEventListener("click", async () => {
  try {
    await api("/api/grid/pause", { method: "POST" });
    refreshAll();
  } catch (e) { alert(`Pause error: ${e.message}`); }
});

document.getElementById("btn-resume").addEventListener("click", async () => {
  try {
    await api("/api/grid/resume", { method: "POST" });
    refreshAll();
  } catch (e) { alert(`Resume error: ${e.message}`); }
});

document.getElementById("btn-cancel-entries").addEventListener("click", async () => {
  try {
    const res = await api("/api/grid/cancel-entries", { method: "POST" });
    alert(`Success: ${res.message}`);
    refreshAll();
  } catch (e) { alert(`Error: ${e.message}`); }
});

document.getElementById("btn-emergency").addEventListener("click", async () => {
  try {
    await api("/api/grid/emergency-stop", { method: "POST" });
    alert("KILL SWITCH ACTIVATED: All entries blocked and orders cancelled.");
    refreshAll();
  } catch (e) { alert(`Error: ${e.message}`); }
});

// TradeHull Episode 6: Multi-Pair Market Scanner
async function fetchMarketScanner() {
  const tbody = document.getElementById("scanner-tbody");
  tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Scanning Delta India markets…</td></tr>`;
  try {
    const data = await api("/api/scanner/market");
    const assets = data.assets || [];
    if (assets.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align: center;">No assets found.</td></tr>`;
      return;
    }
    tbody.innerHTML = assets.map(a => {
      let badgeClass = "signal-neutral";
      if (a.signal.includes("DIP") || a.signal.includes("OVERSOLD")) badgeClass = "signal-dip";
      if (a.signal.includes("RALLY") || a.signal.includes("OVERBOUGHT")) badgeClass = "signal-rally";
      if (a.signal.includes("BREAKOUT")) badgeClass = "signal-breakout";

      const chgColor = a.change24h >= 0 ? "var(--green)" : "var(--red)";
      return `
        <tr>
          <td><strong>${a.symbol}</strong></td>
          <td>$${(a.price || 0).toLocaleString()}</td>
          <td style="color: ${chgColor}; font-weight: 600;">${a.change24h >= 0 ? '+' : ''}${a.change24h}%</td>
          <td>${a.rsi}</td>
          <td><span class="scanner-signal-badge ${badgeClass}">${a.signal}</span></td>
          <td>
            <button class="btn-xs btn-load-scanner" data-sym="${a.symbol}">Load Grid</button>
          </td>
        </tr>
      `;
    }).join("");

    // Hook up Load Grid buttons
    document.querySelectorAll(".btn-load-scanner").forEach(btn => {
      btn.addEventListener("click", async () => {
        const targetSym = btn.getAttribute("data-sym");
        currentActiveSymbol = targetSym;
        if (marketSymbol) marketSymbol.textContent = targetSym;
        // Auto-configure for this symbol
        await api(`/api/grid/auto-configure?symbol=${encodeURIComponent(targetSym)}`, { method: "POST" });
        alert(`Loaded ${targetSym} into Grid Engine! Calibrating ladder…`);
        // Switch to Grid tab
        document.querySelector(`.tab-btn[data-tab="tab-grid"]`).click();
        refreshAll();
      });
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" style="color: var(--red); text-align: center;">Scanner Error: ${e.message}</td></tr>`;
  }
}

document.getElementById("btn-refresh-scanner").addEventListener("click", fetchMarketScanner);

// Tab 4: Connect Delta Exchange & Credentials Manager
async function loadEnvConfig() {
  try {
    const cfg = await api("/api/env-config");
    const modeSelect = document.getElementById("cred-mode-select");
    if (modeSelect) modeSelect.value = cfg.mode || "PAPER";
    const apiKeyInput = document.getElementById("cred-api-key");
    if (apiKeyInput && cfg.apiKeyMasked) apiKeyInput.placeholder = `Current: ${cfg.apiKeyMasked}`;
  } catch (e) {
    console.log("Load env config error:", e);
  }
}

async function fetchClientIP() {
  const ipPill = document.getElementById("detected-client-ip");
  try {
    const res = await api("/api/delta/client-ip");
    if (ipPill) ipPill.textContent = res.ip || "152.59.121.46";
  } catch (e) {
    if (ipPill) ipPill.textContent = "152.59.121.46";
  }
}

document.getElementById("btn-copy-ip").addEventListener("click", () => {
  const ip = document.getElementById("detected-client-ip")?.textContent || "152.59.121.46";
  navigator.clipboard.writeText(ip).then(() => {
    alert(`📋 IP Copied to clipboard: ${ip}\n\nAdd this IP in Delta Exchange API settings: https://testnet.delta.exchange/app/account/api-keys`);
  });
});

document.getElementById("btn-save-credentials").addEventListener("click", async () => {
  const apiKey = document.getElementById("cred-api-key")?.value.trim();
  const apiSecret = document.getElementById("cred-api-secret")?.value.trim();
  const mode = document.getElementById("cred-mode-select")?.value;
  const fb = document.getElementById("cred-feedback");

  try {
    const payload = { mode };
    if (apiKey) payload.apiKey = apiKey;
    if (apiSecret) payload.apiSecret = apiSecret;

    await api("/api/env-config", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    fb.textContent = "Credentials and mode saved successfully!";
    fb.className = "feedback-msg feedback-success";
    refreshAll();
  } catch (e) {
    fb.textContent = `Error saving credentials: ${e.message}`;
    fb.className = "feedback-msg feedback-error";
  }
});

document.getElementById("btn-verify-credentials").addEventListener("click", async () => {
  const apiKey = document.getElementById("cred-api-key")?.value.trim();
  const apiSecret = document.getElementById("cred-api-secret")?.value.trim();
  const fb = document.getElementById("cred-feedback");

  try {
    fb.textContent = "Verifying with Delta Exchange India API…";
    fb.className = "feedback-msg";
    fb.style.display = "block";

    const payload = {};
    if (apiKey) payload.api_key = apiKey;
    if (apiSecret) payload.api_secret = apiSecret;

    const res = await api("/api/delta/verify-credentials", {
      method: "POST",
      body: JSON.stringify(payload)
    });

    if (res.success) {
      fb.textContent = `✅ ${res.message} (Balance: $${res.balance.toFixed(2)})`;
      fb.className = "feedback-msg feedback-success";
      if (headerBalance) headerBalance.textContent = `$${res.balance.toFixed(2)}`;
    } else {
      fb.textContent = `❌ ${res.message}`;
      fb.className = "feedback-msg feedback-error";
    }
  } catch (e) {
    fb.textContent = `Verification error: ${e.message}`;
    fb.className = "feedback-msg feedback-error";
  }
});

// Toggle Paper vs Live mode pill in header
document.getElementById("env-pill").addEventListener("click", async () => {
  const currentText = envPill.textContent;
  const newMode = currentText.includes("TESTNET") ? "PAPER" : "TESTNET";
  try {
    const res = await api("/api/settings/mode", {
      method: "POST",
      body: JSON.stringify({ mode: newMode })
    });
    alert(`Trading mode switched to: ${res.mode}\n\n${res.paper_trading ? 'Orders simulated in paper broker.' : 'Orders are active on Delta Exchange Testnet!'}`);
    refreshAll();
  } catch (e) {
    alert(`Mode switch error: ${e.message}`);
  }
});

// Risk Circuit Breakers & Reconcile
document.getElementById("btn-reset-kill").addEventListener("click", async () => {
  try {
    await api("/api/account/risk/reset-daily-loss", { method: "POST" });
    await api("/api/risk/reset-kill-switch", { method: "POST" });
    alert("Daily loss counter reset and system circuit breakers cleared!");
    refreshAll();
  } catch (e) { alert(`Error: ${e.message}`); }
});

document.getElementById("btn-reconcile").addEventListener("click", async () => {
  try {
    const report = await api("/api/reconcile", { method: "POST" });
    document.getElementById("recon-output").textContent = `Sync (${report.status}): Matched ${report.matched_orders_count} orders. ${report.actions_taken.join("; ")}`;
    refreshAll();
  } catch (e) { alert(`Sync error: ${e.message}`); }
});

// Tab 6: AI & Deterministic Backtest Simulator
document.getElementById("btn-ai-suggest").addEventListener("click", async () => {
  try {
    const sym = currentActiveSymbol || "BTCUSD";
    const rep = await api(`/api/ai/suggest-parameters?symbol=${sym}`);
    const cfg = rep.recommended_config;
    alert(`AI suggested parameters:\n\nCenter: $${cfg.center_price}\nGrid Levels: ${cfg.grid_levels}\nRationale: ${rep.rationale}`);
  } catch (e) { alert(`AI suggestion error: ${e.message}`); }
});

document.getElementById("btn-run-backtest").addEventListener("click", async () => {
  try {
    const payload = {
      symbol: currentActiveSymbol || "BTCUSD",
      levelsAbove: parseInt(document.getElementById("bt-levels-above").value) || 5,
      levelsBelow: parseInt(document.getElementById("bt-levels-below").value) || 5,
      gridGap: parseFloat(document.getElementById("bt-grid-gap").value) || 100,
      coverFees: document.getElementById("bt-cover-fees").checked,
      initialBalance: 10000.0
    };

    const res = await api("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(payload)
    });

    const box = document.getElementById("backtest-results");
    box.style.display = "block";
    const r = res.result || res;
    document.getElementById("bt-pnl").textContent = `$${(r.netProfitUsd || r.net_pnl || 0).toFixed(2)}`;
    document.getElementById("bt-winrate").textContent = `${(r.winRatePct || r.win_rate_pct || 0).toFixed(1)}%`;
    document.getElementById("bt-pf").textContent = (r.profitFactor || r.profit_factor || 1.0).toFixed(2);
    document.getElementById("bt-dd").textContent = `${(r.max_drawdown_pct || 0).toFixed(2)}%`;
    document.getElementById("bt-disclaimer").textContent = res.limitations_notice || "Based on 300 historical candles.";
  } catch (e) { alert(`Backtest error: ${e.message}`); }
});

// Active Delta Tab Auto-Detection
async function detectActiveDeltaTab() {
  const bannerText = document.getElementById("website-sync-text");
  const syncDot = document.getElementById("sync-dot");
  if (!window.chrome || !chrome.tabs) {
    if (bannerText) bannerText.textContent = "Standalone Mode (Delta India)";
    return;
  }

  try {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0];
      if (tab && (tab.url?.includes("delta.exchange") || tab.url?.includes("deltaex.org"))) {
        chrome.tabs.sendMessage(tab.id, { type: "GET_ACTIVE_INSTRUMENT" }, (resp) => {
          if (chrome.runtime.lastError || !resp) {
            const match = tab.url.match(/\/(?:trade\/futures|futures|options|spot)\/([A-Za-z0-9_\-]+)/i);
            const sym = match ? match[1].toUpperCase() : "BTCUSD";
            applyDetectedSymbol(sym, tab.url);
          } else if (resp.success && resp.symbol) {
            applyDetectedSymbol(resp.symbol, tab.url, resp.price);
          }
        });
      } else {
        if (bannerText) bannerText.textContent = "Delta Tab: Open india.delta.exchange to auto-sync";
        if (syncDot) syncDot.className = "status-indicator dot-red";
      }
    });
  } catch (err) {
    console.log("Tab detection err:", err);
  }
}

function applyDetectedSymbol(sym, url) {
  currentActiveSymbol = sym.toUpperCase();
  const bannerText = document.getElementById("website-sync-text");
  const syncDot = document.getElementById("sync-dot");
  if (syncDot) syncDot.className = "status-indicator dot-green";
  try {
    const host = new URL(url).hostname;
    if (bannerText) bannerText.innerHTML = `Active Tab: <strong>${currentActiveSymbol}</strong> (${host})`;
  } catch (_) {
    if (bannerText) bannerText.innerHTML = `Active Tab: <strong>${currentActiveSymbol}</strong>`;
  }
  if (marketSymbol) marketSymbol.textContent = currentActiveSymbol;
}

document.getElementById("btn-sync-tab").addEventListener("click", () => {
  detectActiveDeltaTab();
  refreshAll();
});

// Flatten Position Modal Logic
document.getElementById("btn-flatten").addEventListener("click", () => {
  currentPendingAction = "FLATTEN";
  modalTitle.textContent = "Confirm Position Flatten";
  modalDesc.textContent = "This will cancel all open grid orders and immediately close open positions via market execution. Proceed?";
  modal.style.display = "flex";
});

modalCancel.addEventListener("click", () => {
  modal.style.display = "none";
  currentPendingAction = null;
});

modalProceed.addEventListener("click", async () => {
  modal.style.display = "none";
  if (currentPendingAction === "FLATTEN") {
    try {
      const res = await api("/api/grid/flatten", {
        method: "POST",
        body: JSON.stringify({ confirm: true })
      });
      alert(`Flatten complete: Closed ${res.closed_size || 0} contracts.`);
      refreshAll();
    } catch (e) { alert(`Flatten error: ${e.message}`); }
  }
  currentPendingAction = null;
});

// Initialization
detectActiveDeltaTab();
fetchClientIP();
loadEnvConfig();
refreshAll();
setInterval(refreshAll, 3000);
