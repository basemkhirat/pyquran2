import { useEffect, useRef, useState } from "react";
import { socket } from "../lib/socket";

// How long a detected word stays on screen before fading out.
const TOAST_DURATION_MS = 2000;

/**
 * Bottom-center toast that shows the detected word text from each confirmed
 * `word_result` event. Interim (unconfirmed) results are ignored so only the
 * words the backend has committed to are surfaced.
 */
export function DetectedWordToast() {
    const [text, setText] = useState<string | null>(null);
    const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        const onWordResult = (data: { detected_text?: string; is_interim?: boolean }) => {
            // Only confirmed words — skip interim results that may still self-correct.
            if (data.is_interim === true) return;
            const detected = (data.detected_text ?? "").trim();
            if (!detected) return;

            setText(detected);
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
            timeoutRef.current = setTimeout(() => setText(null), TOAST_DURATION_MS);
        };

        socket.on("word_result", onWordResult);
        return () => {
            socket.off("word_result", onWordResult);
            if (timeoutRef.current) clearTimeout(timeoutRef.current);
        };
    }, []);

    if (!text) return null;

    return (
        <div className="pointer-events-none fixed inset-x-0 bottom-32 z-40 flex justify-center px-4 sm:bottom-28">
            <div
                key={text}
                className="animate-in fade-in slide-in-from-bottom-4 duration-200 max-w-[90vw] truncate rounded-full border border-border/60 bg-surface-elevated/95 px-5 py-2.5 font-arabic text-lg text-text-primary shadow-lg backdrop-blur-md"
            >
                {text}
            </div>
        </div>
    );
}
