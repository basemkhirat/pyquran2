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
    hideUnrecitedWords: boolean;
    scoreThreshold: number;
    sessionMode: SessionMode;

    setSelectedRange: (range: SelectedRange) => void;
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
    getAverageCharScore: () => number;
    getAverageDiacriticScore: () => number;
}

export const useSessionStore = create<SessionState>((set, get) => ({
    selectedRange: null,
    words: [],
    currentWordIndex: 0,
    wordResults: {},
    sessionStatus: "idle",
    hideUnrecitedWords: false,
    // Per-session pass/fail cutoff (0-1) sent with start_session; matches backend SCORE_THRESHOLD default.
    scoreThreshold: 0.76,
    // Per-session mode sent with start_session; "word_by_word" matches the backend default.
    sessionMode: "word_by_word",

    setSelectedRange: (range) => set({ selectedRange: range }),
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
        }),

    getCorrectCount: () => {
        const { wordResults } = get();
        return Object.values(wordResults).filter((r) => r.status === "correct").length;
    },

    getAverageCharScore: () => {
        const { wordResults } = get();
        const values = Object.values(wordResults).filter((r) => r.char_score != null);
        if (values.length === 0) return 0;
        return values.reduce((sum, r) => sum + (r.char_score ?? 0), 0) / values.length;
    },

    getAverageDiacriticScore: () => {
        const { wordResults } = get();
        const values = Object.values(wordResults).filter((r) => r.diacritic_score != null);
        if (values.length === 0) return 0;
        return values.reduce((sum, r) => sum + (r.diacritic_score ?? 0), 0) / values.length;
    },

    getAverageAcousticScore: () => {
        const { wordResults } = get();
        const values = Object.values(wordResults).filter((r) => r.acoustic_score != null);
        if (values.length === 0) return undefined;
        return values.reduce((sum, r) => sum + (r.acoustic_score ?? 0), 0) / values.length;
    },
}));
