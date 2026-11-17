// Layered SVG drawing of a challenge's hypothesis graph (no external libraries).
// Nodes: findings, flags, linked tasks/files. Edges: leads_to, supports, refutes,
// supersedes, attached_to. Layout: longest path from roots, ordered by creation.

const SVG = "http://www.w3.org/2000/svg";
const NODE_W = 214;
const NODE_H = 64;
const GAP_X = 22;
const GAP_Y = 54;

const KIND = {
  observation: "Спостереження",
  hypothesis: "Гіпотеза",
  result: "Результат",
  failed_attempt: "Невдала спроба",
  flag: "Флаг",
  task: "Робота",
  artifact: "Файл",
};
const STATE = {
  open: "відкрита",
  confirmed: "підтверджена",
  refuted: "спростована",
  unverified: "не перевірено",
  rejected: "відхилено",
};
const RELATION = {
  supports: "підтримує",
  refutes: "спростовує",
  supersedes: "заміняє",
};

function svg(tag, attributes = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes))
    if (value != null && value !== false) node.setAttribute(key, value);
  for (const child of children.flat())
    if (child != null && child !== false)
      node.append(
        child instanceof Node ? child : document.createTextNode(child),
      );
  return node;
}

export function nodeTone(node) {
  if (node.type === "flag") return `flag-${node.status}`;
  if (node.kind === "hypothesis") return `hyp-${node.status || "open"}`;
  return node.kind;
}

function layout(nodes, edges) {
  const order = new Map(nodes.map((node, index) => [node.id, index]));
  const parents = new Map(nodes.map((node) => [node.id, []]));
  // supports/refutes/supersedes point from a later finding back to what it judges;
  // for layering the judging finding goes *below* its subject.
  const backward = new Set(["supports", "refutes", "supersedes"]);
  for (const edge of edges) {
    const [parent, child] = backward.has(edge.relation)
      ? [edge.target, edge.source]
      : [edge.source, edge.target];
    if (parents.has(child) && order.has(parent) && parent !== child)
      parents.get(child).push(parent);
  }
  const level = new Map();
  const visiting = new Set();
  const depth = (id) => {
    if (level.has(id)) return level.get(id);
    if (visiting.has(id)) return 0; // cycle: break it
    visiting.add(id);
    const value = Math.max(-1, ...parents.get(id).map(depth)) + 1;
    visiting.delete(id);
    level.set(id, value);
    return value;
  };
  nodes.forEach((node) => depth(node.id));
  // Dense rows even if cycle breaking skipped a level.
  const levels = [...new Set(level.values())].sort((a, b) => a - b);
  const rows = levels.map(() => []);
  for (const node of nodes) rows[levels.indexOf(level.get(node.id))].push(node);
  // Keep children near their parents: order a row by the mean parent position.
  const x = new Map();
  rows.forEach((row) => {
    row.sort((a, b) => {
      const mean = (node) => {
        const values = parents
          .get(node.id)
          .map((id) => x.get(id))
          .filter((value) => value != null);
        return values.length
          ? values.reduce((sum, value) => sum + value, 0) / values.length
          : order.get(node.id);
      };
      return mean(a) - mean(b) || order.get(a.id) - order.get(b.id);
    });
    row.forEach((node, index) => x.set(node.id, index));
  });
  const width = Math.max(...rows.map((row) => row.length), 1);
  const positions = new Map();
  rows.forEach((row, depthIndex) => {
    const offset = ((width - row.length) * (NODE_W + GAP_X)) / 2;
    row.forEach((node, index) =>
      positions.set(node.id, {
        x: 16 + offset + index * (NODE_W + GAP_X),
        y: 16 + depthIndex * (NODE_H + GAP_Y),
      }),
    );
  });
  return {
    positions,
    width: 32 + width * NODE_W + (width - 1) * GAP_X,
    height: 32 + rows.length * NODE_H + (rows.length - 1) * GAP_Y,
  };
}

function wrap(text, size) {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  const lines = [""];
  for (const word of words) {
    const line = lines.at(-1);
    if ((line + " " + word).trim().length <= size)
      lines[lines.length - 1] = (line + " " + word).trim();
    else if (lines.length < 2) lines.push(word.slice(0, size));
    else {
      lines[1] = lines[1].slice(0, size - 1) + "…";
      break;
    }
  }
  return lines;
}

export function graphView(graph, { onSelect } = {}) {
  const { nodes, edges } = graph;
  const wrapper = document.createElement("div");
  wrapper.className = "graph-view";
  if (!nodes.length) {
    wrapper.textContent = "Знахідок ще немає — граф з’явиться автоматично.";
    return wrapper;
  }
  const { positions, width, height } = layout(nodes, edges);
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    role: "img",
    "aria-label": "Граф гіпотез і знахідок завдання",
  });
  // Icon styles size every svg; set the real size through CSSOM (CSP-safe).
  root.style.width = `${width}px`;
  root.style.height = `${height}px`;
  root.style.maxWidth = "none";
  root.append(
    svg(
      "defs",
      {},
      ...["leads_to", "supports", "refutes", "supersedes", "attached_to"].map(
        (relation) =>
          svg(
            "marker",
            {
              id: `arrow-${relation}`,
              viewBox: "0 0 10 10",
              refX: 9,
              refY: 5,
              markerWidth: 7,
              markerHeight: 7,
              orient: "auto-start-reverse",
              class: `arrow ${relation}`,
            },
            relation === "refutes"
              ? svg("path", { d: "M1,1 L9,9 M9,1 L1,9" })
              : svg("path", { d: "M0,0 L10,5 L0,10 z" }),
          ),
      ),
    ),
  );
  const backward = new Set(["supports", "refutes", "supersedes"]);
  for (const edge of edges) {
    // Draw every edge downwards; judging relations put the arrowhead at the start.
    const reverse = backward.has(edge.relation);
    const from = positions.get(reverse ? edge.target : edge.source);
    const to = positions.get(reverse ? edge.source : edge.target);
    if (!from || !to) continue;
    const x1 = from.x + NODE_W / 2;
    const y1 = from.y + NODE_H;
    const x2 = to.x + NODE_W / 2;
    const y2 = to.y;
    const back = y2 <= y1;
    const d = back
      ? `M${from.x + NODE_W},${from.y + NODE_H / 2} C${from.x + NODE_W + 60},${from.y + NODE_H / 2} ${to.x + NODE_W + 60},${to.y + NODE_H / 2} ${to.x + NODE_W},${to.y + NODE_H / 2}`
      : `M${x1},${y1} C${x1},${y1 + GAP_Y / 2} ${x2},${y2 - GAP_Y / 2} ${x2},${y2}`;
    root.append(
      svg("path", {
        d,
        class: `edge ${edge.relation}${edge.note === "implicit" ? " implicit" : ""}`,
        [reverse ? "marker-start" : "marker-end"]:
          `url(#arrow-${edge.relation})`,
      }),
    );
    if (RELATION[edge.relation])
      root.append(
        svg(
          "text",
          {
            x: (x1 + x2) / 2 + 6,
            y: back ? (from.y + to.y) / 2 + NODE_H / 2 : (y1 + y2) / 2 + 4,
            class: `edge-label ${edge.relation}`,
          },
          RELATION[edge.relation],
        ),
      );
  }
  for (const node of nodes) {
    const { x, y } = positions.get(node.id);
    const title = node.type === "flag" ? node.label : node.label;
    const [first, second] = wrap(title, node.type === "flag" ? 26 : 27);
    const state = STATE[node.status] ? ` · ${STATE[node.status]}` : "";
    const group = svg(
      "g",
      {
        class: `node ${nodeTone(node)}`,
        transform: `translate(${x},${y})`,
        tabindex: 0,
        role: "button",
        "aria-label": `${node.ref} ${KIND[node.kind] || node.kind}${state}: ${node.label}`,
      },
      svg(
        "title",
        {},
        [
          node.label,
          node.author,
          node.task,
          node.files?.length && `Файли: ${node.files.join(", ")}`,
        ]
          .filter(Boolean)
          .join("\n"),
      ),
      node.type === "flag"
        ? svg("path", {
            d: `M14,0 H${NODE_W - 14} L${NODE_W},${NODE_H / 2} L${NODE_W - 14},${NODE_H} H14 L0,${NODE_H / 2} z`,
          })
        : svg("rect", { width: NODE_W, height: NODE_H, rx: 10 }),
      svg(
        "text",
        { x: 12, y: 18, class: "node-kind" },
        `${node.ref} · ${KIND[node.kind] || node.kind}${state}${node.files?.length ? ` · 📎${node.files.length}` : ""}`,
      ),
      svg(
        "text",
        {
          x: 12,
          y: 37,
          class: `node-title${node.type === "flag" ? " mono" : ""}`,
        },
        first,
      ),
      second &&
        svg(
          "text",
          {
            x: 12,
            y: 53,
            class: `node-title${node.type === "flag" ? " mono" : ""}`,
          },
          second,
        ),
    );
    if (onSelect) {
      group.addEventListener("click", () => onSelect(node));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(node);
        }
      });
    }
    root.append(group);
  }
  wrapper.append(root);
  return wrapper;
}
