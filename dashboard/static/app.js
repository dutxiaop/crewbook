"use strict";

const $ = (selector) => document.querySelector(selector);
const taskStatus = {running: "进行中", stale: "待更新", completed: "已完成", failed: "失败", interrupted: "已中断", unknown: "状态待确认"};
const stages = {proposed: "待确认", accepted: "已接受", applied: "已写入", verified: "已复验", rejected: "已搁置", observed: "配置变更", archived: "历史归档"};
const origins = {user: "用户反馈", autonomous: "自主提议", file: "配置变更", history: "历史归档"};
const dateTime = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false});
const clock = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false});
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const autoRefreshMs = 30_000;
let snapshot = null;
let role = "all";
let taskView = "active";
let taskGrouping = "employee";
const collapsedTaskGroups = new Set();
const groupOrder = {employee: new Map(), project: new Map()};
let loading = false;
let fingerprint = "";
let toastTimer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function date(value) {
  return value ? dateTime.format(new Date(value * 1000)) : "历史归档";
}

function versionText(value) {
  return `v${String(value).replace(/^v/i, "")}`;
}

function badge(label, type) {
  return element("span", `tag ${type || ""}`, label);
}

function threadLink(label, id, className) {
  if (!uuid.test(id || "")) return element("span", className, label);
  const link = element("a", className, label);
  link.href = `codex://threads/${id}`;
  return link;
}

function copyButton(label, value) {
  const button = element("button", "small-button", label);
  button.type = "button";
  button.title = value;
  button.dataset.copy = value;
  return button;
}

function chosen() {
  return snapshot.staff.find((employee) => employee.id === role);
}

function inScope(item) {
  return role === "all" || item.role === role;
}

function enabledTask(item) {
  return $("#replays").checked || !item.is_replay;
}

function ongoing(item) {
  return item.status === "running" || item.status === "stale";
}

function employeeTitle(id) {
  return snapshot.staff.find((employee) => employee.id === id)?.name || id;
}

function renderStaff() {
  $("#staff-count").textContent = snapshot.staff.length;
  $("#all-staff").setAttribute("aria-pressed", String(role === "all"));
  const focused = document.activeElement?.dataset.role;
  const buttons = snapshot.staff.map((employee) => {
    const active = snapshot.tasks.filter((item) => item.role === employee.id && enabledTask(item) && ongoing(item));
    const present = active.some((item) => item.status === "running") ? "running" : active.length ? "stale" : "idle";
    const button = element("button", "employee");
    button.type = "button";
    button.dataset.role = employee.id;
    button.setAttribute("aria-pressed", String(role === employee.id));
    button.title = employee.responsibility;
    const name = element("span", "employee-name");
    name.append(element("strong", "", employee.name), element("span", "", employee.version ? versionText(employee.version) : "未标版本"));
    const status = element("span", `presence ${present}`);
    status.setAttribute("aria-label", present === "idle" ? "空闲" : taskStatus[present]);
    button.append(element("span", `avatar ${employee.color}`, employee.short), name, status);
    return button;
  });
  $("#staff-list").replaceChildren(...buttons);
  if (focused) buttons.find((button) => button.dataset.role === focused)?.focus({preventScroll: true});
}

function renderScopeActions() {
  const employee = chosen();
  const scope = $("#scope-actions");
  if (employee && scope.dataset.role !== employee.id) {
    scope.replaceChildren(copyButton("复制配置路径", employee.config), copyButton("复制方法记录路径", employee.methods));
    scope.dataset.role = employee.id;
  } else if (!employee && scope.dataset.role) {
    scope.replaceChildren();
    delete scope.dataset.role;
  }
}

function renderTasks() {
  const allEmployees = role === "all";
  const grouping = allEmployees ? taskGrouping : "project";
  const byProject = grouping === "project";
  groupingControl.closest("label").hidden = !allEmployees;
  const candidates = snapshot.tasks.filter((item) => inScope(item) && enabledTask(item) && (taskView === "active" ? ongoing(item) : !ongoing(item)));
  candidates.sort((a, b) => Number(b.status === "running") - Number(a.status === "running") || b.updated_at - a.updated_at);
  $("#task-count").textContent = snapshot.task_error ? "—" : candidates.length;
  if (snapshot.task_error || !candidates.length) {
    $("#tasks").replaceChildren(element("p", "empty", snapshot.task_error || (taskView === "active" ? "当前没有进行中的任务" : "没有最近完成的任务")));
    return;
  }
  const groups = new Map();
  for (const task of candidates) {
    const key = byProject ? (task.project_id || "temporary") : task.role;
    const name = byProject ? (task.project || "临时") : employeeTitle(task.role);
    if (!groups.has(key)) groups.set(key, {name, tasks: []});
    groups.get(key).tasks.push(task);
  }
  const order = groupOrder[grouping];
  for (const key of groups.keys()) if (!order.has(key)) order.set(key, order.size);
  const ordered = [...groups].sort(([a], [b]) => Number(a === "temporary") - Number(b === "temporary") || order.get(a) - order.get(b));
  const cards = (task) => {
    const card = element("article", "task");
    const top = element("div", "task-top");
    const title = threadLink(task.title, task.root, "task-title");
    title.title = task.title;
    const status = element("div", "task-meta");
    status.append(badge(taskStatus[task.status], task.status));
    if (task.is_replay) status.append(badge("回放"));
    top.append(title, status);
    const meta = element("div", "task-meta");
    if (allEmployees) meta.append(element("span", "meta", byProject ? employeeTitle(task.role) : (task.project || "临时")));
    if (task.version) meta.append(element("span", "version", versionText(task.version)));
    if (task.operation) meta.append(badge(task.operation));
    const time = element("span", "meta", `${ongoing(task) ? "最后更新" : "最后记录"} ${date(task.updated_at)}`);
    const bottom = element("div", "task-meta task-footer");
    bottom.append(time, threadLink("查看子任务", task.id, "evidence"));
    card.append(top);
    if (meta.childElementCount) card.append(meta);
    if (task.message) {
      const progress = element("p", "task-progress", task.message);
      progress.title = task.message;
      card.append(progress);
    }
    card.append(bottom);
    return card;
  };
  const list = $("#tasks");
  const focused = document.activeElement?.closest("summary.task-group-heading")?.parentElement.dataset.group;
  const blocks = ordered.map(([key, group], index) => {
    const stateKey = `${grouping}:${key}`;
    const section = element("details", "task-group");
    section.dataset.group = key;
    section.dataset.span = String(Math.min(group.tasks.length, 4));
    section.open = !collapsedTaskGroups.has(stateKey);
    const heading = element("summary", "task-group-heading");
    const label = element("span", "task-group-name", group.name);
    label.id = `task-group-${index}`;
    section.setAttribute("aria-labelledby", label.id);
    const count = element("span", "count", String(group.tasks.length));
    count.setAttribute("aria-label", `${group.tasks.length} 项任务`);
    heading.append(label, count);
    const content = element("div", "task-group-content");
    content.append(...group.tasks.map(cards));
    section.append(heading, content);
    heading.addEventListener("click", (event) => {
      event.preventDefault();
      section.open = !section.open;
      if (section.open) collapsedTaskGroups.delete(stateKey);
      else collapsedTaskGroups.add(stateKey);
      try { localStorage.setItem("employee-dashboard-collapsed-groups", JSON.stringify([...collapsedTaskGroups])); } catch { /* Keep in-memory state. */ }
    });
    return section;
  });
  const grid = element("div", "task-groups");
  grid.append(...blocks);
  list.replaceChildren(grid);
  if (focused) blocks.find((block) => block.dataset.group === focused)?.querySelector("summary")?.focus({preventScroll: true});
}

function evidenceLink(value) {
  if (!value) return null;
  if (value.startsWith("codex://threads/")) return threadLink("打开证据会话", value.slice("codex://threads/".length), "evidence");
  return copyButton("复制证据路径", value);
}

function renderLearning() {
  const container = $("#learning");
  const open = new Set([...container.querySelectorAll("details[open]")].map((node) => node.dataset.event));
  const focused = document.activeElement?.closest?.("details")?.dataset.event;
  const source = $("#learning-source").value;
  const stage = $("#learning-stage").value;
  const items = snapshot.learning.filter((item) => inScope(item) && (source === "all" || source === item.origin) && (stage === "all" || stage === item.stage));
  $("#learning-count").textContent = items.length;
  if (!items.length) {
    container.replaceChildren(element("p", "empty", "当前筛选下没有学习记录"));
    return;
  }
  const details = items.slice(0, 35).map((item) => {
    const card = element("details", "learning");
    card.dataset.event = item.id;
    card.open = open.has(item.id);
    const summary = element("summary");
    const body = element("span", "learning-body");
    const meta = element("span", "learning-meta");
    meta.append(badge(stages[item.stage], item.stage), element("span", "meta", employeeTitle(item.role)));
    if (item.version) meta.append(element("span", "version", versionText(item.version)));
    const timestamp = item.origin === "history" && !item.at ? origins[item.origin] : `${origins[item.origin]} · ${date(item.at)}`;
    body.append(meta, element("strong", "learning-title", item.title), element("span", "meta", timestamp));
    summary.append(body);
    const expanded = element("div", "expanded");
    if (item.note) expanded.append(element("p", "", item.note));
    if (item.details?.length) {
      const list = element("ul");
      for (const line of item.details) list.append(element("li", "", line));
      expanded.append(list);
    }
    for (const step of item.steps || []) {
      const row = element("div", "step");
      row.append(element("strong", "", stages[step.stage]), element("p", "", step.note), element("span", "meta", `${date(step.at)}${step.version ? ` · ${versionText(step.version)}` : ""}`));
      if (step.task_id) row.append(threadLink("查看任务回执", step.task_id, "evidence"));
      if (step.evidence) row.append(evidenceLink(step.evidence));
      expanded.append(row);
    }
    if (!item.steps?.length && item.evidence) expanded.append(evidenceLink(item.evidence));
    card.append(summary, expanded);
    return card;
  });
  container.replaceChildren(...details);
  if (focused) details.find((node) => node.dataset.event === focused)?.querySelector("summary")?.focus({preventScroll: true});
}

function render() {
  if (!snapshot) return;
  renderStaff();
  renderScopeActions();
  renderTasks();
  renderLearning();
}

function showPage() {
  const learning = location.hash === "#learning-records";
  $("#task-view").hidden = learning;
  $("#learning-view").hidden = !learning;
  document.querySelectorAll(".view-nav a").forEach((link) => {
    if ((link.dataset.page === "learning") === learning) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function connection(text, state) {
  $("#connection").className = `connection ${state}`;
  $("#connection-label").textContent = text;
}

async function load() {
  if (loading) return;
  loading = true;
  $("#refresh").disabled = true;
  try {
    const response = await fetch("/api/dashboard", {cache: "no-store", signal: AbortSignal.timeout(6500)});
    if (!response.ok) throw new Error("snapshot unavailable");
    const data = await response.json();
    snapshot = data;
    if (role !== "all" && !chosen()) role = "all";
    connection(data.task_error ? "任务数据暂不可读" : `本机实时 · ${clock.format(new Date(data.at * 1000))}`, data.task_error ? "error" : "live");
    const current = JSON.stringify([data.staff, data.tasks, data.learning, data.task_error]);
    if (current !== fingerprint) {
      fingerprint = current;
      render();
    }
  } catch {
    connection(snapshot ? "连接中断 · 显示上次记录" : "连接失败", "error");
    if (!snapshot) {
      $("#tasks").replaceChildren(element("p", "empty", "暂时无法读取任务"));
      $("#learning").replaceChildren(element("p", "empty", "暂时无法读取学习记录"));
    }
  } finally {
    loading = false;
    $("#refresh").disabled = false;
  }
}

function toast(text) {
  const box = $("#toast");
  box.textContent = text;
  box.className = "shown";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.className = ""; }, 2200);
}

$("#all-staff").addEventListener("click", () => { role = "all"; render(); });
$("#staff-list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-role]");
  if (button) { role = button.dataset.role; render(); }
});
document.querySelectorAll("[data-task-view]").forEach((button) => button.addEventListener("click", () => {
  taskView = button.dataset.taskView;
  document.querySelectorAll("[data-task-view]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  if (snapshot) renderTasks();
}));
$("#replays").addEventListener("change", render);
const groupingControl = $("#task-grouping");
try {
  if (localStorage.getItem("employee-dashboard-task-grouping") === "project") taskGrouping = "project";
  const saved = JSON.parse(localStorage.getItem("employee-dashboard-collapsed-groups") || "[]");
  if (Array.isArray(saved)) saved.filter((key) => typeof key === "string").forEach((key) => collapsedTaskGroups.add(key));
} catch {
  // Grouping remains available for this page even if browser storage is disabled.
}
groupingControl.value = taskGrouping;
groupingControl.addEventListener("change", () => {
  taskGrouping = groupingControl.value;
  try { localStorage.setItem("employee-dashboard-task-grouping", taskGrouping); } catch { /* Keep the current selection. */ }
  if (snapshot) renderTasks();
});
for (const id of ["#learning-source", "#learning-stage"]) $(id).addEventListener("change", () => { if (snapshot) renderLearning(); });
$("#refresh").addEventListener("click", load);
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-copy]");
  if (!button) return;
  try { await navigator.clipboard.writeText(button.dataset.copy); toast("路径已复制"); }
  catch { toast(button.dataset.copy); }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
window.addEventListener("hashchange", showPage);
showPage();
load();
setInterval(load, autoRefreshMs);
