import { useSessionStore } from "../stores/session";
import { RotateCcw, RefreshCw } from "lucide-react";
import { socket } from "../lib/socket";

export function SessionSummary() {
    const {
        words,
        wordResults,
        getCorrectCount,
        getAverageCharScore,
        getAverageDiacriticScore,
        reset,
        setSessionStatus,
        setWords,
        selectedRange,
        scoreThreshold,
        sessionMode,
    } = useSessionStore();

    const totalWords = words.length;
    const correctCount = getCorrectCount();
    const incorrectResults = Object.entries(wordResults).filter(
        ([, r]) => r.status === "incorrect"
    );
    const skippedCount = Object.values(wordResults).filter(
        (r) => r.status === "skipped"
    ).length;
    const avgChar = Math.round(getAverageCharScore() * 100);
    const avgDiacritic = Math.round(getAverageDiacriticScore() * 100);
    const overallPercent = totalWords > 0 ? Math.round((correctCount / totalWords) * 100) : 0;

    const handleRestart = () => {
        reset();
    };

    const handleRetryIncorrect = async () => {
        if (!selectedRange) return;

        // Get incorrect words only
        const incorrectWords = incorrectResults.map(([idx]) => words[Number(idx)]);
        setWords(incorrectWords);
        setSessionStatus("recording");

        const payload = {
            start_chapter_number: selectedRange.startChapter,
            start_verse_number: selectedRange.startVerse,
            end_chapter_number: selectedRange.endChapter,
            end_verse_number: selectedRange.endVerse,
            score_threshold: scoreThreshold,
            mode: sessionMode,
        };
        if (socket.connected) {
            socket.emit("start_session", payload);
        } else {
            socket.connect();
            socket.once("connect", () => socket.emit("start_session", payload));
        }
    };

    // Color for overall score
    const scoreColor =
        overallPercent >= 80
            ? "text-success"
            : overallPercent >= 50
                ? "text-gold"
                : "text-error";

    return (
        <div className="flex items-center justify-center min-h-screen p-4">
            <div className="w-full max-w-lg bg-surface-card/80 backdrop-blur-xl rounded-2xl border border-border p-8 shadow-2xl">
                {/* Header */}
                <div className="text-center mb-8">
                    <h2 className="text-2xl font-bold text-text-primary font-[var(--font-arabic)]">
                        ملخص الجلسة
                    </h2>
                </div>

                {/* Overall Score Circle */}
                <div className="flex justify-center mb-8">
                    <div className="relative w-32 h-32">
                        <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                            <circle
                                cx="50" cy="50" r="42"
                                stroke="currentColor"
                                strokeWidth="6"
                                fill="none"
                                className="text-surface-hover"
                            />
                            <circle
                                cx="50" cy="50" r="42"
                                stroke="currentColor"
                                strokeWidth="6"
                                fill="none"
                                className={scoreColor}
                                strokeDasharray={`${overallPercent * 2.64} 264`}
                                strokeLinecap="round"
                            />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className={`text-3xl font-bold ${scoreColor}`}>
                                {overallPercent}%
                            </span>
                            <span className="text-text-muted text-xs">النتيجة</span>
                        </div>
                    </div>
                </div>

                {/* Stats Grid */}
                <div className="grid grid-cols-2 gap-3 mb-8">
                    <div className="bg-surface/60 rounded-xl p-4 text-center border border-border/50">
                        <p className="text-2xl font-bold text-text-primary">{totalWords}</p>
                        <p className="text-xs text-text-muted mt-1">إجمالي الكلمات</p>
                    </div>
                    <div className="bg-surface/60 rounded-xl p-4 text-center border border-border/50">
                        <p className="text-2xl font-bold text-success">{correctCount}</p>
                        <p className="text-xs text-text-muted mt-1">كلمات صحيحة</p>
                    </div>
                    <div className="bg-surface/60 rounded-xl p-4 text-center border border-border/50">
                        <p className="text-2xl font-bold text-gold">{avgChar}%</p>
                        <p className="text-xs text-text-muted mt-1">دقة الحروف</p>
                    </div>
                    <div className="bg-surface/60 rounded-xl p-4 text-center border border-border/50">
                        <p className="text-2xl font-bold text-gold">{avgDiacritic}%</p>
                        <p className="text-xs text-text-muted mt-1">دقة التشكيل</p>
                    </div>
                </div>

                {/* Skipped info */}
                {skippedCount > 0 && (
                    <p className="text-center text-text-muted text-sm mb-6">
                        تم تخطي {skippedCount} كلمة
                    </p>
                )}

                {/* Actions */}
                <div className="flex gap-3">
                    {incorrectResults.length > 0 && (
                        <button
                            onClick={handleRetryIncorrect}
                            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-gold/10 border border-gold/30 text-gold hover:bg-gold/20 transition-all font-medium"
                        >
                            <RotateCcw className="w-4 h-4" />
                            إعادة الخاطئة ({incorrectResults.length})
                        </button>
                    )}
                    <button
                        onClick={handleRestart}
                        className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-surface-hover/60 border border-border text-text-secondary hover:text-text-primary hover:border-border-light transition-all font-medium"
                    >
                        <RefreshCw className="w-4 h-4" />
                        جلسة جديدة
                    </button>
                </div>
            </div>
        </div>
    );
}
