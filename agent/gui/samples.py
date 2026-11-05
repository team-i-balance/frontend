"""흐름 확인용 예시 프롬프트. 모든 값은 합성(가짜) 데이터다."""

SAMPLES = {
    "상담 안내문 (개인정보)": (
        "광주 광산구 송정동 123-4에 사는 홍길동(17세, 010-1234-5678) 학생이\n"
        "GSM 2학년 3반인데, 지난주 상담에서 성적 문제로 울었어요.\n"
        "학부모님께 보낼 상담 결과 안내문 써줘."
    ),
    "코드네임·고객사 (기밀 이름표)": (
        "오로라 프로젝트 출시 일정이 밀렸는데, 한빛전자 담당자한테 보낼 사과 메일 써줘.\n"
        "담당자는 김민수 과장님이고 메일은 minsu.kim@hanbit-elec.co.kr 이야."
    ),
    "API 키·주민번호 (Claude Code 질문)": (
        "배포 스크립트가 계속 403이 나요. 설정은 아래와 같아요.\n"
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
        "DB password: Kp!2026secure\n"
        "서버는 10.20.30.40 이고, 테스트 계정 주민번호는 950315-1234567 이에요. 원인 찾아줘."
    ),
    "사내 소스코드 (기밀 내용)": (
        "이 코드에서 결제 금액이 가끔 0원으로 나가는 버그 찾아줘.\n"
        "```java\n"
        "package com.kpcorp.billing;\n"
        "public class InvoiceService {\n"
        "    private final RateTable rates;\n"
        "    public long total(Order o) {\n"
        "        long sum = 0;\n"
        "        for (Line l : o.lines()) { sum += rates.price(l) * l.qty(); }\n"
        "        return o.isPromo() ? 0 : sum;\n"
        "    }\n"
        "}\n"
        "```"
    ),
    "공개 질문 (그대로 통과)": "파이썬에서 딕셔너리를 값 기준으로 정렬하는 방법 알려줘.",
}
