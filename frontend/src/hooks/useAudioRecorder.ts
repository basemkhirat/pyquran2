import { useCallback, useRef, useState } from "react";
import { socket } from "../lib/socket";

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

            setIsRecording(true);
        } catch (err) {
            console.error("Microphone access denied:", err);
        }
    }, []);

    const stopRecording = useCallback(() => {
        cancelAnimationFrame(animFrameRef.current);

        if (processorRef.current) {
            processorRef.current.disconnect();
            processorRef.current = null;
        }
        if (contextRef.current) {
            contextRef.current.close();
            contextRef.current = null;
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach((t) => t.stop());
            streamRef.current = null;
        }

        socket.emit("stop_recording");
        setIsRecording(false);
        setVolume(0);
    }, []);

    return { isRecording, volume, startRecording, stopRecording };
}
