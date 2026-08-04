import type { ErrorType, WordErrorDetail } from "../types";

// The three error types in display order, most-basic to most-refined. The UI is RTL, so this
// reads حروف · تشكيل · تجويد right-to-left — in the sidebar tabs and in a chip's dots alike.
export const ERROR_TYPES: ErrorType[] = ["normal", "tashkeel", "tajweed"];

// Tajweed reads as a refinement rather than a mistake -- by default it is reported without
// failing the word (MUAALEM_WEIGHT_TAJWEED=0), so a green word can still carry a tajweed badge.
// Blue, not the app's gold accent: gold marks the *active* word, and the two collided.
export const ERROR_STYLES: Record<ErrorType, string> = {
    tajweed: "bg-info/10 border-info/40 text-info-light",
    tashkeel: "bg-warning/10 border-warning/40 text-warning",
    normal: "bg-error/10 border-error/40 text-error",
};

// Solid fill for the dot markers under a word chip, where there is no room for a label and
// three labelled badges overflowed the chip. The label lives in the dot's tooltip instead.
export const ERROR_DOT_STYLES: Record<ErrorType, string> = {
    tajweed: "bg-info",
    tashkeel: "bg-warning",
    normal: "bg-error",
};

// Arabic labels (used in the sidebar and inline badges, whose UI language is Arabic).
export const ERROR_LABELS_AR: Record<ErrorType, string> = {
    tajweed: "تجويد",
    tashkeel: "تشكيل",
    normal: "حروف",
};

// What the reciter did to the reference phoneme(s).
export const SPEECH_ERROR_LABELS: Record<WordErrorDetail["speech_error_type"], { ar: string }> = {
    insert: { ar: "زيادة" },
    delete: { ar: "نقص" },
    replace: { ar: "إبدال" },
};

/** Count a word's errors of each type. Missing/undefined `errors` -> all zero. */
export function countByType(errors: WordErrorDetail[] | undefined): Record<ErrorType, number> {
    const counts: Record<ErrorType, number> = { tajweed: 0, tashkeel: 0, normal: 0 };
    for (const e of errors ?? []) counts[e.error_type] += 1;
    return counts;
}
