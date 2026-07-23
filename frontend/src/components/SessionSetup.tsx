import { useEffect, useRef, useState } from "react";
import type { Chapter } from "../types";
import { useSessionStore, type SessionMode } from "../stores/session";
import { socket, apiUrl } from "../lib/socket";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { Link } from "react-router-dom";
import { Mic, MicOff, SkipForward, Eye, EyeOff, Settings2, Headphones } from "lucide-react";
import { cn } from "../lib/cn";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { SURAH_NAMES } from "../lib/surahNames";

function getUrlParams() {
    const params = new URLSearchParams(window.location.search);
    return {
        startChapter: params.has("startChapter") ? Number(params.get("startChapter")) : null,
        startVerse: params.has("startVerse") ? Number(params.get("startVerse")) : null,
        endChapter: params.has("endChapter") ? Number(params.get("endChapter")) : null,
        endVerse: params.has("endVerse") ? Number(params.get("endVerse")) : null,
    };
}

function setUrlParams(startChapter: number, startVerse: number, endChapter: number, endVerse: number) {
    const params = new URLSearchParams();
    params.set("startChapter", String(startChapter));
    params.set("startVerse", String(startVerse));
    params.set("endChapter", String(endChapter));
    params.set("endVerse", String(endVerse));
    window.history.replaceState(null, "", `?${params.toString()}`);
}

export function SessionSetup() {
    const urlParams = getUrlParams();
    const [chapters, setChapters] = useState<Chapter[]>([]);
    const [startChapter, setStartChapter] = useState(urlParams.startChapter ?? 1);
    const [startVerse, setStartVerse] = useState(urlParams.startVerse ?? 1);
    const [endChapter, setEndChapter] = useState(urlParams.endChapter ?? 1);
    const [endVerse, setEndVerse] = useState(urlParams.endVerse ?? 7);
    const [startVerseCount, setStartVerseCount] = useState(7);
    const [endVerseCount, setEndVerseCount] = useState(7);
    const initialLoadDone = useRef(false);

    const { setSelectedRange, setWords, setSessionStatus, currentWordIndex, words, hideUnrecitedWords, setHideUnrecitedWords, scoreThreshold, setScoreThreshold, sessionMode, setSessionMode, record, setRecord, lastSession } = useSessionStore();
    const { isRecording, startRecording, stopRecording } = useAudioRecorder();
    const isSessionActive = isRecording;
    const canSkip = isRecording && words.length > 0 && currentWordIndex < words.length;
    const isComplete = words.length > 0 && currentWordIndex >= words.length;

    const isValidRange = () => {
        if (startChapter < endChapter) return true;
        if (startChapter === endChapter && startVerse <= endVerse) return true;
        return false;
    };

    const [thresholdModalOpen, setThresholdModalOpen] = useState(false);
    const [pendingThreshold, setPendingThreshold] = useState(scoreThreshold);
    const [pendingMode, setPendingMode] = useState<SessionMode>(sessionMode);
    const [pendingRecord, setPendingRecord] = useState(record);

    const toggleRecording = () => {
        if (isRecording) {
            stopRecording();
            return;
        }
        if (words.length === 0 || !isValidRange()) return;
        // Ask the user to set the mode and pass/fail threshold before the session begins.
        setPendingThreshold(scoreThreshold);
        setPendingMode(sessionMode);
        setPendingRecord(record);
        setThresholdModalOpen(true);
    };

    const startSessionWithThreshold = (threshold: number, mode: SessionMode, record: boolean) => {
        const payload = {
            start_chapter_number: startChapter,
            start_verse_number: startVerse,
            end_chapter_number: endChapter,
            end_verse_number: endVerse,
            score_threshold: threshold,
            mode,
            record,
        };
        if (socket.connected) {
            socket.emit("start_session", payload);
            startRecording();
        } else {
            socket.connect();
            socket.once("connect", () => {
                socket.emit("start_session", payload);
                startRecording();
            });
        }
    };

    const confirmStart = () => {
        setScoreThreshold(pendingThreshold);
        setSessionMode(pendingMode);
        setRecord(pendingRecord);
        startSessionWithThreshold(pendingThreshold, pendingMode, pendingRecord);
        setThresholdModalOpen(false);
    };

    useEffect(() => {
        fetch(apiUrl("/api/chapters"))
            .then((r) => r.json())
            .then(setChapters)
            .catch(console.error);
    }, []);

    useEffect(() => {
        let cancelled = false;
        fetch(apiUrl(`/api/verse-count?surah=${startChapter}`))
            .then((r) => r.json())
            .then((d) => {
                if (cancelled) return;
                setStartVerseCount(d.count);
                if (!initialLoadDone.current) {
                    const p = getUrlParams();
                    setStartVerse(p.startVerse != null ? Math.min(p.startVerse, d.count) : 1);
                } else {
                    setStartVerse(1);
                }
            })
            .catch(console.error);
        return () => { cancelled = true; };
    }, [startChapter]);

    useEffect(() => {
        let cancelled = false;
        fetch(apiUrl(`/api/verse-count?surah=${endChapter}`))
            .then((r) => r.json())
            .then((d) => {
                if (cancelled) return;
                setEndVerseCount(d.count);
                if (!initialLoadDone.current) {
                    initialLoadDone.current = true;
                    const p = getUrlParams();
                    setEndVerse(p.endVerse != null ? Math.min(p.endVerse, d.count) : d.count);
                } else {
                    setEndVerse(d.count);
                }
            })
            .catch(console.error);
        return () => { cancelled = true; };
    }, [endChapter]);

    useEffect(() => {
        setUrlParams(startChapter, startVerse, endChapter, endVerse);
    }, [startChapter, startVerse, endChapter, endVerse]);

    useEffect(() => {
        if (!isValidRange()) return;
        let cancelled = false;
        fetch(apiUrl(`/api/words?start_chapter=${startChapter}&start_verse=${startVerse}&end_chapter=${endChapter}&end_verse=${endVerse}`))
            .then((r) => r.json())
            .then((words) => {
                if (cancelled) return;
                setSelectedRange({ startChapter, startVerse, endChapter, endVerse });
                setWords(words);
                setSessionStatus("recording");
            })
            .catch((err) => {
                if (!cancelled) console.error("Failed to load verses:", err);
            });
        return () => { cancelled = true; };
    }, [startChapter, startVerse, endChapter, endVerse, setSelectedRange, setWords, setSessionStatus]);

    const handleStartChapterChange = (value: string) => {
        const newChapter = Number(value);
        setStartChapter(newChapter);
        if (newChapter > endChapter) {
            setEndChapter(newChapter);
        }
    };

    const handleEndChapterChange = (value: string) => {
        const newChapter = Number(value);
        setEndChapter(newChapter);
        if (newChapter < startChapter) {
            setStartChapter(newChapter);
        }
    };

    const handleStartVerseChange = (value: number) => {
        const clamped = Math.max(1, Math.min(startVerseCount, value || 1));
        setStartVerse(clamped);
        if (startChapter === endChapter && clamped > endVerse) {
            setEndVerse(clamped);
        }
    };

    const handleEndVerseChange = (value: number) => {
        const clamped = Math.max(1, Math.min(endVerseCount, value || 1));
        setEndVerse(clamped);
        if (startChapter === endChapter && clamped < startVerse) {
            setStartVerse(clamped);
        }
    };

    const triggerClass =
        "h-9 min-w-0 flex-1 sm:min-w-[120px] sm:flex-none font-[var(--font-arabic)] bg-surface/80 border-border focus-visible:ring-gold/50 focus-visible:border-gold text-sm";
    const inputClass =
        "h-9 w-14 shrink-0 sm:w-16 text-center font-[var(--font-arabic)] bg-surface/80 border-border focus-visible:ring-gold/50 focus-visible:border-gold [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none";

    return (
        <div
            className="fixed inset-x-0 bottom-0 z-30 border-t border-border/50 bg-surface/90 shadow-[0_-8px_30px_rgba(0,0,0,0.25)] backdrop-blur-xl"
            dir="ltr"
        >
            <div className="mx-auto flex max-w-5xl flex-col-reverse gap-3 px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                <div className="relative flex w-full items-center justify-center gap-2 sm:w-auto sm:justify-start sm:gap-3">
                    <button
                        onClick={toggleRecording}
                        disabled={isComplete}
                        aria-label={isRecording ? "إيقاف الجلسة" : "بدء جلسة جديدة"}
                        className={cn(
                            "relative w-11 h-11 rounded-full flex items-center justify-center transition-all",
                            isRecording
                                ? "bg-error mic-recording"
                                : isComplete
                                    ? "bg-surface-hover border border-border text-text-muted cursor-not-allowed"
                                    : "bg-gradient-to-br from-gold to-gold-light hover:opacity-90 shadow-md shadow-gold/20",
                        )}
                    >
                        {isRecording ? (
                            <MicOff className="w-5 h-5 text-white" />
                        ) : (
                            <Mic className={cn("w-5 h-5", isComplete ? "text-text-muted" : "text-surface")} />
                        )}
                    </button>
                    {isRecording && sessionMode !== "continuous" && (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <span className="inline-flex">
                                    <button
                                        type="button"
                                        dir="rtl"
                                        aria-label="تخطي الكلمة"
                                        onClick={() => socket.emit("skip_word")}
                                        disabled={!canSkip}
                                        className={cn(
                                            "relative w-11 h-11 rounded-full flex items-center justify-center transition-all border",
                                            canSkip
                                                ? "border-border bg-surface/80 text-text-secondary hover:bg-surface-hover hover:text-gold hover:border-gold/40"
                                                : "border-border bg-surface/80 text-text-muted opacity-60 cursor-not-allowed",
                                        )}
                                    >
                                        <SkipForward className="w-5 h-5 scale-x-[-1]" />
                                    </button>
                                </span>
                            </TooltipTrigger>
                            <TooltipContent side="top" className="font-[var(--font-arabic)]" dir="rtl">
                                تخطي الكلمة
                            </TooltipContent>
                        </Tooltip>
                    )}
                    {words.length > 0 && (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <button
                                    type="button"
                                    aria-label="Dim unrecited words"
                                    onClick={() => setHideUnrecitedWords(!hideUnrecitedWords)}
                                    className={cn(
                                        "absolute right-0 top-1/2 -translate-y-1/2 sm:static sm:translate-y-0 w-11 h-11 rounded-full flex items-center justify-center transition-all border",
                                        hideUnrecitedWords
                                            ? "border-gold/40 bg-gold/10 text-gold"
                                            : "border-border bg-surface/80 text-text-secondary hover:bg-surface-hover hover:text-gold hover:border-gold/40",
                                    )}
                                >
                                    {hideUnrecitedWords ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                                </button>
                            </TooltipTrigger>
                            <TooltipContent side="top" className="font-[var(--font-arabic)]" dir="rtl">
                                تخفيف الكلمات غير المتلوة حتى تلاوتها
                            </TooltipContent>
                        </Tooltip>
                    )}
                    {/* Only shown once `session_ended` has arrived: the session ran with
                        record on AND the server has finished writing the audio. Linking any
                        earlier would open playback on a file whose duration and seeking are
                        still broken. Nothing to show at all when record was off. */}
                    {lastSession && !isRecording && (
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Link
                                    to={`/sessions/${lastSession.id}`}
                                    aria-label="مراجعة الجلسة المسجّلة"
                                    className="flex h-11 w-11 items-center justify-center rounded-full border border-gold/40 bg-gold/10 text-gold transition-all hover:bg-gold/20"
                                >
                                    <Headphones className="h-5 w-5" />
                                </Link>
                            </TooltipTrigger>
                            <TooltipContent side="top" className="font-[var(--font-arabic)]" dir="rtl">
                                مراجعة الجلسة المسجّلة
                            </TooltipContent>
                        </Tooltip>
                    )}
                </div>

                <div className="flex w-full items-center gap-2 flex-row-reverse sm:w-auto sm:justify-end sm:gap-3">
                    <div className="flex min-w-0 flex-1 items-center gap-1.5 flex-row-reverse sm:flex-none sm:gap-2">
                        <Label className="hidden shrink-0 text-xs text-text-secondary sm:inline">من</Label>
                        <Select value={String(startChapter)} onValueChange={handleStartChapterChange}>
                            <SelectTrigger className={triggerClass} dir="rtl" disabled={isSessionActive}>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent dir="rtl" className="font-[var(--font-arabic)] bg-surface-elevated border-border text-text-primary shadow-lg backdrop-blur-sm text-right max-h-[300px]">
                                {chapters.map((ch) => (
                                    <SelectItem key={ch.number} value={String(ch.number)} className="font-[var(--font-arabic)]">
                                        {ch.number}. {SURAH_NAMES[ch.number] || ch.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Input
                            type="number"
                            dir="ltr"
                            min={1}
                            max={startVerseCount}
                            value={startVerse}
                            onChange={(e) => handleStartVerseChange(Number(e.target.value))}
                            className={inputClass}
                            disabled={isSessionActive}
                        />
                    </div>

                    <span className="shrink-0 text-sm text-text-muted">—</span>

                    <div className="flex min-w-0 flex-1 items-center gap-1.5 flex-row-reverse sm:flex-none sm:gap-2">
                        <Label className="hidden shrink-0 text-xs text-text-secondary sm:inline">إلى</Label>
                        <Select value={String(endChapter)} onValueChange={handleEndChapterChange}>
                            <SelectTrigger className={triggerClass} dir="rtl" disabled={isSessionActive}>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent dir="rtl" className="font-[var(--font-arabic)] bg-surface-elevated border-border text-text-primary shadow-lg backdrop-blur-sm text-right max-h-[300px]">
                                {chapters.map((ch) => (
                                    <SelectItem key={ch.number} value={String(ch.number)} className="font-[var(--font-arabic)]">
                                        {ch.number}. {SURAH_NAMES[ch.number] || ch.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Input
                            type="number"
                            dir="ltr"
                            min={startChapter === endChapter ? startVerse : 1}
                            max={endVerseCount}
                            value={endVerse}
                            onChange={(e) => handleEndVerseChange(Number(e.target.value))}
                            className={inputClass}
                            disabled={isSessionActive}
                        />
                    </div>
                </div>
            </div>

            <Dialog open={thresholdModalOpen} onOpenChange={setThresholdModalOpen}>
                <DialogContent dir="rtl" className="overflow-hidden p-0 font-[var(--font-arabic)]">
                    <DialogHeader className="space-y-0 border-b border-border/60 bg-gradient-to-b from-surface/60 to-transparent px-6 py-4 text-start">
                        <div className="flex items-center gap-3">
                            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gold/30 bg-gold/10 text-gold">
                                <Settings2 className="h-5 w-5" />
                            </span>
                            <div className="space-y-1">
                                <DialogTitle className="text-base">إعدادات الجلسة</DialogTitle>
                                <DialogDescription className="text-xs leading-relaxed">
                                    اضبط إعدادات الجلسة قبل بدء التلاوة.
                                </DialogDescription>
                            </div>
                        </div>
                    </DialogHeader>

                    <div className="px-6">
                    <div className="mb-5 space-y-2.5">
                        <div>
                            <Label className="text-sm font-semibold text-text-primary">نمط الجلسة</Label>
                            <p className="mt-1 text-xs leading-relaxed text-text-muted">
                                يحدّد طريقة انتقال الجلسة بين الكلمات أثناء التلاوة.
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                            <button
                                type="button"
                                onClick={() => setPendingMode("word_by_word")}
                                aria-pressed={pendingMode === "word_by_word"}
                                className={cn(
                                    "rounded-xl border px-3 py-2.5 text-sm font-medium transition-colors",
                                    pendingMode === "word_by_word"
                                        ? "border-gold/50 bg-gold/10 text-gold"
                                        : "border-border bg-surface/80 text-text-secondary hover:bg-surface-hover hover:text-text-primary",
                                )}
                            >
                                كلمة بكلمة
                            </button>
                            <button
                                type="button"
                                onClick={() => setPendingMode("continuous")}
                                aria-pressed={pendingMode === "continuous"}
                                className={cn(
                                    "rounded-xl border px-3 py-2.5 text-sm font-medium transition-colors",
                                    pendingMode === "continuous"
                                        ? "border-gold/50 bg-gold/10 text-gold"
                                        : "border-border bg-surface/80 text-text-secondary hover:bg-surface-hover hover:text-text-primary",
                                )}
                            >
                                تلاوة مستمرة
                            </button>
                        </div>
                        <p className="rounded-lg bg-surface/60 px-3 py-2 text-xs leading-relaxed text-text-secondary">
                            {pendingMode === "word_by_word"
                                ? "يتوقّف عند كل كلمة حتى تنطقها بشكل صحيح ثم ينتقل إلى التالية."
                                : "يقيّم كل كلمة ويتابع دون توقّف حتى نهاية المقطع."}
                        </p>
                    </div>

                    <div className="space-y-3 border-t border-border/50 pt-5">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <Label className="text-sm font-semibold text-text-primary">دقة التقييم</Label>
                                <p className="mt-1 text-xs leading-relaxed text-text-muted">
                                    الحد الأدنى لاعتماد الكلمة كصحيحة؛ القيمة الأعلى أكثر صرامة.
                                </p>
                            </div>
                            <span className="shrink-0 rounded-lg border border-gold/30 bg-gold/10 px-3 py-1.5 text-lg font-bold tabular-nums text-gold">
                                {pendingThreshold.toFixed(2)}
                            </span>
                        </div>

                        <Slider
                            dir="ltr"
                            min={0}
                            max={1}
                            step={0.01}
                            value={[pendingThreshold]}
                            onValueChange={(v) => setPendingThreshold(v[0])}
                        />

                        <div className="flex justify-between text-xs text-text-muted" dir="ltr">
                            <span>0.0 · متساهل</span>
                            <span>صارم · 1.0</span>
                        </div>
                    </div>

                    <div className="mt-5 space-y-3 border-t border-border/50 pt-5 pb-1">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <Label htmlFor="record-session" className="text-sm font-semibold text-text-primary">
                                    حفظ تسجيل الجلسة
                                </Label>
                                <p className="mt-1 text-xs leading-relaxed text-text-muted">
                                    حفظ الصوت وبيانات الجلسة على الخادم لمراجعتها لاحقًا.
                                </p>
                            </div>
                            <Switch
                                id="record-session"
                                dir="ltr"
                                className="mt-0.5 shrink-0"
                                checked={pendingRecord}
                                onCheckedChange={setPendingRecord}
                            />
                        </div>
                    </div>
                    </div>

                    <DialogFooter className="gap-2 border-t border-border/60 bg-gradient-to-t from-surface/50 to-transparent px-6 py-4 sm:justify-between">
                        <button
                            type="button"
                            onClick={() => setThresholdModalOpen(false)}
                            className="rounded-lg border border-border bg-surface/80 px-4 py-2.5 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                        >
                            إلغاء
                        </button>
                        <button
                            type="button"
                            onClick={confirmStart}
                            className="rounded-lg bg-gradient-to-br from-gold to-gold-light px-6 py-2.5 text-sm font-semibold text-surface shadow-md shadow-gold/20 transition-opacity hover:opacity-90"
                        >
                            ابدأ الجلسة
                        </button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
