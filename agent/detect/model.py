"""2단계: 소형 개인정보 모델(AEGIS, ONNX)을 이 PC 안에서 실행한다 (§3, Phase 4 항목 18).

팀 backend 레포 `local_ai/detector.py`의 추론과 BIO 라벨 해석을 가져와 앱의 Span으로 바꾼다.

- 모델 파일은 레포와 exe에 들어 있지 않다. 사용자가 지정한 로컬 폴더에서만 읽고, 실행 중에 내려받지 않는다.
- 네트워크를 쓰지 않는다. onnxruntime에는 마이크로소프트 원격 수집(1DS, mobile.events.data.microsoft.com)이
  들어 있고, 1.31.1에서 확인해 보니 disable_telemetry_events()만으로는 추론 중 연결 시도가 계속됐다.
  ORT_DISABLE_TELEMETRY=1을 onnxruntime을 불러오기 전에 넣으면 시도가 사라진다 (2026-11-05 측정).
- 추론이 실패하면 ModelError를 낸다. 호출하는 쪽(pipeline)은 모델 결과를 버리고 '모델 검사 실패'를 알린다.
  규칙 결과만으로 완전한 검사처럼 보이게 하지 않는다.
- 원문은 메모리에서만 다룬다. 같은 글을 다시 검사하지 않도록 최근 결과 몇 개만 메모리에 둔다(키는 해시).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

from ..sanitize.josa import final_sound
from .span import Span

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")  # onnxruntime을 import하기 전에 (아래 AegisModel에서 지연 import)

NAME = "AEGIS"
MODEL_FILES = ("model.onnx", "model_quantized.onnx", "onnx/model_quantized.onnx")
REQUIRED_FILES = ("config.json", "tokenizer.json")
MAX_TOKENS = 512    # 특수 토큰 포함. 넘으면 겹치는 창으로 나눠 모두 검사한다
STRIDE = 32         # 창 사이 겹침: 경계에 걸린 개체도 한 창 안에 온전히 들어오게
CACHE_SIZE = 32
MAX_CONFIDENCE = 0.97
CITY_ONLY_CONFIDENCE = 0.55  # 지역명만 있으면 준식별자 → 자동 변환 기준(0.6) 아래, '확인 필요'로

# AEGIS 라벨 → 앱 유형. backend local_ai의 _TYPES와 같고, 같은 계열 라벨 이름(…NUM)도 받는다.
LABEL_TYPES = {
    "GIVENNAME": "name", "SURNAME": "name",
    "USERNAME": "username", "EMAIL": "email", "TELEPHONENUM": "phone",
    "DATEOFBIRTH": "dob", "CREDITCARDNUMBER": "card",
    "IDCARD": "id_number", "IDCARDNUM": "id_number", "DRIVERLICENSENUM": "id_number",
    "SOCIALNUM": "id_number", "TAXNUM": "id_number",
    "STREET": "address", "CITY": "address", "ZIPCODE": "address", "BUILDINGNUM": "address",
    "IP_ADDRESS": "ip", "PASSWORD": "secret", "ACCOUNTNUM": "account",
}
RAW_LABELS_KO = {  # 표시 순서도 이 순서 (성·이름)
    "SURNAME": "성", "GIVENNAME": "이름", "USERNAME": "계정", "EMAIL": "이메일", "TELEPHONENUM": "전화번호",
    "DATEOFBIRTH": "생년월일", "CREDITCARDNUMBER": "카드번호", "IDCARD": "신분증 번호",
    "IDCARDNUM": "신분증 번호", "DRIVERLICENSENUM": "운전면허 번호", "SOCIALNUM": "주민·사회보장 번호",
    "TAXNUM": "납세 번호", "STREET": "도로명", "CITY": "지역", "ZIPCODE": "우편번호", "BUILDINGNUM": "건물 번호",
    "IP_ADDRESS": "IP 주소", "PASSWORD": "비밀번호", "ACCOUNTNUM": "계좌번호",
}

# 모델이 이름·주소 뒤의 조사까지 잡는 경우가 있다 ("김민수의"). 팀 서버와 같은 규칙으로 뗀다
# (backend local_ai `_trim_korean_particle`, backend#12 · PR #19). 잘못 떼면 이름 한 글자가 그대로 나가고,
# 못 떼면 조사가 함께 바뀔 뿐이라 덜 떼는 쪽을 고른다.
# - 받침에 맞는 조사만: 받침이 있으면 은/을/과/이/으로…, 없으면 는/를/와/가
# - 이름·주소 끝 글자로 흔한 것(나·로·랑·야·아·도·만: 유나, 세로, 사랑, 민아, 테헤란로…)은 조사로 보지 않는다
# - 한 글자 조사는 남는 값이 한글 세 글자 이상일 때, 두 글자 이상 조사는 두 글자 이상일 때만 뗀다
# - 이름 끝 '이'·'가'는 이름 마지막 글자일 수 있어("김다솜이", "박지훈이") 남는 값이 네 글자 이상일 때만 뗀다
# - 복성 + 한 글자 이름 + 한 글자("남궁민이")는 떼지 않는다
# - 모델 구간 바로 뒤가 한글이면(낱말 중간에서 끊김) 떼지 않는다
# 서버보다 더 보는 것: 에·처럼·보다·하고·이에요·이랑, 겹친 조사("김민수님께서" → 께서, 님). 이름 끝 글자로 쓰이지 않는 것들이다.
_JOSA_BATCHIM = ("이에요", "이랑", "으로", "은", "을", "과", "이")
_JOSA_OPEN = ("는", "를", "와", "가")
_JOSA_ANY = ("으로부터", "에게서", "한테서", "께서는", "에게", "한테", "께서", "에서", "까지", "부터", "처럼", "보다", "하고",
             "님께", "님", "씨", "의", "께", "에")
_JOSA_ALL = sorted(_JOSA_BATCHIM + _JOSA_OPEN + _JOSA_ANY, key=len, reverse=True)
_JOSA_AMBIGUOUS = frozenset({"이", "가"})
_COMPOUND_SURNAMES = ("남궁", "황보", "선우", "제갈", "사공", "서문", "독고", "동방")
_TRIM_TYPES = {"name", "address"}


class ModelUnavailable(Exception):
    """모델을 켤 수 없음. 메시지는 사용자에게 그대로 보여 준다 (원문 없음)."""


class ModelError(RuntimeError):
    """추론 실패. 이 글은 모델로 검사하지 못했다는 뜻이다."""


# ====================================================================== 순수 함수 (테스트 가능)
def softmax_max(logits):
    """토큰별 (가장 높은 라벨 번호, 그 확률). logits: numpy [토큰 수, 라벨 수]."""
    import numpy as np
    shifted = logits - logits.max(axis=-1, keepdims=True)
    probs = np.exp(shifted)
    probs /= probs.sum(axis=-1, keepdims=True)
    return probs.argmax(axis=-1), probs.max(axis=-1)


def decode(text: str, offsets, label_ids, probs, id2label: dict) -> list[dict]:
    """토큰별 예측 → 문자 구간 [{start, end, type, raw(set), probs(list)}].

    offsets는 문자 위치이고 특수 토큰은 (0, 0)이다. I- 조각이 같은 유형으로 이어지면
    (공백만 사이에 있을 때 포함) 한 구간으로 붙인다. 성 바로 뒤의 이름은 한 사람으로 합친다.
    """
    spans: list[dict] = []
    active = None
    for (start, end), idx, p in zip(offsets, label_ids, probs):
        if start == end:
            continue
        label = id2label.get(str(int(idx)))
        if label is None:
            raise ModelError("unknown model output label")
        prefix, sep, raw = label.partition("-")
        kind = LABEL_TYPES.get(raw) if sep else None
        if kind is None:
            if active:
                spans.append(active)
                active = None
            continue
        if active and prefix == "I" and active["type"] == kind:
            gap = text[active["end"]:start] if start > active["end"] else ""
            if not gap.strip():
                active["end"] = max(active["end"], end)
                active["raw"].add(raw)
                active["probs"].append(float(p))
                continue
        if active:
            spans.append(active)
        active = {"start": start, "end": end, "type": kind, "raw": {raw}, "probs": [float(p)], "head": raw}
    if active:
        spans.append(active)

    merged: list[dict] = []
    for span in spans:
        prev = merged[-1] if merged else None
        gap = text[prev["end"]:span["start"]] if prev else None
        if prev and prev["head"] == "SURNAME" and span["head"] == "GIVENNAME" and gap in ("", " "):
            _absorb(prev, span)
            prev["head"] = "NAME"  # 합친 뒤에는 다음 이름 조각을 더 붙이지 않는다
        elif (prev and prev["type"] == span["type"] == "address" and gap is not None
              and len(gap) <= 3 and not gap.strip(" ,\t")):
            _absorb(prev, span)  # "서울특별시 강남구"(지역) + "테헤란로"(도로명) + "123"(건물 번호)
        else:
            merged.append(span)
    return merged


def _absorb(into: dict, span: dict) -> None:
    into["end"] = max(into["end"], span["end"])
    into["raw"] |= span["raw"]
    into["probs"] += span["probs"]


def merge_windows(spans: list[dict]) -> list[dict]:
    """겹치는 창에서 두 번 나온 같은 유형의 구간을 하나로 합친다."""
    out: list[dict] = []
    for span in sorted(spans, key=lambda s: (s["type"], s["start"], s["end"])):
        if out and out[-1]["type"] == span["type"] and span["start"] < out[-1]["end"]:
            _absorb(out[-1], span)
        else:
            out.append(dict(span, raw=set(span["raw"]), probs=list(span["probs"])))
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def _hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def trim_josa(text: str, start: int, end: int, kind: str = "name") -> int:
    """이름·주소(kind: name / address) 끝에 붙은 조사를 떼어낸 새 end. 규칙은 위 _JOSA_* 설명."""
    if end < len(text) and _hangul(text[end]):
        return end  # 모델이 낱말 중간에서 끊었다 ("김민수의번호")
    for _ in range(2):  # "김민수님께서" → "께서", "님"
        value = text[start:end]
        for josa in _JOSA_ALL:
            if not value.endswith(josa):
                continue
            stem = value[: -len(josa)]
            if not stem or not (_hangul(stem[-1]) or stem[-1].isdigit()):
                continue
            hangul = sum(map(_hangul, stem))
            if hangul < (3 if len(josa) == 1 else 2):
                continue
            if kind == "name" and len(josa) == 1 and (
                    (len(value) == 4 and value.startswith(_COMPOUND_SURNAMES))
                    or (josa in _JOSA_AMBIGUOUS and hangul < 4)):
                continue
            batchim = final_sound(stem)[0]
            if josa in _JOSA_BATCHIM and not batchim:
                continue
            if josa in _JOSA_OPEN and batchim:
                continue
            end -= len(josa)
            break
        else:
            return end
    return end


def to_spans(text: str, raw_spans: list[dict]) -> list[Span]:
    out = []
    for r in raw_spans:
        start, end = r["start"], r["end"]
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if r["type"] in _TRIM_TYPES:
            end = trim_josa(text, start, end, r["type"])
        if end <= start:
            continue
        conf = min(sum(r["probs"]) / len(r["probs"]), MAX_CONFIDENCE)
        order = list(RAW_LABELS_KO)
        raw = sorted(r["raw"], key=lambda x: order.index(x) if x in order else len(order))
        labels = "·".join(RAW_LABELS_KO.get(x, x) for x in raw)
        reason = f"소형 모델({NAME}) 판단: {labels}"
        if r["type"] == "address" and r["raw"] <= {"CITY"}:
            conf = min(conf, CITY_ONLY_CONFIDENCE)
            reason += " (지역만, 준식별자)"
        out.append(Span(r["type"], start, end, text[start:end], round(conf, 3), "S", reason, "model"))
    return out


# ====================================================================== 모델
def find_model_file(folder: Path) -> Path | None:
    return next((folder / name for name in MODEL_FILES if (folder / name).is_file()), None)


def looks_like_model_dir(folder) -> bool:
    folder = Path(folder)
    return (folder.is_dir() and find_model_file(folder) is not None
            and all((folder / f).is_file() for f in REQUIRED_FILES))


class AegisModel:
    """로컬 폴더의 AEGIS 토큰 분류 모델. 실패는 숨기지 않는다 (ModelUnavailable / ModelError)."""

    name = NAME

    def __init__(self, model_dir, threads: int = 2):
        folder = Path(model_dir).expanduser()
        if not looks_like_model_dir(folder):
            raise ModelUnavailable("모델 폴더에 model.onnx(또는 model_quantized.onnx), tokenizer.json, "
                                   "config.json이 있어야 해요.")
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise ModelUnavailable("모델 실행 모듈이 설치돼 있지 않아요 (numpy, onnxruntime, tokenizers).") from exc
        try:
            ort.disable_telemetry_events()
            labels = json.loads((folder / "config.json").read_text(encoding="utf-8"))["id2label"]
            if not isinstance(labels, dict) or not labels:
                raise ValueError("invalid labels")
            self._labels = {str(k): str(v) for k, v in labels.items()}
            tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=MAX_TOKENS, stride=STRIDE)
            tokenizer.no_padding()
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(1, int(threads))
            opts.inter_op_num_threads = 1
            session = ort.InferenceSession(str(find_model_file(folder)), sess_options=opts,
                                           providers=["CPUExecutionProvider"])
            names = {i.name for i in session.get_inputs()}
            if "input_ids" not in names or names - {"input_ids", "attention_mask", "token_type_ids"}:
                raise ValueError("unsupported model inputs")
        except Exception as exc:
            raise ModelUnavailable("모델을 불러오지 못했어요. 모델 파일이 손상됐거나 지원하지 않는 형식이에요.") from exc
        self.folder = folder
        self._np, self._tokenizer, self._session = np, tokenizer, session
        self._cache: OrderedDict[bytes, list[Span]] = OrderedDict()
        self._lock = threading.Lock()

    def detect(self, text: str) -> list[Span]:
        if not text.strip():
            return []
        key = hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return [Span(**vars(s)) for s in self._cache[key]]
        try:
            spans = to_spans(text, merge_windows(self._infer(text)))
        except ModelError:
            raise
        except Exception as exc:  # 예외 메시지에 원문이 섞일 수 있어 그대로 넘기지 않는다
            raise ModelError("local model inference failed") from exc
        with self._lock:
            self._cache[key] = spans
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return [Span(**vars(s)) for s in spans]

    def _infer(self, text: str) -> list[dict]:
        np = self._np
        first = self._tokenizer.encode(text, add_special_tokens=True)
        found: list[dict] = []
        for chunk in (first, *first.overflowing):
            feed = {}
            for inp in self._session.get_inputs():
                data = {"input_ids": chunk.ids, "attention_mask": chunk.attention_mask}.get(inp.name, chunk.type_ids)
                feed[inp.name] = np.asarray([data], dtype=np.int64)
            logits = self._session.run(None, feed)[0]
            if logits.ndim != 3 or logits.shape[:2] != (1, len(chunk.ids)):
                raise ModelError("unexpected model output dimensions")
            label_ids, probs = softmax_max(logits[0].astype(np.float64))
            found += decode(text, chunk.offsets, label_ids, probs, self._labels)
        return found

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


# ====================================================================== 앱에서 쓰는 상태 관리
class ModelManager:
    """모델을 백그라운드에서 불러오고 상태를 알려 준다. 불러오는 동안에는 규칙·사전만으로 검사한다."""

    def __init__(self, on_change=None):
        self.on_change = on_change  # 다른 스레드에서 불린다 → App.post로 넘길 것
        self.state = "off"           # off / loading / ready / error
        self.message = ""
        self.folder: Path | None = None
        self.model: AegisModel | None = None
        self._generation = 0

    def configure(self, folder, threads: int = 2) -> None:
        self._generation += 1
        gen = self._generation
        self.model = None
        if not folder:
            self.state, self.message, self.folder = "off", "", None
            self._changed()
            return
        self.state, self.message, self.folder = "loading", "", Path(folder)

        def load():
            try:
                model, state, message = AegisModel(folder, threads), "ready", ""
            except ModelUnavailable as exc:
                model, state, message = None, "error", str(exc)
            if gen != self._generation:  # 그사이 설정이 바뀌었으면 버린다
                return
            self.model, self.state, self.message = model, state, message
            self._changed()

        self._changed()
        threading.Thread(target=load, daemon=True, name="model-load").start()

    def detector(self):
        return self.model if self.state == "ready" else None

    def describe(self) -> str:
        if self.state == "off":
            return "꺼짐 · 규칙과 사전으로만 검사해요 (이름·주소는 일부만 잡아요)"
        if self.state == "loading":
            return f"불러오는 중… ({self.folder})"
        if self.state == "ready":
            return f"사용 중 · {NAME} ({self.folder})"
        return f"오류 · {self.message}"

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()
