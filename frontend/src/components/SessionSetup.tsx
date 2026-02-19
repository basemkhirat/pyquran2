import { useEffect, useState } from "react";
import type { Chapter } from "../types";
import { useSessionStore } from "../stores/session";
import { socket } from "../lib/socket";
import { Play } from "lucide-react";

// Arabic names for surahs
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

export function SessionSetup() {
    const [chapters, setChapters] = useState<Chapter[]>([]);
    const [surah, setSurah] = useState(1);
    const [startAyah, setStartAyah] = useState(1);
    const [endAyah, setEndAyah] = useState(7);
    const [verseCount, setVerseCount] = useState(7);
    const [loading, setLoading] = useState(false);

    const { setSelectedRange, setWords, setSessionStatus, sessionStatus } = useSessionStore();

    const isActive = sessionStatus === "recording";

    useEffect(() => {
        fetch("/api/chapters")
            .then((r) => r.json())
            .then(setChapters)
            .catch(console.error);
    }, []);

    useEffect(() => {
        fetch(`/api/verse-count?surah=${surah}`)
            .then((r) => r.json())
            .then((d) => {
                setVerseCount(d.count);
                setStartAyah(1);
                setEndAyah(d.count);
            })
            .catch(console.error);
    }, [surah]);

    const handleStart = async () => {
        setLoading(true);
        try {
            const res = await fetch(
                `/api/words?surah=${surah}&start_ayah=${startAyah}&end_ayah=${endAyah}`
            );
            const words = await res.json();

            setSelectedRange({ surah, startAyah, endAyah });
            setWords(words);
            setSessionStatus("recording");

            if (!socket.connected) socket.connect();
            socket.emit("start_session", { surah, startAyah, endAyah });
        } catch (err) {
            console.error("Failed to start session:", err);
        } finally {
            setLoading(false);
        }
    };

    const selectClass =
        "bg-surface/80 border border-border rounded-lg px-3 py-2 text-text-primary focus:ring-2 focus:ring-gold/50 focus:border-gold outline-none transition-all font-[var(--font-arabic)] text-sm";
    const inputClass =
        "bg-surface/80 border border-border rounded-lg px-3 py-2 text-text-primary text-center focus:ring-2 focus:ring-gold/50 focus:border-gold outline-none transition-all text-sm w-20";

    return (
        <div className="sticky top-0 z-20 bg-surface/90 backdrop-blur-xl border-b border-border/50 px-4 py-3">
            <div className="max-w-4xl mx-auto flex flex-wrap items-center gap-3">
                {/* Surah select */}
                <div className="flex items-center gap-2">
                    <label className="text-xs text-text-secondary whitespace-nowrap">السورة</label>
                    <select
                        value={surah}
                        onChange={(e) => setSurah(Number(e.target.value))}
                        disabled={isActive}
                        className={`${selectClass} min-w-[140px] disabled:opacity-50`}
                    >
                        {chapters.map((ch) => (
                            <option key={ch.number} value={ch.number}>
                                {ch.number}. {SURAH_NAMES[ch.number] || ch.name}
                            </option>
                        ))}
                    </select>
                </div>

                {/* Start verse */}
                <div className="flex items-center gap-2">
                    <label className="text-xs text-text-secondary whitespace-nowrap">من</label>
                    <input
                        type="number"
                        min={1}
                        max={verseCount}
                        value={startAyah}
                        onChange={(e) => setStartAyah(Math.max(1, Math.min(verseCount, Number(e.target.value))))}
                        disabled={isActive}
                        className={`${inputClass} disabled:opacity-50`}
                    />
                </div>

                {/* End verse */}
                <div className="flex items-center gap-2">
                    <label className="text-xs text-text-secondary whitespace-nowrap">إلى</label>
                    <input
                        type="number"
                        min={startAyah}
                        max={verseCount}
                        value={endAyah}
                        onChange={(e) => setEndAyah(Math.max(startAyah, Math.min(verseCount, Number(e.target.value))))}
                        disabled={isActive}
                        className={`${inputClass} disabled:opacity-50`}
                    />
                </div>

                {/* Start button */}
                <button
                    onClick={handleStart}
                    disabled={loading || isActive}
                    className="flex items-center gap-2 bg-gradient-to-l from-gold to-gold-light text-surface font-bold px-5 py-2 rounded-lg hover:opacity-90 transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm shadow-md shadow-gold/20"
                >
                    <Play className="w-4 h-4" />
                    {loading ? "جاري..." : "ابدأ"}
                </button>
            </div>
        </div>
    );
}
