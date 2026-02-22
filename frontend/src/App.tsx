import { useEffect } from "react";
import { SessionSetup } from "./components/SessionSetup";
import { VerseDisplay } from "./components/VerseDisplay";
import { useSessionStore } from "./stores/session";
import { socket } from "./lib/socket";

export default function App() {
  const { sessionStatus, addWordResult, words } = useSessionStore();

  // Socket event listeners
  useEffect(() => {
    const onWordResult = (data: any) => {
      const idx = words.findIndex(
        (w) =>
          w.surah === data.chapter_number &&
          w.ayah === data.verse_number &&
          w.word_index === data.word_number
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

    socket.on("word_result", onWordResult);
    socket.on("session_stopped", onSessionComplete);
    socket.on("timeout", onTimeout);
    socket.on("session_error", onSessionError);

    return () => {
      socket.off("word_result", onWordResult);
      socket.off("session_stopped", onSessionComplete);
      socket.off("timeout", onTimeout);
      socket.off("session_error", onSessionError);
    };
  }, [words, addWordResult]);

  return (
    <div className="min-h-screen">
      {/* Setup bar — always visible */}
      <SessionSetup />

      {/* Verse display — shown when active */}
      {sessionStatus === "recording" && (
        <div className="py-6">
          <VerseDisplay />
        </div>
      )}
    </div>
  );
}
