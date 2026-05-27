const $ = (selector) => document.querySelector(selector);
const ITEMS_PAGE_SIZE = 12;
let itemsPage = 1;
let isAuthenticated = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text || "Réponse serveur illisible" };
  }
  if (!response.ok || data.ok === false) {
    if (response.status === 401 || data.authenticated === false) {
      showLogin();
    }
    throw new Error(data.error || "Erreur inconnue");
  }
  return data;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
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

function showTelegramHelp(message, isError = false) {
  const help = $("#telegramHelp");
  help.textContent = message;
  help.classList.toggle("error", isError);
}

function showLogin() {
  isAuthenticated = false;
  $("#loginShell").hidden = false;
  $("#appShell").hidden = true;
}

function showApp() {
  isAuthenticated = true;
  $("#loginShell").hidden = true;
  $("#appShell").hidden = false;
}

async function loadState() {
  const state = await api("/api/state");
  const items = await api(`/api/items?page=${itemsPage}&page_size=${ITEMS_PAGE_SIZE}`);
  showApp();
  renderState(state);
  renderItems(items);
}

function renderState(state) {
  $("#currentUser").textContent = `Connecté: ${state.user.username}`;
  renderUsers(state);

  const settings = state.settings;
  if (settings.telegram_bot_token) {
    $('[name="telegram_bot_token"]').placeholder = settings.telegram_bot_token;
  }
  if (settings.telegram_chat_id) {
    $('[name="telegram_chat_id"]').placeholder = settings.telegram_chat_id;
  }

  const runtime = state.runtime;
  const bits = [];
  if (runtime.worker_running) bits.push("surveillance active");
  if (runtime.last_check_finished_at) bits.push(`dernier check: ${runtime.last_check_finished_at}`);
  if (runtime.last_error) bits.push(`erreur: ${runtime.last_error}`);
  $("#status").textContent = bits.join(" · ");

  const searches = $("#searches");
  if (!state.searches.length) {
    searches.innerHTML = '<p class="empty">Aucune recherche pour le moment.</p>';
    return;
  }

  searches.innerHTML = state.searches
    .map((search) => `
      <div class="row">
        <div>
          <strong>${escapeHtml(search.name)}</strong>
          <span>${escapeHtml(search.url)}</span>
          <small>
            ${search.enabled ? "Active" : "Pause"} · toutes les ${search.interval_seconds}s
            ${search.last_checked_at ? ` · dernier check ${escapeHtml(search.last_checked_at)}` : ""}
            ${search.last_error ? ` · erreur ${escapeHtml(search.last_error)}` : ""}
          </small>
        </div>
        <div class="rowActions">
          <button data-edit="${search.id}">Modifier</button>
          <button data-toggle="${search.id}">${search.enabled ? "Pause" : "Activer"}</button>
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
      </div>
    `)
    .join("");

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
  if (!state.user.is_admin) return;

  $("#usersSummary").textContent = `${state.users.length} utilisateur(s)`;
  users.innerHTML = state.users
    .map((user) => `
      <div class="row compactRow">
        <div>
          <strong>${escapeHtml(user.username)}</strong>
          <small>${user.is_admin ? "Admin" : "Utilisateur"} · créé le ${escapeHtml(user.created_at)}</small>
        </div>
      </div>
    `)
    .join("");
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
          <span>${escapeHtml(item.price || "Prix non indiqué")}</span>
          <small>${escapeHtml(item.search_name)} · ${escapeHtml(item.created_at)}</small>
        </div>
      </a>
    `)
    .join("");
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

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("#loginError");
  error.textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify(formData(event.target)) });
    event.target.password.value = "";
    await loadState();
  } catch (exception) {
    error.textContent = exception.message;
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  showLogin();
});

$("#userForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("#userError");
  error.textContent = "";
  try {
    await api("/api/users", { method: "POST", body: JSON.stringify(formData(event.target)) });
    event.target.reset();
    await loadState();
  } catch (exception) {
    error.textContent = exception.message;
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

$("#searchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/api/searches", { method: "POST", body: JSON.stringify(formData(event.target)) });
  event.target.reset();
  $('[name="interval_seconds"]').value = 180;
  await loadState();
});

$("#checkNow").addEventListener("click", async () => {
  const button = $("#checkNow");
  button.disabled = true;
  button.textContent = "Vérification...";
  try {
    const result = await api("/api/check-now", { method: "POST", body: "{}" });
    button.textContent = `${result.new_items} nouveau(x)`;
    await loadState();
  } finally {
    setTimeout(() => {
      button.disabled = false;
      button.textContent = "Vérifier maintenant";
    }, 1500);
  }
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
