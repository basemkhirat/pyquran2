import { memo } from "react";
import type { Word, WordErrorDetail, WordResult } from "../types";
import { cn } from "../lib/cn";
import { countByType, ERROR_DOT_STYLES, ERROR_LABELS_AR, ERROR_TYPES } from "../lib/errorDisplay";

export interface WordChipProps {
    word: Word;
    result: WordResult | undefined;
    isActive: boolean;
    isPast: boolean;
    isInterim: boolean;
    dimUnrecited: boolean;
    /** Extra marker beside the score, e.g. the attempt counter on the playback page. */
    badge?: React.ReactNode;
    /** Pinned for the error sidebar. Opt-in: playback wraps the chip in its own seek button,
     *  so chip-level selection is only wired up by callers that own the click.
     *  `onSelect` takes the index so callers can pass one stable function for every chip —
     *  an inline closure per word would break the memo above. */
    index?: number;
    isSelected?: boolean;
    onSelect?: (index: number) => void;
}

// Only the muaalem backend sends errors; under wav2vec2 this renders nothing. One dot per type
// present (tajweed, then tashkeel, then normal) rather than one per error — a word with several
// errors of a type still shows one dot, with the count in its tooltip. Dots rather than labelled
// badges because three labels are wider than the chip and spilled over the neighbouring words;
// the sidebar carries the labels and the numbers.
function ErrorDots({ errors }: { errors: WordErrorDetail[] }) {
    const counts = countByType(errors);
    return (
        <div className="flex justify-center gap-1">
            {ERROR_TYPES.filter((type) => counts[type] > 0).map((type) => (
                <span
                    key={type}
                    role="img"
                    aria-label={`${ERROR_LABELS_AR[type]}: ${counts[type]}`}
                    title={`${ERROR_LABELS_AR[type]} · ${counts[type]}`}
                    className={cn("h-1.5 w-1.5 cursor-help rounded-full", ERROR_DOT_STYLES[type])}
                />
            ))}
        </div>
    );
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
    index,
    isSelected = false,
    onSelect,
}: WordChipProps) {
    // Only graded words carry error detail worth inspecting, so only those are selectable.
    const selectable =
        onSelect != null && index != null && result != null && result.status !== "skipped";
    // An interim result is still settling, so its errors would flicker as the decode catches up.
    const showErrors = !isInterim && !!result?.errors?.length;

    return (
        <div
            role={selectable ? "button" : undefined}
            tabIndex={selectable ? 0 : undefined}
            onClick={selectable ? () => onSelect!(index!) : undefined}
            onKeyDown={
                selectable
                    ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onSelect!(index!);
                          }
                      }
                    : undefined
            }
            className={cn(
                "relative inline-flex flex-col items-center rounded-xl px-2 py-1.5 transition-all duration-300 sm:px-3 sm:py-2",
                selectable && "cursor-pointer",
                // Pinned-selection ring: emerald + static, so it reads apart from the gold
                // active pulse and the white interim pulse.
                isSelected && "ring-2 ring-primary/80 ring-offset-2 ring-offset-transparent",
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

            {/* Always rendered (with an invisible placeholder when there's no score yet) so
                a word's chip is the same height before and after it's recited — otherwise
                every chip in the row resizes the moment one word gets a result. */}
            <div className="mt-1 flex items-center gap-1 text-[10px] text-text-muted">
                {result && result.status !== "skipped" && result.total_score != null ? (
                    <span className="text-text-muted/80" title="Total score">
                        {Math.round(result.total_score * 100)}%
                    </span>
                ) : (
                    <span className="invisible">0%</span>
                )}
                {badge}
            </div>

            {/* Absolutely positioned so a word's errors never stretch its row's height or
                push neighbouring words — it floats below the chip instead. */}
            {showErrors && (
                <div className="absolute left-1/2 top-full z-10 mt-1 -translate-x-1/2">
                    <ErrorDots errors={result!.errors!} />
                </div>
            )}
        </div>
    );
});
