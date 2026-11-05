"""테스트용 가짜 AEGIS 모델 (실제 모델 파일이 레포에 없어서 만든다).

글자 하나를 토큰 하나로 자르는 토크나이저와, 글자마다 정해진 라벨 점수를 내는 ONNX 그래프(Gather 한 개).
실제 모델과 같은 파일 구성(model.onnx, tokenizer.json, config.json)과 입력·출력 형식을 쓰므로
불러오기 → 토큰 나누기(512 토큰 창 + 겹침) → 추론 → BIO 해석 → Span 변환까지 실제 코드로 검사할 수 있다.
"""
from __future__ import annotations

import json
from pathlib import Path

LABELS = ["O", "B-SURNAME", "B-GIVENNAME", "I-GIVENNAME", "I-TELEPHONENUM",
          "B-CITY", "I-CITY", "B-STREET", "I-STREET"]
STRONG, WEAK = 6.0, 1.5  # 강한 예측 → 확률 약 0.98, 약한 예측 → 약 0.36 (라벨 9개 기준 softmax)

CHAR_LABELS = {
    "김": ("B-SURNAME", STRONG), "민": ("B-GIVENNAME", STRONG), "수": ("I-GIVENNAME", STRONG),
    "의": ("I-GIVENNAME", STRONG),  # 모델이 조사까지 잡는 경우 ("김민수의")
    "하": ("B-GIVENNAME", WEAK), "나": ("I-GIVENNAME", WEAK),
    "서": ("B-CITY", STRONG), "울": ("I-CITY", STRONG),
    "테": ("B-STREET", STRONG), "헤": ("I-STREET", STRONG), "란": ("I-STREET", STRONG), "로": ("I-STREET", STRONG),
    **{c: ("I-TELEPHONENUM", STRONG) for c in "0123456789-"},
}


def deps_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnx  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def build(folder) -> Path:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Regex, Tokenizer, models, pre_tokenizers, processors

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    vocab = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2}
    chars = [chr(c) for c in range(0x21, 0x7F)] + [chr(c) for c in range(0xAC00, 0xD7A4)]
    for ch in chars:
        vocab[ch] = len(vocab)

    tok = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.WhitespaceSplit(),
        pre_tokenizers.Split(Regex("."), behavior="isolated"),
    ])
    tok.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    tok.save(str(folder / "tokenizer.json"))

    table = np.zeros((len(vocab), len(LABELS)), dtype=np.float32)
    for ch, (label, strength) in CHAR_LABELS.items():
        table[vocab[ch], LABELS.index(label)] = strength
    graph = helper.make_graph(
        [helper.make_node("Gather", ["table", "input_ids"], ["logits"], axis=0)],
        "toy-aegis",
        [helper.make_tensor_value_info("input_ids", TensorProto.INT64, [1, None]),
         helper.make_tensor_value_info("attention_mask", TensorProto.INT64, [1, None])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, None, len(LABELS)])],
        initializer=[numpy_helper.from_array(table, "table")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(folder / "model.onnx"))
    (folder / "config.json").write_text(
        json.dumps({"id2label": {str(i): label for i, label in enumerate(LABELS)}}), encoding="utf-8")
    return folder
