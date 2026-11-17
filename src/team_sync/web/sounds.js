// Short synthesized cues for live events, a tab-title counter and system notifications
// while the tab is hidden. No audio files: tones are generated with WebAudio, so the
// strict CSP stays intact. Browsers allow sound only after the first click or key.

const STORAGE = "team-sync-sounds";

export const cues = {
  message: [
    "Нове повідомлення вам",
    [
      [660, 0, 0.09],
      [880, 0.1, 0.12],
    ],
  ],
  urgent: [
    "Термінове повідомлення",
    [
      [988, 0, 0.08],
      [988, 0.12, 0.08],
      [1319, 0.24, 0.16],
    ],
  ],
  flag_new: [
    "Новий кандидат у флаги",
    [
      [523, 0, 0.08],
      [659, 0.09, 0.08],
      [784, 0.18, 0.14],
    ],
  ],
  flag_confirmed: [
    "Флаг підтверджено",
    [
      [523, 0, 0.1],
      [659, 0.1, 0.1],
      [784, 0.2, 0.1],
      [1047, 0.3, 0.3],
    ],
  ],
  flag_rejected: [
    "Флаг відхилено / роботу повернуто",
    [
      [440, 0, 0.14],
      [349, 0.15, 0.24],
    ],
  ],
  approval: [
    "Потрібне ваше рішення (підказка, запуск)",
    [
      [784, 0, 0.12],
      [587, 0.14, 0.12],
      [784, 0.28, 0.2],
    ],
  ],
  question: [
    "Нове питання до експертів",
    [
      [587, 0, 0.1],
      [740, 0.12, 0.16],
    ],
  ],
  answer: [
    "Відповідь на ваше питання",
    [
      [740, 0, 0.1],
      [587, 0.12, 0.1],
      [880, 0.24, 0.18],
    ],
  ],
  stalled: [
    "Хтось застряг / агент упав",
    [
      [220, 0, 0.25],
      [196, 0.27, 0.3],
    ],
  ],
};
const PRIORITY = [
  "flag_confirmed",
  "approval",
  "urgent",
  "flag_rejected",
  "answer",
  "flag_new",
  "message",
  "question",
  "stalled",
];

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) || "{}");
    return {
      enabled: saved.enabled !== false,
      volume: typeof saved.volume === "number" ? saved.volume : 0.5,
      notify: Boolean(saved.notify),
      off: Array.isArray(saved.off) ? saved.off : [],
    };
  } catch {
    return { enabled: true, volume: 0.5, notify: false, off: [] };
  }
}

export const soundSettings = load();

export function saveSoundSettings() {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(soundSettings));
  } catch {
    // Private windows may refuse storage; settings then last for this tab only.
  }
}

let context = null;
let lastPlayed = 0;

function unlock() {
  if (context) return;
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) return;
  context = new Audio();
}
for (const type of ["pointerdown", "keydown"])
  window.addEventListener(type, unlock, { once: true, capture: true });

export function audioReady() {
  return Boolean(context);
}

export function playCue(kind, { force = false } = {}) {
  const cue = cues[kind];
  if (!cue) return;
  if (!force && (!soundSettings.enabled || soundSettings.off.includes(kind)))
    return;
  unlock();
  if (!context) return;
  if (context.state === "suspended") context.resume();
  const now = context.currentTime;
  const gain = context.createGain();
  gain.gain.value = Math.max(0, Math.min(1, soundSettings.volume)) * 0.25;
  gain.connect(context.destination);
  for (const [frequency, offset, length] of cue[1]) {
    const tone = context.createOscillator();
    const envelope = context.createGain();
    tone.type = "sine";
    tone.frequency.value = frequency;
    envelope.gain.setValueAtTime(0.0001, now + offset);
    envelope.gain.exponentialRampToValueAtTime(1, now + offset + 0.015);
    envelope.gain.exponentialRampToValueAtTime(0.0001, now + offset + length);
    tone.connect(envelope).connect(gain);
    tone.start(now + offset);
    tone.stop(now + offset + length + 0.02);
  }
}

// ----- attention while the tab is hidden ----------------------------------------------

let unseen = 0;
let baseTitle = document.title;

export function setBaseTitle(title) {
  baseTitle = title;
  renderTitle();
}

function renderTitle() {
  document.title = unseen ? `(${unseen}) ${baseTitle}` : baseTitle;
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    unseen = 0;
    renderTitle();
  }
});

export async function enableNotifications() {
  if (!("Notification" in window)) return false;
  const permission =
    Notification.permission === "default"
      ? await Notification.requestPermission()
      : Notification.permission;
  soundSettings.notify = permission === "granted";
  saveSoundSettings();
  return soundSettings.notify;
}

// signals: [{kind, text}] for one batch of live events; plays the most important cue.
export function signal(signals) {
  if (!signals.length) return;
  const kinds = signals.map((item) => item.kind);
  const top = PRIORITY.find((kind) => kinds.includes(kind));
  const now = Date.now();
  if (top && now - lastPlayed > 1200) {
    lastPlayed = now;
    playCue(top);
  }
  if (document.hidden) {
    unseen += signals.length;
    renderTitle();
    if (
      soundSettings.notify &&
      "Notification" in window &&
      Notification.permission === "granted"
    ) {
      const first = signals.find((item) => item.kind === top) || signals[0];
      try {
        new Notification(baseTitle, {
          body: first.text,
          tag: "team-sync",
          silent: true,
        });
      } catch {
        // Some browsers allow notifications only from a service worker.
      }
    }
  }
}
