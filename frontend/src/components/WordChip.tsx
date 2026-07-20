import { memo } from "react";
import type { Word, WordResult } from "../types";
import { cn } from "../lib/cn";

export interface WordChipProps {
    word: Word;
    result: WordResult | undefined;
    isActive: boolean;
    isPast: boolean;
    isInterim: boolean;
    dimUnrecited: boolean;
    /** Extra marker beside the score, e.g. the attempt counter on the playback page. */
    badge?: React.ReactNode;
}

// Single word node, shared by the live recitation view and session playback. Memoized so an
// incoming word_result (or a playback tick) re-renders only the 1-2 chips whose props
// actually changed, not every mounted word — callers must therefore pass a stable `result`
// reference rather than synthesizing a new object per render.
export const WordChip = memo(function WordChip({
    word,
    result,
    isActive,
    isPast,
    isInterim,
    dimUnrecited,
    badge,
}: WordChipProps) {
    return (
        <div
            className={cn(
                "relative inline-flex flex-col items-center rounded-xl px-2 py-1.5 transition-all duration-300 sm:px-3 sm:py-2",
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
                style={{ fontFamily: "var(--font-quran)" }}
                className={cn(
                    "text-xl leading-relaxed select-none sm:text-2xl",
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

            {((result && result.status !== "skipped" && result.total_score != null) || badge) && (
                <div className="mt-1 flex items-center gap-1 text-[10px] text-text-muted">
                    {result && result.status !== "skipped" && result.total_score != null && (
                        <span className="text-text-muted/80" title="Total score">
                            {Math.round(result.total_score * 100)}%
                        </span>
                    )}
                    {badge}
                </div>
            )}
        </div>
    );
});
