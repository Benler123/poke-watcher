const $ = (sel) => document.querySelector(sel);
const money = (value) => (value == null ? "—" : `$${Number(value).toFixed(2)}`);

let selectedCard = null;

const gradeLabel = (watch) => {
  const company = (watch.grade_company || "").toUpperCase();
  if (company === "RAW") return "Raw (ungraded)";
  return [company, watch.grade_value].filter(Boolean).join(" ");
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.status === 204 ? null : response.json();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "alerts") loadAlerts();
    if (tab.dataset.tab === "settings") loadSettings();
  });
});

async function loadHealth() {
  const health = await api("/api/health");
  const source = health.ebay_source === "browse_api" ? "eBay Browse API" : "eBay HTML scrape";
  const discord = health.discord_configured ? "Discord connected" : "Discord not configured";
  const last = health.monitor.last_run_at
    ? `last sweep ${new Date(health.monitor.last_run_at).toLocaleTimeString()}`
    : "no sweep yet";
  $("#status-bar").innerHTML =
    `${source} · ${discord}<br>${health.stats.active_watches} active watches · ` +
    `${health.stats.alerts} alerts · ${last}`;
  $("#health").textContent = JSON.stringify(health, null, 2);
  $("#index-hint").textContent = health.stats.indexed_products
    ? `${health.stats.indexed_products.toLocaleString()} cards indexed from TCGplayer.`
    : "Card index is empty — build it in Settings, or add a watch with a manual market price.";
  return health;
}

async function searchCards() {
  const query = $("#card-search").value.trim();
  if (!query) return;
  const data = await api(`/api/cards/search?q=${encodeURIComponent(query)}`);
  const list = $("#card-results");
  list.innerHTML = "";
  if (!data.results.length) {
    list.innerHTML = `<li class="empty">No cards found${data.indexed ? "" : " — the card index is empty, build it in Settings"}.</li>`;
    return;
  }
  data.results.forEach((card) => {
    const item = document.createElement("li");
    item.innerHTML = `
      ${card.image_url ? `<img src="${card.image_url}" alt="">` : ""}
      <div class="grow">
        <div>${card.name}</div>
        <div class="meta">${card.group_name}${card.number ? ` · #${card.number}` : ""}${card.rarity ? ` · ${card.rarity}` : ""}</div>
      </div>`;
    item.addEventListener("click", () => selectCard(card));
    list.appendChild(item);
  });
}

async function selectCard(card) {
  selectedCard = card;
  const form = $("#watch-form");
  form.classList.remove("hidden");
  form.querySelector("[name=label]").value = `${card.name} (${card.group_name})`;
  form.querySelector("[name=ebay_query]").value =
    `${card.name}${card.number ? ` ${card.number}` : ""}`.replace(/\s+/g, " ");
  $("#selected-card").innerHTML = `
    ${card.image_url ? `<img src="${card.image_url}" alt="">` : ""}
    <div>
      <div><strong>${card.name}</strong></div>
      <div class="sub">${card.group_name}${card.number ? ` · #${card.number}` : ""} ·
        <a href="${card.url}" target="_blank" rel="noopener">TCGplayer</a></div>
    </div>`;

  const select = $("#sub-type");
  select.innerHTML = `<option value="">Loading printings…</option>`;
  try {
    const data = await api(`/api/cards/${card.product_id}/price`);
    const entries = Object.entries(data.prices);
    select.innerHTML = entries.length
      ? entries
          .map(([name, price]) => `<option value="${name}">${name} — ${money(price)}</option>`)
          .join("")
      : `<option value="">No TCGplayer price found</option>`;
  } catch (error) {
    select.innerHTML = `<option value="">Price lookup failed</option>`;
  }
}

$("#card-search-btn").addEventListener("click", searchCards);
$("#card-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchCards();
});

function updateGradeHint() {
  const company = $("#grade-company").value;
  const grade = $("#grade-value").value.trim();
  const multiplier = Number($("#grade-multiplier").value || 1);
  if (!company || company === "RAW") {
    $("#grade-hint").textContent =
      company === "RAW" ? "Graded listings will be skipped." : "";
    return;
  }
  $("#grade-hint").textContent =
    `Only ${company} ${grade || "(any grade)"} listings alert. TCGplayer prices are for raw cards, ` +
    `so set the multiplier to what this grade sells for — currently ${multiplier}x market.`;
}

["#grade-company", "#grade-value", "#grade-multiplier"].forEach((sel) =>
  $(sel).addEventListener("input", updateGradeHint)
);

$("#cancel-watch").addEventListener("click", () => {
  selectedCard = null;
  $("#watch-form").classList.add("hidden");
});

$("#watch-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const value = (name) => {
    const raw = form.querySelector(`[name=${name}]`).value.trim();
    return raw === "" ? null : Number(raw);
  };
  const payload = {
    label: form.querySelector("[name=label]").value.trim(),
    ebay_query: form.querySelector("[name=ebay_query]").value.trim(),
    product_id: selectedCard ? selectedCard.product_id : null,
    set_name: selectedCard ? selectedCard.group_name : null,
    tcgplayer_url: selectedCard ? selectedCard.url : null,
    image_url: selectedCard ? selectedCard.image_url : null,
    sub_type_name: $("#sub-type").value || null,
    grade_company: $("#grade-company").value,
    grade_value: $("#grade-value").value.trim(),
    grade_price_multiplier: value("grade_price_multiplier") ?? 1,
    manual_market_price: value("manual_market_price"),
    bin_max_pct_of_market: (value("bin_max_pct_of_market") ?? 100) / 100,
    offer_max_pct_of_market: (value("offer_max_pct_of_market") ?? 115) / 100,
    min_price: value("min_price"),
    max_price: value("max_price"),
    exclude_terms: form.querySelector("[name=exclude_terms]").value.trim(),
    active: true,
  };
  await api("/api/watches", { method: "POST", body: JSON.stringify(payload) });
  form.reset();
  form.classList.add("hidden");
  $("#card-results").innerHTML = "";
  $("#card-search").value = "";
  selectedCard = null;
  await loadWatches();
  await loadHealth();
});

async function loadWatches() {
  const watches = await api("/api/watches");
  $("#watch-count").textContent = watches.length;
  const container = $("#watch-list");
  container.innerHTML = "";
  if (!watches.length) {
    container.innerHTML = `<div class="empty">Nothing watched yet — search for a card above.</div>`;
    return;
  }
  watches.forEach((watch) => {
    const market = watch.manual_market_price ?? watch.market_price;
    const row = document.createElement("div");
    row.className = `row${watch.active ? "" : " inactive"}`;
    row.innerHTML = `
      ${watch.image_url ? `<img src="${watch.image_url}" alt="">` : ""}
      <div class="grow">
        <div class="title">${watch.label}</div>
        <div class="sub">
          query: <code>${watch.ebay_query}</code>${watch.sub_type_name ? ` · ${watch.sub_type_name}` : ""}${gradeLabel(watch) ? ` · <span class="tag">${gradeLabel(watch)}</span>` : ""}<br>
          market ${money(market)} · alert BIN &le; ${Math.round(watch.bin_max_pct_of_market * 100)}%
          · offer &le; ${Math.round(watch.offer_max_pct_of_market * 100)}%
          · ${watch.alert_count} alerts
          ${watch.last_checked_at ? ` · checked ${watch.last_checked_at} UTC` : " · never checked"}
        </div>
        ${watch.last_error ? `<div class="sub"><span class="tag err">${watch.last_error}</span></div>` : ""}
      </div>
      <button class="ghost check">Check</button>
      <button class="ghost toggle">${watch.active ? "Pause" : "Resume"}</button>
      <button class="danger remove">Delete</button>`;

    row.querySelector(".check").addEventListener("click", async (event) => {
      event.target.textContent = "Checking…";
      try {
        const result = await api(`/api/watches/${watch.id}/check`, { method: "POST" });
        event.target.textContent = `${result.alerts.length} new`;
      } catch (error) {
        event.target.textContent = "Failed";
      }
      setTimeout(loadWatches, 1200);
    });
    row.querySelector(".toggle").addEventListener("click", async () => {
      await api(`/api/watches/${watch.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !watch.active }),
      });
      loadWatches();
    });
    row.querySelector(".remove").addEventListener("click", async () => {
      if (!confirm(`Stop watching ${watch.label}?`)) return;
      await api(`/api/watches/${watch.id}`, { method: "DELETE" });
      loadWatches();
      loadHealth();
    });
    container.appendChild(row);
  });
}

async function loadAlerts() {
  const alerts = await api("/api/alerts?limit=100");
  const container = $("#alert-list");
  container.innerHTML = "";
  if (!alerts.length) {
    container.innerHTML = `<div class="empty">No alerts yet.</div>`;
    return;
  }
  alerts.forEach((alert) => {
    const pct = Math.round(alert.pct_of_market * 100);
    const tag = alert.reason === "under_market" ? "tag" : "tag warn";
    const label = alert.reason === "under_market" ? `${pct}% of market` : `${pct}% · Best Offer`;
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `
      ${alert.image_url ? `<img src="${alert.image_url}" alt="">` : ""}
      <div class="grow">
        <div class="title"><a href="${alert.url}" target="_blank" rel="noopener">${alert.title}</a></div>
        <div class="sub">
          ${money(alert.price)} + ${alert.shipping ? money(alert.shipping) : "free"} shipping =
          <strong>${money(alert.total_price)}</strong> vs market ${money(alert.market_price)}
          · ${alert.watch_label} · ${alert.created_at} UTC
          ${alert.notified ? "" : " · <span class=\"tag err\">not sent to Discord</span>"}
        </div>
        ${alert.item_id ? `<div class="sub">
          <a href="https://www.ebay.com/atc/binctr?item=${alert.item_id}&quantity=1" target="_blank" rel="noopener">Buy It Now</a>
          ${alert.best_offer ? `· <a href="https://www.ebay.com/itm/${alert.item_id}?boolp=1" target="_blank" rel="noopener">Make Offer</a>` : ""}
        </div>` : ""}
      </div>
      <span class="${tag}">${label}</span>`;
    container.appendChild(row);
  });
}

async function loadSettings() {
  const settings = await api("/api/settings");
  $("#webhook").value = settings.discord_webhook_url || "";
  $("#webhook-status").textContent = settings.discord_webhook_set
    ? "Webhook configured."
    : "No webhook set — alerts will only appear in this UI.";
  $("#notify-endpoint").value = settings.ebay_notification_endpoint || "";
  $("#notify-token").value = settings.ebay_verification_token || "";
  $("#notify-status").textContent =
    settings.ebay_notification_endpoint && settings.ebay_verification_token
      ? "Endpoint ready — register it on developer.ebay.com/my/keys."
      : "Not configured — eBay production keys stay disabled until this is registered.";
  loadHealth();
}

$("#save-webhook").addEventListener("click", async () => {
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ discord_webhook_url: $("#webhook").value.trim() }),
  });
  loadSettings();
});

$("#test-webhook").addEventListener("click", async () => {
  const status = $("#webhook-status");
  try {
    const result = await api("/api/settings/test-discord", { method: "POST" });
    status.textContent = result.sent ? "Test message sent." : "Discord rejected the message.";
  } catch (error) {
    status.textContent = `Test failed — ${error.message}`;
  }
});

$("#save-notify").addEventListener("click", async () => {
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      ebay_notification_endpoint: $("#notify-endpoint").value.trim(),
      ebay_verification_token: $("#notify-token").value.trim(),
    }),
  });
  loadSettings();
});

$("#gen-token").addEventListener("click", async () => {
  const result = await api("/api/settings/ebay-token", { method: "POST" });
  $("#notify-token").value = result.ebay_verification_token;
  $("#notify-status").textContent = "Token generated — save, then register it with eBay.";
});

$("#build-index").addEventListener("click", async () => {
  await api("/api/cards/index", { method: "POST" });
  $("#index-status").textContent = "Indexing started…";
  const timer = setInterval(async () => {
    const health = await loadHealth();
    const progress = health.monitor.indexing;
    if (!progress) return;
    $("#index-status").textContent = progress.complete
      ? `Index complete — ${health.stats.indexed_products.toLocaleString()} cards.`
      : `Indexing set ${progress.done}/${progress.total} — ${progress.products.toLocaleString()} cards.`;
    if (progress.complete) clearInterval(timer);
  }, 3000);
});

$("#run-now").addEventListener("click", async (event) => {
  event.target.textContent = "Checking…";
  try {
    const result = await api("/api/monitor/run", { method: "POST" });
    event.target.textContent = `${result.alerts.length} new alerts`;
  } catch (error) {
    event.target.textContent = "Failed";
  }
  setTimeout(() => {
    event.target.textContent = "Check all now";
    loadWatches();
    loadHealth();
  }, 1500);
});

$("#refresh-alerts").addEventListener("click", loadAlerts);

loadHealth();
loadWatches();
setInterval(loadHealth, 30000);
