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

DEFAULT_MODEL = "gemini-flash-latest"

MAX_TRANSCRIPT_CHARS = 6000

ANALYSIS_PROMPT_TEMPLATE = """당신은 쇼핑 쇼츠 콘텐츠 분석 전문가입니다.
아래 유튜브 쇼츠 영상 정보를 보고, 이 영상이 왜 반응이 좋았는지 분석해서
반드시 아래 JSON 형식으로만 답하세요. 다른 설명이나 마크다운 없이 JSON만
출력하세요.

[영상 제목]
{title}

[채널명]
{channel}

[자막 (형식: "분:초 텍스트", 자막이 없으면 "자막 없음"이라고 표시됨)]
{transcript}

다음 JSON 형식 그대로 답하세요 (키 이름을 정확히 지키세요):
{{
  "제품정보": "이 영상이 소개하는 제품/아이템에 대한 2~3문장 설명. 자막에 제품이 명확히 안 나오면 제목을 근거로 추정하고 문장 끝에 '(제목 기반 추정)'이라고 표시하세요.",
  "추천검색어": ["이 제품을 쇼핑몰에서 찾을 때 쓸만한 검색 키워드 3~5개"],
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


def analyze_video(gemini_api_key, title, channel, transcript_segments, model=DEFAULT_MODEL):
    """
    영상 정보 + 자막을 Gemini에 보내 구조화된 분석 결과(dict)를 받는다.
    자막이 없으면 제목/채널명만으로 제한적인 분석을 시도한다.
    """
    from google import genai
    from google.genai import types

    transcript_text = format_transcript_for_prompt(transcript_segments)
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(title=title, channel=channel, transcript=transcript_text)

    client = genai.Client(api_key=gemini_api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    return parse_analysis_response(response.text)


def extract_video_id(url):
    """https://www.youtube.com/watch?v=XXXX -> XXXX"""
    match = re.search(r"[?&]v=([^&]+)", url)
    return match.group(1) if match else url
