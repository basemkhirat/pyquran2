import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { useSessionStore } from "../stores/session";
import { Mic, MicOff } from "lucide-react";
import { cn } from "../lib/cn";

export function RecordingControls() {
    const { isRecording, volume, startRecording, stopRecording } = useAudioRecorder();
    const { currentWordIndex, words } = useSessionStore();

    const isComplete = currentWordIndex >= words.length;

    const toggleRecording = () => {
        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    };

    return (
        <div className="flex flex-col items-center gap-6 mt-8">
            {/* Volume bars */}
            {isRecording && (
                <div className="flex items-end gap-1 h-8">
                    {Array.from({ length: 20 }).map((_, i) => {
                        const threshold = (i + 1) / 20;
                        const isLit = volume >= threshold;
                        return (
                            <div
                                key={i}
                                className={cn(
                                    "w-1.5 rounded-full transition-all duration-75",
                                    isLit
                                        ? i < 6
                                            ? "bg-success"
                                            : i < 14
                                                ? "bg-gold"
                                                : "bg-error"
                                        : "bg-border/30"
                                )}
                                style={{
                                    height: `${Math.max(4, (Math.sin((i / 20) * Math.PI) * 0.8 + 0.2) * 32)}px`,
                                    transform: isLit ? `scaleY(${0.5 + volume * 0.5})` : "scaleY(0.3)",
                                }}
                            />
                        );
                    })}
                </div>
            )}

            <div className="flex items-center gap-4">
                {/* Mic button */}
                <button
                    onClick={toggleRecording}
                    disabled={isComplete}
                    className={cn(
                        "relative w-16 h-16 rounded-full flex items-center justify-center transition-all",
                        isRecording
                            ? "bg-error mic-recording"
                            : "bg-gradient-to-br from-gold to-gold-light hover:opacity-90 shadow-lg shadow-gold/20",
                        isComplete && "opacity-50 cursor-not-allowed"
                    )}
                >
                    {isRecording ? (
                        <MicOff className="w-7 h-7 text-white" />
                    ) : (
                        <Mic className="w-7 h-7 text-surface" />
                    )}
                </button>
            </div>

            {/* Status text */}
            <p className="text-text-muted text-sm">
                {isComplete
                    ? "انتهت الجلسة"
                    : isRecording
                        ? "جاري التسجيل... اقرأ الكلمة المظللة"
                        : "اضغط على الميكروفون لبدء التسجيل"}
            </p>

            {/* Progress */}
            {!isComplete && (
                <div className="w-full max-w-xs">
                    <div className="flex justify-between text-xs text-text-muted mb-1">
                        <span>التقدم</span>
                        <span>{currentWordIndex} / {words.length}</span>
                    </div>
                    <div className="h-1.5 bg-surface-hover rounded-full overflow-hidden">
                        <div
                            className="h-full bg-gradient-to-l from-gold to-gold-light rounded-full transition-all duration-500"
                            style={{ width: `${(currentWordIndex / words.length) * 100}%` }}
                        />
                    </div>
                </div>
            )}
        </div>
    );
}
