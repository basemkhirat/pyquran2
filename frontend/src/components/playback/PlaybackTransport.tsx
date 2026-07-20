import {
    ChevronsLeft,
    ChevronsRight,
    Pause,
    Play,
    RotateCcw,
    RotateCw,
    Scissors,
    Volume2,
    VolumeX,
} from "lucide-react";
import type { AudioPlayback } from "../../hooks/useAudioPlayback";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "../../lib/cn";

const RATES = [0.75, 1, 1.25, 1.5];

// 40px on phones so seven controls plus the play button still fit across a 375px screen;
// the 44px target is restored from sm: up.
const BUTTON =
    "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-surface/80 text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/50 disabled:opacity-40 disabled:hover:bg-surface/80 sm:h-11 sm:w-11";

export function PlaybackTransport({
    playback,
    onPrevWord,
    onNextWord,
    hasTimeline,
    autoSkip,
    onAutoSkipChange,
}: {
    playback: AudioPlayback;
    onPrevWord: () => void;
    onNextWord: () => void;
    hasTimeline: boolean;
    autoSkip: boolean;
    onAutoSkipChange: (value: boolean) => void;
}) {
    const { isPlaying, rate, muted, toggle, skip, setRate, toggleMute } = playback;

    return (
        // Three tracks with equal-width outer columns, so the play cluster stays optically
        // centred no matter how many buttons sit on either side.
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1 sm:gap-2" dir="ltr">
            <div className="flex items-center justify-start gap-1.5 sm:gap-2">
                <button
                    type="button"
                    className={cn(BUTTON, "text-xs font-semibold tabular-nums")}
                    onClick={() => setRate(RATES[(RATES.indexOf(rate) + 1) % RATES.length])}
                    aria-label={`سرعة التشغيل ${rate}x`}
                    title="سرعة التشغيل"
                >
                    {rate}x
                </button>

                {/* Phones have hardware volume keys, and hiding this is what keeps the row
                    inside a 375px viewport. */}
                <button
                    type="button"
                    className={cn(BUTTON, "hidden sm:flex")}
                    onClick={toggleMute}
                    aria-label={muted ? "إلغاء الكتم" : "كتم الصوت"}
                    title={muted ? "إلغاء الكتم" : "كتم الصوت"}
                >
                    {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
                </button>
            </div>

            <div className="flex items-center justify-center gap-1.5 sm:gap-2">
            {/* Word skips matter more than usual here: recordings routinely contain long
                silent stretches, so scrubbing blind is impractical. */}
            <button
                type="button"
                className={BUTTON}
                onClick={onPrevWord}
                disabled={!hasTimeline}
                aria-label="الكلمة السابقة"
                title="الكلمة السابقة"
            >
                <ChevronsLeft className="h-5 w-5" />
            </button>

            <button
                type="button"
                className={BUTTON}
                onClick={() => skip(-5000)}
                aria-label="رجوع ٥ ثوان"
                title="رجوع ٥ ثوان"
            >
                <RotateCcw className="h-4 w-4" />
            </button>

            <button
                type="button"
                onClick={toggle}
                aria-label={isPlaying ? "إيقاف مؤقت" : "تشغيل"}
                className="mx-1 flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-gold to-gold-light text-surface shadow-lg shadow-gold/20 transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/50 focus-visible:ring-offset-2 focus-visible:ring-offset-surface sm:h-14 sm:w-14"
            >
                {isPlaying ? (
                    <Pause className="h-6 w-6 fill-current" />
                ) : (
                    <Play className="h-6 w-6 translate-x-0.5 fill-current" />
                )}
            </button>

            <button
                type="button"
                className={BUTTON}
                onClick={() => skip(5000)}
                aria-label="تقدم ٥ ثوان"
                title="تقدم ٥ ثوان"
            >
                <RotateCw className="h-4 w-4" />
            </button>

            <button
                type="button"
                className={BUTTON}
                onClick={onNextWord}
                disabled={!hasTimeline}
                aria-label="الكلمة التالية"
                title="الكلمة التالية"
            >
                <ChevronsRight className="h-5 w-5" />
            </button>
            </div>

            <div className="flex items-center justify-end gap-1.5 sm:gap-2">
                {/* Icon-only, so it needs the tooltip to stay legible. Active state matches
                    the dim-words toggle in SessionSetup. */}
                <Tooltip>
                    <TooltipTrigger asChild>
                        <button
                            type="button"
                            onClick={() => onAutoSkipChange(!autoSkip)}
                            disabled={!hasTimeline}
                            aria-pressed={autoSkip}
                            aria-label="تخطي الصمت"
                            className={cn(
                                BUTTON,
                                autoSkip &&
                                    "border-gold/40 bg-gold/10 text-gold hover:bg-gold/20 hover:text-gold"
                            )}
                        >
                            <Scissors className="h-4 w-4" />
                        </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="font-[var(--font-arabic)]" dir="rtl">
                        {autoSkip ? "تخطي الصمت مُفعّل" : "تخطي الصمت مُعطّل"}
                    </TooltipContent>
                </Tooltip>
            </div>
        </div>
    );
}
