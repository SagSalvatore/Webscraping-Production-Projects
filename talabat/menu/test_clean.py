#!/usr/bin/env python3
"""Quick smoke test for the cleaning functions."""
import re
import unicodedata
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# --- Replicate cleaning logic inline for test ---
_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FFFF"
    r"\U00002600-\U000027BF"
    r"\U0001F300-\U0001F9FF"
    r"⏩-⏳"
    r"▪-▫▶◀◻-◾"
    r"☔-☕♈-♓♿⚓⚡⚪-⚫"
    r"⚽-⚾⛄-⛅⛎-⛏⛔⛪"
    r"⛲-⛳⛵⛺⛽"
    r"✂✅✈-✍✏"
    r"✒✔✖✝✡✨✳-✴❄❇"
    r"❌❎❓-❕❗❣-❤➕-➗"
    r"➡➰➿⤴-⤵⬅-⬇⬛-⬜"
    r"⭐⭕〰〽㊗㊙]+",
    flags=re.UNICODE,
)
_INVISIBLE_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\xad"
    r"​-‏‪-‮⁠-⁤"
    r"⁪-⁯﻿]+",
    flags=re.UNICODE,
)
_ARABIC_PUNCT = str.maketrans({"؟": "?", "،": ",", "؛": ";"})
_WS_RE = re.compile(r"\s+")
_ARABIC_DETECT = re.compile(r"[؀-ۿ]")


def clean_text(text):
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", text)
    s = _INVISIBLE_RE.sub(" ", s)
    s = _EMOJI_RE.sub(" ", s)
    s = s.translate(_ARABIC_PUNCT)
    s = _WS_RE.sub(" ", s).strip()
    return s if s else None


def extract_english_from_bilingual(text):
    if text is None:
        return None
    if not _ARABIC_DETECT.search(text):
        return clean_text(text)
    for sep in [" - ", " – ", "- ", " -"]:
        parts = text.split(sep, 1)
        if len(parts) == 2:
            eng, ara = parts
            if re.search(r"[a-zA-Z]", eng) and _ARABIC_DETECT.search(ara):
                return clean_text(eng)
    text = text.translate(_ARABIC_PUNCT)
    stripped = re.sub(r"[؀-ۿ]+", "", text)
    return clean_text(stripped)


# --- Test cases ---
tests = [
    ("item_key: soccer emoji",            "⚽ budget goal rush combo",              "budget goal rush combo"),
    ("item_key: RTL mark prefix",         "‏ arabic meat biryani",                 "arabic meat biryani"),
    ("item_key: word joiner prefix",      "⁠knafeh kheshneh",                      "knafeh kheshneh"),
    ("description: food emoji",          "Crispy crackers \U0001f60b served as snack", "Crispy crackers served as snack"),
    ("description: ZWSP trailing",       "Coconut Milk ​​",                  "Coconut Milk"),
    ("category: bilingual EN-AR",        "ADD-ONs Section - قسم الاضافات",
                                          "ADD-ONs Section"),
    ("category: Arabic clock emoji",     "⏱ LIMITED MONTHLY OFFER",               "LIMITED MONTHLY OFFER"),
    ("category: Arabic question mark",   "Got a Sweet Tooth؟",                    "Got a Sweet Tooth?"),
    ("category: Arabic only suffix",     "50% Discount علي المنيو", "50% Discount"),
    ("no change needed",                 "Drinks",                                      "Drinks"),
]

passed = 0
failed = 0
for label, inp, expected in tests:
    if _ARABIC_DETECT.search(inp):
        result = extract_english_from_bilingual(inp)
    else:
        result = clean_text(inp)
    ok = result is not None and result.strip() == expected.strip()
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"[{status}] {label}")
    if not ok:
        print(f"       IN : {repr(inp)}")
        print(f"       GOT: {repr(result)}")
        print(f"       EXP: {repr(expected)}")

print(f"\n{passed}/{len(tests)} tests passed")
print(f"OPEN_AI_API set: {bool(os.environ.get('OPEN_AI_API'))}")
sys.exit(0 if failed == 0 else 1)
