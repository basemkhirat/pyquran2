import { useEffect, useRef, useState } from "react";
import type { Chapter } from "../types";
import { useSessionStore } from "../stores/session";
import { socket } from "../lib/socket";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { Mic, MicOff, SkipForward } from "lucide-react";
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
import { Switch } from "@/components/ui/switch";

const SURAH_NAMES: Record<number, string> = {
    1: "الفاتحة", 2: "البقرة", 3: "آل عمران", 4: "النساء", 5: "المائدة",
    6: "الأنعام", 7: "الأعراف", 8: "الأنفال", 9: "التوبة", 10: "يونس",
    11: "هود", 12: "يوسف", 13: "الرعد", 14: "إبراهيم", 15: "الحجر",
    16: "النحل", 17: "الإسراء", 18: "الكهف", 19: "مريم", 20: "طه",
    21: "الأنبياء", 22: "الحج", 23: "المؤمنون", 24: "النور", 25: "الفرقان",
    26: "الشعراء", 27: "النمل", 28: "القصص", 29: "العنكبوت", 30: "الروم",
    31: "لقمان", 32: "السجدة", 33: "الأحزاب", 34: "سبأ", 35: "فاطر",
    36: "يس", 37: "الصافات", 38: "ص", 39: "الزمر", 40: "غافر",
    41: "فصلت", 42: "الشورى", 43: "الزخرف", 44: "الدخان", 45: "الجاثية",
    46: "الأحقاف", 47: "محمد", 48: "الفتح", 49: "الحجرات", 50: "ق",
    51: "الذاريات", 52: "الطور", 53: "النجم", 54: "القمر", 55: "الرحمن",
    56: "الواقعة", 57: "الحديد", 58: "المجادلة", 59: "الحشر", 60: "الممتحنة",
    61: "الصف", 62: "الجمعة", 63: "المنافقون", 64: "التغابن", 65: "الطلاق",
    66: "التحريم", 67: "الملك", 68: "القلم", 69: "الحاقة", 70: "المعارج",
    71: "نوح", 72: "الجن", 73: "المزمل", 74: "المدثر", 75: "القيامة",
    76: "الإنسان", 77: "المرسلات", 78: "النبأ", 79: "النازعات", 80: "عبس",
    81: "التكوير", 82: "الانفطار", 83: "المطففين", 84: "الانشقاق", 85: "البروج",
    86: "الطارق", 87: "الأعلى", 88: "الغاشية", 89: "الفجر", 90: "البلد",
    91: "الشمس", 92: "الليل", 93: "الضحى", 94: "الشرح", 95: "التين",
    96: "العلق", 97: "القدر", 98: "البينة", 99: "الزلزلة", 100: "العاديات",
    101: "القارعة", 102: "التكاثر", 103: "العصر", 104: "الهمزة", 105: "الفيل",
    106: "قريش", 107: "الماعون", 108: "الكوثر", 109: "الكافرون", 110: "النصر",
    111: "المسد", 112: "الإخلاص", 113: "الفلق", 114: "الناس",
};

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

    const { setSelectedRange, setWords, setSessionStatus, currentWordIndex, words, allowMistakes, setAllowMistakes } = useSessionStore();
    const { isRecording, startRecording, stopRecording } = useAudioRecorder();
    const isSessionActive = isRecording;
    const canSkip = isRecording && words.length > 0 && currentWordIndex < words.length;
    const isComplete = words.length > 0 && currentWordIndex >= words.length;

    const isValidRange = () => {
        if (startChapter < endChapter) return true;
        if (startChapter === endChapter && startVerse <= endVerse) return true;
        return false;
    };

    const toggleRecording = () => {
        if (isRecording) {
            stopRecording();
            return;
        }
        if (words.length === 0 || !isValidRange()) return;
        const payload = {
            start_chapter_number: startChapter,
            start_verse_number: startVerse,
            end_chapter_number: endChapter,
            end_verse_number: endVerse,
            allow_mistakes: allowMistakes,
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

    useEffect(() => {
        fetch("/api/chapters")
            .then((r) => r.json())
            .then(setChapters)
            .catch(console.error);
    }, []);

    useEffect(() => {
        let cancelled = false;
        fetch(`/api/verse-count?surah=${startChapter}`)
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
        fetch(`/api/verse-count?surah=${endChapter}`)
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
        fetch(`/api/words?start_chapter=${startChapter}&start_verse=${startVerse}&end_chapter=${endChapter}&end_verse=${endVerse}`)
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
        "min-w-[120px] font-[var(--font-arabic)] bg-surface/80 border-border focus-visible:ring-gold/50 focus-visible:border-gold h-9 text-sm";
    const inputClass =
        "w-16 text-center font-[var(--font-arabic)] bg-surface/80 border-border focus-visible:ring-gold/50 focus-visible:border-gold h-9 spinner-left [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none";

    return (
        <div className="sticky top-0 z-20 bg-surface/90 backdrop-blur-xl border-b border-border/50 px-4 py-3" dir="ltr">
            <div className="max-w-5xl mx-auto flex items-center justify-between gap-4 flex-nowrap">
                <div className="flex items-center gap-3 shrink-0">
                    <button
                        onClick={toggleRecording}
                        disabled={isComplete}
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
                    {isRecording && (
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
                            <TooltipContent side="bottom" className="font-[var(--font-arabic)]" dir="rtl">
                                تخطي الكلمة
                            </TooltipContent>
                        </Tooltip>
                    )}
                    <div className="h-5 w-px bg-border" aria-hidden />

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <div className="flex items-center gap-2">
                                <Switch
                                    checked={allowMistakes}
                                    onCheckedChange={setAllowMistakes}
                                    disabled={isSessionActive}
                                />
                            </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" className="font-[var(--font-arabic)]" dir="rtl">
                            استمر رغم الأخطاء
                        </TooltipContent>
                    </Tooltip>
                </div>

                <div className="flex items-center gap-3 flex-row-reverse">
                    <div className="flex items-center gap-2 flex-row-reverse">
                        <Label className="text-xs text-text-secondary whitespace-nowrap">من</Label>
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
                            min={1}
                            max={startVerseCount}
                            value={startVerse}
                            onChange={(e) => handleStartVerseChange(Number(e.target.value))}
                            className={inputClass}
                            disabled={isSessionActive}
                        />
                    </div>

                    <span className="text-text-muted text-sm">—</span>

                    <div className="flex items-center gap-2 flex-row-reverse">
                        <Label className="text-xs text-text-secondary whitespace-nowrap">إلى</Label>
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
        </div>
    );
}
