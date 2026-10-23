/**
 * Delta India AI Trading Add-on - Content Script
 * Runs in the context of Delta Exchange India (india.delta.exchange / deltaex.org)
 * Detects the active trading pair ("share" / contract), URL state, and price.
 */

(function () {
  console.log("[Delta Add-on] Content script active on Delta Exchange India.");

  const INVALID_SYMBOLS = new Set([
    "TRADE", "APP", "FUTURES", "OPTIONS", "SPOT", "PORTFOLIO",
    "ANALYTICS", "ORDERS", "INDEX", "HOME", "DELTA", "LOGIN", "SIGNUP", "DASHBOARD"
  ]);

  function isValidSymbol(sym) {
    if (!sym || typeof sym !== "string") return false;
    const clean = sym.trim().toUpperCase();
    if (INVALID_SYMBOLS.has(clean)) return false;
    if (clean.length < 3 || clean.length > 12) return false;
    return true;
  }

  function getActiveSymbol() {
    // 1. Try URL path (e.g. /app/trade/futures/BTCUSD or /futures/BTCUSD)
    const path = window.location.pathname;
    const match = path.match(/\/(?:trade\/futures|futures|options|spot)\/([A-Za-z0-9_\-]+)/i);
    if (match && match[1] && isValidSymbol(match[1])) {
      return match[1].toUpperCase();
    }

    // 2. Try URL search params (?symbol=BTCUSD)
    const urlParams = new URLSearchParams(window.location.search);
    const symParam = urlParams.get("symbol");
    if (symParam && isValidSymbol(symParam)) {
      return symParam.toUpperCase();
    }

    // 3. Try document title (e.g. "BTCUSD | Delta Exchange India")
    const titleMatch = document.title.match(/^([A-Za-z0-9_\-]+)\s*[|\-]/);
    if (titleMatch && titleMatch[1] && isValidSymbol(titleMatch[1])) {
      return titleMatch[1].toUpperCase();
    }

    // 4. Try common DOM elements on Delta UI
    const symbolElement = document.querySelector('[data-testid="contract-selector"], .contract-name, .symbol-title, .market-selector');
    if (symbolElement && symbolElement.textContent) {
      const text = symbolElement.textContent.trim().toUpperCase();
      const clean = text.replace(/[^A-Z0-9]/g, "");
      if (isValidSymbol(clean)) {
        return clean;
      }
    }

    return "BTCUSD"; // Default fallback
  }

  function getScrapedPrice() {
    // Attempt to extract displayed mark price or last traded price
    const priceSelectors = [
      '.last-price',
      '[data-testid="last-price"]',
      '.mark-price',
      '.header-price'
    ];
    for (const sel of priceSelectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent) {
        const num = parseFloat(el.textContent.replace(/[^0-9.]/g, ""));
        if (!isNaN(num) && num > 0) return num;
      }
    }
    return null;
  }

  // Handle messages from the extension popup
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request && request.type === "GET_ACTIVE_INSTRUMENT") {
      const symbol = getActiveSymbol();
      const price = getScrapedPrice();
      sendResponse({
        success: true,
        symbol: symbol,
        price: price,
        url: window.location.href,
        hostname: window.location.hostname
      });
    }
    return true; // Keep channel open for async response
  });
})();
