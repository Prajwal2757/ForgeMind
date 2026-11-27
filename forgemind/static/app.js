"use strict";
// All server text is inserted with textContent, never as HTML.
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); e.textContent = text; if (cls) e.className = cls; return e; };
let token = sessionStorage.getItem("token") || "";
const say = text => { $("message").textContent = text; };

function signedOut(text) {
  token = ""; sessionStorage.removeItem("token");
  $("app").hidden = true; $("login").hidden = false;
  $("out").replaceChildren(); $("docs").replaceChildren(); $("q").value = "";
  say(text || "");
}

async function api(path, opts = {}) {
  const headers = token ? {Authorization: "Bearer " + token} : {};
  const res = await fetch(path, {...opts, headers});
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && token) { signedOut("Your session ended. Sign in again."); }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

const guard = fn => async event => {
  if (event) event.preventDefault();
  say("");
  try { await fn(); } catch (e) { say(e.message); }
};

async function refresh() {
  const {documents} = await api("/api/documents");
  $("docs").replaceChildren(...documents.map(d => {
    const li = el("li", d.title + (d.groups.length ? " [" + d.groups.join(", ") + "]" : ""));
    const b = el("button", "Delete");
    b.type = "button";
    b.onclick = guard(async () => {
      if (!confirm("Delete " + d.title + "?")) return;
      await api("/api/documents/" + d.id, {method: "DELETE"});
      await refresh();
    });
    li.append(b);
    return li;
  }));
}

async function start() {
  const me = await api("/api/me");
  $("who").textContent = `${me.user} (${me.role}) at ${me.tenant}`;
  $("uploader").hidden = me.role === "viewer";
  $("login").hidden = true; $("app").hidden = false;
  await refresh();
}

$("login").onsubmit = guard(async () => {
  const body = JSON.stringify({tenant: $("tenant").value.trim(), user: $("user").value.trim(), password: $("password").value});
  token = "";
  const result = await api("/api/login", {method: "POST", body});
  token = result.token; sessionStorage.setItem("token", token);
  $("password").value = "";
  await start();
});

$("logout").onclick = guard(async () => { await api("/api/logout", {method: "POST"}); signedOut("Signed out."); });

$("ask").onclick = guard(async () => {
  $("out").replaceChildren(el("div", "Thinking…", "card"));
  try {
    const r = await api("/api/ask", {method: "POST", body: JSON.stringify({question: $("q").value})});
    const nodes = [el("div", r.answer, "card")];
    r.sources.forEach(s => nodes.push(el("div", `[${s.n}] ${s.title}\n${s.snippet}`, "card")));
    const d = document.createElement("details");
    d.append(el("summary", "Agent trace"), el("div", r.trace.map(t => JSON.stringify(t)).join("\n"), "card"));
    $("out").replaceChildren(...nodes, d);
  } catch (e) { $("out").replaceChildren(); throw e; }
});

$("upload").onclick = guard(async () => {
  const f = $("file").files[0];
  if (!f) throw new Error("Choose a file first");
  const qs = new URLSearchParams({filename: f.name, groups: $("groups").value});
  const {job_id} = await api("/api/documents?" + qs, {method: "POST", body: f});
  say("Processing " + f.name + "…");
  for (let i = 0; i < 90; i++) {
    await new Promise(r => setTimeout(r, 1000));
    const job = await api("/api/jobs/" + job_id);
    if (job.status === "failed") throw new Error(job.error);
    if (job.status === "done") { say(f.name + " added."); return refresh(); }
  }
  say("Still processing. Reload the page later.");
});

$("change").onclick = guard(async () => {
  await api("/api/password", {method: "POST", body: JSON.stringify({current: $("current").value, new: $("new").value})});
  $("current").value = ""; $("new").value = "";
  signedOut("Password changed. Sign in again.");
});

if (token) guard(start)();
