import { useState } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { ListChecks } from "lucide-react";
import type {
    ErrorType,
    MuaalemWordDetail,
    RecitedUnit,
    TajweedRule,
    Word,
    WordErrorDetail,
} from "../types";
import { cn } from "../lib/cn";
import {
    countByType,
    ERROR_LABELS_AR,
    ERROR_STYLES,
    ERROR_TYPES,
    SPEECH_ERROR_LABELS,
} from "../lib/errorDisplay";
import { verseHeading } from "../lib/surahNames";

/**
 * Presentational error panel for one word, shared by the live view (ErrorSidebar) and the
 * session playback page. It takes the word and its muaalem detail as props rather than
 * reading a store, because playback keeps its timeline outside the session store.
 */

/** The subset of a result the panel needs — satisfied by both WordResult and a timeline entry. */
export interface ErrorPanelResult extends MuaalemWordDetail {
    status: "correct" | "incorrect" | "skipped";
    total_score: number;
}

function scoreColor(status: ErrorPanelResult["status"] | undefined): string {
    if (status === "correct") return "text-success";
    if (status === "incorrect") return "text-error";
    return "text-text-muted";
}

/** The tab to open first for a word: the flagged dominant type, else the busiest, else the
 *  first tab. Ties go to whichever comes first in ERROR_TYPES, i.e. the rightmost tab. */
function preferredTab(result: ErrorPanelResult, counts: Record<ErrorType, number>): ErrorType {
    if (result.error_type) return result.error_type;
    const top = ERROR_TYPES.reduce((a, b) => (counts[b] > counts[a] ? b : a), ERROR_TYPES[0]);
    return counts[top] > 0 ? top : ERROR_TYPES[0];
}

function TajweedRuleRow({ rule }: { rule: TajweedRule }) {
    return (
        <div className="flex items-baseline gap-2 text-xs">
            <span className="font-medium text-text-primary">{rule.name_ar || rule.name_en}</span>
            {rule.name_ar && rule.name_en && <span className="text-text-muted">{rule.name_en}</span>}
            {rule.golden_len != null && <span className="text-text-muted">· {rule.golden_len}</span>}
        </div>
    );
}

function ErrorCard({ error, type }: { error: WordErrorDetail; type: ErrorType }) {
    const speech = SPEECH_ERROR_LABELS[error.speech_error_type];
    const isLength = error.expected_len != null || error.predicted_len != null;

    return (
        <div className="rounded-lg border border-border/60 bg-surface/60 p-3">
            <span
                className={cn(
                    "inline-block rounded border px-1.5 py-0.5 text-xs leading-tight",
                    ERROR_STYLES[type]
                )}
            >
                {speech.ar}
            </span>

            {isLength ? (
                <div className="mt-2 flex items-center gap-3 text-base">
                    <span className="text-text-muted">الطول</span>
                    <span className="font-mono text-text-primary">
                        {error.expected_len ?? "?"}
                        <span className="text-text-muted"> ← </span>
                        {error.predicted_len ?? "?"}
                    </span>
                </div>
            ) : (
                <div className="mt-2 grid grid-cols-2 gap-2 text-base">
                    <div className="min-w-0">
                        <div className="text-xs text-text-muted">المتوقّع</div>
                        <div className="font-phoneme text-lg leading-relaxed text-text-primary [overflow-wrap:anywhere]">
                            {error.expected_ph || "—"}
                        </div>
                    </div>
                    <div className="min-w-0">
                        <div className="text-xs text-text-muted">المنطوق</div>
                        <div className="font-phoneme text-lg leading-relaxed text-text-primary [overflow-wrap:anywhere]">
                            {error.predicted_ph || "—"}
                        </div>
                    </div>
                </div>
            )}

            {!!error.rules?.length && (
                <div className="mt-2 space-y-1 border-t border-border/40 pt-2">
                    {error.rules.map((rule, i) => (
                        <TajweedRuleRow key={i} rule={rule} />
                    ))}
                </div>
            )}
        </div>
    );
}

/** What the reciter actually produced for the word (its phonetic unit), vs. what's expected. */
function RecitedSection({ recited }: { recited: RecitedUnit }) {
    const merged = recited.words.length > 1;
    return (
        <div className="mt-3 rounded-xl border border-border/60 bg-surface/50 p-3">
            {recited.ph ? (
                /* Stacked, not two columns: a phoneme string is as long as the whole word and
                   has no spaces to break at, so half a 320px panel wrapped it to shreds.
                   `anywhere` still guards the rare unit that outruns even the full width. */
                <div className="space-y-3 text-center">
                    <div>
                        <div className="text-xs text-text-muted">المنطوق</div>
                        <div className="mt-1.5 font-phoneme text-xl leading-relaxed text-text-primary [overflow-wrap:anywhere]">
                            {recited.ph}
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-text-muted">المتوقّع</div>
                        <div className="mt-1.5 font-phoneme text-xl leading-relaxed text-text-secondary [overflow-wrap:anywhere]">
                            {recited.expected_ph}
                        </div>
                        {/* Reference words, not the reciter's output — so it belongs under
                            المتوقّع; anywhere above it read as what the reciter said. An idgham
                            unit can merge five or more words, hence the wrapping rather than a
                            one-line badge. */}
                        {merged && (
                            <div
                                className="mt-1.5 text-xs leading-relaxed text-gold-light [overflow-wrap:anywhere]"
                                title="حروف متداخلة صوتيًا (إدغام) — تُنطق ككلمة واحدة"
                            >
                                مُدغمة: {recited.words.join(" + ")}
                            </div>
                        )}
                    </div>
                </div>
            ) : (
                <div className="text-center text-sm text-text-muted">لم تُنطق</div>
            )}
        </div>
    );
}

/**
 * Tabs + issue list for one word. Remounted (via `key`) whenever the described word changes,
 * so the active tab resets to that word's preferred tab without a state-syncing effect.
 */
function WordErrorTabs({ result }: { result: ErrorPanelResult }) {
    const counts = countByType(result.errors);
    const [activeTab, setActiveTab] = useState<ErrorType>(() => preferredTab(result, counts));
    const activeErrors = (result.errors ?? []).filter((e) => e.error_type === activeTab);
    const totalErrors = result.errors?.length ?? 0;

    return (
        <>
            <div className="mt-4 flex gap-1.5">
                {ERROR_TYPES.map((type) => {
                    const active = type === activeTab;
                    return (
                        <button
                            key={type}
                            aria-pressed={active}
                            onClick={() => setActiveTab(type)}
                            className={cn(
                                "flex-1 rounded-lg border px-2 py-1.5 text-xs transition-colors",
                                active
                                    ? ERROR_STYLES[type]
                                    : "border-border bg-surface/60 text-text-secondary hover:text-text-primary"
                            )}
                        >
                            <span className="block">{ERROR_LABELS_AR[type]}</span>
                            <span className="mt-1.5 block text-[10px] opacity-80">{counts[type]}</span>
                        </button>
                    );
                })}
            </div>

            <div className="mt-4 space-y-2">
                {activeErrors.length > 0 ? (
                    activeErrors.map((error, i) => <ErrorCard key={i} error={error} type={activeTab} />)
                ) : totalErrors === 0 ? (
                    <p className="py-6 text-center text-sm text-success">لا أخطاء — قراءة سليمة ✓</p>
                ) : (
                    <p className="py-6 text-center text-sm text-text-muted">لا أخطاء من هذا النوع في هذه الكلمة.</p>
                )}
            </div>
        </>
    );
}

export interface ErrorPanelProps {
    /** Identity of the described word; changing it resets the open tab. */
    wordKey: number | string | null;
    word: Word | undefined;
    result: ErrorPanelResult | undefined;
    /** True when the user pinned this word rather than following along. */
    isPinned: boolean;
    onUnpin: () => void;
    /** What the panel follows when nothing is pinned — the live view tracks the latest
     *  scored word, playback tracks the playhead. */
    followLabel?: string;
    /** Shown when no word has been graded yet. */
    emptyMessage: string;
}

export function ErrorPanel({
    wordKey,
    word,
    result,
    isPinned,
    onUnpin,
    followLabel = "متابعة آخر كلمة",
    emptyMessage,
}: ErrorPanelProps) {
    return (
        <>
            <div className="flex items-center justify-between">
                <h2 className="text-sm font-medium text-text-secondary">تفاصيل الأخطاء</h2>
                {isPinned ? (
                    <button
                        onClick={onUnpin}
                        className="text-[11px] text-text-muted transition-colors hover:text-text-primary"
                        title="العودة إلى متابعة آخر كلمة"
                    >
                        ✕
                    </button>
                ) : (
                    <span className="text-[11px] text-text-muted">{followLabel}</span>
                )}
            </div>

            {!word || !result ? (
                <p className="mt-8 text-center text-sm text-text-muted">{emptyMessage}</p>
            ) : (
                <>
                    <div className="relative mt-4 rounded-xl border border-border/60 bg-surface/50 p-4 text-center">
                        {result.total_score != null && (
                            <span
                                className={cn(
                                    "absolute left-3 top-3 text-xs font-medium",
                                    scoreColor(result.status)
                                )}
                            >
                                {Math.round(result.total_score * 100)}%
                            </span>
                        )}
                        <div className="font-quran text-3xl leading-relaxed text-text-primary">
                            {word.uthmani_text}
                        </div>
                        <div className="mt-2 flex items-center justify-center gap-2 text-sm text-text-muted">
                            <span>
                                {verseHeading(word.surah, word.ayah)} · كلمة {word.word_index}
                            </span>
                        </div>
                    </div>

                    {result.recited && <RecitedSection recited={result.recited} />}

                    <WordErrorTabs key={wordKey} result={result} />
                </>
            )}
        </>
    );
}

/**
 * Mobile-only: the panel as a sheet that slides in from the same physical side the desktop
 * column occupies, opened from a button pinned to the top corner. Everything here is
 * `md:hidden`, so the desktop layout is untouched. Radix handles the backdrop, Escape, focus
 * trapping and scroll locking; the sheet itself scrolls like the desktop column does.
 */
function MobileErrorPanelSheet({ children }: { children: React.ReactNode }) {
    const [open, setOpen] = useState(false);

    return (
        <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
            <DialogPrimitive.Trigger asChild>
                <button
                    type="button"
                    aria-label="تفاصيل الأخطاء"
                    // Physical left, like the desktop column: the shell is mounted inside a
                    // dir=ltr wrapper, so a logical `end-3` would have landed on the right.
                    className="fixed left-3 top-3 z-30 flex h-10 w-10 items-center justify-center rounded-xl border border-border/60 bg-surface/90 text-text-secondary shadow-lg shadow-black/30 backdrop-blur transition-colors hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/50 md:hidden"
                >
                    <ListChecks className="h-5 w-5" />
                </button>
            </DialogPrimitive.Trigger>
            <DialogPrimitive.Portal>
                <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 md:hidden" />
                <DialogPrimitive.Content
                    dir="rtl"
                    className="fixed inset-y-0 left-0 z-50 flex w-[88%] max-w-sm flex-col overflow-y-auto overscroll-contain border-s border-border/60 bg-surface-elevated px-4 pt-6 pb-40 [scrollbar-color:var(--color-border-light)_transparent] [scrollbar-width:thin] data-[state=closed]:animate-out data-[state=open]:animate-in data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left md:hidden"
                >
                    {/* Radix requires a title; the panel renders its own visible heading. */}
                    <DialogPrimitive.Title className="sr-only">تفاصيل الأخطاء</DialogPrimitive.Title>
                    {children}
                </DialogPrimitive.Content>
            </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
    );
}

/**
 * Shared shell for the panel, in two forms.
 *
 * Desktop (md+): a fixed column that never scrolls with the page. The parent is a `flex` row,
 * but a fixed element takes no space in flow — so a same-sized spacer sits alongside it purely
 * to reserve the gap the real (fixed) aside occupies.
 *
 * Mobile: width is too scarce for a permanent column, so the same panel opens on demand from a
 * button in the top corner. `children` is rendered in both, but only one is ever visible; each
 * copy keeps its own open-tab state, which resets per word anyway.
 */
export function ErrorPanelAside({ children }: { children: React.ReactNode }) {
    return (
        <>
            <MobileErrorPanelSheet>{children}</MobileErrorPanelSheet>
            <div aria-hidden className="hidden w-80 shrink-0 md:block" />
            <aside
                dir="rtl"
                // A word with many errors outgrows the viewport, so the column scrolls on its
                // own: overscroll-contain keeps the verse list behind it still once it bottoms
                // out, and the thin always-visible scrollbar makes the overflow discoverable
                // (macOS overlay scrollbars are invisible until something is already scrolling).
                // pb-40 at every width, matching the page's own clearance: the fixed player and
                // the setup bar are z-30 and would otherwise cover the last error card, which a
                // smaller ≥sm padding used to allow.
                className="fixed inset-y-0 left-0 z-20 hidden w-80 flex-col overflow-y-auto overscroll-contain border-e border-border/60 bg-surface-elevated/40 px-4 pt-6 pb-40 [scrollbar-color:var(--color-border-light)_transparent] [scrollbar-width:thin] md:flex"
            >
                {children}
            </aside>
        </>
    );
}
