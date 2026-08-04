import { create } from "zustand";
import type { AcousticModel, SessionEnded, Word, WordResult } from "../types";

export type SessionStatus = "idle" | "recording" | "processing" | "complete";

// "word_by_word": reciter repeats a word until it passes; "continuous": every word is
// scored and the session always advances (never blocks on a wrong word).
export type SessionMode = "word_by_word" | "continuous";

interface SelectedRange {
    startChapter: number;
    startVerse: number;
    endChapter: number;
    endVerse: number;
}

interface SessionState {
    selectedRange: SelectedRange | null;
    words: Word[];
    currentWordIndex: number;
    // The word whose error detail the sidebar is pinned to. null = follow the latest scored
    // word. Keyed by the same global index into `words` used for `wordResults`.
    selectedWordIndex: number | null;
    wordResults: Record<number, WordResult>;
    sessionStatus: SessionStatus;
    // True only while the mic is actually recording. `sessionStatus` becomes "recording"
    // as soon as a range is picked (to preview the verses), so this is the real signal for
    // "the reciter has started" — used to gate the active-word highlight.
    isSessionActive: boolean;
    hideUnrecitedWords: boolean;
    scoreThreshold: number;
    sessionMode: SessionMode;
    // Which acoustic model scores the session. Only muaalem reports tajweed/tashkeel errors.
    model: AcousticModel;
    record: boolean;
    /** The most recent *finished* recording, from the `session_ended` event, so the UI can
     *  link to its playback page. Set only once the server has closed the WAV — linking on
     *  the id from `session_started` would offer playback of a file still being written. */
    lastSession: SessionEnded | null;

    setSelectedRange: (range: SelectedRange) => void;
    setSessionActive: (active: boolean) => void;
    setHideUnrecitedWords: (hide: boolean) => void;
    setScoreThreshold: (value: number) => void;
    setSessionMode: (mode: SessionMode) => void;
    setModel: (model: AcousticModel) => void;
    setRecord: (value: boolean) => void;
    setLastSession: (session: SessionEnded | null) => void;
    setWords: (words: Word[]) => void;
    setCurrentWordIndex: (index: number) => void;
    setSelectedWordIndex: (index: number | null) => void;
    addWordResult: (index: number, result: WordResult) => void;
    setSessionStatus: (status: SessionStatus) => void;
    advanceWord: () => void;
    resetProgress: () => void;
    reset: () => void;
    getCorrectCount: () => number;
}

export const useSessionStore = create<SessionState>((set, get) => ({
    selectedRange: null,
    words: [],
    currentWordIndex: 0,
    selectedWordIndex: null,
    wordResults: {},
    sessionStatus: "idle",
    isSessionActive: false,
    hideUnrecitedWords: false,
    // Per-session pass/fail cutoff (0-1) sent with start_session; matches backend SCORE_THRESHOLD default.
    scoreThreshold: 0.76,
    // Per-session mode sent with start_session; "word_by_word" matches the backend default.
    sessionMode: "word_by_word",
    // Per-session acoustic model sent with start_session; matches the backend default.
    model: "wav2vec2",
    // Whether the backend persists this session's audio + results. On by default, so a
    // session is replayable unless the reciter turns it off; sent explicitly with
    // start_session, so the backend's SAVE_SESSION_DATA fallback never applies.
    record: true,
    lastSession: null,

    setSelectedRange: (range) => set({ selectedRange: range }),
    setSessionActive: (active) => set({ isSessionActive: active }),
    setHideUnrecitedWords: (hide) => set({ hideUnrecitedWords: hide }),
    setScoreThreshold: (value) => set({ scoreThreshold: Math.min(1, Math.max(0, value)) }),
    setSessionMode: (mode) => set({ sessionMode: mode }),
    setModel: (model) => set({ model }),
    setRecord: (value) => set({ record: value }),
    setLastSession: (session) => set({ lastSession: session }),
    setWords: (words) => set({ words, currentWordIndex: 0, wordResults: {}, selectedWordIndex: null }),
    setCurrentWordIndex: (index) => set({ currentWordIndex: index }),
    setSelectedWordIndex: (index) => set({ selectedWordIndex: index }),
    addWordResult: (index, result) =>
        set((state) => {
            // Interim words: store the result but don't advance the index.
            // In continuous mode any confirmed word advances (even incorrect ones);
            // in word_by_word mode only correct/skipped words advance.
            const shouldAdvance =
                !result.is_interim &&
                (state.sessionMode === "continuous" ||
                    result.status === "correct" ||
                    result.status === "skipped");
            return {
                wordResults: { ...state.wordResults, [index]: result },
                currentWordIndex: shouldAdvance
                    ? Math.max(state.currentWordIndex, index + 1)
                    : state.currentWordIndex,
            };
        }),
    setSessionStatus: (status) => set({ sessionStatus: status }),
    advanceWord: () => set((state) => ({ currentWordIndex: state.currentWordIndex + 1 })),
    // Clear the last run's scores while keeping the loaded range, so the same verses can be
    // recited again. Only `setWords` used to do this, so a second attempt at an unchanged
    // range began with currentWordIndex already past the last word — i.e. already "finished".
    resetProgress: () => set({ currentWordIndex: 0, wordResults: {}, selectedWordIndex: null }),
    reset: () =>
        set({
            selectedRange: null,
            words: [],
            currentWordIndex: 0,
            selectedWordIndex: null,
            wordResults: {},
            sessionStatus: "idle",
            isSessionActive: false,
            lastSession: null,
        }),

    getCorrectCount: () => {
        const { wordResults } = get();
        return Object.values(wordResults).filter((r) => r.status === "correct").length;
    },
}));
