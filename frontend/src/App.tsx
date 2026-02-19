import { useEffect } from "react";
import { SessionSetup } from "./components/SessionSetup";
import { VerseDisplay } from "./components/VerseDisplay";
import { RecordingControls } from "./components/RecordingControls";
import { useSessionStore } from "./stores/session";
import { socket } from "./lib/socket";

export default function App() {
  const {
    sessionStatus,
    addWordResult,
    setSessionStatus,
    reset,
    currentWordIndex,
    words,
  } = useSessionStore();

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
      reset();
    };

    const onTimeout = () => {
      // Timeout just means silence — user must keep trying the same word
    };

    const onSessionError = (data: any) => {
      console.error("Session error:", data?.reason);
    };

    socket.on("word_result", onWordResult);
    socket.on("session_complete", onSessionComplete);
    socket.on("timeout", onTimeout);
    socket.on("session_error", onSessionError);

    return () => {
      socket.off("word_result", onWordResult);
      socket.off("session_complete", onSessionComplete);
      socket.off("timeout", onTimeout);
      socket.off("session_error", onSessionError);
    };
  }, [words, addWordResult, setSessionStatus, reset]);

  // Auto-complete: reset to idle when all words are processed
  useEffect(() => {
    if (
      sessionStatus === "recording" &&
      words.length > 0 &&
      currentWordIndex >= words.length
    ) {
      reset();
    }
  }, [currentWordIndex, words.length, sessionStatus, reset]);

  return (
    <div className="min-h-screen">
      {/* Setup bar — always visible */}
      <SessionSetup />

      {/* Verse display & recording controls — shown when active */}
      {sessionStatus === "recording" && (
        <div className="py-8">
          <VerseDisplay />
          <RecordingControls />
        </div>
      )}
    </div>
  );
}
