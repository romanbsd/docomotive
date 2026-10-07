"""Shared language settings; English defaults preserve existing book profiles."""

from pathlib import Path


def ui_label(book, key):
    labels = {
        "cover": ("Cover", "Обложка"),
        "copyright": ("Copyright and permissions", "Сведения об издании"),
        "edition": ("About this edition", "Об электронном издании"),
        "contents": ("Contents", "Оглавление"),
        "pages": ("Original pages", "Страницы оригинала"),
        "landmarks": ("Landmarks", "Разделы"),
        "start": ("Start of text", "Начало текста"),
        "notes": ("Notes", "Примечания"),
        "page": ("Original page ", "Страница оригинала "),
        "return": ("Return to text", "Вернуться к тексту"),
        "chapter_notes": ("Notes for this chapter", "Примечания к этой главе"),
        "notes_page": ("Notes for original page ", "Примечания к странице оригинала "),
        "note_ref": ("[note]", "[прим.]"),
    }
    return labels[key][language_code(book) == "ru"]


def language_code(book):
    return book.get("language", "en").replace("_", "-").split("-")[0].lower()


def dictionary_locale(book):
    code = language_code(book)
    return book.get("dictionary_locale", {"en": "en_US", "ru": "ru_RU"}.get(code, code))


def ocr_settings(book):
    code = language_code(book)
    return dict(
        tesseract=book.get(
            "ocr_tesseract_language", {"en": "eng", "ru": "rus"}.get(code, code)
        ),
        vision=book.get(
            "ocr_vision_language",
            {"en": "en-US", "ru": "ru-RU"}.get(code, book["language"]),
        ),
        rapid=book.get(
            "ocr_rapid_language", {"en": "en", "ru": "cyrillic"}.get(code, code)
        ),
        tessdata=(
            Path(book["ocr_tessdata_dir"]).resolve()
            if book.get("ocr_tessdata_dir")
            else None
        ),
    )
