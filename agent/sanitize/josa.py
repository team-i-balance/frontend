"""조사 보정: 값을 바꾼 뒤 이/가, 은/는, 을/를, 과/와, 으로/로 등을 새 값의 받침에 맞춘다 (§5)."""
from __future__ import annotations

# 숫자를 한국어로 읽었을 때 (받침 유무, ㄹ 받침 여부)
_DIGIT = {"0": (True, False), "1": (True, True), "2": (False, False), "3": (True, False),
          "4": (False, False), "5": (False, False), "6": (True, False), "7": (True, True),
          "8": (True, True), "9": (False, False)}

# (받침 있을 때, 받침 없을 때). 긴 것부터 검사한다.
_PAIRS = [("이에요", "예요"), ("이랑", "랑"), ("이나", "나"), ("이며", "며"), ("으로", "로"),
          ("은", "는"), ("을", "를"), ("과", "와"), ("이", "가"), ("아", "야")]


def _hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def final_sound(word: str) -> tuple[bool, bool]:
    """(받침 있음, ㄹ 받침)"""
    w = word.rstrip(")]}>\"' ")
    if not w:
        return (False, False)
    c = w[-1]
    if _hangul(c):
        k = (ord(c) - 0xAC00) % 28
        return (k != 0, k == 8)
    if c.isdigit():
        return _DIGIT[c]
    if c.isascii() and c.isalpha():
        low = c.lower()
        return (low in "lmnr", low in "lr")
    return (False, False)


def has_batchim(word: str) -> bool:
    return final_sound(word)[0]


def fix_josa(word: str, text: str, pos: int) -> tuple[str, int]:
    """text[pos:]가 조사로 시작하고 word와 안 맞으면 (바른 조사, 바꿀 길이)를 돌려준다. 맞으면 ("", 0)."""
    bat, rieul = final_sound(word)
    for with_b, without_b in _PAIRS:
        for cand in (with_b, without_b):
            if not text.startswith(cand, pos):
                continue
            nxt = text[pos + len(cand):pos + len(cand) + 1]
            if nxt and _hangul(nxt):  # 조사가 아니라 단어의 일부("이번", "가방")
                continue
            if with_b == "으로":
                want = "로" if (not bat or rieul) else "으로"
            else:
                want = with_b if bat else without_b
            return (want, len(cand)) if want != cand else ("", 0)
    return ("", 0)
