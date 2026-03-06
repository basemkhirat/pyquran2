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


def get_words_range(
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> List[Dict[str, Any]]:
    """Get words for a range that may span multiple chapters.

    Parameters
    ----------
    start_chapter : int
        Starting surah number (1-114).
    start_verse : int
        First verse in the starting surah.
    end_chapter : int
        Ending surah number (1-114).
    end_verse : int
        Last verse in the ending surah.

    Returns
    -------
    list
        Flat list of word dicts with surah, ayah, word_index, emlaey_text, uthmani_text.
    """
    data = _load_data()
    words = []

    for ch in data["chapters"]:
        ch_num = ch["number"]
        if ch_num < start_chapter or ch_num > end_chapter:
            continue

        for verse in ch["verses"]:
            verse_num = verse["number"]

            if ch_num == start_chapter and verse_num < start_verse:
                continue
            if ch_num == end_chapter and verse_num > end_verse:
                continue

            for word in verse["words"]:
                words.append({
                    "surah": ch_num,
                    "ayah": verse_num,
                    "word_index": word["number"],
                    "emlaey_text": word["emlaey_text"],
                    "uthmani_text": word["uthmani_text"],
                })

    return words
