// Project access without accounts. Credentials never enter browser storage.
const projectPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function connection(value, origin) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    (value.schema_version !== undefined && value.schema_version !== 1) ||
    !projectPattern.test(value.project_id) ||
    typeof value.token !== "string" ||
    !/^[A-Za-z0-9_-]{16,512}$/.test(value.token)
  ) {
    throw new Error(
      "Це не файл доступу Team Sync. Отримайте новий файл у власника CTF.",
    );
  }
  if (value.url) {
    const server = new URL(value.url);
    if (
      !["http:", "https:"].includes(server.protocol) ||
      server.username ||
      server.password ||
      server.search ||
      server.hash ||
      server.origin !== origin
    ) {
      throw new Error(
        "Цей файл належить іншому серверу. Відкрийте Team Sync за адресою з файла.",
      );
    }
  }
  return { project_id: value.project_id, token: value.token };
}

export function accessLink(origin, projectId, token) {
  const value = connection({ project_id: projectId, token }, origin);
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const encoded = btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
  return `${origin}/#join=${encoded}`;
}

export function readAccessLink(hash, origin) {
  if (!hash.startsWith("#join=")) return null;
  const encoded = hash.slice(6);
  if (encoded.length > 4096 || !/^[A-Za-z0-9_-]+$/.test(encoded)) {
    throw new Error(
      "Посилання доступу пошкоджене. Попросіть власника надіслати його повторно.",
    );
  }
  try {
    const bytes = Uint8Array.from(
      atob(encoded.replaceAll("-", "+").replaceAll("_", "/")),
      (c) => c.charCodeAt(0),
    );
    return connection(JSON.parse(new TextDecoder().decode(bytes)), origin);
  } catch {
    throw new Error(
      "Посилання доступу пошкоджене. Попросіть власника надіслати його повторно.",
    );
  }
}
