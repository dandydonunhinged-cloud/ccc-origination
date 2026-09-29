// DanDon Media Studio — shared client helpers.
// Every editable control carries data-e="entity:id:field"; drag lists carry
// data-entity; buttons carry data-move. Everything talks to /studio/api/*.
(function () {
  "use strict";

  const S = (window.Studio = {});

  S.toast = function (msg, isErr) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el); }
    el.textContent = msg; el.className = "toast on" + (isErr ? " err" : "");
    clearTimeout(el._t); el._t = setTimeout(() => (el.className = "toast"), isErr ? 7000 : 2500);
  };

  S.api = async function (method, url, body, isForm) {
    const opts = { method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    }
    const res = await fetch(url, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) {
      const msg = (data && (data.detail || data.message)) || res.statusText;
      if (res.status === 401) { location.href = "/admin/login/?next=" + encodeURIComponent(location.pathname); }
      S.toast(typeof msg === "string" ? msg : JSON.stringify(msg), true);
      throw new Error(msg);
    }
    return data;
  };

  // ---- inline editing: data-e="entity:id:field" [data-type="lines|json|number"] ----
  function valueOf(el) {
    if (el.type === "checkbox") return el.checked;
    const t = el.dataset.type;
    if (t === "lines") return el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (t === "number") return el.value === "" ? null : Number(el.value);
    if (t === "json") return JSON.parse(el.value || "null");
    return el.value;
  }
  document.addEventListener("change", async (ev) => {
    const el = ev.target.closest("[data-e]");
    if (!el) return;
    const [entity, id, field] = el.dataset.e.split(":");
    let v;
    try { v = valueOf(el); } catch (e) { S.toast("Invalid JSON", true); return; }
    await S.api("PATCH", `/studio/api/${entity}/${id}`, { [field]: v });
    el.classList.add("saved"); setTimeout(() => el.classList.remove("saved"), 700);
    if (el.dataset.reload !== undefined) location.reload();
  });

  // ---- reorder helpers ----
  function idsOf(list) { return [...list.children].filter((li) => li.dataset.id).map((li) => li.dataset.id); }
  S.saveOrder = async function (list, movedId) {
    if (list.dataset.entity && list.dataset.entity !== "none") {
      const payload = { entity: list.dataset.entity, ids: idsOf(list) };
      if (list.dataset.status) payload.status = list.dataset.status;
      if (movedId) payload.moved = movedId;
      await S.api("POST", "/studio/api/reorder", payload);
    }
    renumber(list);
    if (list.dataset.onsave) window[list.dataset.onsave](list);
  };
  function renumber(list) {
    [...list.children].forEach((li, i) => { const n = li.querySelector(":scope > .rankno"); if (n) n.textContent = i + 1; });
  }

  // ---- move buttons: data-move="up|down|top|bottom|left|right" ----
  const COLS = ["todo", "running", "review", "done", "blocked"];
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-move]");
    if (!b) return;
    ev.preventDefault();
    const li = b.closest("li[data-id]"); const list = li.parentElement; const dir = b.dataset.move;
    if (dir === "up" && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
    else if (dir === "down" && li.nextElementSibling) list.insertBefore(li.nextElementSibling, li);
    else if (dir === "top") list.insertBefore(li, list.firstElementChild);
    else if (dir === "bottom") list.appendChild(li);
    else if (dir === "left" || dir === "right") {
      // Move a task card one column backward/forward in the board.
      const i = COLS.indexOf(list.dataset.status);
      const next = COLS[i + (dir === "right" ? 1 : -1)];
      if (!next) return;
      const target = document.querySelector(`.sortable[data-entity="task"][data-status="${next}"]`);
      if (!target) return;
      target.appendChild(li);
      await S.saveOrder(target, li.dataset.id);
      updateCounts();
      return;
    } else return;
    await S.saveOrder(list, li.dataset.id);
  });

  // ---- drag and drop (desktop). Lists with the same data-group accept each other's items. ----
  let dragged = null, fromList = null;
  document.addEventListener("dragstart", (ev) => {
    const li = ev.target.closest && ev.target.closest(".sortable > li[data-id]");
    if (!li) return;
    dragged = li; fromList = li.parentElement;
    li.classList.add("dragging");
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", li.dataset.id);
  });
  document.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    document.querySelectorAll(".drop-hover").forEach((x) => x.classList.remove("drop-hover"));
    dragged = null;
  });
  function acceptable(list) {
    return list && dragged && (list === fromList ||
      (list.dataset.group && list.dataset.group === fromList.dataset.group));
  }
  function afterEl(list, y) {
    const items = [...list.querySelectorAll(":scope > li[data-id]:not(.dragging)")];
    return items.find((it) => { const r = it.getBoundingClientRect(); return y < r.top + r.height / 2; });
  }
  document.addEventListener("dragover", (ev) => {
    const list = ev.target.closest && ev.target.closest(".sortable");
    if (!acceptable(list)) return;
    ev.preventDefault();
    list.classList.add("drop-hover");
    const after = afterEl(list, ev.clientY);
    if (after) list.insertBefore(dragged, after); else list.appendChild(dragged);
  });
  document.addEventListener("dragleave", (ev) => {
    const list = ev.target.closest && ev.target.closest(".sortable");
    if (list && !list.contains(ev.relatedTarget)) list.classList.remove("drop-hover");
  });
  document.addEventListener("drop", async (ev) => {
    const list = ev.target.closest && ev.target.closest(".sortable");
    if (!acceptable(list)) return;
    ev.preventDefault();
    const moved = dragged.dataset.id;
    list.classList.remove("drop-hover");
    await S.saveOrder(list, moved);
    if (list !== fromList) { await S.saveOrder(fromList); updateCounts(); }
  });

  function updateCounts() {
    document.querySelectorAll("[data-count-for]").forEach((c) => {
      const list = document.querySelector(`.sortable[data-status="${c.dataset.countFor}"]`);
      if (list) c.textContent = list.querySelectorAll(":scope > li[data-id]").length;
    });
  }

  // ---- tabs ----
  S.tabs = function () {
    const btns = document.querySelectorAll(".tabs button[data-tab]");
    if (!btns.length) return;
    const show = (name) => {
      btns.forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
      try { localStorage.setItem("studio-tab:" + location.pathname, name); } catch (e) { /* ignore */ }
    };
    btns.forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
    let saved = null;
    try { saved = localStorage.getItem("studio-tab:" + location.pathname); } catch (e) { /* ignore */ }
    const hash = location.hash.replace("#", "");
    show([...btns].some((b) => b.dataset.tab === hash) ? hash
      : [...btns].some((b) => b.dataset.tab === saved) ? saved : btns[0].dataset.tab);
  };

  // ---- confirm + act buttons: data-act="METHOD url" [data-confirm] [data-then=reload|url] ----
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    ev.preventDefault();
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    const [method, url] = b.dataset.act.split(" ");
    b.disabled = true;
    try {
      const res = await S.api(method, url, b.dataset.body ? JSON.parse(b.dataset.body) : undefined);
      if (b.dataset.msg) S.toast(b.dataset.msg);
      const then = b.dataset.then || "reload";
      if (then === "reload") location.reload();
      else if (then === "url" && res && res.url) location.href = res.url;
      else if (then !== "none") location.href = then;
    } catch (e) { /* toast already shown */ } finally { b.disabled = false; }
  });

  S.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  document.addEventListener("DOMContentLoaded", () => { S.tabs(); updateCounts(); });
})();
