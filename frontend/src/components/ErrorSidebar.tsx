import { useSessionStore } from "../stores/session";
import { ErrorPanel, ErrorPanelAside } from "./ErrorPanel";

/** True once any word carries muaalem-only fields; false for the whole wav2vec2 session. */
function useHasErrorData(): boolean {
    return useSessionStore((s) =>
        Object.values(s.wordResults).some(
            (r) => r.errors !== undefined || r.error_type !== undefined || r.tajweed_score !== undefined
        )
    );
}

/** Live-session error panel: follows the latest scored word unless one is pinned. */
export function ErrorSidebar() {
    const hasErrorData = useHasErrorData();
    const words = useSessionStore((s) => s.words);
    const wordResults = useSessionStore((s) => s.wordResults);
    const selectedWordIndex = useSessionStore((s) => s.selectedWordIndex);
    const setSelectedWordIndex = useSessionStore((s) => s.setSelectedWordIndex);

    // Under wav2vec2 (no error data) the panel never appears and the layout is unchanged.
    if (!hasErrorData) return null;

    // "Latest scored" = highest global index with a result — i.e. the furthest word reached.
    const keys = Object.keys(wordResults);
    const latest = keys.length ? Math.max(...keys.map(Number)) : null;
    const isPinned = selectedWordIndex !== null;
    const index = isPinned ? selectedWordIndex : latest;

    return (
        <ErrorPanelAside>
            <ErrorPanel
                wordKey={index}
                word={index != null ? words[index] : undefined}
                result={index != null ? wordResults[index] : undefined}
                isPinned={isPinned}
                onUnpin={() => setSelectedWordIndex(null)}
                emptyMessage="ابدأ التلاوة لعرض تفاصيل الأخطاء."
            />
        </ErrorPanelAside>
    );
}
