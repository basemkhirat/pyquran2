import json
from typing import List, Dict, Any, Optional
from backend.config import config


_quran_data: Optional[Dict] = None


def _load_data() -> Dict:
    global _quran_data
    if _quran_data is None:
        with open(config.hafs_json_path, "r", encoding="utf-8") as f:
            _quran_data = json.load(f)
    return _quran_data


def get_chapters() -> List[Dict[str, Any]]:
    data = _load_data()
    return [
        {"number": ch["number"], "name": ch["name"]}
        for ch in data["chapters"]
    ]


def get_chapter_verse_count(surah: int) -> int:
    data = _load_data()
    for ch in data["chapters"]:
        if ch["number"] == surah:
            return len(ch["verses"])
    return 0


def get_words(surah: int, start_ayah: int, end_ayah: int) -> List[Dict[str, Any]]:
    data = _load_data()
    words = []
    for ch in data["chapters"]:
        if ch["number"] != surah:
            continue
        for verse in ch["verses"]:
            if verse["number"] < start_ayah or verse["number"] > end_ayah:
                continue
            for word in verse["words"]:
                words.append({
                    "surah": surah,
                    "ayah": verse["number"],
                    "word_index": word["number"],
                    "emlaey_text": word["emlaey_text"],
                    "uthmani_text": word["uthmani_text"],
                })
        break
    return words
