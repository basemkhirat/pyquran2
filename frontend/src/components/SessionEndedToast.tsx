import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, ChevronLeft, Headphones, X } from "lucide-react";
import { socket } from "../lib/socket";
import { cn } from "../lib/cn";
import { useSessionStore } from "../stores/session";

// How long the notice stays up before it fades out on its own.
const VISIBLE_MS = 6000;
// Longer once it becomes a link: 6s is enough to read a closing notice, but not to notice an
// invitation, decide, and click it — and it only appears partway through that window.
const REVIEW_VISIBLE_MS = 12000;
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
 *
 * When the session was recorded, the whole notice doubles as the link to its playback page —
 * the moment the recording exists is exactly when it is worth offering. `lastSession` arrives
 * with `session_ended`, which follows `session_stopped` once the server has closed the WAV, so
 * the invitation appears a beat after the notice rather than with it. That ordering is the
 * point: linking any earlier would open playback on a file still being written.
 */
export function SessionEndedToast() {
    const [visible, setVisible] = useState(false);
    const [leaving, setLeaving] = useState(false);
    const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
    // Null unless the session that just ended was recorded — App clears it on session_started
    // and sets it from session_ended only when that event carried a recording URL.
    const lastSession = useSessionStore((s) => s.lastSession);

    const clearTimers = useCallback(() => {
        timers.current.forEach(clearTimeout);
        timers.current = [];
    }, []);

    const dismiss = useCallback(() => {
        clearTimers();
        setLeaving(false);
        setVisible(false);
    }, [clearTimers]);

    const scheduleHide = useCallback(
        (ms: number) => {
            clearTimers();
            timers.current.push(setTimeout(() => setLeaving(true), ms));
            timers.current.push(
                setTimeout(() => {
                    setVisible(false);
                    setLeaving(false);
                }, ms + EXIT_MS)
            );
        },
        [clearTimers]
    );

    // The recording lands after the notice is already up, so restart the clock when it does —
    // otherwise the link inherits whatever is left of the plain notice's 6s. Once the notice has
    // started fading there is nothing worth reviving: session_ended follows session_stopped by
    // well under the 6s window, so this only skips a race that cannot happen in practice.
    useEffect(() => {
        if (!visible || leaving || !lastSession) return;
        scheduleHide(REVIEW_VISIBLE_MS);
    }, [visible, leaving, lastSession, scheduleHide]);

    useEffect(() => {
        const onSessionStopped = () => {
            setVisible(true);
            setLeaving(false);
            scheduleHide(VISIBLE_MS);
        };

        socket.on("session_stopped", onSessionStopped);
        // A new session supersedes the previous one's notice immediately.
        socket.on("session_started", dismiss);
        return () => {
            socket.off("session_stopped", onSessionStopped);
            socket.off("session_started", dismiss);
            clearTimers();
        };
    }, [clearTimers, dismiss, scheduleHide]);

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
                {lastSession ? (
                    // The whole notice is the target, not a small icon inside it: the invitation
                    // and the thing it invites you to click are then the same object.
                    <Link
                        to={`/sessions/${lastSession.id}`}
                        onClick={dismiss}
                        className="group flex min-w-0 items-center gap-3 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-gold/50"
                    >
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-gold/30 bg-gold/10 text-gold transition-colors group-hover:bg-gold/20">
                            <Headphones className="h-5 w-5" />
                        </span>
                        <span className="min-w-0">
                            <span className="block text-sm font-semibold text-text-primary">
                                انتهت الجلسة
                            </span>
                            <span className="block text-xs text-gold">
                                اضغط لمراجعة التلاوة والاستماع إلى التسجيل
                            </span>
                        </span>
                        <ChevronLeft className="h-4 w-4 shrink-0 text-gold transition-transform group-hover:-translate-x-0.5" />
                    </Link>
                ) : (
                    <>
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-gold/30 bg-gold/10 text-gold">
                            <CheckCircle2 className="h-5 w-5" />
                        </span>
                        <p className="min-w-0 text-sm font-semibold text-text-primary">
                            انتهت الجلسة
                        </p>
                    </>
                )}
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
