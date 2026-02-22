export interface Word {
    surah: number;
    ayah: number;
    word_index: number;
    emlaey_text: string;
    uthmani_text: string;
}

export interface WordResult {
    chapter_number: number;
    verse_number: number;
    word_number: number;
    transcribed?: string;
    expected?: string;
    char_score?: number;
    diacritic_score?: number;
    total_score?: number;
    acoustic_score?: number;
    status: "correct" | "incorrect" | "skipped";
}

export interface Chapter {
    number: number;
    name: string;
}
