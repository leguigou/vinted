const $ = (selector) => document.querySelector(selector);
const ITEMS_PAGE_SIZE = 12;
const DASHBOARD_ITEMS_LIMIT = 10;
const MOBILE_MENU_QUERY = window.matchMedia("(max-width: 780px)");
const VIEW_TITLES = {
  account: "Mon compte",
  users: "Utilisateurs",
  telegram: "Telegram",
  settings: "Parametres",
  "new-search": "Nouvelle recherche",
  searches: "Tableau de bord",
  items: "Historique alertes",
  prices: "Analyse prix",
};
const VIEW_DESCRIPTIONS = {
  account: "Tes informations personnelles et la securite de ton compte.",
  users: "Gere les acces et les roles de ton espace.",
  telegram: "Configure le bot qui recevra les nouvelles annonces.",
  settings: "Ajuste le rythme et le comportement de la surveillance.",
  "new-search": "Cree une veille a partir d'une URL ou d'une recherche rapide.",
  searches: "L'essentiel de tes recherches, alertes et opportunites.",
  items: "Retrouve les annonces detectees par ordre chronologique.",
  prices: "Compare les prix et repere les annonces sous la mediane.",
};
let itemsPage = 1;
let isAuthenticated = false;
let activeView = "searches";
let latestState = null;

function applyTheme(theme) {
  const activeTheme = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = activeTheme;
  localStorage.setItem("vinted_theme", activeTheme);

  const isDark = activeTheme === "dark";
  [
    { button: $("#themeToggle"), text: $("#themeToggleText") },
    { button: $("#sideThemeToggle"), text: $("#sideThemeToggleText") },
  ].forEach(({ button, text }) => {
    if (!button || !text) return;
    button.setAttribute("aria-pressed", String(isDark));
    text.textContent = isDark ? "Theme clair" : "Theme sombre";
  });
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: cleanServerError(text, response.status) };
  }
  if (!response.ok || data.ok === false) {
    if (response.status === 401 || data.authenticated === false) {
      showLogin();
    }
    throw new Error(data.error || "Erreur inconnue");
  }
  return data;
}

function cleanServerError(text, status) {
  const value = String(text || "").trim();
  const lower = value.toLowerCase();
  if (lower.includes("<html") || lower.startsWith("<!doctype html")) {
    if (status === 403) {
      return "Accès refusé (403). Le serveur a refusé la requête.";
    }
    return `Réponse serveur illisible${status ? ` (${status})` : ""}.`;
  }
  return value || "Réponse serveur illisible";
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function normalizeFilterText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function buildSearchPayload(form) {
  const data = formData(form);
  const mode = data.search_mode || "manual";
  const searchText = String(data.search_text || "").trim();
  const manualUrl = String(data.manual_url || "").trim();
  const priceMin = String(data.price_min || "").trim().replace(",", ".");
  const priceMax = String(data.price_max || "").trim().replace(",", ".");
  const name = String(data.name || searchText || "Recherche Vinted").trim();

  if (mode === "manual") {
    if (!manualUrl) {
      throw new Error("Colle une URL de recherche Vinted ou passe en recherche rapide.");
    }
    return {
      name,
      url: manualUrl,
      interval_seconds: data.interval_seconds || "180",
    };
  }

  if (!searchText) {
    throw new Error("Tape une recherche ou colle une URL Vinted complete.");
  }

  const params = new URLSearchParams();
  params.set("search_text", searchText);
  params.set("order", "newest_first");
  params.set("currency", "EUR");

  if (priceMin && priceMax && Number(priceMin) > Number(priceMax)) {
    throw new Error("Le prix min doit etre inferieur ou egal au prix max.");
  }
  if (priceMin) {
    params.set("price_from", priceMin);
  }
  if (priceMax) {
    params.set("price_to", priceMax);
  }

  return {
    name,
    url: `https://www.vinted.fr/catalog?${params.toString()}`,
    interval_seconds: data.interval_seconds || "180",
  };
}

function syncSearchMode() {
  const form = $("#searchForm");
  if (!form) return;
  const mode = form.querySelector('[name="search_mode"]:checked')?.value || "manual";
  form.querySelectorAll("[data-search-mode-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.searchModePanel !== mode;
  });
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function parseLocalDate(value) {
  if (!value) return null;
  const normalized = String(value).trim().replace(" ", "T");
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function relativeTime(value) {
  const date = parseLocalDate(value);
  if (!date) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  const units = [
    ["d", 86400],
    ["h", 3600],
    ["m", 60],
  ];
  for (const [label, size] of units) {
    if (seconds >= size) return `${Math.floor(seconds / size)}${label} ago`;
  }
  return "now";
}

function refreshRelativeTimes() {
  document.querySelectorAll("[data-relative-time]").forEach((element) => {
    const label = relativeTime(element.dataset.relativeTime);
    if (label) element.textContent = label;
  });
}

function showTelegramHelp(message, isError = false) {
  const help = $("#telegramHelp");
  help.textContent = message;
  help.classList.toggle("error", isError);
}

function showAppSettingsHelp(message, isError = false) {
  const help = $("#appSettingsHelp");
  help.textContent = message;
  help.classList.toggle("error", isError);
}

function showCheckNowError(message) {
  const status = $("#status");
  if (!status) return;
  status.textContent = `erreur: ${message}`;
  status.classList.add("error");
}

function updateRandomIntervalLabel(value) {
  const percent = Number(value || 0);
  $("#randomIntervalValue").textContent = `${percent}%`;
}

function menuControls() {
  return [$("#menuToggle"), $("#mobileMenuToggle")].filter(Boolean);
}

function setMenuExpanded(expanded) {
  const isExpanded = Boolean(expanded);
  document.body.classList.toggle("menuCollapsed", !isExpanded);
  menuControls().forEach((button) => {
    button.setAttribute("aria-expanded", String(isExpanded));
    button.setAttribute("aria-label", isExpanded ? "Fermer le menu" : "Ouvrir le menu");
  });

  const backdrop = $("#menuBackdrop");
  if (backdrop) {
    backdrop.hidden = !(MOBILE_MENU_QUERY.matches && isExpanded && isAuthenticated);
  }
  document.body.classList.toggle("menuOpen", MOBILE_MENU_QUERY.matches && isExpanded);
  if (MOBILE_MENU_QUERY.matches && window.scrollX) {
    window.scrollTo(0, window.scrollY);
  }
}

function syncMenuForViewport() {
  setMenuExpanded(!MOBILE_MENU_QUERY.matches);
}

function showLogin() {
  isAuthenticated = false;
  setMenuExpanded(false);
  $("#loginShell").hidden = false;
  $("#appShell").hidden = true;
}

function showApp() {
  const wasHidden = $("#appShell").hidden;
  isAuthenticated = true;
  $("#loginShell").hidden = true;
  $("#appShell").hidden = false;
  if (wasHidden) {
    syncMenuForViewport();
  }
}

function switchView(view) {
  activeView = view;
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === view);
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  $("#viewTitle").textContent = VIEW_TITLES[view] || "Vinted Alerts";
  $("#viewDescription").textContent = VIEW_DESCRIPTIONS[view] || "Surveillance de recherches Vinted.";

  if (MOBILE_MENU_QUERY.matches) {
    setMenuExpanded(false);
  }
}

function showAccountPasswordForm() {
  switchView("account");
  const form = $("#accountPasswordForm");
  const message = $("#accountPasswordMessage");
  form.hidden = false;
  $("#accountPasswordToggle").hidden = true;
  message.textContent = "";
  message.classList.remove("error");
  form.querySelector('input[name="password"]').focus();
}

async function loadState() {
  const state = await api("/api/state");
  latestState = state;
  showApp();
  let dashboardItems = { items: [] };
  try {
    dashboardItems = await api(`/api/dashboard-items?limit=${DASHBOARD_ITEMS_LIMIT}`);
  } catch (error) {
    try {
      const fallback = await api("/api/items?page=1&page_size=50");
      dashboardItems = {
        items: dashboardItemsFromRecent(state.searches, fallback.items || []),
      };
    } catch {
      $("#status").textContent = error.message;
    }
  }
  renderState(state, dashboardItems);
  try {
    const items = await api(`/api/items?page=${itemsPage}&page_size=${ITEMS_PAGE_SIZE}`);
    renderItems(items);
  } catch (error) {
    $("#items").innerHTML = '<p class="empty">Impossible de charger les articles pour le moment.</p>';
    $("#status").textContent = error.message;
  }
  try {
    renderPriceAnalytics(await api("/api/price-analytics"));
  } catch (error) {
    $("#priceAnalytics").innerHTML = '<p class="empty">Impossible de charger l analyse des prix.</p>';
    $("#priceSummary").textContent = error.message;
  }
}

function dashboardItemsFromRecent(searches, items) {
  const searchByName = new Map(searches.map((search) => [String(search.name).toLowerCase(), search]));
  const counts = new Map();
  const normalized = [];
  items.forEach((item) => {
    const search = searchByName.get(String(item.search_name || "").toLowerCase());
    if (!search) return;
    const key = String(search.id);
    const count = counts.get(key) || 0;
    if (count >= DASHBOARD_ITEMS_LIMIT) return;
    counts.set(key, count + 1);
    normalized.push({ ...item, search_id: search.id });
  });
  return normalized;
}

function renderDashboardSearches(container, searches, items) {
  const itemsBySearch = new Map();
  items.forEach((item) => {
    const key = String(item.search_id);
    if (!itemsBySearch.has(key)) itemsBySearch.set(key, []);
    itemsBySearch.get(key).push(item);
  });

  container.innerHTML = searches
    .map((search) => {
      const latestItems = itemsBySearch.get(String(search.id)) || [];
      const meta = [
        `Toutes les ${search.interval_seconds}s`,
        search.last_checked_at ? `verifiee ${escapeHtml(relativeTime(search.last_checked_at))}` : "jamais verifiee",
      ].filter(Boolean).join(" - ");
      const slider = latestItems.length
        ? latestItems.map((item) => `
            <a class="sliderItem" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
              <span class="sliderPhoto">
                ${item.photo_url ? `<img src="${escapeHtml(item.photo_url)}" alt="" loading="lazy" />` : '<span class="noPhoto sliderNoPhoto"></span>'}
                <span class="sliderPrice">${escapeHtml(displayItemPrice(item))}</span>
              </span>
              <strong>${escapeHtml(item.title)}</strong>
              <small class="sliderTime">
                <span class="iconGlyph iconHistory" aria-hidden="true"></span>
                <span data-relative-time="${escapeAttr(item.created_at)}">${escapeHtml(relativeTime(item.created_at))}</span>
              </small>
            </a>
          `).join("")
        : '<p class="empty sliderEmpty">Aucun article detecte pour cette recherche.</p>';
      return `
        <article class="dashboardSearch ${search.enabled ? "isActive" : "isPaused"} ${search.last_error ? "hasError" : ""}">
          <div class="dashboardSearchHeader">
            <div class="dashboardSearchTitle">
              <div class="dashboardSearchTitleLine">
                <strong>${escapeHtml(search.name)}</strong>
                <span class="statusPill ${search.last_error ? "error" : search.enabled ? "success" : "neutral"}">
                  ${search.last_error ? "Erreur" : search.enabled ? "Active" : "En pause"}
                </span>
              </div>
              <small>${meta}</small>
              ${search.last_error ? `<small class="searchError">${escapeHtml(search.last_error)}</small>` : ""}
            </div>
            <button class="searchSwitch" type="button" role="switch" aria-checked="${search.enabled ? "true" : "false"}" data-toggle="${search.id}">
              <span class="switchTrack"><span class="switchThumb"></span></span>
              <span class="switchLabel">${search.enabled ? "Suspendre" : "Activer"}</span>
            </button>
          </div>
          <div class="dashboardSearchUrl">${escapeHtml(search.url)}</div>
          <div class="searchSlider" aria-label="Derniers articles ${escapeAttr(search.name)}">
            ${slider}
          </div>
          <div class="rowActions dashboardActions">
            <button data-edit="${search.id}">Modifier</button>
            <button data-delete="${search.id}" class="danger">Supprimer</button>
          </div>
          <form class="editForm" data-edit-form="${search.id}" hidden>
            <label>
              Nom
              <input name="name" value="${escapeAttr(search.name)}" required />
            </label>
            <label>
              URL Vinted
              <input name="url" value="${escapeAttr(search.url)}" required />
            </label>
            <label>
              Intervalle en secondes
              <input name="interval_seconds" type="number" min="60" value="${search.interval_seconds}" />
            </label>
            <div class="actions">
              <button type="submit">Sauvegarder</button>
              <button type="button" data-cancel-edit="${search.id}">Annuler</button>
            </div>
            <small data-edit-error="${search.id}"></small>
          </form>
        </article>
      `;
    })
    .join("");
}

function renderState(state, dashboardItems = { items: [] }) {
  $("#currentUser").textContent = state.user.username;
  $("#userAvatar").textContent = state.user.username.slice(0, 1) || "?";
  $("#sideCurrentUser").textContent = state.user.username;
  $("#sideUserAvatar").textContent = state.user.username.slice(0, 1) || "?";
  $("#sideUserRole").textContent = state.user.is_admin ? "Administrateur" : "Compte utilisateur";
  $("#accountUsername").textContent = state.user.username;
  $("#accountAvatar").textContent = state.user.username.slice(0, 1) || "?";
  $("#accountRole").textContent = state.user.is_admin ? "Administrateur" : "Utilisateur";
  $("#accountStatus").textContent = state.user.is_admin ? "Compte administrateur" : "Compte utilisateur";
  renderUsers(state);

  const settings = state.settings;
  if (settings.telegram_bot_token) {
    $('[name="telegram_bot_token"]').placeholder = settings.telegram_bot_token;
  }
  if (settings.telegram_chat_id) {
    $('[name="telegram_chat_id"]').placeholder = settings.telegram_chat_id;
  }
  const randomIntervalInput = $('[name="random_interval_percent"]');
  randomIntervalInput.value = settings.random_interval_percent ?? 5;
  updateRandomIntervalLabel(randomIntervalInput.value);

  const runtime = state.runtime;
  const bits = [];
  if (runtime.worker_running) bits.push("surveillance active");
  if (runtime.last_check_finished_at) bits.push(`dernier check: ${runtime.last_check_finished_at}`);
  if (runtime.last_error) bits.push(`erreur: ${runtime.last_error}`);
  $("#status").textContent = bits.join(" · ");
  $("#status").classList.toggle("error", Boolean(runtime.last_error));

  const activeSearches = state.searches.filter((search) => search.enabled).length;
  $("#dashboardActiveSearches").textContent = String(activeSearches);
  $("#dashboardTotalSearches").textContent = String(state.searches.length);
  $("#dashboardLastCheck").textContent = runtime.last_check_finished_at
    ? relativeTime(runtime.last_check_finished_at)
    : "Jamais";
  const telegramReady = Boolean(settings.telegram_bot_token && settings.telegram_chat_id);
  $("#dashboardTelegramStatus").textContent = telegramReady ? "Operationnelles" : "A configurer";
  $("#dashboardTelegramStatus").dataset.tone = telegramReady ? "success" : "warning";

  const searches = $("#searches");
  if (!state.searches.length) {
    searches.innerHTML = `
      <div class="emptyState">
        <span class="emptyStateIcon iconGlyph iconSearch" aria-hidden="true"></span>
        <h3>Aucune recherche pour le moment</h3>
        <p>Ajoute ta premiere recherche pour commencer a recevoir de nouvelles annonces.</p>
      </div>
    `;
    return;
  }

  renderDashboardSearches(searches, state.searches, dashboardItems.items || []);

  searches.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = searches.querySelector(`[data-edit-form="${button.dataset.edit}"]`);
      form.hidden = !form.hidden;
    });
  });

  searches.querySelectorAll("[data-cancel-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = searches.querySelector(`[data-edit-form="${button.dataset.cancelEdit}"]`);
      form.hidden = true;
    });
  });

  searches.querySelectorAll("[data-edit-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const id = form.dataset.editForm;
      const error = searches.querySelector(`[data-edit-error="${id}"]`);
      error.textContent = "";
      try {
        await api(`/api/searches/${id}/save`, {
          method: "POST",
          body: JSON.stringify(formData(form)),
        });
        await loadState();
      } catch (exception) {
        error.textContent = exception.message;
      }
    });
  });

  searches.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/searches/${button.dataset.toggle}/toggle`, { method: "POST", body: "{}" });
      await loadState();
    });
  });

  searches.querySelectorAll("[data-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/searches/${button.dataset.delete}/delete`, { method: "POST", body: "{}" });
      await loadState();
    });
  });
}

function renderUsers(state) {
  const panel = $("#adminPanel");
  const users = $("#users");
  panel.hidden = !state.user.is_admin;
  document.querySelectorAll("[data-admin-only]").forEach((element) => {
    element.hidden = !state.user.is_admin;
  });
  if (!state.user.is_admin) {
    if (activeView === "users") switchView("account");
    return;
  }

  const activeCount = state.users.filter((user) => user.is_active).length;
  const adminCount = state.users.filter((user) => user.is_admin && user.is_active).length;
  $("#usersSummary").textContent = `${activeCount} compte${activeCount > 1 ? "s" : ""} actif${activeCount > 1 ? "s" : ""} · ${adminCount} administrateur${adminCount > 1 ? "s" : ""}`;

  const filter = normalizeFilterText($("#userFilter").value.trim());
  const filteredUsers = state.users.filter((user) => {
    const labels = [
      user.username,
      user.is_admin ? "administrateur admin" : "utilisateur",
      user.is_active ? "actif" : "desactive suspendu",
    ].join(" ");
    return !filter || normalizeFilterText(labels).includes(filter);
  });

  if (!filteredUsers.length) {
    users.innerHTML = `
      <div class="emptyState userEmptyState">
        <span class="emptyStateIcon iconGlyph iconUsers" aria-hidden="true"></span>
        <h3>Aucun utilisateur trouve</h3>
        <p>Modifie le filtre ou cree un nouveau compte.</p>
      </div>
    `;
    return;
  }

  users.innerHTML = filteredUsers
    .map((user) => `
      <article class="userCard ${user.is_active ? "" : "isDisabled"}">
        <header class="userCardHeader">
          <div class="userIdentity">
            <span class="userAvatar">${escapeHtml(user.username.slice(0, 1) || "?")}</span>
            <div>
              <strong>${escapeHtml(user.username)}</strong>
              <span class="userBadges">
                <span class="statusPill ${user.is_admin ? "admin" : "neutral"}">${user.is_admin ? "Administrateur" : "Utilisateur"}</span>
                <span class="statusPill ${user.is_active ? "success" : "error"}">${user.is_active ? "Actif" : "Desactive"}</span>
                ${user.is_system_admin ? '<span class="statusPill system">Compte systeme</span>' : ""}
                ${user.id === state.user.id ? '<span class="statusPill current">Compte actuel</span>' : ""}
              </span>
            </div>
          </div>
          ${user.is_system_admin ? "" : `<button class="linkButton" type="button" data-user-edit-toggle="${user.id}">Modifier</button>`}
        </header>
        <div class="userMetrics">
          <span><small>Recherches</small><strong>${user.search_count}</strong></span>
          <span><small>Sessions</small><strong>${user.session_count}</strong></span>
          <span><small>Derniere connexion</small><strong>${user.last_login_at ? escapeHtml(relativeTime(user.last_login_at)) : "Jamais"}</strong></span>
        </div>
        <small class="userCreatedAt">Compte cree ${escapeHtml(relativeTime(user.created_at))}</small>
        <div class="userCardActions">
          ${user.id === state.user.id ? `
            <span class="muted">Gere ton mot de passe depuis Mon compte.</span>
          ` : `
            <button type="button" data-password-toggle="${user.id}">Mot de passe</button>
            <button type="button" data-user-sessions="${user.id}" ${user.session_count ? "" : "disabled"}>Deconnecter</button>
            ${user.is_system_admin ? "" : `<button type="button" class="danger" data-user-delete="${user.id}">Supprimer</button>`}
          `}
        </div>
        <small class="userActionMessage" data-user-action-message="${user.id}"></small>
        <form class="userEditForm" data-user-edit-form="${user.id}" hidden>
          <label>
            Nom d'utilisateur
            <input name="username" value="${escapeAttr(user.username)}" minlength="3" maxlength="50" required />
          </label>
          <label class="checkboxLabel">
            <input name="is_admin" type="checkbox" ${user.is_admin ? "checked" : ""} ${user.id === state.user.id ? "disabled" : ""} />
            Administrateur
          </label>
          <label class="checkboxLabel">
            <input name="is_active" type="checkbox" ${user.is_active ? "checked" : ""} ${user.id === state.user.id ? "disabled" : ""} />
            Compte actif
          </label>
          ${user.id === state.user.id ? '<input name="is_admin" type="hidden" value="on" /><input name="is_active" type="hidden" value="on" />' : ""}
          <div class="actions">
            <button type="submit">Enregistrer</button>
            <button type="button" data-user-edit-cancel="${user.id}">Annuler</button>
          </div>
          <small data-user-edit-error="${user.id}"></small>
        </form>
        <form class="passwordForm" data-password-form="${user.id}" hidden>
          <label>
            Nouveau mot de passe
            <input name="password" type="password" minlength="6" autocomplete="new-password" required />
          </label>
          <button type="submit">Modifier</button>
          <button type="button" data-password-cancel="${user.id}">Annuler</button>
          <small data-password-error="${user.id}"></small>
        </form>
      </article>
    `)
    .join("");

  users.querySelectorAll("[data-user-edit-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = users.querySelector(`[data-user-edit-form="${button.dataset.userEditToggle}"]`);
      form.hidden = !form.hidden;
    });
  });

  users.querySelectorAll("[data-user-edit-cancel]").forEach((button) => {
    button.addEventListener("click", () => {
      users.querySelector(`[data-user-edit-form="${button.dataset.userEditCancel}"]`).hidden = true;
    });
  });

  users.querySelectorAll("[data-user-edit-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const id = form.dataset.userEditForm;
      const error = users.querySelector(`[data-user-edit-error="${id}"]`);
      error.textContent = "";
      try {
        await api(`/api/users/${id}/save`, {
          method: "POST",
          body: JSON.stringify(formData(form)),
        });
        await loadState();
      } catch (exception) {
        error.textContent = exception.message;
      }
    });
  });

  users.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = users.querySelector(`[data-password-form="${button.dataset.passwordToggle}"]`);
      form.hidden = false;
      button.hidden = true;
      form.querySelector('input[name="password"]').focus();
    });
  });

  users.querySelectorAll("[data-password-cancel]").forEach((button) => {
    button.addEventListener("click", () => {
      const form = users.querySelector(`[data-password-form="${button.dataset.passwordCancel}"]`);
      const toggle = users.querySelector(`[data-password-toggle="${button.dataset.passwordCancel}"]`);
      const error = users.querySelector(`[data-password-error="${button.dataset.passwordCancel}"]`);
      form.reset();
      form.hidden = true;
      toggle.hidden = false;
      error.textContent = "";
      error.classList.remove("error");
    });
  });

  users.querySelectorAll("[data-password-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const id = form.dataset.passwordForm;
      const error = users.querySelector(`[data-password-error="${id}"]`);
      const toggle = users.querySelector(`[data-password-toggle="${id}"]`);
      error.textContent = "";
      try {
        await api(`/api/users/${id}/password`, {
          method: "POST",
          body: JSON.stringify(formData(form)),
        });
        form.reset();
        form.hidden = true;
        toggle.hidden = false;
        error.textContent = "Mot de passe modifié.";
        error.classList.remove("error");
      } catch (exception) {
        error.textContent = exception.message;
        error.classList.add("error");
      }
    });
  });

  users.querySelectorAll("[data-user-sessions]").forEach((button) => {
    button.addEventListener("click", async () => {
      const user = state.users.find((candidate) => String(candidate.id) === button.dataset.userSessions);
      if (!window.confirm(`Deconnecter ${user?.username || "cet utilisateur"} de tous ses appareils ?`)) return;
      const message = users.querySelector(`[data-user-action-message="${button.dataset.userSessions}"]`);
      try {
        await api(`/api/users/${button.dataset.userSessions}/sessions`, { method: "POST", body: "{}" });
        await loadState();
      } catch (error) {
        message.textContent = error.message;
      }
    });
  });

  users.querySelectorAll("[data-user-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const user = state.users.find((candidate) => String(candidate.id) === button.dataset.userDelete);
      const message = `Supprimer definitivement ${user?.username || "cet utilisateur"} et ses recherches ?`;
      if (!window.confirm(message)) return;
      const feedback = users.querySelector(`[data-user-action-message="${button.dataset.userDelete}"]`);
      try {
        await api(`/api/users/${button.dataset.userDelete}/delete`, { method: "POST", body: "{}" });
        await loadState();
      } catch (error) {
        feedback.textContent = error.message;
      }
    });
  });
}

function formatMoney(value, currency = "EUR") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  const amount = Number(value);
  const code = String(currency || "EUR").toUpperCase();
  const formatted = new Intl.NumberFormat("fr-FR", {
    minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
    maximumFractionDigits: Number.isInteger(amount) ? 0 : 2,
  }).format(amount);
  if (code === "EUR") return `${formatted}€`;
  if (code === "USD") return `$${formatted}`;
  if (code === "GBP") return `£${formatted}`;
  return `${formatted} ${code}`;
}

function formatPriceText(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const hasEuro = /€|\bEUR\b/i.test(text);
  const match = text.match(/(\d+(?:[\s\u00a0]?\d{3})*(?:[,.]\d+)?)/);
  if (!match) {
    return hasEuro ? text.replace(/\bEUR\b/gi, "€").replace(/\s+€/g, "€") : text;
  }
  const amount = Number(match[1].replace(/[\s\u00a0]/g, "").replace(",", "."));
  if (Number.isNaN(amount)) {
    return hasEuro ? text.replace(/\bEUR\b/gi, "€").replace(/\s+€/g, "€") : text;
  }
  if (hasEuro) return formatMoney(amount, "EUR");
  return text;
}

function displayItemPrice(item) {
  return formatPriceText(item.price) || "Prix non indique";
}

function displayItemPriceWithFallback(item) {
  return formatPriceText(item.price) || formatMoney(item.price_amount, item.currency);
}

function formatDelta(value) {
  if (value === null || value === undefined) return "";
  const sign = Number(value) > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(1)}%`;
}

function priceArrow(status) {
  if (["deal", "good"].includes(status)) return "↓";
  if (["expensive", "high"].includes(status)) return "↑";
  return "→";
}

function trendArrow(direction) {
  if (direction === "down") return "↓";
  if (direction === "up") return "↑";
  return "→";
}

function renderMiniChart(history, currency) {
  const points = history || [];
  if (!points.length) return '<div class="miniChart emptyChart"></div>';
  const values = points.map((point) => Number(point.median || point.average || 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  return `
    <div class="miniChart" aria-label="Evolution des prix">
      ${points
        .map((point, index) => {
          const value = Number(point.median || point.average || 0);
          const height = 18 + ((value - min) / range) * 62;
          return `
            <span
              style="height:${height}%"
              title="${escapeAttr(point.date)} - mediane ${escapeAttr(formatMoney(value, currency))}"
            >
              <i>${index === points.length - 1 ? escapeHtml(formatMoney(value, currency)) : ""}</i>
            </span>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderPriceAnalytics(data) {
  const container = $("#priceAnalytics");
  const summary = $("#priceSummary");
  const searches = data.searches || [];
  summary.textContent = searches.length ? `${searches.length} famille(s) suivie(s)` : "";
  if (!searches.length) {
    container.innerHTML = '<p class="empty">Ajoute une recherche et laisse quelques articles remonter pour construire une cote.</p>';
    return;
  }

  const deals = data.best_deals || [];
  const dealsHtml = deals.length
    ? `
      <div class="dealStrip">
        ${deals
          .map((item) => `
            <a class="dealChip ${escapeAttr(item.position.status)}" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
              <strong>${escapeHtml(item.title)}</strong>
              <span>${escapeHtml(formatMoney(item.price_amount, item.currency))}</span>
              <small><b class="priceArrow">${escapeHtml(priceArrow(item.position.status))}</b>${escapeHtml(item.search_name)} · ${escapeHtml(item.position.label)} ${escapeHtml(formatDelta(item.position.delta_percent))}</small>
            </a>
          `)
          .join("")}
      </div>
    `
    : '<p class="empty compactEmpty">Aucune bonne affaire nette dans les derniers articles compares.</p>';

  container.innerHTML = `
    <section class="priceOverview">
      <div>
        <h3>Meilleures opportunites</h3>
        ${dealsHtml}
      </div>
    </section>
    <div class="priceGrid">
      ${searches
        .map((search) => `
          <article class="priceCard">
            <header>
              <div>
                <h3>${escapeHtml(search.search_name)}</h3>
                <small>${search.count} article(s) avec prix exploitable</small>
              </div>
              <span class="trendBadge ${escapeAttr(search.trend.direction)}">
                <b class="trendArrow">${escapeHtml(trendArrow(search.trend.direction))}</b>
                ${escapeHtml(search.trend.label)}
                ${search.trend.delta_percent === null ? "" : `<b>${escapeHtml(formatDelta(search.trend.delta_percent))}</b>`}
              </span>
            </header>
            <div class="priceStats">
              <span><small>Mediane</small><strong>${escapeHtml(formatMoney(search.median, search.currency))}</strong></span>
              <span><small>Moyenne</small><strong>${escapeHtml(formatMoney(search.average, search.currency))}</strong></span>
              <span><small>Bas / haut</small><strong>${escapeHtml(formatMoney(search.minimum, search.currency))} - ${escapeHtml(formatMoney(search.maximum, search.currency))}</strong></span>
            </div>
            ${renderMiniChart(search.history, search.currency)}
            <div class="latestPriceItems">
              ${(search.latest_items || [])
                .map((item) => `
                  <a class="priceItem ${escapeAttr(item.position.status)}" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
                    ${item.photo_url ? `<img src="${escapeHtml(item.photo_url)}" alt="" loading="lazy" />` : '<span class="noPhoto"></span>'}
                    <span>
                      <strong>${escapeHtml(item.title)}</strong>
                      <small><b class="priceArrow">${escapeHtml(priceArrow(item.position.status))}</b>${escapeHtml(displayItemPriceWithFallback(item))} · ${escapeHtml(item.position.label)} ${escapeHtml(formatDelta(item.position.delta_percent))}</small>
                    </span>
                  </a>
                `)
                .join("")}
            </div>
          </article>
        `)
        .join("")}
    </div>
  `;
}

function renderItems(data) {
  const container = $("#items");
  const items = data.items || [];
  const summary = $("#itemsSummary");
  const pagination = $("#itemsPagination");
  const pageLabel = $("#itemsPage");
  const prev = $("#itemsPrev");
  const next = $("#itemsNext");

  itemsPage = data.page || 1;
  summary.textContent = data.total ? `${data.total} article(s)` : "";
  pagination.hidden = !data.total || data.total_pages <= 1;
  pageLabel.textContent = `Page ${itemsPage} / ${data.total_pages || 1}`;
  prev.disabled = itemsPage <= 1;
  next.disabled = itemsPage >= (data.total_pages || 1);

  if (!items.length) {
    container.innerHTML = '<p class="empty">Aucun article détecté pour le moment.</p>';
    return;
  }
  container.innerHTML = items
    .map((item) => `
      <a class="item" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
        ${item.photo_url ? `<img src="${escapeHtml(item.photo_url)}" alt="" loading="lazy" />` : '<div class="noPhoto"></div>'}
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml(displayItemPrice(item).replace("Prix non indique", "Prix non indiqué"))}</span>
          <small>${escapeHtml(item.search_name)} · ${escapeHtml(item.created_at)}</small>
        </div>
      </a>
    `)
    .join("");

  refreshRelativeTimes();
}

$("#itemsPrev").addEventListener("click", async () => {
  if (itemsPage <= 1) return;
  itemsPage -= 1;
  await loadState();
});

$("#itemsNext").addEventListener("click", async () => {
  itemsPage += 1;
  await loadState();
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

menuControls().forEach((button) => {
  button.addEventListener("click", () => {
    setMenuExpanded(document.body.classList.contains("menuCollapsed"));
  });
});

$("#menuBackdrop").addEventListener("click", () => setMenuExpanded(false));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && MOBILE_MENU_QUERY.matches) {
    setMenuExpanded(false);
  }
});

function toggleTheme() {
  const nextTheme = document.body.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(nextTheme);
}

$("#themeToggle").addEventListener("click", toggleTheme);

$("#sideThemeToggle").addEventListener("click", toggleTheme);

applyTheme(localStorage.getItem("vinted_theme"));
syncMenuForViewport();

if (MOBILE_MENU_QUERY.addEventListener) {
  MOBILE_MENU_QUERY.addEventListener("change", syncMenuForViewport);
} else {
  MOBILE_MENU_QUERY.addListener(syncMenuForViewport);
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("#loginError");
  error.textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify(formData(event.target)) });
    localStorage.removeItem("vinted_session_token");
    event.target.password.value = "";
    await loadState();
  } catch (exception) {
    error.textContent = exception.message;
  }
});

async function logout() {
  await api("/api/logout", { method: "POST", body: "{}" });
  localStorage.removeItem("vinted_session_token");
  showLogin();
}

$("#logout").addEventListener("click", logout);
$("#sideLogout").addEventListener("click", logout);

function setUserFormOpen(open) {
  const form = $("#userForm");
  const toggle = $("#userFormToggle");
  const error = $("#userError");
  form.hidden = !open;
  toggle.hidden = open;
  if (open) {
    error.textContent = "";
    form.querySelector('input[name="username"]').focus();
    return;
  }
  form.reset();
  error.textContent = "";
}

$("#userFormToggle").addEventListener("click", () => setUserFormOpen(true));
$("#userFormCancel").addEventListener("click", () => setUserFormOpen(false));
$("#userFilter").addEventListener("input", () => {
  if (latestState) renderUsers(latestState);
});

$("#userForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("#userError");
  error.textContent = "";
  try {
    await api("/api/users", { method: "POST", body: JSON.stringify(formData(event.target)) });
    setUserFormOpen(false);
    await loadState();
  } catch (exception) {
    error.textContent = exception.message;
  }
});

$("#accountPasswordToggle").addEventListener("click", showAccountPasswordForm);

$("#accountSecurityShortcut").addEventListener("click", showAccountPasswordForm);

$("#accountPasswordCancel").addEventListener("click", () => {
  const form = $("#accountPasswordForm");
  const message = $("#accountPasswordMessage");
  form.reset();
  form.hidden = true;
  $("#accountPasswordToggle").hidden = false;
  message.textContent = "";
  message.classList.remove("error");
});

$("#accountPasswordForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const message = $("#accountPasswordMessage");
  message.textContent = "";
  message.classList.remove("error");
  try {
    await api("/api/account/password", {
      method: "POST",
      body: JSON.stringify(formData(form)),
    });
    form.reset();
    form.hidden = true;
    $("#accountPasswordToggle").hidden = false;
    message.textContent = "Mot de passe modifiÃ©.";
  } catch (exception) {
    message.textContent = exception.message;
    message.classList.add("error");
  }
});

$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(formData(event.target)) });
    event.target.reset();
    showTelegramHelp("Paramètres Telegram enregistrés.");
    await loadState();
  } catch (error) {
    showTelegramHelp(error.message, true);
  }
});

$("#clearTelegram").addEventListener("click", async () => {
  if (!window.confirm("Effacer le token Telegram et le Chat ID ?")) return;
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ clear_telegram_settings: true }),
    });
    $("#settingsForm").reset();
    $('[name="telegram_bot_token"]').placeholder = "123456:ABC...";
    $('[name="telegram_chat_id"]').placeholder = "123456789";
    showTelegramHelp("Configuration Telegram effacee.");
    await loadState();
  } catch (error) {
    showTelegramHelp(error.message, true);
  }
});

$("#appSettingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(formData(event.target)) });
    showAppSettingsHelp("Parametres enregistres.");
    await loadState();
  } catch (error) {
    showAppSettingsHelp(error.message, true);
  }
});

$('[name="random_interval_percent"]').addEventListener("input", (event) => {
  updateRandomIntervalLabel(event.target.value);
});

$("#searchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const help = $("#searchFormHelp");
  help.textContent = "";
  help.classList.remove("error");
  try {
    await api("/api/searches", { method: "POST", body: JSON.stringify(buildSearchPayload(form)) });
    form.reset();
    form.querySelector('[name="interval_seconds"]').value = 180;
    form.querySelector('[name="search_mode"][value="manual"]').checked = true;
    syncSearchMode();
    help.textContent = "Recherche ajoutee.";
    await loadState();
  } catch (error) {
    help.textContent = error.message;
    help.classList.add("error");
  }
});

document.querySelectorAll('[name="search_mode"]').forEach((input) => {
  input.addEventListener("change", syncSearchMode);
});
syncSearchMode();

async function checkNow() {
  const buttons = document.querySelectorAll(".checkNowButton");
  buttons.forEach((button) => {
    button.disabled = true;
    button.dataset.defaultText = button.dataset.defaultText || button.textContent;
    button.textContent = "Verification...";
  });
  try {
    const result = await api("/api/check-now", { method: "POST", body: "{}" });
    buttons.forEach((button) => {
      button.textContent = `${result.new_items} nouveau(x)`;
    });
    await loadState();
  } catch (error) {
    buttons.forEach((button) => {
      button.textContent = "Erreur";
    });
    showCheckNowError(error.message);
    try {
      await loadState();
    } catch {
      // Keep the direct error message visible if the state refresh also fails.
    }
  } finally {
    setTimeout(() => {
      buttons.forEach((button) => {
        button.disabled = false;
        button.textContent = button.dataset.defaultText || "Verifier maintenant";
      });
    }, 1500);
  }
}

document.querySelectorAll(".checkNowButton").forEach((button) => {
  button.addEventListener("click", checkNow);
});

$("#testTelegram").addEventListener("click", async () => {
  try {
    await api("/api/telegram/test", { method: "POST", body: "{}" });
    showTelegramHelp("Message de test envoyé.");
  } catch (error) {
    showTelegramHelp(error.message, true);
  }
});

$("#findChat").addEventListener("click", async () => {
  const button = $("#findChat");
  button.disabled = true;
  button.textContent = "Recherche...";
  try {
    const settings = formData($("#settingsForm"));
    if (settings.telegram_bot_token || settings.telegram_chat_id) {
      await api("/api/settings", { method: "POST", body: JSON.stringify(settings) });
    }

    const data = await api("/api/telegram/updates");
    if (!data.updates.length) {
      showTelegramHelp("Aucun Chat ID trouvé. Ouvre ton bot Telegram, envoie /start ou un message, puis reclique ici.");
      return;
    }
    const latest = data.updates[data.updates.length - 1];
    $('[name="telegram_chat_id"]').value = latest.chat_id;
    showTelegramHelp(`Chat ID trouvé: ${latest.chat_id}. Clique sur Enregistrer, puis Tester.`);
  } catch (error) {
    showTelegramHelp(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Trouver mon Chat ID";
  }
});

loadState().catch((error) => {
  $("#status").textContent = error.message;
});

setInterval(() => {
  if (!isAuthenticated) return;
  loadState().catch((error) => {
    $("#status").textContent = error.message;
  });
}, 15000);

setInterval(() => {
  if (!isAuthenticated) return;
  refreshRelativeTimes();
}, 30000);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  });
}
