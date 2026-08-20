#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
신박살림 아이템 발굴기 - 웹 앱 버전

로컬 CLI 스크립트(youtube_item_finder.py)와 같은 핵심 로직을 그대로
사용하되, 브라우저에서 키워드를 입력하고 결과를 표로 바로 볼 수 있게
Streamlit UI를 씌운 버전입니다.

탭 구성:
  1) 어제 인기 쇼핑쇼츠 100개 - 미리 정해둔 쇼핑 키워드로 어제(+여유 1일)
     업로드된 쇼츠 중 조회수 높은 순 top N을 바로 확인하는 빠른 모드
  2) 커스텀 검색 - 원하는 키워드로 자유롭게 검색 (기존 기능)

로컬 실행:
    streamlit run streamlit_app.py

배포 방법은 DEPLOY.md 참고 (GitHub + Streamlit Community Cloud, 무료).
"""

import os
from datetime import datetime

import pandas as pd
import streamlit as st

from youtube_item_finder import find_items, find_yesterday_top_shopping, DEFAULT_SHOPPING_KEYWORDS

st.set_page_config(page_title="신박살림 아이템 발굴기", page_icon="🔎", layout="wide")

COLUMN_ORDER = [
    "검색키워드", "제목", "채널명", "URL", "조회수", "구독자수",
    "영상점수", "조회수/구독자배수", "참여율(%)", "영상길이(초)", "업로드일",
]


def get_api_key():
    """API 키를 우선순위대로 찾는다: Streamlit Secrets -> 환경변수 -> 사이드바 직접 입력."""
    try:
        if "YOUTUBE_API_KEY" in st.secrets:
            return st.secrets["YOUTUBE_API_KEY"], "secrets"
    except Exception:
        pass

    env_key = os.environ.get("YOUTUBE_API_KEY")
    if env_key:
        return env_key, "env"

    return None, "manual"


def reorder(df):
    return df[[c for c in COLUMN_ORDER if c in df.columns]]


def render_results(df, keyword_count, sort_label):
    """결과 표시 (지표 + 표 + 다운로드) - 두 탭에서 공용으로 사용."""
    st.success(f"총 {len(df)}건을 찾았어요! ({sort_label} 기준 정렬됨)")

    top_score = df[pd.to_numeric(df["영상점수"], errors="coerce").notna()]
    hot_count = (pd.to_numeric(top_score["영상점수"], errors="coerce") >= 5).sum() if len(top_score) else 0
    col1, col2, col3 = st.columns(3)
    col1.metric("수집된 영상", f"{len(df)}건")
    col2.metric("영상점수 5점 이상", f"{hot_count}건")
    col3.metric("검색 키워드", f"{keyword_count}개")

    table_height = min(38 + 35 * len(df), 800)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=table_height,
        column_config={
            "URL": st.column_config.LinkColumn("링크", display_text="영상 보기"),
            "영상점수": st.column_config.NumberColumn("영상점수", format="%.2f"),
        },
    )

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV로 다운로드",
        csv,
        file_name=f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        key=f"download_{keyword_count}_{len(df)}_{sort_label}",
    )


st.title("🔎 신박살림 아이템 발굴기")
st.caption("유튜브 영상을 검색해서, 뜨고 있는 쇼핑 아이템과 쇼츠 소재를 찾아드려요.")

api_key, key_source = get_api_key()

with st.sidebar:
    st.header("설정")
    if key_source == "manual":
        api_key_input = st.text_input(
            "유튜브 API 키",
            type="password",
            help="배포된 앱이라면 Streamlit Cloud의 Secrets에 등록해두는 게 안전합니다. "
                 "지금은 이 창에만 입력되고 저장되지 않습니다.",
        )
        if api_key_input:
            api_key = api_key_input
    else:
        st.success("API 키가 설정되어 있어요 ✅")

for key in ["preset_result_df", "preset_result_keywords", "custom_result_df", "custom_result_keywords"]:
    if key not in st.session_state:
        st.session_state[key] = None

tab1, tab2 = st.tabs(["🔥 어제 인기 쇼핑쇼츠 100개", "🔍 커스텀 검색"])

# ---------------------------------------------------------------------------
# 탭 1: 어제 인기 쇼핑쇼츠 100개 (프리셋 모드)
# ---------------------------------------------------------------------------
with tab1:
    st.write(
        "미리 정해둔 쇼핑 관련 키워드로, **어제 하루 업로드된 쇼츠 중 조회수가 높은 순**으로 "
        "정리해드려요. (유튜브 인덱싱 지연을 감안해 최근 2일 범위로 넉넉하게 검색합니다.)"
    )

    preset_keywords_input = st.text_area(
        "검색할 쇼핑 키워드 (쉼표로 구분, 필요하면 자유롭게 수정하세요)",
        value=", ".join(DEFAULT_SHOPPING_KEYWORDS),
        height=80,
    )
    top_n = st.slider("상위 몇 개까지 볼까요?", min_value=20, max_value=100, value=100, step=10)

    run_preset = st.button("🔥 어제 인기 쇼핑쇼츠 확인하기", type="primary", key="run_preset")

    if run_preset:
        if not api_key:
            st.error("먼저 왼쪽 사이드바에 유튜브 API 키를 입력해주세요.")
        else:
            preset_keywords = [k.strip() for k in preset_keywords_input.split(",") if k.strip()]
            if not preset_keywords:
                st.error("키워드를 최소 1개는 남겨주세요.")
            else:
                with st.spinner(f"쇼핑 키워드 {len(preset_keywords)}개로 어제 인기 쇼츠 검색 중... (1~2분 걸릴 수 있어요)"):
                    try:
                        rows = find_yesterday_top_shopping(
                            api_key=api_key,
                            keywords=preset_keywords,
                            top_n=top_n,
                        )
                    except Exception as e:
                        st.error(f"검색 중 오류가 발생했습니다: {e}")
                        rows = []

                if rows:
                    st.session_state.preset_result_df = reorder(pd.DataFrame(rows))
                    st.session_state.preset_result_keywords = preset_keywords
                else:
                    st.session_state.preset_result_df = None
                    st.warning("검색 결과가 없어요. 키워드를 조정해보세요.")

    if st.session_state.preset_result_df is not None:
        render_results(
            st.session_state.preset_result_df,
            len(st.session_state.preset_result_keywords),
            "조회수",
        )
    else:
        st.info("키워드를 확인하고 위 버튼을 눌러보세요.")

# ---------------------------------------------------------------------------
# 탭 2: 커스텀 검색 (기존 기능)
# ---------------------------------------------------------------------------
with tab2:
    max_results = st.slider("키워드당 최대 수집 개수", min_value=10, max_value=50, value=30, step=5, key="custom_max_results")
    days = st.slider("최근 며칠 이내 영상만", min_value=1, max_value=90, value=30, key="custom_days")
    shorts_only = st.checkbox("쇼츠(짧은 영상)만 보기", value=True, key="custom_shorts_only")
    shorts_max_seconds = st.slider(
        "쇼츠 판단 기준(초)", min_value=60, max_value=240, value=180,
        disabled=not shorts_only, key="custom_shorts_max_seconds",
    )
    sort_choice = st.radio("정렬 기준", ["영상점수 (채널 규모 대비 잘 터진 순)", "조회수 (단순 조회수 순)"], horizontal=True)
    sort_by = "views" if sort_choice.startswith("조회수") else "score"

    keywords_input = st.text_input(
        "검색 키워드 (쉼표로 여러 개 구분)",
        placeholder="신박한 아이템, 살림 꿀템, 혼자 사는 살림템",
    )
    submitted = st.button("🔍 아이템 발굴 시작", type="primary", key="run_custom")

    if submitted:
        if not api_key:
            st.error("먼저 왼쪽 사이드바에 유튜브 API 키를 입력해주세요.")
        elif not keywords_input.strip():
            st.error("키워드를 최소 1개 입력해주세요.")
        else:
            keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
            with st.spinner(f"'{', '.join(keywords)}' 검색 중... (키워드가 많으면 시간이 좀 걸려요)"):
                try:
                    rows = find_items(
                        api_key=api_key,
                        keywords=keywords,
                        max_results=max_results,
                        days=days,
                        shorts_only=shorts_only,
                        shorts_max_seconds=shorts_max_seconds,
                        sort_by=sort_by,
                    )
                except Exception as e:
                    st.error(f"검색 중 오류가 발생했습니다: {e}")
                    rows = []

            if rows:
                st.session_state.custom_result_df = reorder(pd.DataFrame(rows))
                st.session_state.custom_result_keywords = keywords
            else:
                st.session_state.custom_result_df = None
                st.warning("검색 결과가 없어요. 키워드나 기간, 필터 조건을 조정해보세요.")

    if st.session_state.custom_result_df is not None:
        render_results(
            st.session_state.custom_result_df,
            len(st.session_state.custom_result_keywords),
            "조회수" if sort_by == "views" else "영상점수",
        )
    else:
        st.info("키워드를 입력한 뒤 '아이템 발굴 시작' 버튼을 눌러보세요.")
