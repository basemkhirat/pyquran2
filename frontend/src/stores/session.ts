import { create } from "zustand";
import type { Word, WordResult } from "../types";

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
    wordResults: Record<number, WordResult>;
    sessionStatus: SessionStatus;
    // True only while the mic is actually recording. `sessionStatus` becomes "recording"
    // as soon as a range is picked (to preview the verses), so this is the real signal for
    // "the reciter has started" — used to gate the active-word highlight.
    isSessionActive: boolean;
    hideUnrecitedWords: boolean;
    scoreThreshold: number;
    sessionMode: SessionMode;

    setSelectedRange: (range: SelectedRange) => void;
    setSessionActive: (active: boolean) => void;
    setHideUnrecitedWords: (hide: boolean) => void;
    setScoreThreshold: (value: number) => void;
    setSessionMode: (mode: SessionMode) => void;
    setWords: (words: Word[]) => void;
    setCurrentWordIndex: (index: number) => void;
    addWordResult: (index: number, result: WordResult) => void;
    setSessionStatus: (status: SessionStatus) => void;
    advanceWord: () => void;
    reset: () => void;
    getCorrectCount: () => number;
}

export const useSessionStore = create<SessionState>((set, get) => ({
    selectedRange: null,
    words: [],
    currentWordIndex: 0,
    wordResults: {},
    sessionStatus: "idle",
    isSessionActive: false,
    hideUnrecitedWords: false,
    // Per-session pass/fail cutoff (0-1) sent with start_session; matches backend SCORE_THRESHOLD default.
    scoreThreshold: 0.76,
    // Per-session mode sent with start_session; "word_by_word" matches the backend default.
    sessionMode: "word_by_word",

    setSelectedRange: (range) => set({ selectedRange: range }),
    setSessionActive: (active) => set({ isSessionActive: active }),
    setHideUnrecitedWords: (hide) => set({ hideUnrecitedWords: hide }),
    setScoreThreshold: (value) => set({ scoreThreshold: Math.min(1, Math.max(0, value)) }),
    setSessionMode: (mode) => set({ sessionMode: mode }),
    setWords: (words) => set({ words, currentWordIndex: 0, wordResults: {} }),
    setCurrentWordIndex: (index) => set({ currentWordIndex: index }),
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
    reset: () =>
        set({
            selectedRange: null,
            words: [],
            currentWordIndex: 0,
            wordResults: {},
            sessionStatus: "idle",
            isSessionActive: false,
        }),

    getCorrectCount: () => {
        const { wordResults } = get();
        return Object.values(wordResults).filter((r) => r.status === "correct").length;
    },
}));
