#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
영상 분석 모듈

유튜브 영상의 자막(타임코드 포함)을 가져와서, Google Gemini API에게
"이 영상이 왜 잘 됐는지"를 구조화된 JSON으로 분석하게 시킨다.

주의사항:
- 자막 추출은 youtube-transcript-api(비공식 라이브러리)를 사용한다.
  유튜브가 화면에 공개적으로 보여주는 자막을 가져오는 것이라 영상
  자체를 다운로드하거나 우회 접근하는 것은 아니지만, 유튜브 공식
  API가 아니므로 자막이 없는 영상이거나 유튜브 쪽 사정으로 실패할
  수 있다. 이 경우 제목/채널명만으로 제한적인 분석을 시도한다.
- Gemini 모델 이름은 자주 바뀐다(2026년에도 여러 차례 모델이
  단종/교체됨). 아래 DEFAULT_MODEL은 "항상 최신 Flash 모델을
  가리키는" 별칭을 기본값으로 쓰지만, 무료 한도에 걸리면
  aistudio.google.com 에서 현재 무료로 넉넉하게 쓸 수 있는 모델
  이름으로 바꿔서 쓰는 것을 권장한다.
"""

import json
import re

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    TranscriptsDisabled,
)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
# 2026-08-22: Google이 API 오류 메시지로 직접 "gemini-2.5-flash-lite는 신규
# 사용자에게 더 이상 제공되지 않으니 gemini-3.5-flash-lite로 바꾸라"고 안내함.
# 모델 이름이 매우 자주 바뀌니, 또 404가 나면 오류 메시지에 적힌 새 모델
# 이름으로 이 값을 바꿔주면 된다.

MAX_TRANSCRIPT_CHARS = 6000

ANALYSIS_PROMPT_TEMPLATE = """당신은 쇼핑 쇼츠 콘텐츠 분석 전문가입니다.
아래 유튜브 쇼츠 영상 정보를 보고, 이 영상이 왜 반응이 좋았는지 분석해서
반드시 아래 JSON 형식으로만 답하세요. 다른 설명이나 마크다운 없이 JSON만
출력하세요.

[영상 제목]
{title}

[채널명]
{channel}
{hint_section}
[자막 (형식: "분:초 텍스트", 자막이 없으면 "자막 없음"이라고 표시됨)]
{transcript}

다음 JSON 형식 그대로 답하세요 (키 이름을 정확히 지키세요):
{{
  "제품정보": "이 영상이 소개하는 제품/아이템에 대한 2~3문장 설명. [실제 제품 정보]가 주어졌다면 그 내용을 우선 활용하고, 없고 자막에도 제품이 명확히 안 나오면 제목을 근거로 추정하고 문장 끝에 '(제목 기반 추정)'이라고 표시하세요.",
  "추천검색어": {{
    "한국어": ["이 제품을 한국 쇼핑몰(쿠팡 등)에서 찾을 때 쓸만한 한국어 검색 키워드 3~5개"],
    "중국어": ["같은 제품을 중국 쇼핑 플랫폼(타오바오, 샤오홍슈 등)에서 찾을 때 쓸만한 중국어(간체) 검색 키워드 3~5개"]
  }},
  "후킹방식": {{
    "시작멘트": "영상 초반 후킹 멘트를 자막에서 그대로 발췌 (자막 없으면 제목으로 대체)",
    "방식": "어떤 후킹 기법을 썼는지 한 줄로 (예: 도발형/궁금증유발형/반전예고형/가치비교형 등)"
  }},
  "떡상요인": "이 영상이 왜 조회수가 잘 나왔을지 2~3문장으로 분석",
  "영상흐름": [
    {{"구간": "0~3초", "설명": "이 구간에서 일어나는 일"}}
  ],
  "cta": "영상이 시청자에게 어떤 행동을 유도하는지 (댓글/저장/구매/팔로우 등)"
}}

영상흐름은 전체 길이를 고려해 3~5개 구간으로 나누세요.
"""


def get_transcript(video_id, languages=("ko", "en")):
    """
    영상 자막을 [{start, duration, text}, ...] 리스트로 반환.
    자막이 없거나 가져올 수 없으면 None을 반환한다 (예외를 던지지 않음).
    """
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=list(languages))
        return [
            {"start": snippet.start, "duration": snippet.duration, "text": snippet.text}
            for snippet in fetched
        ]
    except (TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript):
        return None
    except Exception:
        # youtube-transcript-api는 비공식 라이브러리라 예상 못한 예외가
        # 날 수 있음 - 분석 자체는 계속 진행되도록 조용히 실패 처리
        return None


def format_transcript_for_prompt(segments):
    """자막 세그먼트를 'M:SS 텍스트' 줄 단위 문자열로 변환 (길면 자름)."""
    if not segments:
        return "자막 없음"

    lines = []
    for seg in segments:
        total_sec = int(seg["start"])
        m, s = divmod(total_sec, 60)
        lines.append(f"{m}:{s:02d} {seg['text']}")

    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n...(이하 생략)"
    return text


def _strip_code_fence(raw):
    """모델이 ```json ... ``` 형태로 감싸서 응답한 경우를 대비한 방어 처리."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def parse_analysis_response(raw_text):
    """모델 응답 문자열 -> dict. 형식이 깨졌으면 ValueError."""
    cleaned = _strip_code_fence(raw_text)
    data = json.loads(cleaned)

    required_keys = ["제품정보", "추천검색어", "후킹방식", "떡상요인", "영상흐름", "cta"]
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise ValueError(f"응답에 필요한 항목이 빠졌습니다: {missing}")

    return data


class QuotaExceededError(Exception):
    """Gemini 무료 할당량을 초과했을 때 (429) 구분해서 처리하기 위한 전용 예외."""
    pass


class ModelUnavailableError(Exception):
    """모델이 단종/변경되었을 때 (404) 구분해서 처리하기 위한 전용 예외."""
    pass


def _is_quota_error(e):
    from google.genai.errors import ClientError
    return isinstance(e, ClientError) and getattr(e, "code", None) == 429


def _is_model_unavailable_error(e):
    from google.genai.errors import ClientError
    return isinstance(e, ClientError) and getattr(e, "code", None) == 404


def _classify_and_reraise(e):
    if _is_quota_error(e):
        raise QuotaExceededError(str(e)) from e
    if _is_model_unavailable_error(e):
        raise ModelUnavailableError(str(e)) from e
    raise


def analyze_video(gemini_api_key, title, channel, transcript_segments, product_hint=None, model=DEFAULT_MODEL):
    """
    영상 정보 + 자막을 Gemini에 보내 구조화된 분석 결과(dict)를 받는다.
    자막이 없으면 제목/채널명만으로 제한적인 분석을 시도한다.
    product_hint: 자막이 없거나 부정확할 때, 사용자가 직접 알려주는 실제
    제품 정보(예: "매립형 식기세척기"). 주어지면 프롬프트에 최우선 근거로 포함된다.
    """
    from google import genai
    from google.genai import types

    transcript_text = format_transcript_for_prompt(transcript_segments)
    hint_section = ""
    if product_hint and product_hint.strip():
        hint_section = f"\n[실제 제품 정보 - 사용자가 직접 확인한 내용, 반드시 우선 활용]\n{product_hint.strip()}\n"

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        title=title, channel=channel, transcript=transcript_text, hint_section=hint_section
    )

    client = genai.Client(api_key=gemini_api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as e:
        _classify_and_reraise(e)

    return parse_analysis_response(response.text)


# ---------------------------------------------------------------------------
# 대본 자동 생성 (스타일 템플릿 기반)
# ---------------------------------------------------------------------------

SCRIPT_STYLES = {
    "가치비교형": (
        "가격만 보면 별거 아닌 것 같지만 실제로는 훨씬 큰 가치를 준다는 걸 "
        "강조하는 방식. '이 가격에 이게 된다고?' 같은 반전으로 시작해서, "
        "비슷한 가격대의 다른 제품과 비교하며 왜 더 낫는지 보여준다."
    ),
    "전후비교형": (
        "쓰기 전과 쓴 후의 극적인 차이를 대비시키는 방식. 불편했던 상황을 "
        "먼저 보여주고, 제품을 쓴 뒤 확 달라진 모습으로 전환한다."
    ),
    "정체공개형": (
        "처음엔 정체를 숨기거나 궁금증을 유발하는 질문으로 시작해서, "
        "점점 힌트를 주다가 마지막에 제품의 정체를 공개하는 방식."
    ),
    "베스트공개형": (
        "여러 개의 후보 아이템을 순위나 리스트 형태로 소개하며, 각각의 "
        "핵심 장점을 짧고 강렬하게 전달하는 방식."
    ),
}

SCRIPT_GENERATION_PROMPT_TEMPLATE = """당신은 쇼핑 쇼츠 대본 작가입니다. 아래 제품 설명과 스타일 가이드를
참고해서, 새로운 쇼츠 대본을 작성하세요.

[제품 설명]
{product_info}

[스타일: {style_name}]
{style_guide}

[조건]
- 전체 길이는 {target_length_sec}초 분량 (문장 7~10개 정도)
- 각 문장은 짧고 구어체로, 실제로 소리 내어 말하듯 자연스럽게
- 첫 문장은 반드시 시청자의 시선을 3초 안에 붙잡는 강한 후킹으로 시작
- 마지막 문장에는 댓글/저장/구매 유도 등 자연스러운 CTA를 포함
- 과장광고나 확인되지 않은 효능 주장은 하지 말 것

대본만 출력하세요. 문장 사이는 줄바꿈으로 구분하고, 번호나 따옴표,
설명은 붙이지 마세요.
"""


def generate_script(gemini_api_key, product_info, style_key, target_length_sec=30, model=DEFAULT_MODEL):
    """제품 설명 + 스타일 템플릿 -> 쇼츠 대본(문장별 줄바꿈 문자열)."""
    from google import genai

    style_guide = SCRIPT_STYLES.get(style_key)
    if not style_guide:
        raise ValueError(f"알 수 없는 스타일: {style_key}")

    prompt = SCRIPT_GENERATION_PROMPT_TEMPLATE.format(
        product_info=product_info,
        style_name=style_key,
        style_guide=style_guide,
        target_length_sec=target_length_sec,
    )

    client = genai.Client(api_key=gemini_api_key)
    try:
        response = client.models.generate_content(model=model, contents=prompt)
    except Exception as e:
        _classify_and_reraise(e)

    lines = [line.strip() for line in response.text.strip().split("\n") if line.strip()]
    return "\n".join(lines)


def extract_video_id(url):
    """https://www.youtube.com/watch?v=XXXX -> XXXX"""
    match = re.search(r"[?&]v=([^&]+)", url)
    return match.group(1) if match else url
