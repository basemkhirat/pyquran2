import { useEffect } from "react";
import type { AudioPlayback } from "../../hooks/useAudioPlayback";
import {
    nextAttemptAfter,
    prevAttemptBefore,
    type TimelineIndex,
} from "../../lib/playbackTimeline";
import { PlaybackProgress } from "./PlaybackProgress";
import { PlaybackTransport } from "./PlaybackTransport";

// Silence shorter than this is left alone — it's the natural pause between words rather
// than the reciter being away.
const GAP_SKIP_THRESHOLD_MS = 2000;

export function PlaybackAudioBar({
    src,
    index,
    playback,
    autoSkip,
    onAutoSkipChange,
}: {
    src: string;
    index: TimelineIndex;
    playback: AudioPlayback;
    autoSkip: boolean;
    onAutoSkipChange: (value: boolean) => void;
}) {
    const { attachAudio, subscribe, seek, isPlaying, error } = playback;
    const hasTimeline = index.attempts.length > 0;

    const goToPrevWord = () => {
        const i = prevAttemptBefore(index, playback.timeMsRef.current);
        seek(i >= 0 ? index.attempts[i].startMs : 0);
    };
    const goToNextWord = () => {
        const i = nextAttemptAfter(index, playback.timeMsRef.current);
        if (i >= 0) seek(index.attempts[i].startMs);
    };

    // Jump the long silences that sit between utterances in a real session.
    useEffect(() => {
        if (!isPlaying || !hasTimeline || !autoSkip) return;
        // A seek takes a moment to apply, so the next few frames still report the old
        // position; without this the same jump would be issued repeatedly and stutter.
        let lastTarget = -1;
        return subscribe((timeMs) => {
            const next = nextAttemptAfter(index, timeMs);
            if (next < 0) return;
            const gapStart = index.attempts[next - 1]?.endMs ?? 0;
            const gapEnd = index.attempts[next].startMs;
            if (gapEnd !== lastTarget && timeMs >= gapStart && gapEnd - timeMs > GAP_SKIP_THRESHOLD_MS) {
                lastTarget = gapEnd;
                seek(gapEnd);
            }
        });
    }, [subscribe, index, seek, isPlaying, hasTimeline, autoSkip]);

    return (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border/50 bg-surface/90 shadow-[0_-8px_30px_rgba(0,0,0,0.25)] backdrop-blur-xl">
            {/* No crossOrigin: this is a plain media load, so it isn't preflighted.
                preload="metadata" keeps the initial hit small — the WAV is uncompressed. */}
            <audio ref={attachAudio} src={src} preload="metadata" />

            <div className="mx-auto max-w-5xl px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
                {error ? (
                    <p className="w-full py-2 text-center text-sm text-error">
                        تعذّر تحميل التسجيل الصوتي.
                    </p>
                ) : (
                    <>
                        <PlaybackTransport
                            playback={playback}
                            onPrevWord={goToPrevWord}
                            onNextWord={goToNextWord}
                            hasTimeline={hasTimeline}
                            autoSkip={autoSkip}
                            onAutoSkipChange={onAutoSkipChange}
                        />

                        {/* Seek bar spans the full bar width beneath the controls. */}
                        <div className="mt-3 w-full">
                            <PlaybackProgress index={index} playback={playback} />
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
