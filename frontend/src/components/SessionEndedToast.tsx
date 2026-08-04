import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, X } from "lucide-react";
import { socket } from "../lib/socket";
import { cn } from "../lib/cn";

// How long the notice stays up before it fades out on its own.
const VISIBLE_MS = 6000;
// Must match the exit animation's duration below: the node is unmounted only once it has
// finished animating out.
const EXIT_MS = 200;

/**
 * Top-center notice that the session is over.
 *
 * Driven by `session_stopped`, which the server emits exactly once per session whichever
 * path ended it — the range running out, the last word being skipped, or the reciter
 * pressing stop. The automatic ends are the ones that need announcing: nothing else on
 * screen changes much when the last word of the range passes, so without this the session
 * closes silently.
 */
export function SessionEndedToast() {
    const [visible, setVisible] = useState(false);
    const [leaving, setLeaving] = useState(false);
    const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

    const clearTimers = useCallback(() => {
        timers.current.forEach(clearTimeout);
        timers.current = [];
    }, []);

    const dismiss = useCallback(() => {
        clearTimers();
        setLeaving(false);
        setVisible(false);
    }, [clearTimers]);

    useEffect(() => {
        const onSessionStopped = () => {
            clearTimers();
            setVisible(true);
            setLeaving(false);
            timers.current.push(setTimeout(() => setLeaving(true), VISIBLE_MS));
            timers.current.push(
                setTimeout(() => {
                    setVisible(false);
                    setLeaving(false);
                }, VISIBLE_MS + EXIT_MS)
            );
        };

        socket.on("session_stopped", onSessionStopped);
        // A new session supersedes the previous one's notice immediately.
        socket.on("session_started", dismiss);
        return () => {
            socket.off("session_stopped", onSessionStopped);
            socket.off("session_started", dismiss);
            clearTimers();
        };
    }, [clearTimers, dismiss]);

    if (!visible) return null;

    return (
        // Sits above the fixed control bar, at the same offsets DetectedWordToast uses. The
        // two never overlap: that one clears itself on session_stopped, which is this one's cue.
        <div className="pointer-events-none fixed inset-x-0 bottom-32 z-50 flex justify-center px-4 sm:bottom-28">
            <div
                dir="rtl"
                role="status"
                aria-live="polite"
                className={cn(
                    "pointer-events-auto flex max-w-[92vw] items-center gap-3 rounded-2xl border border-gold/30 bg-surface-elevated/95 px-4 py-3 font-arabic shadow-lg backdrop-blur-md",
                    leaving
                        ? "animate-out fade-out slide-out-to-bottom-2 fill-mode-forwards duration-200"
                        : "animate-in fade-in slide-in-from-bottom-2 duration-300"
                )}
            >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-gold/30 bg-gold/10 text-gold">
                    <CheckCircle2 className="h-5 w-5" />
                </span>
                <p className="min-w-0 text-sm font-semibold text-text-primary">انتهت الجلسة</p>
                <button
                    type="button"
                    aria-label="إغلاق"
                    onClick={dismiss}
                    className="ms-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-surface-hover hover:text-text-primary"
                >
                    <X className="h-4 w-4" />
                </button>
            </div>
        </div>
    );
}
