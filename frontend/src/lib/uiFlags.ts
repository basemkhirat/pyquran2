/**
 * Temporary UI switches. One place to flip, so a feature parked for now doesn't have to be
 * deleted from the pages that mount it — and comes back by changing a single line here.
 */

/**
 * The floating line of recognized words: `DetectedWordToast` in the live view and
 * `PlaybackDetectedToast` on the session page. Both are still wired up and tested; they are
 * simply not rendered while this is false. Typed as `boolean` (not the literal) so the mounts
 * that read it don't get narrowed to dead code.
 */
export const SHOW_DETECTED_WORD_TOAST: boolean = false;
