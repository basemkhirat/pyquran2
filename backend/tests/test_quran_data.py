import pytest
from backend.quran_data import get_chapters, get_words, get_chapter_verse_count


class TestGetChapters:
    def test_returns_114_chapters(self):
        chapters = get_chapters()
        assert len(chapters) == 114

    def test_first_chapter(self):
        chapters = get_chapters()
        assert chapters[0]["number"] == 1
        assert chapters[0]["name"] == "Al Fatihah"

    def test_last_chapter(self):
        chapters = get_chapters()
        assert chapters[-1]["number"] == 114


class TestGetWords:
    def test_fatiha_verse_1(self):
        words = get_words(1, 1, 1)
        assert len(words) == 4  # بسم الله الرحمن الرحيم
        assert words[0]["emlaey_text"] == "بِسْمِ"
        assert words[0]["surah"] == 1
        assert words[0]["ayah"] == 1

    def test_fatiha_all_verses(self):
        words = get_words(1, 1, 7)
        assert len(words) > 20

    def test_empty_range(self):
        words = get_words(1, 10, 20)  # Fatiha only has 7 verses
        assert len(words) == 0


class TestGetChapterVerseCount:
    def test_fatiha(self):
        assert get_chapter_verse_count(1) == 7

    def test_invalid(self):
        assert get_chapter_verse_count(999) == 0
