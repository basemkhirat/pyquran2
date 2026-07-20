import { useEffect, useMemo, useRef, useState } from "react";
import type { SessionPlayback } from "../../types";
import type { AudioPlayback } from "../../hooks/useAudioPlayback";
import { cursorAt, wordStateAt, type TimelineIndex } from "../../lib/playbackTimeline";
import { WordChip } from "../WordChip";
import { verseLabel } from "../../lib/surahNames";
import { cn } from "../../lib/cn";

interface VerseGroup {
    surah: number;
    ayah: number;
    startIdx: number;
    endIdx: number;
}

// How long to leave auto-scroll off after the user scrolls by hand, so browsing back
// through the session isn't fought by playback dragging the view forward again.
const USER_SCROLL_GRACE_MS = 3000;

export function PlaybackVerses({
    session,
    index,
    playback,
}: {
    session: SessionPlayback;
    index: TimelineIndex;
    playback: AudioPlayback;
}) {
    const { subscribe, seek, isPlaying } = playback;
    // Re-render on attempt boundaries only — a couple of times per word rather than per
    // frame. Both edges matter: the cursor moving marks an attempt *starting*, and `inside`
    // going false marks it *ending*, which is when the verdict colour appears. Without the
    // second edge a word would stay gold through the silence that follows it.
    // A fresh object each time, so an `inside` flip still re-renders even though the cursor
    // number is unchanged (setState with an equal value would bail out).
    const [tick, setTick] = useState({ cursor: -1, inside: false });
    const cursor = tick.cursor;
    const activeVerseRef = useRef<HTMLDivElement>(null);
    const lastScrolledGroup = useRef(-1);
    const userScrolledAt = useRef(0);

    useEffect(() => {
        let lastKey = Number.NaN;
        return subscribe((timeMs) => {
            const next = cursorAt(index, timeMs);
            const inside = next >= 0 && timeMs < index.attempts[next].endMs;
            const key = next * 2 + (inside ? 0 : 1);
            if (key !== lastKey) {
                lastKey = key;
                setTick({ cursor: next, inside });
            }
        });
    }, [subscribe, index]);

    // Group words by verse in one linear pass, matching the live VerseDisplay.
    const verseGroups = useMemo<VerseGroup[]>(() => {
        const groups: VerseGroup[] = [];
        let prevKey = "";
        session.words.forEach((w, i) => {
            const key = `${w.surah}:${w.ayah}`;
            if (key !== prevKey) {
                groups.push({ surah: w.surah, ayah: w.ayah, startIdx: i, endIdx: i });
                prevKey = key;
            } else {
                groups[groups.length - 1].endIdx = i;
            }
        });
        return groups;
    }, [session.words]);

    const activeDisplayIndex = cursor >= 0 ? index.attempts[cursor].displayIndex : -1;
    const activeGroupIdx = useMemo(() => {
        if (activeDisplayIndex < 0) return -1;
        return verseGroups.findIndex(
            (g) => activeDisplayIndex >= g.startIdx && activeDisplayIndex <= g.endIdx
        );
    }, [verseGroups, activeDisplayIndex]);

    useEffect(() => {
        const onUserScroll = () => {
            userScrolledAt.current = Date.now();
        };
        window.addEventListener("wheel", onUserScroll, { passive: true });
        window.addEventListener("touchmove", onUserScroll, { passive: true });
        return () => {
            window.removeEventListener("wheel", onUserScroll);
            window.removeEventListener("touchmove", onUserScroll);
        };
    }, []);

    // Scroll only when the active *verse* changes, only while playing, and not while the
    // user is browsing by hand.
    useEffect(() => {
        if (!isPlaying || activeGroupIdx < 0) return;
        if (activeGroupIdx === lastScrolledGroup.current) return;
        if (Date.now() - userScrolledAt.current < USER_SCROLL_GRACE_MS) return;
        lastScrolledGroup.current = activeGroupIdx;
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        activeVerseRef.current?.scrollIntoView({
            behavior: reduced ? "auto" : "smooth",
            block: "center",
        });
    }, [activeGroupIdx, isPlaying]);

    const timeMs = playback.timeMsRef.current;

    return (
        <div className="mx-auto w-full max-w-4xl px-4">
            {verseGroups.map((group, groupIdx) => (
                <div
                    key={`${group.surah}:${group.ayah}`}
                    ref={groupIdx === activeGroupIdx ? activeVerseRef : undefined}
                    className="mb-6"
                >
                    <div className="mb-3 flex items-center gap-3">
                        <span
                            className={cn(
                                "inline-flex h-8 shrink-0 items-center justify-center rounded-full border px-3 text-sm font-medium transition-colors",
                                groupIdx === activeGroupIdx
                                    ? "border-gold/50 bg-gold/20 text-gold"
                                    : "border-gold/20 bg-gold/10 text-gold/70"
                            )}
                        >
                            {verseLabel(group.surah, group.ayah)}
                        </span>
                        <div className="h-px flex-1 bg-border/40" />
                    </div>

                    <div className="flex flex-wrap justify-start gap-1.5 sm:gap-2">
                        {session.words.slice(group.startIdx, group.endIdx + 1).map((word, offset) => {
                            const displayIdx = group.startIdx + offset;
                            const state = wordStateAt(index, displayIdx, timeMs);
                            const attempts = index.byWord.get(displayIdx);
                            const firstAttempt = attempts?.length
                                ? index.attempts[attempts[0]]
                                : undefined;

                            return (
                                <button
                                    key={`${group.surah}:${group.ayah}:${displayIdx}`}
                                    type="button"
                                    disabled={!firstAttempt}
                                    onClick={() => firstAttempt && seek(firstAttempt.startMs)}
                                    aria-label={
                                        firstAttempt
                                            ? `${word.uthmani_text} — ${
                                                  state.result?.status === "incorrect" ? "خطأ" : "صحيحة"
                                              }`
                                            : word.uthmani_text
                                    }
                                    className={cn(
                                        "rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/50",
                                        firstAttempt ? "cursor-pointer" : "cursor-default"
                                    )}
                                >
                                    <WordChip
                                        word={word}
                                        result={state.result}
                                        isActive={state.isActive}
                                        isPast={false}
                                        isInterim={false}
                                        dimUnrecited={false}
                                        badge={
                                            state.attemptCount > 1 ? (
                                                <span
                                                    className="rounded bg-warning/15 px-1 text-warning"
                                                    title="عدد المحاولات"
                                                >
                                                    {state.attemptNo > 0
                                                        ? `${state.attemptNo}/${state.attemptCount}`
                                                        : `×${state.attemptCount}`}
                                                </span>
                                            ) : undefined
                                        }
                                    />
                                </button>
                            );
                        })}
                    </div>
                </div>
            ))}
        </div>
    );
}
