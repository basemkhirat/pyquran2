import { useEffect, useState } from "react";
import type { AudioPlayback } from "../../hooks/useAudioPlayback";
import { cursorAt, type TimelineIndex } from "../../lib/playbackTimeline";
import { cn } from "../../lib/cn";

// Matches DetectedWordToast on the recording page.
const MAX_WORDS = 7;
const SUBTITLE_CLEAR_MS = 7000;

/**
 * Playback counterpart of DetectedWordToast: the rolling subtitle of what the recognizer
 * actually heard, replayed in step with the audio.
 *
 * The live version accumulates words as socket events arrive. Here the whole timeline is
 * already known, so the window is *derived* from the playback position instead — which is
 * what makes seeking backwards work: the line is recomputed from the cursor rather than
 * appended to.
 */
export function PlaybackDetectedToast({
    index,
    playback,
}: {
    index: TimelineIndex;
    playback: AudioPlayback;
}) {
    const { subscribe } = playback;
    // `visible` goes false during a long silence, mirroring the live toast fading out
    // after a pause in speech. Recomputed from position, so no timers are involved.
    const [tick, setTick] = useState({ cursor: -1, visible: false });

    useEffect(() => {
        let lastKey = "";
        return subscribe((timeMs) => {
            const cursor = cursorAt(index, timeMs);
            const visible =
                cursor >= 0 && timeMs - index.attempts[cursor].endMs < SUBTITLE_CLEAR_MS;
            const key = `${cursor}:${visible}`;
            if (key !== lastKey) {
                lastKey = key;
                setTick({ cursor, visible });
            }
        });
    }, [subscribe, index]);

    if (!tick.visible || tick.cursor < 0) return null;

    // Walk back from the cursor, stopping at a silence long enough that the live toast
    // would have cleared — so a new utterance starts a fresh line rather than resuming the
    // one from before the pause.
    let start = tick.cursor;
    const floor = Math.max(0, tick.cursor - MAX_WORDS + 1);
    while (start > floor) {
        const gap = index.attempts[start].startMs - index.attempts[start - 1].endMs;
        if (gap >= SUBTITLE_CLEAR_MS) break;
        start--;
    }

    const words = index.attempts
        .slice(start, tick.cursor + 1)
        .map((a, i) => ({ ...a, key: `${start + i}` }))
        .filter((a) => a.detectedText);

    // Nothing was stored for these words (a session recorded before detected_text existed).
    if (words.length === 0) return null;

    return (
        <div className="pointer-events-none fixed inset-x-0 bottom-36 z-40 flex justify-center px-4">
            <div
                dir="rtl"
                className="animate-in fade-in slide-in-from-bottom-4 duration-200 flex max-w-[90vw] flex-nowrap items-baseline justify-center gap-x-2 overflow-hidden whitespace-nowrap rounded-full border border-border/60 bg-surface-elevated/95 px-5 py-2.5 font-arabic text-lg text-text-primary shadow-lg backdrop-blur-md"
            >
                {words.map((w) => (
                    // Keyed by text so a word entering the window animates in while the
                    // ones already on screen stay put — same trick as the live toast.
                    <span
                        key={`${w.key}-${w.detectedText}`}
                        className={cn(
                            "inline-block animate-in fade-in zoom-in-95 slide-in-from-bottom-2 duration-300 ease-out",
                            w.status === "incorrect" ? "text-error/90" : "text-text-primary"
                        )}
                    >
                        {w.detectedText}
                    </span>
                ))}
            </div>
        </div>
    );
}
