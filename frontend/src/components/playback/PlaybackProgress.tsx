import { useEffect, useRef, useState } from "react";
import type { AudioPlayback } from "../../hooks/useAudioPlayback";
import { formatTime, type TimelineIndex } from "../../lib/playbackTimeline";
import { Slider } from "@/components/ui/slider";

/**
 * Seek bar with a marker per recorded attempt.
 *
 * The bar and elapsed label are written straight to the DOM from the playback subscription
 * — putting the position in React state would re-render the whole bar every frame for no
 * visual gain. Only scrubbing uses state, because that has to override the live position.
 */
export function PlaybackProgress({
    index,
    playback,
}: {
    index: TimelineIndex;
    playback: AudioPlayback;
}) {
    const { subscribe, seek } = playback;
    const duration = index.durationMs;
    const [scrubMs, setScrubMs] = useState<number | null>(null);
    // Low-rate mirror of the position, only so the underlying Radix slider's own value
    // (used for keyboard stepping and the aria value) doesn't go stale. The visible fill
    // and thumb are written directly to the DOM below at full frame rate.
    const [syncMs, setSyncMs] = useState(0);

    const fillRef = useRef<HTMLDivElement>(null);
    const thumbRef = useRef<HTMLDivElement>(null);
    const elapsedRef = useRef<HTMLSpanElement>(null);
    // Set from the drag handlers rather than derived from state, so the subscription stops
    // fighting the thumb on the very first pointer move instead of one render later.
    const scrubbingRef = useRef(false);

    useEffect(() => {
        let lastSync = 0;
        return subscribe((timeMs) => {
            if (scrubbingRef.current) return;
            const pct = duration > 0 ? Math.min(100, (timeMs / duration) * 100) : 0;
            if (fillRef.current) fillRef.current.style.width = `${pct}%`;
            if (thumbRef.current) thumbRef.current.style.left = `${pct}%`;
            if (elapsedRef.current) elapsedRef.current.textContent = formatTime(timeMs);
            if (Math.abs(timeMs - lastSync) >= 200) {
                lastSync = timeMs;
                setSyncMs(timeMs);
            }
        });
    }, [subscribe, duration]);

    return (
        <div className="flex w-full items-center gap-3" dir="ltr">
            <span
                ref={elapsedRef}
                className="w-10 shrink-0 text-right text-xs tabular-nums text-text-secondary"
            >
                {formatTime(0)}
            </span>

            <div className="relative flex-1">
                {/* Track + attempt markers sit behind the slider; the slider itself is
                    transparent so it only contributes the thumb and interaction. */}
                <div className="pointer-events-none absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-surface-hover">
                    <div
                        ref={fillRef}
                        className="h-full rounded-full bg-gradient-to-r from-gold to-gold-light"
                        style={{ width: "0%" }}
                    />
                </div>
                <div className="pointer-events-none absolute inset-x-0 top-1/2 h-3 -translate-y-1/2">
                    {index.attempts.map((a, i) => (
                        <span
                            key={i}
                            className={`absolute top-0 h-3 w-0.5 rounded-full ${
                                a.status === "correct" ? "bg-success/70" : "bg-error"
                            }`}
                            style={{
                                left: duration > 0 ? `${(a.startMs / duration) * 100}%` : "0%",
                            }}
                        />
                    ))}
                </div>
                <div
                    ref={thumbRef}
                    className="pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-gold bg-white shadow-md"
                    style={{ left: "0%" }}
                />

                {/* dir="ltr" is mandatory — under the page's global RTL the slider would
                    otherwise fill from the right. */}
                <Slider
                    dir="ltr"
                    aria-label="موضع التشغيل"
                    min={0}
                    max={Math.max(1, duration)}
                    step={100}
                    value={[Math.min(scrubMs ?? syncMs, Math.max(1, duration))]}
                    onValueChange={(v) => {
                        scrubbingRef.current = true;
                        setScrubMs(v[0]);
                        const pct = duration > 0 ? Math.min(100, (v[0] / duration) * 100) : 0;
                        if (fillRef.current) fillRef.current.style.width = `${pct}%`;
                        if (thumbRef.current) thumbRef.current.style.left = `${pct}%`;
                        if (elapsedRef.current) elapsedRef.current.textContent = formatTime(v[0]);
                    }}
                    onValueCommit={(v) => {
                        scrubbingRef.current = false;
                        seek(v[0]);
                        setScrubMs(null);
                    }}
                    className="relative z-10 py-3 [&_[data-slot=slider-thumb]]:opacity-0 [&_[data-slot=slider-track]]:bg-transparent"
                />
            </div>

            <span className="w-10 shrink-0 text-xs tabular-nums text-text-secondary">
                {formatTime(duration)}
            </span>
        </div>
    );
}
