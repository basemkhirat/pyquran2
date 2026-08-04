export interface Word {
    surah: number;
    ayah: number;
    word_index: number;
    emlaey_text: string;
    uthmani_text: string;
}

/** Which acoustic model scores a session. Chosen at start_session; defaults to wav2vec2. */
export type AcousticModel = "wav2vec2" | "muaalem";

/** How a pronunciation error is classified. Only the muaalem backend reports these. */
export type ErrorType = "tajweed" | "tashkeel" | "normal";

export interface TajweedRule {
    name_ar: string;
    name_en: string;
    golden_len?: number | null;
    correctness_type?: "match" | "count" | null;
}

export interface WordErrorDetail {
    error_type: ErrorType;
    /** What the reciter did to the reference: added, dropped, or said something else. */
    speech_error_type: "insert" | "delete" | "replace";
    /** Phonemes, in Quran Phonetic Script -- not Arabic text. */
    expected_ph: string;
    predicted_ph: string;
    /** Madd lengths, when the error is one of duration rather than sound. */
    expected_len?: number | null;
    predicted_len?: number | null;
    rules?: TajweedRule[];
}

/**
 * What the reciter actually produced for one recitation unit, in Quran Phonetic Script.
 * A unit is usually one word, but phonetically-merged neighbours (idgham, hamzat wasl) share
 * a unit, so `words` may list more than one. muaalem only.
 */
export interface RecitedUnit {
    ph: string;
    expected_ph: string;
    words: string[];
}

/** The muaalem-only pronunciation detail carried by a live `word_result`. */
export interface MuaalemWordDetail {
    /**
     * Present only for muaalem sessions. A word can be `status: "correct"` and still carry
     * errors -- tajweed is surfaced without failing the word unless MUAALEM_WEIGHT_TAJWEED
     * is raised above 0.
     */
    errors?: WordErrorDetail[];
    /** The most serious error_type on this word, for a single label. */
    error_type?: ErrorType;
    tajweed_score?: number;
    /** The reciter's actual phonemes for this word's unit. */
    recited?: RecitedUnit;
}

/**
 * The muaalem-only detail as *stored* in a session's info.json, and echoed back by playback
 * and `session_ended`. Flatter than the live event: the recited unit is kept as its two
 * phoneme strings, and `error_type` / `tajweed_score` are not persisted — both are
 * derivable from `errors`.
 */
export interface MuaalemStoredDetail {
    /** Present on every word of a muaalem session — `[]` when the word was clean. Absent
     *  for wav2vec2 sessions, which measure no errors at all. */
    errors?: WordErrorDetail[];
    /** What the reciter produced for this word's unit, in Quran Phonetic Script. */
    detected_ph?: string;
    /** The phonemes the reference expects for it. */
    expected_ph?: string;
}

export interface WordResult extends MuaalemWordDetail {
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
export interface SessionTimelineEntry extends MuaalemStoredDetail {
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
    /** Which acoustic model scored the session. Sessions recorded before per-session model
     *  selection report "wav2vec2". */
    model: AcousticModel;
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

// --- Recorded session handoff (`session_ended` socket event) -----------------------------

/** One spoken word in a finished recording, as stored in the session's info.json. */
export interface SessionInfoWord extends MuaalemStoredDetail {
    chapter_number: number;
    verse_number: number;
    word_number: number;
    /** The reference text for this word. */
    expected_text: string;
    /** What the recognizer heard. */
    detected_text: string;
    /** "skipped" is never persisted — a skipped word has no audio to record. */
    status: "correct" | "incorrect";
    total_score: number;
    /** Milliseconds relative to the start of the recording. */
    start_time: number;
    end_time: number;
}

/**
 * Payload of the `session_ended` event: the session's info.json flattened, plus the audio
 * URL. Emitted once per *recorded* session, and only after the server has closed the WAV.
 *
 * Receiving it is the signal that `url` is safe to fetch: before the file is closed its
 * RIFF length fields are still placeholders, so the audio reports an infinite duration and
 * cannot be seeked. Sessions started with `record: false` never emit it.
 */
export interface SessionEnded {
    id: string;
    type: "word_by_word" | "continuous";
    narration_id: number;
    /** Which acoustic model scored the session. */
    model: AcousticModel;
    score_threshold: number | null;
    /** Length of the recording, in milliseconds. */
    duration: number;
    start_chapter_number: number | null;
    start_verse_number: number | null;
    end_chapter_number: number | null;
    end_verse_number: number | null;
    /** Absolute URL of the session audio (WAV), or null when the session wasn't recorded
     *  (record: false). Supports range requests. */
    url: string | null;
    words: SessionInfoWord[];
}
