const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
const $ = (id) => document.getElementById(id);
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
async function call(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "X-Runner-Token": token, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
function list(el, items, render, empty) {
  el.replaceChildren(
    ...(items.length ? items.map(render) : [node("p", empty, "muted")]),
  );
}
async function load() {
  let s;
  try {
    s = await call("api/state");
  } catch (e) {
    $("error").textContent =
      "Немає доступу: відкрийте посилання з терміналу runner'а.";
    return;
  }
  $("title").textContent = `Runner ${s.name}`;
  $("sub").textContent = `${s.owner} · ${s.project} · ${s.server}`;
  $("error").textContent = s.error || "";
  $("pause").textContent = s.paused
    ? "Відновити"
    : "Пауза (не брати нову роботу)";
  $("pause").onclick = () =>
    call("api/pause", { paused: !s.paused }).then(load);
  list(
    $("awaiting"),
    s.awaiting,
    (r) => {
      const d = node("div", null, "row");
      d.append(
        node("b", r.profile),
        node("span", r.purpose, "badge"),
        node("span", r.note, "muted"),
      );
      const ok = node("button", "Запустити");
      ok.onclick = () =>
        call("api/confirm", { id: r.id, start: true }).then(load);
      const no = node("button", "Відхилити");
      no.onclick = () =>
        call("api/confirm", { id: r.id, start: false }).then(load);
      d.append(ok, no);
      return d;
    },
    "Нічого.",
  );
  list(
    $("running"),
    s.running,
    (r) => {
      const d = node("div");
      const h = node("div", null, "row");
      h.append(
        node("b", r.profile),
        node("span", r.purpose, "badge"),
        node("span", `${r.agent} · ${Math.round(r.seconds / 60)} хв`, "muted"),
      );
      const b = node("button", r.stopping ? "Зупиняється…" : "Зупинити");
      b.disabled = !!r.stopping;
      b.onclick = () => call("api/stop", { id: r.id }).then(load);
      h.append(b);
      d.append(h);
      if (r.tail) d.append(node("pre", r.tail));
      return d;
    },
    "Жоден агент не працює.",
  );
  list(
    $("profiles"),
    s.profiles,
    (p) => {
      const d = node("div", null, "row");
      d.append(
        node("b", p.name),
        node("span", p.command, "badge"),
        node(
          "span",
          [
            p.model,
            p.answers_questions ? "відповідає на питання" : "",
            p.coordinator ? "координатор" : "",
            p.confirm_locally ? "лише з локальним підтвердженням" : "",
            `≤${p.max_minutes} хв`,
          ]
            .filter(Boolean)
            .join(" · "),
          "muted",
        ),
      );
      return d;
    },
    "",
  );
  list(
    $("history"),
    s.history,
    (r) => {
      const d = node("details");
      d.append(
        node(
          "summary",
          `${r.profile} · ${r.purpose} · ${r.status} (код ${r.exit_code}) · ${Math.round(r.seconds / 60)} хв`,
        ),
        node("pre", r.tail),
      );
      return d;
    },
    "Ще не було.",
  );
}
$("stopall").onclick = () =>
  confirm("Зупинити всіх агентів і поставити runner на паузу?") &&
  call("api/stop-all", {}).then(load);
load();
setInterval(load, 3000);
