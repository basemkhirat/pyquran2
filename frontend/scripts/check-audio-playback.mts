/**
 * Playback checks that need a DOM.
 *
 * 1. useAudioPlayback: the <audio> element mounts LATER than the hook, because
 *    SessionPlaybackPage renders a spinner until the session has loaded. A plain RefObject
 *    silently fails there — the listener effect runs once with a null ref and never re-runs
 *    — so playback advances with a frozen UI. This asserts the callback ref works.
 * 2. PlaybackDetectedToast: the subtitle window is derived from playback position rather
 *    than accumulated, so it has to be correct when seeking backwards too.
 *
 *   yarn check:playback
 *
 * Deliberately not a test-runner suite: yarn 1 cannot link vitest against vite in this
 * project, so this runs on bare node + jsdom.
 */
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
    url: "http://localhost/",
    pretendToBeVisual: true,
});
const g = globalThis as Record<string, unknown>;
g.window = dom.window;
g.document = dom.window.document;
Object.defineProperty(globalThis, "navigator", {
    value: dom.window.navigator,
    configurable: true,
});
g.HTMLElement = dom.window.HTMLElement;
g.HTMLMediaElement = dom.window.HTMLMediaElement;
g.Event = dom.window.Event;
g.requestAnimationFrame = (cb: FrameRequestCallback) => dom.window.setTimeout(() => cb(Date.now()), 16);
g.cancelAnimationFrame = (id: number) => dom.window.clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useAudioPlayback } = await import("../src/hooks/useAudioPlayback.ts");

let failures = 0;
function check(label: string, actual: unknown, expected: unknown) {
    const ok = Object.is(actual, expected);
    if (!ok) failures++;
    console.log(`${ok ? "ok  " : "FAIL"}  ${label}${ok ? "" : `  (got ${actual}, want ${expected})`}`);
}

let api: ReturnType<typeof useAudioPlayback>;
const received: number[] = [];

function Harness({ ready }: { ready: boolean }) {
    const playback = useAudioPlayback();
    api = playback;
    React.useEffect(() => playback.subscribe((ms) => received.push(ms)), [playback.subscribe]);
    // Mirrors the real page: nothing media-related exists during loading.
    if (!ready) return React.createElement("div", null, "loading");
    return React.createElement("audio", { ref: playback.attachAudio, src: "/x.wav" });
}

const root = createRoot(document.getElementById("root")!);

// 1. First render: still loading, no <audio> in the tree at all.
await act(async () => {
    root.render(React.createElement(Harness, { ready: false }));
});
check("no audio element while loading", document.querySelector("audio"), null);
check("not playing initially", api!.isPlaying, false);

// 2. Session loads -> the <audio> element mounts, well after the hook first ran.
await act(async () => {
    root.render(React.createElement(Harness, { ready: true }));
});
const audio = document.querySelector("audio") as HTMLAudioElement;
check("audio element mounted", audio !== null, true);

// 3. The regression: a play event must reach the hook. Before the callback-ref fix the
//    listener was never attached, so isPlaying stayed false and the rAF loop never ran.
await act(async () => {
    audio.dispatchEvent(new dom.window.Event("play"));
});
check("play event flips isPlaying", api!.isPlaying, true);

// 4. Position updates must reach subscribers (this is what moves the words and slider).
received.length = 0;
Object.defineProperty(audio, "currentTime", { value: 12.5, configurable: true });
await act(async () => {
    audio.dispatchEvent(new dom.window.Event("timeupdate"));
});
check("timeupdate publishes position", received.at(-1), 12500);

received.length = 0;
Object.defineProperty(audio, "currentTime", { value: 3.25, configurable: true });
await act(async () => {
    audio.dispatchEvent(new dom.window.Event("seeked"));
});
check("seek publishes position", received.at(-1), 3250);

// 5. Pause must stop the loop.
await act(async () => {
    audio.dispatchEvent(new dom.window.Event("pause"));
});
check("pause clears isPlaying", api!.isPlaying, false);

// ---------------------------------------------------------------------------------------
// PlaybackDetectedToast: the "what the recognizer heard" subtitle.
// ---------------------------------------------------------------------------------------
console.log("\nPlaybackDetectedToast:");

const { buildTimelineIndex } = await import("../src/lib/playbackTimeline.ts");
const { PlaybackDetectedToast } = await import("../src/components/playback/PlaybackDetectedToast.tsx");

const entry = (word: number, detected: string, start: number, status = "correct") => ({
    display_index: word - 1,
    chapter_number: 1, verse_number: 1, word_number: word,
    word_text: `ref${word}`, detected_text: detected, status,
    score: 1, start_time: start, end_time: start + 400,
});

// Three words in quick succession, then a long silence, then a misheard word.
const session = {
    id: "s", mode: "word_by_word", narration_id: 1, score_threshold: 0.5,
    range: { start_chapter: 1, start_verse: 1, end_chapter: 1, end_verse: 1 },
    range_inferred: false, duration_ms: 60000, has_recording: true,
    words: [], stats: {} as never,
    timeline: [
        entry(1, "بسم", 1000),
        entry(2, "الله", 2000),
        entry(3, "الرحمن", 3000),
        entry(4, "الرحيب", 50000, "incorrect"),
    ],
} as never;

const toastIndex = buildTimelineIndex(session);
const subscribers = new Set<(ms: number) => void>();
const stubPlayback = {
    subscribe: (fn: (ms: number) => void) => {
        subscribers.add(fn);
        fn(0);
        return () => subscribers.delete(fn);
    },
} as never;

const toastHost = document.createElement("div");
document.body.appendChild(toastHost);
const toastRoot = createRoot(toastHost);

await act(async () => {
    toastRoot.render(
        React.createElement(PlaybackDetectedToast, { index: toastIndex, playback: stubPlayback })
    );
});

const publish = async (ms: number) => {
    await act(async () => {
        for (const fn of subscribers) fn(ms);
    });
};
// Words are separate spans separated by a CSS gap, not by whitespace, so join them here.
const toastText = () =>
    [...toastHost.querySelectorAll("span")].map((s) => s.textContent ?? "").join(" ").trim();

await publish(500);
check("hidden before the first word", toastText(), "");

await publish(1200);
check("shows the first detected word", toastText(), "بسم");

await publish(3200);
check("accumulates a rolling line", toastText(), "بسم الله الرحمن");

// Deep in the 47-second silence the line clears, like the live toast fading after a pause.
await publish(30000);
check("clears during a long silence", toastText(), "");

await publish(50200);
check("shows the misheard word after the gap", toastText(), "الرحيب");

// Seeking backwards must recompute the window, not append to it.
await publish(2200);
check("recomputes when seeking backwards", toastText(), "بسم الله");

console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
