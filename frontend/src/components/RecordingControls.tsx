import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { cn } from "../lib/cn";

export function RecordingControls() {
    const { isRecording, volume } = useAudioRecorder();

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


        </div>
    );
}
