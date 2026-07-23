import { useEffect } from "react";
import { SessionSetup } from "./components/SessionSetup";
import { VerseDisplay } from "./components/VerseDisplay";
import { DetectedWordToast } from "./components/DetectedWordToast";
import { useSessionStore } from "./stores/session";
import { socket } from "./lib/socket";
import type { SessionEnded } from "./types";

export default function App() {
  const { sessionStatus, addWordResult, words, setCurrentWordIndex, setLastSession } = useSessionStore();

  // Socket event listeners
  useEffect(() => {
    const wordIndexFor = (data: { chapter_number: number; verse_number: number; word_number: number }) =>
      words.findIndex(
        (w) =>
          w.surah === data.chapter_number &&
          w.ayah === data.verse_number &&
          w.word_index === data.word_number
      );

    const onWordResult = (data: any) => {
      console.log(
        "word_result — expected:", data.expected_text,
        "| detected:", data.detected_text,
        "| score:", data.total_score,
        "| status:", data.status,
        "| interim:", data.is_interim === true
      );
      if (data.is_interim === true) {
        console.log("  ⏳ interim word (may self-correct):", data.expected_text);
      }
      const idx = wordIndexFor(data);
      if (idx !== -1) addWordResult(idx, data);
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

    const onVerseDetected = (data: any) => {
      const idx = wordIndexFor(data);
      if (idx !== -1) setCurrentWordIndex(idx);
    };

    const onVerseDetectionFailed = () => {
      // Detection failed — system keeps listening for next utterance
    };

    // A new session invalidates any previous recording link.
    const onSessionStarted = () => {
      setLastSession(null);
    };

    // Recorded sessions only (record: true), and only after the server has closed the WAV.
    // Waiting for this — rather than linking to the id from session_started — is what keeps
    // the UI from offering playback of a file that is still being written.
    const onSessionEnded = (data: SessionEnded) => {
      console.log(
        "session_ended — id:", data.id,
        "| duration:", data.duration, "ms",
        "| words:", data.words?.length ?? 0
      );
      setLastSession(data);
    };

    socket.on("session_started", onSessionStarted);
    socket.on("session_ended", onSessionEnded);
    socket.on("word_result", onWordResult);
    socket.on("session_stopped", onSessionComplete);
    socket.on("timeout", onTimeout);
    socket.on("session_error", onSessionError);
    socket.on("verse_detected", onVerseDetected);
    socket.on("verse_detection_failed", onVerseDetectionFailed);

    return () => {
      socket.off("session_started", onSessionStarted);
      socket.off("session_ended", onSessionEnded);
      socket.off("word_result", onWordResult);
      socket.off("session_stopped", onSessionComplete);
      socket.off("timeout", onTimeout);
      socket.off("session_error", onSessionError);
      socket.off("verse_detected", onVerseDetected);
      socket.off("verse_detection_failed", onVerseDetectionFailed);
    };
  }, [words, addWordResult, setCurrentWordIndex, setLastSession]);

  return (
    <div className="min-h-screen pb-40 sm:pb-24">
      {/* Control bar — fixed at the bottom, always visible */}
      <SessionSetup />

      {/* Verse display — shown when active */}
      {sessionStatus === "recording" && (
        <div className="pt-6">
          <VerseDisplay />
        </div>
      )}

      {/* Bottom-center toast with the detected word text (confirmed words only) */}
      <DetectedWordToast />
    </div>
  );
}
