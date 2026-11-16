// A small, safe Markdown renderer: builds DOM nodes, never parses HTML.
// Supports headings, paragraphs, lists, quotes, fences, rules, **bold**,
// _italic_, ~~strike~~, `code` and http(s) links. Everything else is text.
import { el } from "./ui.js";

const INLINE =
  /(`[^`]+`|\*\*[^*]+\*\*|~~[^~]+~~|(?<![\p{L}\p{N}_])_[^_\s][^_]*_(?![\p{L}\p{N}_])|\[[^\]]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s)<>]+|(?<![\p{L}\p{N}&#])#\d+\b)/gu;
let onReference = null;

export function inline(text) {
  const nodes = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) nodes.push(el("code", {}, token.slice(1, -1)));
    else if (token.startsWith("**"))
      nodes.push(el("strong", {}, inline(token.slice(2, -2))));
    else if (token.startsWith("~~"))
      nodes.push(el("del", {}, inline(token.slice(2, -2))));
    else if (token.startsWith("_"))
      nodes.push(el("em", {}, inline(token.slice(1, -1))));
    else if (token.startsWith("#")) {
      const handler = onReference;
      nodes.push(
        handler
          ? el(
              "a",
              {
                class: "ref",
                href: "#",
                onclick: (event) => {
                  event.preventDefault();
                  handler(token);
                },
              },
              token,
            )
          : token,
      );
    } else {
      const link = token.match(/^\[([^\]]+)\]\((.+)\)$/);
      const href = link ? link[2] : token;
      nodes.push(
        el(
          "a",
          { href, target: "_blank", rel: "noopener noreferrer" },
          link ? link[1] : token,
        ),
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

// renderBlock(lang, code) may return a node to replace a fenced block (e.g. mermaid).
export function markdown(source, { renderBlock, onRef } = {}) {
  onReference = onRef || null;
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let paragraph = [];
  let list = null;
  let listBreak = false;
  const flush = () => {
    if (paragraph.length) out.push(el("p", {}, inline(paragraph.join(" "))));
    paragraph = [];
    if (list) out.push(list);
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^(`{3,}|~{3,})\s*([\w-]*)\s*$/);
    if (fence) {
      flush();
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith(fence[1]))
        body.push(lines[i++]);
      const code = body.join("\n");
      const custom = renderBlock?.(fence[2], code);
      out.push(
        custom ||
          el(
            "pre",
            { class: "md-code" },
            el("code", { "data-lang": fence[2] || null }, code),
          ),
      );
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flush();
      const ref = heading[2].match(/^#(\d+)\s/);
      out.push(
        el(
          `h${heading[1].length + 1}`,
          { "data-ref": ref ? `#${ref[1]}` : null },
          inline(heading[2]),
        ),
      );
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      flush();
      out.push(el("hr"));
      continue;
    }
    const item = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (item) {
      if (paragraph.length) {
        out.push(el("p", {}, inline(paragraph.join(" "))));
        paragraph = [];
      }
      const ordered = /\d/.test(item[2]);
      if (!list || list.tagName !== (ordered ? "OL" : "UL")) {
        if (list) out.push(list);
        list = el(ordered ? "ol" : "ul", { class: "md-list" });
      }
      list.append(
        el("li", { class: item[1].length ? "nested" : null }, inline(item[3])),
      );
      listBreak = false;
      continue;
    }
    if (line.startsWith(">")) {
      flush();
      out.push(el("blockquote", {}, inline(line.replace(/^>\s?/, ""))));
      continue;
    }
    if (line.startsWith("|")) {
      flush();
      out.push(el("pre", { class: "md-table" }, line));
      continue;
    }
    if (!line.trim()) {
      // A blank line inside a list keeps the list open when the next line is another
      // item or an indented continuation (a multi-line result stays in its item).
      const next = lines[i + 1] || "";
      if (
        list &&
        (/^(\s*)([-*]|\d+\.)\s+/.test(next) || /^\s{2,}\S/.test(next))
      ) {
        listBreak = true;
        continue;
      }
      flush();
      continue;
    }
    if (list && /^\s{2,}\S/.test(line)) {
      // Continuation of the previous list item; after a blank line, a new line in it.
      if (listBreak) list.lastChild.append(el("br"));
      list.lastChild.append(listBreak ? "" : " ", ...inline(line.trim()));
      listBreak = false;
      continue;
    }
    if (list) flush();
    paragraph.push(line.trim());
  }
  flush();
  return el("div", { class: "markdown" }, out);
}
