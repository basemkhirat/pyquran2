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

// --- Recorded session playback (GET /api/sessions/{id}) ---------------------------------

export interface SessionVerseRange {
    start_chapter: number;
    start_verse: number;
    end_chapter: number;
    end_verse: number;
}

/** One recorded attempt at a word. A word retried in word_by_word mode has several. */
export interface SessionTimelineEntry {
    /** Index into SessionPlayback.words — the bridge between the two naming schemes.
     *  Null when the entry has no matching display word. */
    display_index: number | null;
    chapter_number: number;
    verse_number: number;
    word_number: number;
    /** The reference text for this word. */
    word_text: string;
    /** What the recognizer heard. Empty for sessions recorded before this was stored. */
    detected_text: string;
    /** "skipped" is never persisted — a skipped word has no audio to record. */
    status: "correct" | "incorrect";
    score: number;
    /** Milliseconds relative to the start of the recording. */
    start_time: number;
    end_time: number;
}

export interface SessionStats {
    total_words: number;
    attempts: number;
    distinct_recited: number;
    correct: number;
    incorrect: number;
}

export interface SessionPlayback {
    id: string;
    mode: "word_by_word" | "continuous";
    narration_id: number;
    score_threshold: number | null;
    /** Null when the session recorded nothing and stored no range. */
    range: SessionVerseRange | null;
    /** True when the range was derived from the timeline (sessions predating the range fields). */
    range_inferred: boolean;
    duration_ms: number | null;
    has_recording: boolean;
    words: Word[];
    timeline: SessionTimelineEntry[];
    stats: SessionStats;
}
