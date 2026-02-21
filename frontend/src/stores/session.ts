import { create } from "zustand";
import type { Word, WordResult } from "../types";

export type SessionStatus = "idle" | "recording" | "processing" | "complete";

interface SessionState {
    selectedRange: { surah: number; startAyah: number; endAyah: number } | null;
    words: Word[];
    currentWordIndex: number;
    wordResults: Record<number, WordResult>;
    sessionStatus: SessionStatus;
    lastTranscription: string | null;

    setSelectedRange: (range: { surah: number; startAyah: number; endAyah: number }) => void;
    setWords: (words: Word[]) => void;
    setCurrentWordIndex: (index: number) => void;
    addWordResult: (index: number, result: WordResult) => void;
    setSessionStatus: (status: SessionStatus) => void;
    setLastTranscription: (text: string | null) => void;
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
    lastTranscription: null,

    setSelectedRange: (range) => set({ selectedRange: range }),
    setWords: (words) => set({ words, currentWordIndex: 0, wordResults: {} }),
    setCurrentWordIndex: (index) => set({ currentWordIndex: index }),
    addWordResult: (index, result) =>
        set((state) => ({
            wordResults: { ...state.wordResults, [index]: result },
            // Only advance to next word if correct or skipped; incorrect stays on same word
            currentWordIndex:
                result.status === "correct" || result.status === "skipped"
                    ? Math.max(state.currentWordIndex, index + 1)
                    : state.currentWordIndex,
        })),
    setSessionStatus: (status) => set({ sessionStatus: status }),
    setLastTranscription: (text) => set({ lastTranscription: text }),
    advanceWord: () => set((state) => ({ currentWordIndex: state.currentWordIndex + 1 })),
    reset: () =>
        set({
            selectedRange: null,
            words: [],
            currentWordIndex: 0,
            wordResults: {},
            sessionStatus: "idle",
            lastTranscription: null,
        }),

    getCorrectCount: () => {
        const { wordResults } = get();
        return Object.values(wordResults).filter((r) => r.status === "correct").length;
    },

    getAverageCharScore: () => {
        const { wordResults } = get();
        const values = Object.values(wordResults);
        if (values.length === 0) return 0;
        return values.reduce((sum, r) => sum + r.char_score, 0) / values.length;
    },

    getAverageDiacriticScore: () => {
        const { wordResults } = get();
        const values = Object.values(wordResults);
        if (values.length === 0) return 0;
        return values.reduce((sum, r) => sum + r.diacritic_score, 0) / values.length;
    },

    getAverageAcousticScore: () => {
        const { wordResults } = get();
        const values = Object.values(wordResults).filter((r) => r.acoustic_score != null);
        if (values.length === 0) return undefined;
        return values.reduce((sum, r) => sum + (r.acoustic_score ?? 0), 0) / values.length;
    },
}));
