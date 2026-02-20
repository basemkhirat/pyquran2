import { useEffect, useRef, useState } from "react";
import { SessionSetup } from "./components/SessionSetup";
import { VerseDisplay } from "./components/VerseDisplay";
import { useSessionStore } from "./stores/session";
import { socket } from "./lib/socket";

export default function App() {
  const {
    sessionStatus,
    addWordResult,
    setSessionStatus,
    setLastTranscription,
    lastTranscription,
    words,
  } = useSessionStore();

  // Subtitle auto-fade timer
  const [subtitleVisible, setSubtitleVisible] = useState(false);
  const fadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Socket event listeners
  useEffect(() => {
    const onWordResult = (data: any) => {
      const idx = words.findIndex(
        (w) =>
          w.surah === data.surah &&
          w.ayah === data.ayah &&
          w.word_index === data.word_index
      );
      if (idx !== -1) {
        addWordResult(idx, data);
      }
    };

    const onSessionComplete = () => {
      // Keep verse display and results visible; no reset so no blank screen
    };

    const onTimeout = () => {
      // Timeout just means silence — user must keep trying the same word
    };

    const onSessionError = (data: any) => {
      console.error("Session error:", data?.reason);
    };

    const onTranscription = (data: any) => {
      if (data?.text) {
        setLastTranscription(data.text);
        setSubtitleVisible(true);
        // Clear previous timer
        if (fadeTimer.current) clearTimeout(fadeTimer.current);
        // Auto-fade after 3 seconds
        fadeTimer.current = setTimeout(() => {
          setSubtitleVisible(false);
        }, 3000);
      }
    };

    socket.on("word_result", onWordResult);
    socket.on("session_complete", onSessionComplete);
    socket.on("timeout", onTimeout);
    socket.on("session_error", onSessionError);
    socket.on("transcription", onTranscription);

    return () => {
      socket.off("word_result", onWordResult);
      socket.off("session_complete", onSessionComplete);
      socket.off("timeout", onTimeout);
      socket.off("session_error", onSessionError);
      socket.off("transcription", onTranscription);
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
    };
  }, [words, addWordResult, setSessionStatus, setLastTranscription]);

  return (
    <div className="min-h-screen">
      {/* Setup bar — always visible */}
      <SessionSetup />

      {/* Verse display — shown when active */}
      {sessionStatus === "recording" && (
        <div className="py-6">
          <VerseDisplay />

          {/* Live transcription subtitle */}
          {lastTranscription && (
            <div
              className={`subtitle-toast ${subtitleVisible ? "subtitle-visible" : "subtitle-hidden"
                }`}
            >
              <span className="subtitle-text">{lastTranscription}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
