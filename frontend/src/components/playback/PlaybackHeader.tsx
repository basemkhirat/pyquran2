import { Link } from "react-router-dom";
import { ArrowRight, Repeat } from "lucide-react";
import type { SessionPlayback } from "../../types";
import { formatTime } from "../../lib/playbackTimeline";
import { verseLabel } from "../../lib/surahNames";

function Stat({ value, label, className }: { value: string; label: string; className?: string }) {
    return (
        <div className="rounded-xl border border-border/50 bg-surface/60 px-3 py-2.5 text-center">
            <p className={`text-lg font-bold tabular-nums sm:text-xl ${className ?? "text-text-primary"}`}>
                {value}
            </p>
            <p className="mt-0.5 text-[11px] text-text-muted">{label}</p>
        </div>
    );
}

export function PlaybackHeader({ session }: { session: SessionPlayback }) {
    const { stats, range } = session;
    const scored = stats.attempts || 1;
    const percent = Math.round((stats.correct / scored) * 100);
    const scoreColor =
        percent >= 80 ? "text-success" : percent >= 50 ? "text-gold" : "text-error";
    const retried = stats.attempts - stats.distinct_recited;

    return (
        <header className="mx-auto w-full max-w-4xl px-4 pt-5">
            <div className="mb-4 flex items-center justify-between gap-3">
                <Link
                    to="/"
                    className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/50"
                >
                    <ArrowRight className="h-4 w-4" />
                    رجوع
                </Link>

                <div className="flex items-center gap-2 text-xs text-text-muted">
                    <span className="rounded-full border border-border/60 bg-surface/70 px-2.5 py-1">
                        {session.mode === "continuous" ? "تلاوة مستمرة" : "كلمة بكلمة"}
                    </span>
                    {session.duration_ms != null && (
                        <span className="tabular-nums" dir="ltr">
                            {formatTime(session.duration_ms)}
                        </span>
                    )}
                </div>
            </div>

            <div className="mb-5">
                <h1 className="text-xl font-bold text-text-primary sm:text-2xl">
                    مراجعة الجلسة
                </h1>
                {range && (
                    <p className="mt-1 text-sm text-text-secondary">
                        {verseLabel(range.start_chapter, range.start_verse)}
                        {" — "}
                        {verseLabel(range.end_chapter, range.end_verse)}
                        {session.range_inferred && (
                            <span className="ms-2 text-text-muted">(مستنتج من التلاوة)</span>
                        )}
                    </p>
                )}
            </div>

            <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
                <Stat value={`${percent}%`} label="نسبة الإتقان" className={scoreColor} />
                <Stat value={String(stats.correct)} label="كلمات صحيحة" className="text-success" />
                <Stat value={String(stats.incorrect)} label="كلمات خاطئة" className="text-error" />
                <Stat value={String(stats.distinct_recited)} label="كلمات مقروءة" />
            </div>

            {retried > 0 && (
                <p className="mb-5 flex items-center justify-center gap-1.5 text-xs text-text-muted">
                    <Repeat className="h-3.5 w-3.5" />
                    {retried} إعادة محاولة أثناء الجلسة
                </p>
            )}
        </header>
    );
}
