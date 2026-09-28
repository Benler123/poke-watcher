const $ = (sel) => document.querySelector(sel);
const money = (value) => (value == null ? "—" : `$${Number(value).toFixed(2)}`);
const SEALED_EXCLUDES = "empty, opened, damaged, code card, proxy, repack";

let selectedCard = null;

const productType = () =>
  document.querySelector("input[name=product_type]:checked").value;
const isSealed = () => productType() === "sealed";

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
  const indexing = health.monitor.indexing;
  const building = indexing && !indexing.complete;
  $("#index-hint").textContent = health.stats.indexed_products
    ? `${health.stats.indexed_products.toLocaleString()} cards indexed from TCGplayer.`
    : building
      ? "Card index is building — card search will work in a few minutes."
      : "Card index is empty — it builds automatically, or add a watch with a manual market price.";
  $("#index-status").textContent = indexStatus(health);
  return health;
}

function indexStatus(health) {
  const progress = health.monitor.indexing;
  if (progress && !progress.complete) {
    return `Indexing set ${progress.done}/${progress.total} — ${progress.products.toLocaleString()} cards.`;
  }
  if (progress && progress.error) return `Last index build failed: ${progress.error}`;
  const built = health.stats.index_built_at;
  return built
    ? `${health.stats.indexed_products.toLocaleString()} cards, last built ${new Date(built).toLocaleString()}.`
    : "";
}

async function searchCards() {
  const query = $("#card-search").value.trim();
  if (!query) return;
  const data = await api(
    `/api/cards/search?q=${encodeURIComponent(query)}&product_type=${productType()}`
  );
  const list = $("#card-results");
  list.innerHTML = "";
  if (!data.results.length) {
    const what = isSealed() ? "sealed products" : "cards";
    list.innerHTML = `<li class="empty">No ${what} found${data.indexed ? "" : " — the product index is empty or still building"}.</li>`;
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
  const name = card.name.replace(/\s+-\s+/g, " ");
  const withNumber = !isSealed() && card.number && !name.includes(card.number);
  form.querySelector("[name=ebay_query]").value =
    `${name}${withNumber ? ` ${card.number}` : ""}`.replace(/\s+/g, " ").trim();
  if (isSealed() && !form.querySelector("[name=exclude_terms]").value) {
    form.querySelector("[name=exclude_terms]").value = SEALED_EXCLUDES;
  }
  $("#selected-card").innerHTML = `
    ${card.image_url ? `<img src="${card.image_url}" alt="">` : ""}
    <div>
      <div><strong>${card.name}</strong></div>
      <div class="sub">${card.group_name}${card.number ? ` · #${card.number}` : ""} ·
        <a href="${card.url}" target="_blank" rel="noopener">TCGplayer</a></div>
    </div>`;
}

$("#card-search-btn").addEventListener("click", searchCards);
$("#card-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchCards();
});

function applyProductType() {
  const sealed = isSealed();
  document
    .querySelectorAll(".grade-field")
    .forEach((field) => field.classList.toggle("hidden", sealed));
  $("#card-search").placeholder = sealed
    ? "Search sealed product, e.g. Prismatic Evolutions Elite Trainer Box"
    : "Search a card, e.g. Charizard ex 199";
  $("#card-results").innerHTML = "";
  $("#watch-form").classList.add("hidden");
  selectedCard = null;
  updateGradeHint();
}

document
  .querySelectorAll("input[name=product_type]")
  .forEach((radio) => radio.addEventListener("change", applyProductType));

function updateGradeHint() {
  if (isSealed()) {
    $("#grade-hint").textContent =
      "Sealed watches search eBay's sealed box/pack/deck/case categories for new listings only.";
    return;
  }
  const company = $("#grade-company").value;
  const grade = $("#grade-value").value.trim();
  if (!company || company === "RAW") {
    $("#grade-hint").textContent =
      company === "RAW" ? "Graded listings will be skipped." : "";
    return;
  }
  $("#grade-hint").textContent =
    `Only ${company} ${grade || "(any grade)"} listings alert. Set the market price to what this grade sells for.`;
}

["#grade-company", "#grade-value"].forEach((sel) =>
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
    product_type: productType(),
    product_id: selectedCard ? selectedCard.product_id : null,
    set_name: selectedCard ? selectedCard.group_name : null,
    tcgplayer_url: selectedCard ? selectedCard.url : null,
    image_url: selectedCard ? selectedCard.image_url : null,
    grade_company: isSealed() ? "" : $("#grade-company").value,
    grade_value: isSealed() ? "" : $("#grade-value").value.trim(),
    manual_market_price: value("manual_market_price"),
    bin_max_pct_of_market: (value("bin_max_pct_of_market") ?? 100) / 100,
    offer_max_pct_of_market: (value("offer_max_pct_of_market") ?? 115) / 100,
    min_price: value("min_price"),
    max_price: value("max_price"),
    exclude_terms: form.querySelector("[name=exclude_terms]").value.trim(),
    strict_match: form.querySelector("[name=strict_match]").checked,
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
    const row = document.createElement("div");
    row.className = `row${watch.active ? "" : " inactive"}`;
    row.innerHTML = `
      ${watch.image_url ? `<img src="${watch.image_url}" alt="">` : ""}
      <div class="grow">
        <div class="title">${watch.label}</div>
        <div class="sub">
          query: <code>${watch.ebay_query}</code>${watch.product_type === "sealed" ? ` · <span class="tag">Sealed</span>` : ""}${gradeLabel(watch) ? ` · <span class="tag">${gradeLabel(watch)}</span>` : ""}<br>
          market ${watch.manual_market_price != null ? money(watch.manual_market_price) : `<span class="tag err">not set</span>`} · alert BIN &le; ${Math.round(watch.bin_max_pct_of_market * 100)}%
          · offer &le; ${Math.round(watch.offer_max_pct_of_market * 100)}%
          · ${watch.alert_count} alerts
          ${watch.last_checked_at ? ` · checked ${watch.last_checked_at} UTC` : " · never checked"}
        </div>
        ${watch.last_error ? `<div class="sub"><span class="tag err">${watch.last_error}</span></div>` : ""}
        <div class="price-edit hidden">
          <input type="number" step="0.01" min="0.01" placeholder="market price"
            value="${watch.manual_market_price ?? ""}">
          <button class="save-price">Save</button>
          <button type="button" class="ghost cancel-price">Cancel</button>
        </div>
      </div>
      <button class="ghost edit-price">Market price</button>
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
    const priceEdit = row.querySelector(".price-edit");
    const setPrice = async (price) => {
      await api(`/api/watches/${watch.id}`, {
        method: "PATCH",
        body: JSON.stringify({ manual_market_price: price }),
      });
      loadWatches();
    };
    row.querySelector(".edit-price").addEventListener("click", () => {
      priceEdit.classList.toggle("hidden");
      priceEdit.querySelector("input").focus();
    });
    row.querySelector(".cancel-price").addEventListener("click", () => priceEdit.classList.add("hidden"));
    row.querySelector(".save-price").addEventListener("click", () => {
      const price = Number(priceEdit.querySelector("input").value);
      if (price > 0) setPrice(price);
    });
    priceEdit.querySelector("input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") row.querySelector(".save-price").click();
      if (event.key === "Escape") priceEdit.classList.add("hidden");
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
          · watch #${alert.watch_id} ${alert.watch_label} · ${alert.created_at} UTC
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
  $("#notify-forward").checked = Boolean(settings.forward_deletion_notices);
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
      forward_deletion_notices: $("#notify-forward").checked,
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
    if (!health.monitor.indexing || health.monitor.indexing.complete) clearInterval(timer);
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

applyProductType();
loadHealth();
loadWatches();
setInterval(loadHealth, 30000);
