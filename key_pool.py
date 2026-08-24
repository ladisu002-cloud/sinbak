#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
여러 개의 Gemini API 키를 등록해두고, 하나가 무료 한도(429)에 걸리면
자동으로 다음 키로 넘어가는 유틸리티.

주의: Gemini 무료 한도는 "키" 단위가 아니라 "프로젝트" 단위로 체크된다.
같은 프로젝트 안에서 키를 여러 개 만들어봐야 할당량은 하나를 공유하므로
소용없다. 여기 등록하는 키들은 반드시 서로 다른 프로젝트에서 발급받은
키여야 실제로 할당량이 늘어나는 효과가 있다.
"""


def parse_keys(raw):
    """쉼표로 구분된 키 문자열 -> 공백 제거된 키 리스트 (빈 항목 제외)."""
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def call_with_fallback(keys, func, *args, quota_errors=(), **kwargs):
    """
    keys에 담긴 API 키를 순서대로 시도해서 func(key, *args, **kwargs)를
    호출한다. quota_errors에 해당하는 예외가 나면 다음 키로 넘어간다.
    모든 키가 실패하면 마지막 예외를 그대로 던진다.
    """
    if not keys:
        raise ValueError("사용 가능한 API 키가 없습니다.")

    last_error = None
    for key in keys:
        try:
            return func(key, *args, **kwargs)
        except quota_errors as e:
            last_error = e
            continue
    raise last_error
