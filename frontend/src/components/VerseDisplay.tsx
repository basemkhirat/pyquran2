import { memo, useEffect, useMemo, useRef } from "react";
import { useSessionStore } from "../stores/session";
import type { Word, WordResult } from "../types";
import { cn } from "../lib/cn";

// How many verses to render ahead of the active verse. The long tail of unrecited future
// verses in a large range is not mounted until the reciter approaches it. Look-behind is
// intentionally unbounded so the user can scroll back through all recited history.
const VERSES_AFTER = 12;

interface VerseGroup {
    surah: number;
    ayah: number;
    startIdx: number;
    endIdx: number;
}

// Single word node. Memoized so an incoming word_result re-renders only the 1-2 chips whose
// props actually changed, not every mounted word.
const WordChip = memo(function WordChip({
    word,
    result,
    isActive,
    isPast,
    isInterim,
    dimUnrecited,
}: {
    word: Word;
    result: WordResult | undefined;
    isActive: boolean;
    isPast: boolean;
    isInterim: boolean;
    dimUnrecited: boolean;
}) {
    return (
        <div
            className={cn(
                "relative inline-flex flex-col items-center px-3 py-2 rounded-xl transition-all duration-300",
                dimUnrecited && "opacity-0",
                !dimUnrecited && !isPast && !isActive && !isInterim && !result && "opacity-40",
                isActive && !result && !dimUnrecited && "word-active bg-gold/10 border border-gold/40 opacity-100",
                isInterim && "word-interim bg-white/5 border border-white/30 opacity-90",
                !isInterim && result?.status === "correct" && "bg-success/10 border border-success/30 opacity-100",
                !isInterim && result?.status === "incorrect" && "bg-error/10 border border-error/30 opacity-100",
                result?.status === "skipped" && "bg-surface-hover/50 border border-border opacity-70"
            )}
        >
            <span
                className={cn(
                    "text-2xl leading-relaxed font-[var(--font-quran)] select-none",
                    isInterim && "text-white",
                    !isInterim && result?.status === "correct" && "text-success",
                    !isInterim && result?.status === "incorrect" && "text-error",
                    result?.status === "skipped" && "text-text-muted",
                    isActive && !result && "text-gold-light",
                    !result && !isActive && "text-text-primary"
                )}
            >
                {word.uthmani_text}
            </span>

            {result && result.status !== "skipped" && result.total_score != null && (
                <div className="flex flex-col items-center gap-0.5 mt-1 text-[10px] text-text-muted">
                    <span className="text-text-muted/80" title="Total score">{Math.round(result.total_score * 100)}%</span>
                </div>
            )}
        </div>
    );
});

export function VerseDisplay() {
    const { words, currentWordIndex, wordResults, hideUnrecitedWords } = useSessionStore();
    const activeVerseRef = useRef<HTMLDivElement>(null);

    // Group words by (surah, ayah) to support cross-chapter ranges. Memoized so the O(n)
    // grouping loop doesn't run on every scoring re-render.
    const verseGroups = useMemo<VerseGroup[]>(() => {
        const groups: VerseGroup[] = [];
        let prevKey = "";
        words.forEach((w, i) => {
            const key = `${w.surah}:${w.ayah}`;
            if (key !== prevKey) {
                groups.push({ surah: w.surah, ayah: w.ayah, startIdx: i, endIdx: i });
                prevKey = key;
            } else {
                groups[groups.length - 1].endIdx = i;
            }
        });
        return groups;
    }, [words]);

    // Index of the verse group containing the active word. Clamps to the last group when the
    // session is complete (currentWordIndex >= words.length) and to 0 when empty.
    const activeGroupIdx = useMemo(() => {
        if (verseGroups.length === 0) return 0;
        const idx = verseGroups.findIndex(
            (g) => currentWordIndex >= g.startIdx && currentWordIndex <= g.endIdx
        );
        return idx === -1 ? verseGroups.length - 1 : idx;
    }, [verseGroups, currentWordIndex]);

    // Render all recited history + a capped look-ahead. The active verse is always inside
    // this slice, so activeVerseRef + scrollIntoView keep working unchanged.
    const visibleGroups = useMemo(
        () => verseGroups.slice(0, Math.min(verseGroups.length, activeGroupIdx + VERSES_AFTER + 1)),
        [verseGroups, activeGroupIdx]
    );

    useEffect(() => {
        activeVerseRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, [currentWordIndex]);

    return (
        <div className="w-full max-w-4xl mx-auto px-4">
            {visibleGroups.map((group) => {
                const isActiveVerse = currentWordIndex >= group.startIdx && currentWordIndex <= group.endIdx;
                return (
                <div
                    key={`${group.surah}:${group.ayah}`}
                    ref={isActiveVerse ? activeVerseRef : undefined}
                    className="mb-6"
                >
                    {/* Chapter:Verse badge */}
                    <div className="flex items-center gap-3 mb-3">
                        <span className="inline-flex items-center justify-center min-w-[3rem] px-2 h-8 rounded-full bg-gold/10 text-gold text-sm font-medium border border-gold/20">
                            {group.surah}:{group.ayah}
                        </span>
                        <div className="flex-1 h-px bg-border/40" />
                    </div>

                    {/* Words */}
                    <div className="flex flex-wrap gap-2 justify-start">
                        {words.slice(group.startIdx, group.endIdx + 1).map((word, offsetIdx) => {
                            const globalIdx = group.startIdx + offsetIdx;
                            const result = wordResults[globalIdx];
                            const isActive = globalIdx === currentWordIndex;
                            const isPast = globalIdx < currentWordIndex;
                            const isInterim = result?.is_interim === true;
                            const notRecitedYet = globalIdx >= currentWordIndex; // current + future
                            const dimUnrecited = hideUnrecitedWords && notRecitedYet && !isInterim;

                            return (
                                <WordChip
                                    key={`${group.surah}:${group.ayah}:${globalIdx}`}
                                    word={word}
                                    result={result}
                                    isActive={isActive}
                                    isPast={isPast}
                                    isInterim={isInterim}
                                    dimUnrecited={dimUnrecited}
                                />
                            );
                        })}
                    </div>
                </div>
                );
            })}
        </div>
    );
}
