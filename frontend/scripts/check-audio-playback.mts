/**
 * Regression check for useAudioPlayback: the <audio> element mounts LATER than the hook,
 * because SessionPlaybackPage renders a spinner until the session has loaded. A plain
 * RefObject silently fails there — the listener effect runs once with a null ref and never
 * re-runs — so playback advances with a frozen UI. This asserts the callback ref works.
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

console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
