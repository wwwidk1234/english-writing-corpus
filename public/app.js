/* 英语写作语料库 · 前端逻辑
 *
 * 两种运行模式（由 config.js 决定）：
 *   local  —— 连接本地 FastAPI 后端，支持增删改查、分页、搜索、标签筛选
 *   static —— 读取 public/api 下的静态 JSON，只读浏览（搜索/分页/筛选在浏览器内完成）
 */

(function () {
  "use strict";

  var CONFIG = window.APP_CONFIG || { mode: "local", apiBase: "http://127.0.0.1:8000/api" };
  var MODE = CONFIG.mode === "static" ? "static" : "local";
  var API_BASE = String(CONFIG.apiBase || "/api").replace(/\/+$/, "");
  var IS_LOCAL = MODE === "local";

  var MODULES = ["practical", "continuous"];
  var DEFAULT_PAGE_SIZE = 20;

  // ---------------------------------------------------------------- 全局状态

  var state = {
    module: MODULES[0],
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    q: "",
    scope: "all",
    tag: "",
    total: 0,
    totalPages: 1,
    items: [],
    editingId: null
  };

  var requestSeq = 0;      // 只接受最新一次请求的结果
  var staticCache = {};    // { module: { sentences, tags } }

  // ---------------------------------------------------------------- DOM

  var el = {};

  function cacheDom() {
    [
      "tabs", "alert", "search-input", "scope-select", "tag-select", "new-btn",
      "result-meta", "mode-badge", "list", "prev-btn", "next-btn", "pager-info",
      "modal", "modal-title", "sentence-form", "form-content", "form-translation",
      "form-tags", "form-error", "cancel-btn", "submit-btn",
      "tag-modal", "tag-search-btn", "tag-search-input", "tag-search-list",
      "tag-search-hint", "tag-clear-btn", "tag-done-btn"
    ].forEach(function (id) {
      el[id] = document.getElementById(id);
    });
  }

  // ---------------------------------------------------------------- 工具函数

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  var alertTimer = null;

  function showError(message) {
    if (!el.alert) { return; }
    el.alert.textContent = message;
    el.alert.hidden = false;
    if (alertTimer) { clearTimeout(alertTimer); }
    alertTimer = setTimeout(function () {
      el.alert.hidden = true;
      el.alert.textContent = "";
    }, 3000);
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  /** 拆分标签输入：中英文逗号（含顿号）分隔，去空、去重。 */
  function parseTags(text) {
    var seen = {}, out = [];
    String(text || "")
      .replace(/[，、]/g, ",")
      .split(",")
      .forEach(function (part) {
        var name = part.trim();
        if (name && !seen[name]) { seen[name] = true; out.push(name); }
      });
    return out;
  }

  // ---------------------------------------------------------------- 数据源：本地模式

  function buildPath(path, params) {
    var url = path;
    if (params) {
      var parts = [];
      Object.keys(params).forEach(function (key) {
        var value = params[key];
        if (value === "" || value == null) { return; }
        parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(value));
      });
      if (parts.length) { url += "?" + parts.join("&"); }
    }
    return url;
  }

  function request(path, options) {
    var opts = options || {};
    return fetch(API_BASE + path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (err) { data = null; }
        }
        if (!response.ok) {
          throw new Error(extractError(data, response.status));
        }
        return data;
      });
    }, function () {
      throw new Error("无法连接本地后端，请确认已运行 python main.py");
    });
  }

  function extractError(data, status) {
    if (data && typeof data.detail === "string") { return data.detail; }
    if (data && Array.isArray(data.detail) && data.detail.length) {
      var first = data.detail[0];
      var field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "";
      return "请求参数有误" + (field ? "：" + field + " " : " ") + (first.msg || "");
    }
    return "请求失败（HTTP " + status + "）";
  }

  // ---------------------------------------------------------------- 数据源：静态模式

  function loadStatic(module) {
    if (staticCache[module]) { return Promise.resolve(staticCache[module]); }
    // file:// 下浏览器禁止 fetch 本地 JSON，改用 <script> 加载 data.js（由 build_static.py 生成）
    if (location.protocol === "file:") { return loadScriptData(module); }
    return Promise.all([
      fetch("api/" + module + "/sentences.json", { cache: "no-cache" }).then(ensureOk),
      fetch("api/" + module + "/tags.json", { cache: "no-cache" }).then(ensureOk)
    ]).then(function (results) {
      var payload = { sentences: results[0] || [], tags: results[1] || [] };
      staticCache[module] = payload;
      return payload;
    });
  }

  function loadScriptData(module) {
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = "api/" + module + "/data.js";
      script.onload = function () {
        var data = window.__CORPUS_DATA__ && window.__CORPUS_DATA__[module];
        if (data && data.sentences) {
          staticCache[module] = data;
          resolve(data);
        } else {
          reject(new Error("data.js 里没有 " + module + " 的数据，请重新运行 python build_static.py"));
        }
      };
      script.onerror = function () {
        reject(new Error("无法加载 api/" + module + "/data.js，请先运行 python build_static.py 生成静态数据"));
      };
      document.head.appendChild(script);
    });
  }

  function ensureOk(response) {
    if (!response.ok) {
      throw new Error("静态数据加载失败（HTTP " + response.status + "），请先运行 python build_static.py");
    }
    return response.json();
  }

  /** 把底层报错翻译成人话（尤其是 file:// 双击打开的场景）。 */
  function friendlyError(err) {
    var msg = (err && err.message) || String(err);
    if (msg === "Failed to fetch" || msg.indexOf("NetworkError") !== -1 || msg.indexOf("Failed to fetch") !== -1) {
      if (location.protocol === "file:") {
        return "当前是直接双击打开的页面（file://），浏览器会阻止读取本地数据。两种解决办法："
          + "① 静态浏览：在项目目录运行 python -m http.server 8300，然后访问 http://127.0.0.1:8300；"
          + "② 本地编辑：运行 python main.py 后把 config.js 切回本地模式。详见 导入教程.md。";
      }
      return "网络请求失败：静态模式需要先运行 python build_static.py 生成数据，并通过 HTTP 访问页面（不要用 file:// 直接打开）。";
    }
    return msg;
  }

  function staticFilter(payload, query) {
    var keyword = query.q.trim().toLowerCase();
    var items = payload.sentences.slice();

    if (keyword) {
      items = items.filter(function (item) {
        var content = String(item.content || "").toLowerCase();
        var translation = String(item.translation || "").toLowerCase();
        if (query.scope === "content") { return content.indexOf(keyword) !== -1; }
        if (query.scope === "translation") { return translation.indexOf(keyword) !== -1; }
        return content.indexOf(keyword) !== -1 || translation.indexOf(keyword) !== -1;
      });
    }

    if (query.tag) {
      items = items.filter(function (item) {
        return (item.tags || []).indexOf(query.tag) !== -1;
      });
    }

    items.sort(function (a, b) { return b.id - a.id; });

    var pageSize = query.pageSize || DEFAULT_PAGE_SIZE;
    var total = items.length;
    var totalPages = total ? Math.ceil(total / pageSize) : 0;
    var start = (query.page - 1) * pageSize;

    return {
      items: items.slice(start, start + pageSize),
      total: total,
      page: query.page,
      page_size: pageSize,
      total_pages: totalPages
    };
  }

  // ---------------------------------------------------------------- 统一读取接口

  function currentQuery() {
    return {
      page: state.page,
      page_size: state.pageSize,
      q: state.q,
      scope: state.scope,
      tag: state.tag,
      sort: "id_desc"
    };
  }

  function loadList() {
    var seq = ++requestSeq;

    if (!IS_LOCAL) {
      return loadStatic(state.module).then(function (payload) {
        if (seq !== requestSeq) { return; }
        applyList(staticFilter(payload, currentQuery()));
      }).catch(function (err) {
        if (seq !== requestSeq) { return; }
        showError(friendlyError(err));
        applyList({ items: [], total: 0, page: state.page, total_pages: 0 });
      });
    }

    return request(buildPath("/" + state.module + "/sentences", currentQuery()))
      .then(function (data) {
        if (seq !== requestSeq) { return; }
        applyList(data);
      })
      .catch(function (err) {
        if (seq !== requestSeq) { return; }
        showError(friendlyError(err));
        applyList({ items: [], total: 0, page: state.page, total_pages: 0 });
      });
  }

  function applyList(data) {
    state.items = (data && data.items) || [];
    state.total = (data && data.total) || 0;
    state.totalPages = Math.max(1, (data && data.total_pages) || 0);
    if (state.page > state.totalPages) {
      state.page = state.totalPages;
    }
    renderList();
    renderPager();
    renderMeta();
    renderTagOptions(null);
  }

  // ---------------------------------------------------------------- 标签

  function loadTags() {
    tagIndexCache[state.module] = null;   // 数据可能已变化，弹窗下次打开时重新拉取
    if (!IS_LOCAL) {
      return loadStatic(state.module).then(function (payload) {
        renderTagOptions((payload.tags || []).map(function (entry) {
          return entry && entry.tag != null ? entry.tag : String(entry);
        }));
      }).catch(function (err) {
        showError(friendlyError(err));
      });
    }

    return request("/" + state.module + "/tags").then(function (data) {
      renderTagOptions((data && data.tags) || []);
    }).catch(function (err) {
      showError(friendlyError(err));
    });
  }

  function renderTagOptions(tags) {
    if (!el["tag-select"]) { return; }
    var select = el["tag-select"];

    if (tags) {
      var options = ['<option value="">全部标签</option>'];
      tags.forEach(function (tag) {
        options.push('<option value="' + escapeHtml(tag) + '">' + escapeHtml(tag) + "</option>");
      });
      select.innerHTML = options.join("");
      select.value = state.tag;
      return;
    }

    // 静态模式：从当前分区全量数据里统计标签
    if (!IS_LOCAL && staticCache[state.module]) {
      var counter = {};
      staticCache[state.module].sentences.forEach(function (item) {
        (item.tags || []).forEach(function (tag) {
          counter[tag] = (counter[tag] || 0) + 1;
        });
      });
      var names = Object.keys(counter).sort(function (a, b) {
        return counter[b] - counter[a] || a.localeCompare(b, "zh-Hans-CN");
      });
      select.innerHTML = ['<option value="">全部标签</option>']
        .concat(names.map(function (tag) {
          return '<option value="' + escapeHtml(tag) + '">' + escapeHtml(tag) + "</option>";
        })).join("");
      select.value = state.tag;
    }
  }

  // -------------------------------------------------------- 标签搜索弹窗

  var tagIndexCache = {};   // { module: [{tag, count|null}] }，count 为 null 表示本地模式无统计

  /** 拉取当前分区全部标签（带可选的出现次数），结果按模块缓存。 */
  function getTagIndex() {
    if (tagIndexCache[state.module]) {
      return Promise.resolve(tagIndexCache[state.module]);
    }
    if (!IS_LOCAL) {
      return loadStatic(state.module).then(function (payload) {
        var list = (payload.tags || []).map(function (entry) {
          return entry && entry.tag != null
            ? { tag: String(entry.tag), count: (entry.count == null ? null : Number(entry.count)) }
            : { tag: String(entry), count: null };
        });
        tagIndexCache[state.module] = list;
        return list;
      });
    }
    return request("/" + state.module + "/tags").then(function (data) {
      var list = ((data && data.tags) || []).map(function (tag) {
        return { tag: String(tag), count: null };
      });
      tagIndexCache[state.module] = list;
      return list;
    });
  }

  function openTagModal() {
    if (!el["tag-modal"]) { return; }
    el["tag-search-input"].value = "";
    el["tag-modal"].hidden = false;
    el["tag-search-input"].focus();
    renderTagSearchList("");                      // 先用缓存立即渲染
    getTagIndex().then(function () {
      renderTagSearchList(el["tag-search-input"].value);
    }).catch(function (err) {
      el["tag-modal"].hidden = true;
      showError(friendlyError(err));
    });
  }

  function closeTagModal() {
    if (el["tag-modal"]) { el["tag-modal"].hidden = true; }
  }

  function renderTagSearchList(keyword) {
    if (!el["tag-search-list"]) { return; }
    var list = tagIndexCache[state.module] || [];
    var kw = String(keyword || "").trim().toLowerCase();
    var filtered = kw
      ? list.filter(function (entry) { return entry.tag.toLowerCase().indexOf(kw) !== -1; })
      : list;

    if (el["tag-search-hint"]) {
      el["tag-search-hint"].textContent = !list.length
        ? ""
        : (kw
          ? "匹配 " + filtered.length + " / " + list.length + " 个标签"
          : "共 " + list.length + " 个标签，点击即可筛选");
    }

    if (!filtered.length) {
      el["tag-search-list"].innerHTML = list.length
        ? '<div class="tag-search-empty">没有匹配「' + escapeHtml(kw) + "」的标签</div>"
        : '<div class="tag-search-empty">暂无标签数据</div>';
      return;
    }

    el["tag-search-list"].innerHTML = filtered.map(function (entry) {
      var active = entry.tag === state.tag ? " is-active" : "";
      var count = entry.count == null ? "" :
        '<span class="tag-chip-count">' + entry.count + "</span>";
      return '<button type="button" class="tag-chip' + active + '" data-tag="' +
        escapeHtml(entry.tag) + '">' + escapeHtml(entry.tag) + count + "</button>";
    }).join("");
  }

  function applyTagSelection(tag) {
    state.tag = tag || "";
    if (el["tag-select"]) { el["tag-select"].value = state.tag; }
    closeTagModal();
    applyFilters();
  }

  // ---------------------------------------------------------------- 渲染

  function renderList() {
    if (!el.list) { return; }

    if (!state.items.length) {
      el.list.innerHTML = '<div class="empty">' +
        (state.total === 0 && !state.q && !state.tag ? "该分区暂无语料，去 raw/ 导入或点击「+ 新增」。" : "没有匹配的结果，试试调整关键词或标签。") +
        "</div>";
      return;
    }

    var rows = state.items.map(function (item) {
      var content = item.content == null ? "" : item.content;
      var translation = item.translation == null ? "" : item.translation;
      var tags = item.tags || [];

      var translationHtml = translation
        ? '<p class="card-translation">' + escapeHtml(translation) + "</p>"
        : '<p class="card-translation is-empty">（暂无译文）</p>';

      var tagsHtml = tags.map(function (tag) {
        return '<button type="button" class="tag" data-tag="' + escapeHtml(tag) + '">' +
          escapeHtml(tag) + "</button>";
      }).join("");

      var actionsHtml = IS_LOCAL
        ? '<div class="card-actions">' +
            '<button type="button" class="btn btn-small" data-action="edit" data-id="' + item.id + '">编辑</button>' +
            '<button type="button" class="btn btn-small btn-danger" data-action="delete" data-id="' + item.id + '">删除</button>' +
          "</div>"
        : "";

      return '<article class="card" data-id="' + item.id + '">' +
        '<p class="card-content">' + escapeHtml(content) + "</p>" +
        translationHtml +
        '<div class="card-footer">' +
          '<span class="card-id">#' + item.id + "</span>" +
          '<div class="tags">' + tagsHtml + "</div>" +
          actionsHtml +
        "</div>" +
      "</article>";
    });

    el.list.innerHTML = rows.join("");
  }

  function renderPager() {
    if (el["pager-info"]) {
      el["pager-info"].textContent = "第 " + state.page + " / " + state.totalPages + " 页 · 共 " + state.total + " 条";
    }
    if (el["prev-btn"]) { el["prev-btn"].disabled = state.page <= 1; }
    if (el["next-btn"]) { el["next-btn"].disabled = state.page >= state.totalPages; }
  }

  var SCOPE_LABEL = { all: "全部", content: "仅原文", translation: "仅译文" };

  function renderMeta() {
    if (!el["result-meta"]) { return; }
    var bits = ["共 " + state.total + " 条"];
    if (state.q) { bits.push("关键词「" + state.q + "」(" + SCOPE_LABEL[state.scope] + ")"); }
    if (state.tag) { bits.push("标签「" + state.tag + "」"); }
    el["result-meta"].textContent = bits.join(" · ");
  }

  function renderMode() {
    if (el["mode-badge"]) {
      el["mode-badge"].textContent = IS_LOCAL ? "本地编辑模式" : "只读浏览模式";
    }
    if (el["new-btn"]) { el["new-btn"].hidden = !IS_LOCAL; }
  }

  function renderTabs() {
    if (!el.tabs) { return; }
    Array.prototype.forEach.call(el.tabs.querySelectorAll(".tab"), function (tab) {
      var active = tab.getAttribute("data-module") === state.module;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function renderTheme() {
    document.body.classList.toggle("theme-practical", state.module === "practical");
    document.body.classList.toggle("theme-continuous", state.module === "continuous");
  }

  // ---------------------------------------------------------------- 交互

  function switchModule(module) {
    if (MODULES.indexOf(module) === -1 || module === state.module) { return; }
    state.module = module;
    state.page = 1;
    state.tag = "";
    if (el["tag-select"]) { el["tag-select"].value = ""; }
    renderTheme();
    renderTabs();
    loadTags();
    loadList();
  }

  function gotoPage(page) {
    if (page < 1 || page > state.totalPages || page === state.page) { return; }
    state.page = page;
    loadList();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function applyFilters() {
    state.page = 1;
    loadList();
  }

  // ---------------------------------------------------------------- 表单

  function openForm(item) {
    state.editingId = item ? item.id : null;
    el["modal-title"].textContent = item ? "编辑语料 #" + item.id : "新增语料";
    el["form-content"].value = item ? (item.content || "") : "";
    el["form-translation"].value = item ? (item.translation || "") : "";
    el["form-tags"].value = item ? (item.tags || []).join("，") : "";
    el["form-error"].hidden = true;
    el["form-error"].textContent = "";
    el.modal.hidden = false;
    el["form-content"].focus();
  }

  function closeForm() {
    state.editingId = null;
    el.modal.hidden = true;
    el["sentence-form"].reset();
  }

  function submitForm(event) {
    event.preventDefault();
    var content = el["form-content"].value.trim();
    var translation = el["form-translation"].value.trim();
    var tags = parseTags(el["form-tags"].value);

    if (!content) {
      el["form-error"].textContent = "英文原文不能为空。";
      el["form-error"].hidden = false;
      return;
    }

    var payload = { content: content, translation: translation || null, tags: tags };
    var editingId = state.editingId;
    var path = "/" + state.module + "/sentences" + (editingId ? "/" + editingId : "");

    el["submit-btn"].disabled = true;
    el["cancel-btn"].disabled = true;

    fetch(API_BASE + path, {
      method: editingId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
        if (!response.ok) { throw new Error(extractError(data, response.status)); }
        return data;
      });
    }, function () {
      throw new Error("无法连接本地后端，请确认已运行 python main.py");
    }).then(function () {
      closeForm();
      return Promise.all([loadTags(), loadList()]);
    }).catch(function (err) {
      el["form-error"].textContent = friendlyError(err);
      el["form-error"].hidden = false;
    }).then(function () {
      el["submit-btn"].disabled = false;
      el["cancel-btn"].disabled = false;
    });
  }

  function deleteItem(itemId) {
    if (!window.confirm("确定删除 #" + itemId + " 这条语料吗？此操作会先生成备份。")) { return; }
    request("/" + state.module + "/sentences/" + itemId, { method: "DELETE" })
      .then(function () {
        return Promise.all([loadTags(), loadList()]);
      })
      .catch(function (err) {
        showError(friendlyError(err));
      });
  }

  // ---------------------------------------------------------------- 事件绑定

  function bindEvents() {
    if (el.tabs) {
      el.tabs.addEventListener("click", function (event) {
        var tab = event.target.closest(".tab");
        if (tab) { switchModule(tab.getAttribute("data-module")); }
      });
    }

    if (el["search-input"]) {
      var onSearch = debounce(function () {
        state.q = el["search-input"].value.trim();
        applyFilters();
      }, 300);
      el["search-input"].addEventListener("input", onSearch);
    }

    if (el["scope-select"]) {
      el["scope-select"].addEventListener("change", function () {
        state.scope = el["scope-select"].value;
        applyFilters();
      });
    }

    if (el["tag-select"]) {
      el["tag-select"].addEventListener("change", function () {
        state.tag = el["tag-select"].value;
        applyFilters();
      });
    }

    if (el["new-btn"]) {
      el["new-btn"].addEventListener("click", function () { openForm(null); });
    }

    if (el["cancel-btn"]) {
      el["cancel-btn"].addEventListener("click", closeForm);
    }

    if (el["modal"]) {
      el["modal"].addEventListener("click", function (event) {
        if (event.target === el["modal"]) { closeForm(); }
      });
    }

    if (el["tag-search-btn"]) {
      el["tag-search-btn"].addEventListener("click", openTagModal);
    }
    if (el["tag-search-input"]) {
      el["tag-search-input"].addEventListener("input", function () {
        renderTagSearchList(el["tag-search-input"].value);
      });
    }
    if (el["tag-search-list"]) {
      el["tag-search-list"].addEventListener("click", function (event) {
        var chip = event.target.closest(".tag-chip");
        if (chip) { applyTagSelection(chip.getAttribute("data-tag")); }
      });
    }
    if (el["tag-clear-btn"]) {
      el["tag-clear-btn"].addEventListener("click", function () { applyTagSelection(""); });
    }
    if (el["tag-done-btn"]) {
      el["tag-done-btn"].addEventListener("click", closeTagModal);
    }
    if (el["tag-modal"]) {
      el["tag-modal"].addEventListener("click", function (event) {
        if (event.target === el["tag-modal"]) { closeTagModal(); }
      });
    }

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        if (el.modal && !el.modal.hidden) { closeForm(); }
        if (el["tag-modal"] && !el["tag-modal"].hidden) { closeTagModal(); }
      }
    });

    if (el["sentence-form"]) {
      el["sentence-form"].addEventListener("submit", submitForm);
    }

    if (el.list) {
      el.list.addEventListener("click", function (event) {
        var tagBtn = event.target.closest(".tag");
        if (tagBtn) {
          state.tag = tagBtn.getAttribute("data-tag") || "";
          if (el["tag-select"]) { el["tag-select"].value = state.tag; }
          applyFilters();
          return;
        }

        var actionBtn = event.target.closest("[data-action]");
        if (!actionBtn) { return; }
        var itemId = parseInt(actionBtn.getAttribute("data-id"), 10);
        var action = actionBtn.getAttribute("data-action");

        if (action === "edit") {
          var item = state.items.filter(function (it) { return it.id === itemId; })[0];
          if (item) { openForm(item); }
        } else if (action === "delete") {
          deleteItem(itemId);
        }
      });
    }

    if (el["prev-btn"]) {
      el["prev-btn"].addEventListener("click", function () { gotoPage(state.page - 1); });
    }
    if (el["next-btn"]) {
      el["next-btn"].addEventListener("click", function () { gotoPage(state.page + 1); });
    }
  }

  // ---------------------------------------------------------------- 启动

  function init() {
    cacheDom();
    renderMode();
    renderTheme();
    renderTabs();
    bindEvents();
    loadTags();
    loadList();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
