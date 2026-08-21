#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
장면 분할 + AI 장면 설명 + 대본-장면 자동 매칭 모듈

흐름:
  1. detect_scenes()      - 업로드된 영상을 장면(컷) 단위로 자동 분할
  2. extract_thumbnail()  - 각 장면의 대표 프레임을 썸네일 이미지로 추출
  3. describe_scene()     - Gemini 비전으로 각 장면이 뭘 보여주는지 짧게 설명
  4. match_script_to_scenes() - 대본 문장마다 가장 어울리는 장면을 AI가 선택
  5. export_scene_clip()  - (선택) 실제로 그 구간만 잘라서 개별 영상 파일로 저장

주의:
- 장면 분할은 scenedetect 라이브러리(내용 기반 컷 감지)를 쓴다. 컷이
  뚜렷하지 않은 영상(계속 이어지는 롱테이크)은 장면이 하나로 뭉치거나
  과하게 쪼개질 수 있다 - min_scene_len으로 최소 길이를 보정한다.
- 실제 클립 추출(export_scene_clip)은 ffmpeg가 있어야 한다. Streamlit
  Cloud에 배포할 때는 packages.txt에 "ffmpeg" 한 줄을 추가해야 한다.
"""

import json
import os
import re
import subprocess
import tempfile

import cv2
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector

DEFAULT_MODEL = "gemini-flash-latest"

MAX_SCENES_PER_VIDEO = 40  # 너무 잘게 쪼개지는 것 방지 (안전장치)


def detect_scenes(video_path, threshold=27.0, min_scene_len_sec=0.6):
    """
    영상을 장면 단위로 분할해서 [(start_sec, end_sec), ...] 리스트 반환.
    threshold가 낮을수록 더 민감하게(장면을 더 잘게) 나눈다.
    """
    video = open_video(video_path)
    fps = video.frame_rate
    min_scene_len_frames = max(1, int(min_scene_len_sec * fps))

    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len_frames))
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    if not scene_list:
        # 컷이 하나도 감지 안 되면(단일 롱테이크) 영상 전체를 장면 1개로 취급
        duration = video.duration.get_seconds()
        return [(0.0, duration)]

    result = [(start.get_seconds(), end.get_seconds()) for start, end in scene_list]
    return result[:MAX_SCENES_PER_VIDEO]


def extract_thumbnail(video_path, at_sec):
    """영상의 특정 시각(초)의 프레임을 JPEG 바이트로 추출."""
    cap = cv2.VideoCapture(video_path)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, at_sec * 1000)
        success, frame = cap.read()
        if not success:
            return None
        success, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not success:
            return None
        return buf.tobytes()
    finally:
        cap.release()


def build_scene_library(video_path, video_name, threshold=27.0, min_scene_len_sec=0.6):
    """
    영상 1개 -> 장면 리스트(썸네일 포함, 설명은 아직 없음).
    각 항목: {scene_id, video_name, start_sec, end_sec, thumbnail(bytes)}
    """
    scenes = detect_scenes(video_path, threshold=threshold, min_scene_len_sec=min_scene_len_sec)
    library = []
    for i, (start, end) in enumerate(scenes):
        mid = start + (end - start) / 2
        thumb = extract_thumbnail(video_path, mid)
        library.append({
            "scene_id": f"{video_name}#{i}",
            "video_name": video_name,
            "video_path": video_path,
            "scene_index": i,
            "start_sec": round(start, 2),
            "end_sec": round(end, 2),
            "thumbnail": thumb,
            "description": None,
        })
    return library


def describe_scene(gemini_api_key, thumbnail_bytes, model=DEFAULT_MODEL):
    """장면 썸네일 이미지 -> AI가 2~6단어 정도의 짧은 한국어 설명 생성."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=gemini_api_key)
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=thumbnail_bytes, mime_type="image/jpeg"),
            "이 쇼츠 영상의 한 장면이야. 이 장면에서 뭘 보여주고 있는지 "
            "2~6단어의 짧은 한국어 구절로만 답해. 예: '제품 언박싱 시연', "
            "'제품 클로즈업', '사용 전후 비교'. 다른 설명 없이 구절만 출력해.",
        ],
    )
    return response.text.strip().strip('"').strip("'")


MATCH_PROMPT_TEMPLATE = """당신은 쇼츠 영상 편집자입니다. 아래 대본 문장들과, 사용 가능한 장면
목록이 있습니다. 각 대본 문장에 가장 잘 어울리는 장면을 하나씩 골라주세요.
같은 장면을 여러 문장에 중복으로 써도 괜찮습니다 (적절한 장면이 그것뿐이라면).

[대본 문장]
{script_lines}

[사용 가능한 장면 목록 (id: 설명)]
{scene_list}

반드시 아래 JSON 형식으로만 답하세요. 다른 설명 없이 JSON만 출력하세요:
{{
  "매칭": [
    {{"문장번호": 1, "장면id": "영상파일명#장면번호", "이유": "왜 이 장면을 골랐는지 한 줄"}}
  ]
}}
"""


def match_script_to_scenes(gemini_api_key, script_lines, scene_library, model=DEFAULT_MODEL):
    """
    대본 문장 리스트 + 장면 라이브러리 -> {문장 인덱스(0-based): scene_id} 매핑.
    설명이 없는(None) 장면은 매칭 후보에서 제외한다.
    """
    from google import genai
    from google.genai import types

    usable = [s for s in scene_library if s.get("description")]
    if not usable:
        raise ValueError("설명이 붙은 장면이 없습니다. 먼저 장면 설명을 생성해주세요.")

    numbered_lines = "\n".join(f"{i+1}. {line}" for i, line in enumerate(script_lines))
    scene_list_text = "\n".join(f"{s['scene_id']}: {s['description']}" for s in usable)

    prompt = MATCH_PROMPT_TEMPLATE.format(script_lines=numbered_lines, scene_list=scene_list_text)

    client = genai.Client(api_key=gemini_api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)

    valid_scene_ids = {s["scene_id"] for s in usable}
    result = {}
    for item in data.get("매칭", []):
        line_no = item.get("문장번호")
        scene_id = item.get("장면id")
        if line_no is None or scene_id not in valid_scene_ids:
            continue
        result[line_no - 1] = {"scene_id": scene_id, "이유": item.get("이유", "")}
    return result


def export_scene_clip(video_path, start_sec, end_sec, output_path):
    """ffmpeg로 영상의 특정 구간만 잘라서 output_path에 저장 (재인코딩, 정확한 컷)."""
    duration = max(0.1, end_sec - start_sec)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duration),
        "-c:v", "libx264", "-c:a", "aac",
        "-loglevel", "error",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 클립 추출 실패: {result.stderr[:500]}")
    return output_path


def ffmpeg_available():
    """ffmpeg 바이너리가 시스템에 있는지 확인 (클립 내보내기 기능용)."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
