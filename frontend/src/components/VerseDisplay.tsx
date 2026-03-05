import { useSessionStore } from "../stores/session";
import { cn } from "../lib/cn";

export function VerseDisplay() {
    const { words, currentWordIndex, wordResults } = useSessionStore();

    // Group words by ayah
    const ayahGroups: { ayah: number; startIdx: number; endIdx: number }[] = [];
    let prevAyah = -1;
    words.forEach((w, i) => {
        if (w.ayah !== prevAyah) {
            ayahGroups.push({ ayah: w.ayah, startIdx: i, endIdx: i });
            prevAyah = w.ayah;
        } else {
            ayahGroups[ayahGroups.length - 1].endIdx = i;
        }
    });

    return (
        <div className="w-full max-w-4xl mx-auto px-4">
            {ayahGroups.map((group) => (
                <div key={group.ayah} className="mb-6">
                    {/* Ayah number badge */}
                    <div className="flex items-center gap-3 mb-3">
                        <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-gold/10 text-gold text-sm font-medium border border-gold/20">
                            {group.ayah}
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

                            return (
                                <div
                                    key={globalIdx}
                                    className={cn(
                                        "relative inline-flex flex-col items-center px-3 py-2 rounded-xl transition-all duration-300",
                                        // Base dimmed state
                                        !isPast && !isActive && !isInterim && !result && "opacity-40",
                                        // Active word
                                        isActive && !result && "word-active bg-gold/10 border border-gold/40 opacity-100",
                                        // Interim (unconfirmed) — white pulsing
                                        isInterim && "word-interim bg-white/5 border border-white/30 opacity-90",
                                        // Confirmed correct
                                        !isInterim && result?.status === "correct" && "bg-success/10 border border-success/30 opacity-100",
                                        // Confirmed incorrect
                                        !isInterim && result?.status === "incorrect" && "bg-error/10 border border-error/30 opacity-100",
                                        // Skipped
                                        result?.status === "skipped" && "bg-surface-hover/50 border border-border opacity-70"
                                    )}
                                >
                                    {/* Uthmani text */}
                                    <span
                                        className={cn(
                                            "text-2xl leading-relaxed font-[var(--font-arabic)] select-none",
                                            // Interim text color — white
                                            isInterim && "text-white",
                                            // Confirmed colors
                                            !isInterim && result?.status === "correct" && "text-success",
                                            !isInterim && result?.status === "incorrect" && "text-error",
                                            result?.status === "skipped" && "text-text-muted",
                                            isActive && !result && "text-gold-light",
                                            !result && !isActive && "text-text-primary"
                                        )}
                                    >
                                        {word.uthmani_text}
                                    </span>

                                    {/* Score display: only when server sends details */}
                                    {result && result.status !== "skipped" && result.total_score != null && (
                                        <div className="flex flex-col items-center gap-0.5 mt-1 text-[10px] text-text-muted">
                                            {result.char_score != null && <span title="Character score">Char: {Math.round(result.char_score * 100)}%</span>}
                                            {result.diacritic_score != null && <span title="Diacritics score">Diac: {Math.round(result.diacritic_score * 100)}%</span>}
                                            {result.acoustic_score != null && <span title="Acoustic score">Ac: {Math.round(result.acoustic_score * 100)}%</span>}
                                            <span className="text-text-muted/80" title="Total score">{Math.round(result.total_score * 100)}%</span>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>
            ))}
        </div>
    );
}
