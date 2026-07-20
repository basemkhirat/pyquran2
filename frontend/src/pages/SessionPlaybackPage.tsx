import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AudioLines, FileQuestion, MicOff } from "lucide-react";
import type { SessionPlayback } from "../types";
import { apiUrl } from "../lib/socket";
import { useAudioPlayback } from "../hooks/useAudioPlayback";
import { buildTimelineIndex, nextAttemptAfter, prevAttemptBefore } from "../lib/playbackTimeline";
import { PlaybackHeader } from "../components/playback/PlaybackHeader";
import { PlaybackVerses } from "../components/playback/PlaybackVerses";
import { PlaybackAudioBar } from "../components/playback/PlaybackAudioBar";

type LoadState =
    | { status: "loading" }
    | { status: "not_found"; id: string }
    | { status: "error"; id: string }
    | { status: "ready"; id: string; session: SessionPlayback };

function CenteredMessage({
    icon,
    title,
    detail,
    action,
}: {
    icon: React.ReactNode;
    title: string;
    detail?: string;
    action?: React.ReactNode;
}) {
    return (
        <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-border/60 bg-surface/70 text-text-muted">
                {icon}
            </div>
            <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
            {detail && <p className="max-w-sm text-sm text-text-secondary">{detail}</p>}
            {action}
        </div>
    );
}

const BACK_LINK = (
    <Link
        to="/"
        className="mt-2 rounded-xl border border-gold/30 bg-gold/10 px-4 py-2.5 text-sm font-medium text-gold transition-colors hover:bg-gold/20"
    >
        العودة للرئيسية
    </Link>
);

export function SessionPlaybackPage() {
    const { sessionId } = useParams<{ sessionId: string }>();
    const [loaded, setLoaded] = useState<LoadState>({ status: "loading" });
    const [attempt, setAttempt] = useState(0);
    const [autoSkip, setAutoSkip] = useState(true);
    const playback = useAudioPlayback();

    // Derived rather than reset inside the effect: a result belonging to a different id is
    // stale by definition, so switching sessions shows the spinner without an extra render.
    const state: LoadState =
        loaded.status !== "loading" && loaded.id !== sessionId ? { status: "loading" } : loaded;

    useEffect(() => {
        if (!sessionId) return;
        let cancelled = false;

        fetch(apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}`))
            .then(async (res) => {
                if (cancelled) return;
                if (res.status === 404) {
                    setLoaded({ status: "not_found", id: sessionId });
                    return;
                }
                if (!res.ok) throw new Error(String(res.status));
                const session = (await res.json()) as SessionPlayback;
                if (!cancelled) setLoaded({ status: "ready", id: sessionId, session });
            })
            .catch(() => {
                if (!cancelled) setLoaded({ status: "error", id: sessionId });
            });

        return () => {
            cancelled = true;
        };
    }, [sessionId, attempt]);

    const session = state.status === "ready" ? state.session : null;
    const index = useMemo(
        () =>
            session
                ? buildTimelineIndex(session)
                : { attempts: [], startsMs: [], byWord: new Map(), results: [], durationMs: 0 },
        [session]
    );

    // Keyboard transport. Arrows are deliberately NOT mirrored for RTL — every media
    // player treats Left as rewind, and users expect that regardless of text direction.
    const { toggle, skip, seek, toggleMute, timeMsRef } = playback;
    const canPlay = session?.has_recording ?? false;
    useEffect(() => {
        if (!canPlay) return;
        const onKeyDown = (e: KeyboardEvent) => {
            const target = e.target as HTMLElement | null;
            if (
                target &&
                (target.isContentEditable ||
                    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
            ) {
                return;
            }
            switch (e.key) {
                case " ":
                case "k":
                    e.preventDefault();
                    toggle();
                    break;
                case "ArrowLeft":
                    e.preventDefault();
                    skip(-5000);
                    break;
                case "ArrowRight":
                    e.preventDefault();
                    skip(5000);
                    break;
                case "[": {
                    const i = prevAttemptBefore(index, timeMsRef.current);
                    seek(i >= 0 ? index.attempts[i].startMs : 0);
                    break;
                }
                case "]": {
                    const i = nextAttemptAfter(index, timeMsRef.current);
                    if (i >= 0) seek(index.attempts[i].startMs);
                    break;
                }
                case "m":
                    toggleMute();
                    break;
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [canPlay, index, toggle, skip, seek, toggleMute, timeMsRef]);

    if (state.status === "loading") {
        return (
            <div className="flex min-h-[100dvh] items-center justify-center">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-gold/30 border-t-gold" />
            </div>
        );
    }

    if (state.status === "not_found") {
        return (
            <CenteredMessage
                icon={<FileQuestion className="h-6 w-6" />}
                title="الجلسة غير موجودة"
                detail="ربما لم يتم حفظ هذه الجلسة، أو تم حذفها من الخادم."
                action={BACK_LINK}
            />
        );
    }

    if (state.status === "error") {
        return (
            <CenteredMessage
                icon={<FileQuestion className="h-6 w-6" />}
                title="تعذّر تحميل الجلسة"
                detail="تحقق من اتصالك بالخادم ثم أعد المحاولة."
                action={
                    <button
                        type="button"
                        onClick={() => setAttempt((n) => n + 1)}
                        className="mt-2 rounded-xl border border-gold/30 bg-gold/10 px-4 py-2.5 text-sm font-medium text-gold transition-colors hover:bg-gold/20"
                    >
                        إعادة المحاولة
                    </button>
                }
            />
        );
    }

    if (state.session.words.length === 0) {
        return (
            <CenteredMessage
                icon={<AudioLines className="h-6 w-6" />}
                title="لا توجد كلمات مسجّلة"
                detail="بدأت هذه الجلسة لكن لم تُسجَّل فيها أي كلمة."
                action={BACK_LINK}
            />
        );
    }

    return (
        // Clears the fixed player, which is now the same two rows at every breakpoint.
        <div className="min-h-[100dvh] pb-40">
            <PlaybackHeader session={state.session} />
            <PlaybackVerses session={state.session} index={index} playback={playback} />

            {state.session.has_recording ? (
                <PlaybackAudioBar
                    src={apiUrl(`/api/sessions/${encodeURIComponent(state.session.id)}/recording`)}
                    index={index}
                    playback={playback}
                    autoSkip={autoSkip}
                    onAutoSkipChange={setAutoSkip}
                />
            ) : (
                <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border/50 bg-surface/90 py-4 backdrop-blur-xl">
                    <p className="flex items-center justify-center gap-2 text-sm text-text-secondary">
                        <MicOff className="h-4 w-4" />
                        لا يوجد تسجيل صوتي لهذه الجلسة
                    </p>
                </div>
            )}
        </div>
    );
}
