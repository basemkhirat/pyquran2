import { useEffect, useRef, useState } from "react";
import { socket } from "../lib/socket";
import { cn } from "../lib/cn";

// Most recent detected words to keep on screen at once — the subtitle window size.
const MAX_WORDS = 7;
// How long the subtitle line lingers after the last confirmed word before it clears.
// Generous so a slow, word-by-word recitation keeps building one line instead of
// clearing between words; a real pause in speech still fades it out.
const SUBTITLE_CLEAR_MS = 7000;

interface SubtitleWord {
    // Unique Quran word slot (chapter:verse:word). Lets a retried/self-corrected word
    // update in place instead of being appended a second time.
    key: string;
    text: string;
    // True while this is a live, unconfirmed transcript that may still self-correct.
    isInterim: boolean;
}

interface WordResultEvent {
    detected_text?: string;
    is_interim?: boolean;
    chapter_number?: number;
    verse_number?: number;
    word_number?: number;
}

/**
 * Bottom-center subtitle that accumulates detected word text from `word_result`
 * events into a rolling line of up to {@link MAX_WORDS} words — like movie
 * subtitles. Interim (unconfirmed) results are shown live, dimmed, and settle
 * to full opacity once confirmed. When the same Quran word slot is re-emitted
 * (live updates, retries, self-corrections), its text is updated in place
 * rather than duplicated.
 */
export function DetectedWordToast() {
    const [words, setWords] = useState<SubtitleWord[]>([]);
    const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        const clearTimer = () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
                timeoutRef.current = null;
            }
        };

        const onWordResult = (data: WordResultEvent) => {
            const detected = (data.detected_text ?? "").trim();
            if (!detected) return;

            const isInterim = data.is_interim === true;
            const key = `${data.chapter_number}:${data.verse_number}:${data.word_number}`;
            setWords((prev) => {
                const last = prev[prev.length - 1];
                // Same word slot re-emitted (live update / retry / self-correction): update in place.
                const entry: SubtitleWord = { key, text: detected, isInterim };
                const next = last && last.key === key ? [...prev.slice(0, -1), entry] : [...prev, entry];
                // Keep only the most recent MAX_WORDS so it reads like a subtitle window.
                return next.slice(-MAX_WORDS);
            });

            clearTimer();
            timeoutRef.current = setTimeout(() => setWords([]), SUBTITLE_CLEAR_MS);
        };

        // A new or ending session starts the subtitle line fresh.
        const onSessionReset = () => {
            clearTimer();
            setWords([]);
        };

        socket.on("word_result", onWordResult);
        socket.on("session_started", onSessionReset);
        socket.on("session_stopped", onSessionReset);
        return () => {
            socket.off("word_result", onWordResult);
            socket.off("session_started", onSessionReset);
            socket.off("session_stopped", onSessionReset);
            clearTimer();
        };
    }, []);

    if (words.length === 0) return null;

    return (
        <div className="pointer-events-none fixed inset-x-0 bottom-32 z-40 flex justify-center px-4 sm:bottom-28">
            {/* One persistent toast. It animates in once when it first appears; each word is
                its own span keyed by slot+text, so only the newly entered/updated word
                animates in — older words stay put with no remount or re-animation. */}
            <div
                dir="rtl"
                className="animate-in fade-in slide-in-from-bottom-4 duration-200 flex max-w-[90vw] flex-nowrap items-baseline justify-center gap-x-2 overflow-hidden whitespace-nowrap rounded-full border border-border/60 bg-surface-elevated/95 px-5 py-2.5 font-arabic text-lg text-text-primary shadow-lg backdrop-blur-md"
            >
                {words.map((w) => (
                    <span
                        key={`${w.key}-${w.text}-${w.isInterim ? "interim" : "final"}`}
                        className={cn(
                            "inline-block animate-in fade-in zoom-in-95 slide-in-from-bottom-2 duration-300 ease-out",
                            w.isInterim ? "text-text-secondary/70" : "text-text-primary"
                        )}
                    >
                        {w.text}
                    </span>
                ))}
            </div>
        </div>
    );
}
