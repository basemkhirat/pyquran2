import { useCallback, useEffect, useRef, useState } from "react";

type TimeListener = (timeMs: number) => void;

export interface AudioPlayback {
    /** Callback ref for the <audio> element. Must be a callback, not a RefObject: the
     *  element mounts later than this hook (the page renders a spinner first), and only a
     *  callback ref re-runs the effect that attaches the media listeners. */
    attachAudio: (el: HTMLAudioElement | null) => void;
    isPlaying: boolean;
    isReady: boolean;
    error: boolean;
    rate: number;
    muted: boolean;
    /** Current position in ms. A ref, not state — reading it never triggers a render. */
    timeMsRef: React.RefObject<number>;
    /** Subscribe to position updates; returns an unsubscribe function. */
    subscribe: (fn: TimeListener) => () => void;
    toggle: () => void;
    seek: (ms: number) => void;
    skip: (deltaMs: number) => void;
    setRate: (rate: number) => void;
    toggleMute: () => void;
}

/**
 * Drives an <audio> element and publishes its position to subscribers.
 *
 * The element's own `timeupdate` event fires around 4Hz, which is far too coarse here —
 * recorded words are often only 300-400ms long. So position comes from a single
 * requestAnimationFrame loop, and is pushed to subscribers rather than held in state:
 * consumers decide for themselves when a change is worth a re-render (the verse list
 * re-renders about once per word; the progress bar writes to the DOM directly).
 */
export function useAudioPlayback(): AudioPlayback {
    const audioRef = useRef<HTMLAudioElement | null>(null);
    // Kept in state as well as a ref, so effects re-run when the element finally mounts.
    const [audio, setAudio] = useState<HTMLAudioElement | null>(null);
    const timeMsRef = useRef(0);
    const listenersRef = useRef(new Set<TimeListener>());
    const rafRef = useRef<number | null>(null);

    const attachAudio = useCallback((el: HTMLAudioElement | null) => {
        audioRef.current = el;
        setAudio(el);
    }, []);

    const [isPlaying, setIsPlaying] = useState(false);
    const [isReady, setIsReady] = useState(false);
    const [error, setError] = useState(false);
    const [rate, setRateState] = useState(1);
    const [muted, setMuted] = useState(false);

    const publish = useCallback((ms: number) => {
        timeMsRef.current = ms;
        for (const fn of listenersRef.current) fn(ms);
    }, []);

    const subscribe = useCallback((fn: TimeListener) => {
        listenersRef.current.add(fn);
        fn(timeMsRef.current);
        return () => {
            listenersRef.current.delete(fn);
        };
    }, []);

    // One rAF loop for the whole page, running only while audio is playing.
    useEffect(() => {
        if (!isPlaying) return;
        const tick = () => {
            const audio = audioRef.current;
            if (audio) publish(audio.currentTime * 1000);
            rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
        return () => {
            if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
        };
    }, [isPlaying, publish]);

    // Depends on `audio` (state), so it runs again when the element mounts. StrictMode
    // double-invokes effects in dev, so every listener here must be removable.
    useEffect(() => {
        if (!audio) return;
        const onPlay = () => setIsPlaying(true);
        const onPause = () => setIsPlaying(false);
        const onEnded = () => setIsPlaying(false);
        const onReady = () => {
            setIsReady(true);
            setError(false);
        };
        const onError = () => {
            setError(true);
            setIsReady(false);
        };
        // Keep the position honest when the user seeks while paused.
        const onSeeked = () => publish(audio.currentTime * 1000);
        const onTimeUpdate = () => publish(audio.currentTime * 1000);

        // Coarse (~4Hz) but independent of the rAF loop, so the UI still advances if a
        // frame callback is throttled (e.g. a backgrounded tab).
        audio.addEventListener("timeupdate", onTimeUpdate);
        audio.addEventListener("play", onPlay);
        audio.addEventListener("pause", onPause);
        audio.addEventListener("ended", onEnded);
        audio.addEventListener("loadedmetadata", onReady);
        audio.addEventListener("canplay", onReady);
        audio.addEventListener("error", onError);
        audio.addEventListener("seeked", onSeeked);
        return () => {
            audio.removeEventListener("timeupdate", onTimeUpdate);
            audio.removeEventListener("play", onPlay);
            audio.removeEventListener("pause", onPause);
            audio.removeEventListener("ended", onEnded);
            audio.removeEventListener("loadedmetadata", onReady);
            audio.removeEventListener("canplay", onReady);
            audio.removeEventListener("error", onError);
            audio.removeEventListener("seeked", onSeeked);
        };
    }, [audio, publish]);

    const toggle = useCallback(() => {
        const audio = audioRef.current;
        if (!audio) return;
        if (audio.paused) {
            // Autoplay policy rejects this when there has been no user gesture; swallow it
            // rather than surfacing an unhandled rejection.
            void audio.play().catch(() => setIsPlaying(false));
        } else {
            audio.pause();
        }
    }, []);

    const seek = useCallback((ms: number) => {
        const audio = audioRef.current;
        if (!audio) return;
        const seconds = Math.max(0, ms / 1000);
        audio.currentTime = seconds;
        publish(seconds * 1000);
    }, [publish]);

    const skip = useCallback((deltaMs: number) => {
        const audio = audioRef.current;
        if (!audio) return;
        seek(audio.currentTime * 1000 + deltaMs);
    }, [seek]);

    const setRate = useCallback((next: number) => {
        const audio = audioRef.current;
        if (audio) audio.playbackRate = next;
        setRateState(next);
    }, []);

    const toggleMute = useCallback(() => {
        const audio = audioRef.current;
        if (!audio) return;
        audio.muted = !audio.muted;
        setMuted(audio.muted);
    }, []);

    return {
        attachAudio, isPlaying, isReady, error, rate, muted,
        timeMsRef, subscribe, toggle, seek, skip, setRate, toggleMute,
    };
}
