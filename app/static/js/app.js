/* app.js — 公共脚本：API 封装、状态元数据、轻量 Markdown 渲染、提示条 */
"use strict";

const STATUS_ORDER = ["需求确认", "开发中", "待验收", "已验收", "已上线", "暂停", "归档"];

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json; charset=utf-8";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let payload = null;
  try { payload = await res.json(); } catch (_) { /* 空响应 */ }
  if (!res.ok || !payload || payload.ok !== true) {
    const msg = (payload && payload.error) || `请求失败（HTTP ${res.status}）`;
    throw new Error(msg);
  }
  return payload.data;
}
const getJSON = (url) => api("GET", url);
const postJSON = (url, body) => api("POST", url, body ?? {});
const patchJSON = (url, body) => api("PATCH", url, body);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

function stamp(status) {
  return `<span class="stamp st-${esc(status)}">${esc(status)}</span>`;
}

let toastTimer = null;
function toast(message, isError = false) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

/* ---------- 公共 SVG 滤镜（判子印章的印泥质感）---------- */
/* feTurbulence 大波纹扭曲圆边（手盖的歪斜感）+ 高频噪声抠出飞白（印泥不匀）。
   三页共用，body 存在时注入一次。 */
(function injectHankoFilter() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "0");
  svg.setAttribute("height", "0");
  svg.style.position = "absolute";
  svg.innerHTML =
    '<filter id="hanko-ink" x="-15%" y="-15%" width="130%" height="130%">' +
    // 低频噪声：仅让圆边与笔画轻微歪扭（幅度小，保住可读性）
    '<feTurbulence type="fractalNoise" baseFrequency="0.05" numOctaves="2" seed="7" result="warp"/>' +
    '<feDisplacementMap in="SourceGraphic" in2="warp" scale="1.3" xChannelSelector="R" yChannelSelector="G" result="displaced"/>' +
    // 稀疏蛀点：高频噪声只抠出少量小飞白（不再啃穿笔画主体）
    '<feTurbulence type="fractalNoise" baseFrequency="0.35" numOctaves="2" seed="11" result="grain"/>' +
    '<feColorMatrix in="grain" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.55 0.55 0.55 0 -0.88" result="grainHoles"/>' +
    '<feComposite in="displaced" in2="grainHoles" operator="out"/>' +
    "</filter>";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(svg));
})();

/* ---------- 轻量 Markdown 渲染（标题/段落/列表/表格/引用/粗体/代码） ---------- */
function inlineMd(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderMarkdown(md) {
  const lines = String(md || "").split(/\r?\n/);
  const out = [];
  let list = null;      // "ul" | "ol"
  let table = null;     // {rows: [[...]]}
  let quote = null;     // []

  const flushAll = () => {
    if (list) { out.push(`<${list.tag}>` + list.items.map((i) => `<li>${i}</li>`).join("") + `</${list.tag}>`); list = null; }
    if (table) {
      const [head, ...rest] = table.rows;
      out.push("<table><thead><tr>" + head.map((c) => `<th>${inlineMd(c)}</th>`).join("") + "</tr></thead><tbody>"
        + rest.map((r) => "<tr>" + r.map((c) => `<td>${inlineMd(c)}</td>`).join("") + "</tr>").join("")
        + "</tbody></table>");
      table = null;
    }
    if (quote) { out.push("<blockquote>" + quote.map((q) => `<p>${inlineMd(q)}</p>`).join("") + "</blockquote>"); quote = null; }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (/^\s*$/.test(line)) { flushAll(); continue; }

    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flushAll(); out.push(`<h${h[1].length}>${inlineMd(h[2])}</h${h[1].length}>`); continue; }

    if (/^\s*[-*]\s+/.test(line)) {
      if (table || quote) flushAll();
      if (!list || list.tag !== "ul") { flushAll(); list = { tag: "ul", items: [] }; }
      list.items.push(inlineMd(line.replace(/^\s*[-*]\s+/, "")));
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      if (table || quote) flushAll();
      if (!list || list.tag !== "ol") { flushAll(); list = { tag: "ol", items: [] }; }
      list.items.push(inlineMd(line.replace(/^\s*\d+\.\s+/, "")));
      continue;
    }

    if (/^\s*>/.test(line)) {
      if (list || table) flushAll();
      if (!quote) quote = [];
      quote.push(line.replace(/^\s*>\s?/, ""));
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (list || quote) flushAll();
      const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue; // 分隔行
      if (!table) table = { rows: [] };
      table.rows.push(cells);
      continue;
    }

    if (/^---+$/.test(line.trim())) { flushAll(); out.push("<hr>"); continue; }

    flushAll();
    out.push(`<p>${inlineMd(line)}</p>`);
  }
  flushAll();
  return out.join("\n");
}

/* ---------- 未配置时引导去设置页 ---------- */
async function ensureConfigured() {
  const boot = await getJSON("/api/bootstrap");
  if (!boot.configured) {
    location.href = "/admin.html?wizard=1";
    return null;
  }
  return boot;
}
