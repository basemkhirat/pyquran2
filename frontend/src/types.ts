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
    status: "correct" | "incorrect" | "skipped";
    total_score: number;
    expected_text: string;
    detected_text: string;
    is_interim?: boolean;
}

export interface Chapter {
    number: number;
    name: string;
}
