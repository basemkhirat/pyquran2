import type { SessionPlayback, SessionTimelineEntry, WordResult } from "../types";

/**
 * Turns a recorded session's timeline into an index that answers, for any playback
 * position: which word is being recited, and how should each word be coloured.
 *
 * Two properties of the stored data drive the design:
 *
 * - The timeline is *sparse* — only spoken words are recorded, so most words in the
 *   displayed range may have no entry at all.
 * - The timeline is *not unique* — in word_by_word mode a failed word is re-recorded on
 *   every retry, so one word can have several attempts with different verdicts.
 */

/** One recorded attempt, resolved against the display word list. */
export interface Attempt {
    /** Index into SessionPlayback.words; -1 when the entry matched no display word. */
    displayIndex: number;
    status: "correct" | "incorrect";
    score: number;
    startMs: number;
    endMs: number;
    /** 1-based position among that word's attempts, and how many there are in total. */
    attemptNo: number;
    attemptCount: number;
}

export interface TimelineIndex {
    attempts: Attempt[];
    /** attempts[i].startMs, extracted so binary search works on a flat number array. */
    startsMs: number[];
    /** displayIndex -> indices into attempts, ascending. */
    byWord: Map<number, number[]>;
    /** One WordResult per attempt, built once so memoized chips keep a stable reference. */
    results: WordResult[];
    durationMs: number;
}

/** What a single word should look like at a given moment. */
export interface WordState {
    isActive: boolean;
    result: WordResult | undefined;
    attemptNo: number;
    attemptCount: number;
}

const IDLE_STATE: WordState = { isActive: false, result: undefined, attemptNo: 0, attemptCount: 0 };

/** First index whose value is > target (i.e. std::upper_bound). */
function upperBound(values: number[], target: number): number {
    let lo = 0;
    let hi = values.length;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (values[mid] <= target) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

export function buildTimelineIndex(session: SessionPlayback): TimelineIndex {
    // The API already sorts by start_time, but sorting again is cheap insurance against a
    // corrupt file — every lookup below assumes monotonic starts.
    const entries: SessionTimelineEntry[] = [...session.timeline].sort(
        (a, b) => a.start_time - b.start_time || a.end_time - b.end_time
    );

    // Clamp to the true recording length. If the store's writer task ever dies mid-session
    // the sample clock keeps advancing, leaving later timestamps past the end of the audio.
    const lastEnd = entries.length ? entries[entries.length - 1].end_time : 0;
    const durationMs = session.duration_ms ?? lastEnd + 2000;

    const counts = new Map<number, number>();
    for (const e of entries) {
        if (e.display_index == null) continue;
        counts.set(e.display_index, (counts.get(e.display_index) ?? 0) + 1);
    }

    const attempts: Attempt[] = [];
    const startsMs: number[] = [];
    const byWord = new Map<number, number[]>();
    const results: WordResult[] = [];
    const seen = new Map<number, number>();

    entries.forEach((e) => {
        const displayIndex = e.display_index ?? -1;
        const attemptNo = (seen.get(displayIndex) ?? 0) + 1;
        seen.set(displayIndex, attemptNo);

        const startMs = Math.min(e.start_time, durationMs);
        const endMs = Math.min(Math.max(e.end_time, startMs), durationMs);

        const i = attempts.length;
        attempts.push({
            displayIndex,
            status: e.status,
            score: e.score,
            startMs,
            endMs,
            attemptNo,
            attemptCount: counts.get(displayIndex) ?? 1,
        });
        startsMs.push(startMs);
        // WordChip is memoized on `result` identity, so this object must be created once
        // here and reused every frame — never rebuilt per render.
        results.push({
            chapter_number: e.chapter_number,
            verse_number: e.verse_number,
            word_number: e.word_number,
            status: e.status,
            total_score: e.score,
            expected_text: e.word_text,
            detected_text: "",
        });

        if (displayIndex >= 0) {
            const list = byWord.get(displayIndex);
            if (list) list.push(i);
            else byWord.set(displayIndex, [i]);
        }
    });

    return { attempts, startsMs, byWord, results, durationMs };
}

/**
 * Index of the last attempt that has started by `timeMs`, or -1 before the first one.
 * Drives "what is playing now" and the auto-scroll; says nothing about whether that
 * attempt is still in progress (compare against its endMs for that).
 */
export function cursorAt(index: TimelineIndex, timeMs: number): number {
    return upperBound(index.startsMs, timeMs) - 1;
}

/** True when `timeMs` falls after the cursor's attempt ended — i.e. in silence. */
export function isInGap(index: TimelineIndex, timeMs: number): boolean {
    const cursor = cursorAt(index, timeMs);
    if (cursor < 0) return true;
    return timeMs > index.attempts[cursor].endMs;
}

/** The next attempt starting strictly after `timeMs`, or -1 if there is none. */
export function nextAttemptAfter(index: TimelineIndex, timeMs: number): number {
    const i = upperBound(index.startsMs, timeMs);
    return i < index.attempts.length ? i : -1;
}

/** The last attempt starting strictly before `timeMs`, or -1 if there is none. */
export function prevAttemptBefore(index: TimelineIndex, timeMs: number): number {
    // Step back from the current attempt's own start so repeated presses keep moving.
    const cursor = cursorAt(index, timeMs - 1);
    return cursor >= 0 ? cursor : -1;
}

/**
 * How a word should render at `timeMs`.
 *
 * The verdict flips at the attempt's *end*, not its start: while a word is being spoken
 * the result isn't known yet, which maps onto WordChip's "active, no result" gold pulse.
 * That is what makes a retried word go gold -> red -> gold again -> green as playback
 * crosses each attempt, instead of jumping straight from red to green.
 */
export function wordStateAt(index: TimelineIndex, displayIndex: number, timeMs: number): WordState {
    const list = index.byWord.get(displayIndex);
    if (!list || list.length === 0) return IDLE_STATE;

    let active = -1;
    let lastFinished = -1;
    for (const i of list) {
        const attempt = index.attempts[i];
        if (attempt.startMs > timeMs) break;
        if (timeMs < attempt.endMs) {
            active = i;
            break;
        }
        lastFinished = i;
    }

    if (active >= 0) {
        const attempt = index.attempts[active];
        // Verdict deliberately withheld until the attempt finishes.
        return {
            isActive: true,
            result: undefined,
            attemptNo: attempt.attemptNo,
            attemptCount: attempt.attemptCount,
        };
    }
    if (lastFinished >= 0) {
        const attempt = index.attempts[lastFinished];
        return {
            isActive: false,
            result: index.results[lastFinished],
            attemptNo: attempt.attemptNo,
            attemptCount: attempt.attemptCount,
        };
    }
    return { ...IDLE_STATE, attemptCount: index.attempts[list[0]].attemptCount };
}

export function formatTime(ms: number): string {
    const total = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
