import { useCallback, useEffect, useRef, useState } from "react";
import { socket } from "../lib/socket";
import { useSessionStore } from "../stores/session";

const SAMPLE_RATE = 16000;
const CHUNK_DURATION_MS = 150;

export function useAudioRecorder() {
    const [isRecording, setIsRecording] = useState(false);
    const [volume, setVolume] = useState(0);
    const contextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);
    const streamRef = useRef<MediaStream | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);
    const animFrameRef = useRef<number>(0);
    // Mirrors `isRecording` for the socket listener and for stopRecording, neither of which
    // can read the state value (the listener closes over the render it was registered in).
    const recordingRef = useRef(false);

    const startRecording = useCallback(async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: SAMPLE_RATE,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                },
            });
            streamRef.current = stream;

            const context = new AudioContext({ sampleRate: SAMPLE_RATE });
            contextRef.current = context;

            const source = context.createMediaStreamSource(stream);

            // Analyser for volume visualization
            const analyser = context.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyserRef.current = analyser;

            // ScriptProcessor for raw PCM data
            const bufferSize = Math.ceil((SAMPLE_RATE * CHUNK_DURATION_MS) / 1000);
            const processor = context.createScriptProcessor(
                Math.pow(2, Math.ceil(Math.log2(bufferSize))),
                1,
                1
            );
            processorRef.current = processor;

            source.connect(processor);
            processor.connect(context.destination);

            processor.onaudioprocess = (e) => {
                const float32 = e.inputBuffer.getChannelData(0);
                // Convert float32 to int16 PCM
                const int16 = new Int16Array(float32.length);
                for (let i = 0; i < float32.length; i++) {
                    const s = Math.max(-1, Math.min(1, float32[i]));
                    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
                }
                socket.emit("audio_chunk", int16.buffer);
            };

            // Volume meter loop
            const updateVolume = () => {
                if (!analyserRef.current) return;
                const data = new Uint8Array(analyserRef.current.frequencyBinCount);
                analyserRef.current.getByteFrequencyData(data);
                const avg = data.reduce((a, b) => a + b, 0) / data.length;
                setVolume(avg / 255);
                animFrameRef.current = requestAnimationFrame(updateVolume);
            };
            updateVolume();

            recordingRef.current = true;
            setIsRecording(true);
            // Mic is live now — mark the session active so the UI can highlight the current word.
            useSessionStore.getState().setSessionActive(true);
        } catch (err) {
            console.error("Microphone access denied:", err);
        }
    }, []);

    /** Release the mic and the audio graph. Says nothing to the server — the caller decides
     *  whether the server still needs telling (a user-pressed stop) or already knows (the
     *  server ended the session itself). */
    const teardown = useCallback(() => {
        if (!recordingRef.current) return;
        recordingRef.current = false;

        cancelAnimationFrame(animFrameRef.current);

        if (processorRef.current) {
            // Clear the handler before disconnecting: a callback already queued would
            // otherwise emit one more audio_chunk into a session that is over.
            processorRef.current.onaudioprocess = null;
            processorRef.current.disconnect();
            processorRef.current = null;
        }
        analyserRef.current = null;
        if (contextRef.current) {
            contextRef.current.close();
            contextRef.current = null;
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach((t) => t.stop());
            streamRef.current = null;
        }

        setIsRecording(false);
        setVolume(0);
        useSessionStore.getState().setSessionActive(false);
    }, []);

    const stopRecording = useCallback(() => {
        teardown();
        socket.emit("stop_session");
    }, [teardown]);

    // The server ends a session on its own once the range is exhausted (or the last word is
    // skipped) — `session_stopped` is that signal, and it arrives without any stop_session
    // from us. Without this the mic stays open streaming chunks into a finished session, and
    // every control gated on `isRecording` (the mic button, the range pickers, the review
    // link) stays stuck in its recording state with no way back.
    useEffect(() => {
        const onServerStopped = () => teardown();
        socket.on("session_stopped", onServerStopped);
        return () => {
            socket.off("session_stopped", onServerStopped);
        };
    }, [teardown]);

    return { isRecording, volume, startRecording, stopRecording };
}
