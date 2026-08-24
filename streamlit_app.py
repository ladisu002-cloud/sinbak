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
  3) 장면 매칭 - 다운로드해둔 벤치마킹 영상을 업로드하면 장면 단위로
     쪼개고 AI가 설명을 붙인 뒤, 대본 문장마다 어울리는 장면을 매칭

로컬 실행:
    streamlit run streamlit_app.py

배포 방법은 DEPLOY.md 참고 (GitHub + Streamlit Community Cloud, 무료).
"""

import html
import os
import tempfile
import zipfile
from datetime import datetime

import pandas as pd
import streamlit as st

from youtube_item_finder import find_items, find_yesterday_top_shopping, DEFAULT_SHOPPING_KEYWORDS
from video_analyzer import (
    get_transcript, analyze_video, extract_video_id, generate_script, SCRIPT_STYLES,
    QuotaExceededError as AnalyzerQuotaError,
    ModelUnavailableError as AnalyzerModelError,
)
from scene_matcher import (
    build_scene_library, describe_scene, match_script_to_scenes,
    export_scene_clip, ffmpeg_available,
    QuotaExceededError as SceneQuotaError,
    ModelUnavailableError as SceneModelError,
)
from key_pool import parse_keys, call_with_fallback

st.set_page_config(page_title="신박살림 아이템 발굴기", page_icon="🔎", layout="wide")

COLUMN_ORDER = [
    "검색키워드", "썸네일", "제목", "채널명", "URL", "조회수", "구독자수",
    "영상점수", "조회수/구독자배수", "참여율(%)", "영상길이(초)", "업로드일",
]

CARD_CSS = """
<style>
.vcard-thumb { position:relative; width:100%; aspect-ratio:9/16; background:#000;
  overflow:hidden; border-radius:10px; }
.vcard-thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.vcard-meta { font-size:12px; color:#8a8a99; margin:4px 0 2px; }
.vcard-title { font-size:13px; font-weight:600; line-height:1.35; margin-bottom:4px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.7em; }
.vcard-channel { font-size:11.5px; color:#8a8a99; margin-bottom:6px; }
</style>
"""


def render_analysis(result):
    """AI 영상 분석 결과(dict)를 보기 좋게 표시."""
    st.markdown(f"**🎯 제품정보**\n\n{result.get('제품정보', '-')}")

    keywords = result.get("추천검색어") or {}
    if isinstance(keywords, dict):
        kr = keywords.get("한국어") or []
        cn = keywords.get("중국어") or []
        if kr or cn:
            st.markdown("**🔑 추천 검색어**")
        if kr:
            st.caption("🇰🇷 한국어")
            st.write(" · ".join(f"`{k}`" for k in kr))
        if cn:
            st.caption("🇨🇳 중국어")
            st.write(" · ".join(f"`{k}`" for k in cn))
    elif isinstance(keywords, list) and keywords:
        st.markdown("**🔑 추천 검색어**")
        st.write(" · ".join(f"`{k}`" for k in keywords))

    hook = result.get("후킹방식") or {}
    if hook:
        st.markdown("**🪝 후킹 방식**")
        st.markdown(f"- 시작 멘트: _{hook.get('시작멘트', '-')}_")
        st.markdown(f"- 방식: {hook.get('방식', '-')}")

    st.markdown(f"**🚀 떡상요인**\n\n{result.get('떡상요인', '-')}")

    flow = result.get("영상흐름") or []
    if flow:
        st.markdown("**🎬 영상 흐름**")
        for seg in flow:
            st.markdown(f"- **{seg.get('구간', '-')}**: {seg.get('설명', '-')}")

    st.markdown(f"**📣 CTA**\n\n{result.get('cta', '-')}")


def run_analysis(video_id, title, channel, gemini_key, product_hint=None):
    """영상 1개를 분석해서 세션 상태에 저장 (버튼 클릭 시 호출)."""
    if "analysis_results" not in st.session_state:
        st.session_state.analysis_results = {}

    if not gemini_key:
        st.session_state.analysis_results[video_id] = {
            "error": "Gemini API 키가 없습니다. 사이드바에서 입력해주세요."
        }
        return

    try:
        segments = get_transcript(video_id)
        result = call_with_fallback(
            parse_keys(gemini_key),
            analyze_video,
            title,
            channel,
            segments,
            product_hint=product_hint,
            quota_errors=(AnalyzerQuotaError,),
        )
        result["_had_transcript"] = segments is not None
        st.session_state.analysis_results[video_id] = result
    except AnalyzerQuotaError:
        st.session_state.analysis_results[video_id] = {
            "error": "Gemini 무료 API 일일 한도를 초과했어요. 잠시 후 다시 시도해주세요."
        }
    except AnalyzerModelError as e:
        st.session_state.analysis_results[video_id] = {
            "error": f"현재 설정된 AI 모델을 더 이상 쓸 수 없어요. 아래 안내를 참고해 "
                     f"`video_analyzer.py`의 DEFAULT_MODEL 값을 바꿔주세요.\n\n{e}"
        }
    except Exception as e:
        st.session_state.analysis_results[video_id] = {"error": f"분석 중 오류: {e}"}


@st.dialog("영상 분석", width="large")
def show_video_dialog(row, api_key, gemini_key):
    """원본 쇼츠 + AI 분석 + 관련 영상 찾기를 한 화면(모달)에서 보여준다."""
    video_id = extract_video_id(row["URL"])

    col_video, col_info = st.columns([1, 1.2])

    with col_video:
        st.video(row["URL"])
        st.caption(f"**{row.get('제목', '')}**")
        st.caption(f"{row.get('채널명', '')} · 조회수 {row.get('조회수', '-')}")

    with col_info:
        if video_id not in st.session_state.analysis_results:
            if not gemini_key:
                st.warning("Gemini API 키가 없어 분석할 수 없어요. 사이드바에서 입력해주세요.")
            else:
                with st.spinner("분석 중..."):
                    run_analysis(video_id, row.get("제목", ""), row.get("채널명", ""), gemini_key)

        result = st.session_state.analysis_results.get(video_id)

        if result:
            with st.container(height=380, border=False):
                if "error" in result:
                    st.error(result["error"])
                else:
                    if not result.get("_had_transcript", True):
                        st.caption(
                            "⚠️ 자막을 가져오지 못해 제목 기반으로 추정한 분석이에요. "
                            "아래에 실제 제품 정보를 입력하고 다시 분석해보세요."
                        )
                    render_analysis(result)

            hint_key = f"hint_{video_id}"
            hint_col1, hint_col2 = st.columns([3, 1])
            hint_value = hint_col1.text_input(
                "제품 힌트",
                key=hint_key,
                label_visibility="collapsed",
                placeholder="예: 매립형 식기세척기, 코멧 브랜드 (영상 보고 실제 제품을 알려주면 더 정확해져요)",
            )
            if hint_col2.button("🔄 다시 분석", key=f"reanalyze_{video_id}", width="stretch"):
                with st.spinner("힌트를 반영해서 다시 분석 중..."):
                    run_analysis(
                        video_id, row.get("제목", ""), row.get("채널명", ""),
                        gemini_key, product_hint=hint_value,
                    )
                st.rerun()

            if "error" not in result:
                st.divider()
                related_key = f"related_result_{video_id}"

                if st.button("🔍 관련 영상 더 찾기 (유튜브)", key=f"related_btn_{video_id}", width="stretch"):
                    kr_keywords = (result.get("추천검색어") or {}).get("한국어") or []
                    if not kr_keywords:
                        st.session_state[related_key] = []
                        st.warning("추천 검색어가 없어 관련 영상을 찾을 수 없어요.")
                    elif not api_key:
                        st.warning("유튜브 API 키가 없어 관련 영상을 찾을 수 없어요.")
                    else:
                        with st.spinner("관련 영상 찾는 중..."):
                            try:
                                related_rows = find_items(
                                    api_key=api_key,
                                    keywords=kr_keywords[:3],
                                    max_results=15,
                                    days=180,
                                    shorts_only=True,
                                    shorts_max_seconds=180,
                                    sort_by="views",
                                )
                                st.session_state[related_key] = related_rows[:8]
                            except Exception as e:
                                st.session_state[related_key] = []
                                st.error(f"검색 중 오류: {e}")

                if related_key in st.session_state:
                    related_rows = st.session_state[related_key]
                    if related_rows:
                        st.markdown("**관련 영상 (유튜브)**")
                        for r in related_rows:
                            try:
                                views_fmt = f"{int(r['조회수']):,}"
                            except (TypeError, ValueError):
                                views_fmt = str(r.get("조회수", "-"))
                            st.markdown(f"- [{r['제목']}]({r['URL']}) · {r['채널명']} · {views_fmt}회")
                    else:
                        st.caption("관련 영상을 찾지 못했어요.")

                st.caption(
                    "ℹ️ 인스타그램은 공식적으로 열려있는 검색 API가 없어, "
                    "자동으로 관련 영상을 찾아주는 기능은 지원하지 않아요."
                )


def render_card_grid(df, api_key, gemini_key, cols_per_row=5):
    """썸네일 카드 그리드 뷰 + 카드별 'AI 분석' 버튼(누르면 모달 오픈)."""
    st.markdown(CARD_CSS, unsafe_allow_html=True)

    if "analysis_results" not in st.session_state:
        st.session_state.analysis_results = {}

    records = df.to_dict("records")
    for start in range(0, len(records), cols_per_row):
        chunk = records[start:start + cols_per_row]
        cols = st.columns(cols_per_row)
        for rank_offset, (col, row) in enumerate(zip(cols, chunk)):
            rank = start + rank_offset + 1
            with col:
                thumb = html.escape(str(row.get("썸네일") or ""), quote=True)
                title = str(row.get("제목", ""))
                channel = str(row.get("채널명", ""))
                url = str(row.get("URL", ""))

                try:
                    views_label = f"{int(row.get('조회수', 0)):,}회"
                except (TypeError, ValueError):
                    views_label = str(row.get("조회수", "-"))

                try:
                    dur = int(row.get("영상길이(초)", 0))
                    dur_label = f"{dur // 60}:{dur % 60:02d}"
                except (TypeError, ValueError):
                    dur_label = "-"

                subs = row.get("구독자수", "-")
                try:
                    subs_label = f"구독자 {int(subs):,}"
                except (TypeError, ValueError):
                    subs_label = "구독자 비공개"

                st.markdown(
                    f'<div class="vcard-thumb"><img src="{thumb}" loading="lazy" alt="thumbnail"/></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div class="vcard-meta">#{rank} · {views_label} · {dur_label}</div>'
                    f'<div class="vcard-title">{html.escape(title)}</div>'
                    f'<div class="vcard-channel">{html.escape(channel)} · {subs_label}</div>',
                    unsafe_allow_html=True,
                )

                btn_col1, btn_col2 = st.columns(2)
                btn_col1.link_button("▶ 보기", url, width="stretch")
                if btn_col2.button("🔍 분석", key=f"analyze_{extract_video_id(url)}_{rank}", width="stretch"):
                    show_video_dialog(row, api_key, gemini_key)

                st.divider()


def get_api_key():
    """유튜브 API 키를 우선순위대로 찾는다: Streamlit Secrets -> 환경변수 -> 사이드바 직접 입력."""
    try:
        if "YOUTUBE_API_KEY" in st.secrets:
            return st.secrets["YOUTUBE_API_KEY"], "secrets"
    except Exception:
        pass

    env_key = os.environ.get("YOUTUBE_API_KEY")
    if env_key:
        return env_key, "env"

    return None, "manual"


def get_gemini_key():
    """Gemini API 키를 우선순위대로 찾는다 (AI 영상 분석 기능용, 선택사항)."""
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"], "secrets"
    except Exception:
        pass

    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key, "env"

    return None, "manual"


def reorder(df):
    return df[[c for c in COLUMN_ORDER if c in df.columns]]


def render_results(df, keyword_count, sort_label, view_mode, api_key, gemini_key):
    """결과 표시 (지표 + 카드/표 + 다운로드) - 두 탭에서 공용으로 사용."""
    st.success(f"총 {len(df)}건을 찾았어요! ({sort_label} 기준 정렬됨)")

    top_score = df[pd.to_numeric(df["영상점수"], errors="coerce").notna()]
    hot_count = (pd.to_numeric(top_score["영상점수"], errors="coerce") >= 5).sum() if len(top_score) else 0
    col1, col2, col3 = st.columns(3)
    col1.metric("수집된 영상", f"{len(df)}건")
    col2.metric("영상점수 5점 이상", f"{hot_count}건")
    col3.metric("검색 키워드", f"{keyword_count}개")

    if view_mode == "카드형":
        render_card_grid(df, api_key, gemini_key)
    else:
        table_height = min(38 + 35 * len(df), 800)
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            height=table_height,
            column_config={
                "URL": st.column_config.LinkColumn("링크", display_text="영상 보기"),
                "썸네일": st.column_config.ImageColumn("썸네일"),
                "영상점수": st.column_config.NumberColumn("영상점수", format="%.2f"),
            },
        )

    csv = df.drop(columns=["썸네일"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
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
gemini_key, gemini_key_source = get_gemini_key()

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
        st.success("유튜브 API 키가 설정되어 있어요 ✅")

    if gemini_key_source == "manual":
        gemini_key_input = st.text_input(
            "Gemini API 키 (선택 - AI 영상 분석용)",
            type="password",
            help="aistudio.google.com에서 무료로 발급받을 수 있어요. "
                 "이 키가 없어도 검색·수집 기능은 그대로 쓸 수 있고, '🔍 AI 분석' 버튼만 못 씁니다. "
                 "여러 개 쓰려면 쉼표(,)로 구분해서 입력하세요 - 첫 번째 키가 무료 한도를 "
                 "초과하면 자동으로 다음 키로 넘어갑니다. 단, 무료 한도는 '키'가 아니라 "
                 "'프로젝트' 단위로 체크되니, 서로 다른 프로젝트에서 발급받은 키를 넣어야 "
                 "실제로 효과가 있어요 (같은 프로젝트의 키 여러 개는 소용없음).",
        )
        if gemini_key_input:
            gemini_key = gemini_key_input
    else:
        st.success("Gemini API 키가 설정되어 있어요 ✅")

    st.divider()
    view_mode = st.radio("보기 방식", ["카드형", "표형"], horizontal=True)

for key in ["preset_result_df", "preset_result_keywords", "custom_result_df", "custom_result_keywords"]:
    if key not in st.session_state:
        st.session_state[key] = None

tab1, tab2, tab3 = st.tabs(["🔥 어제 인기 쇼핑쇼츠 100개", "🔍 커스텀 검색", "🎬 장면 매칭"])

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
            view_mode,
            api_key,
            gemini_key,
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
            view_mode,
            api_key,
            gemini_key,
        )
    else:
        st.info("키워드를 입력한 뒤 '아이템 발굴 시작' 버튼을 눌러보세요.")

# ---------------------------------------------------------------------------
# 탭 3: 장면 매칭 (벤치마킹 영상 업로드 -> 장면 분할 -> AI 설명 -> 대본 매칭)
# ---------------------------------------------------------------------------
MAX_UPLOAD_VIDEOS = 4

for key, default in [
    ("scene_library", []),
    ("scene_processed_names", set()),
    ("scene_temp_dir", None),
    ("script_matching", {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

with tab3:
    st.write(
        "다운로드해둔 벤치마킹 영상을 올리면, 장면 단위로 쪼개서 AI가 각 장면을 설명하고 "
        f"대본 문장마다 어울리는 장면을 찾아드려요. **한 번에 최대 {MAX_UPLOAD_VIDEOS}개** 정도로 "
        "올리는 걸 권장해요 (그 이상은 느려지거나 무료 API 한도에 걸릴 수 있어요)."
    )

    if not gemini_key:
        st.warning("이 탭은 Gemini API 키가 꼭 필요해요. 사이드바에서 먼저 입력해주세요.")

    uploaded_videos = st.file_uploader(
        "벤치마킹 영상 업로드",
        type=["mp4", "mov", "m4v", "webm"],
        accept_multiple_files=True,
        key="scene_uploader",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        max_scenes_per_video = st.slider("영상당 최대 장면 수", min_value=5, max_value=20, value=8)
    with col_b:
        scene_sensitivity = st.slider(
            "장면 전환 민감도 (낮을수록 더 잘게 쪼갬)", min_value=10, max_value=50, value=27
        )

    if uploaded_videos:
        est_calls = len(uploaded_videos[:MAX_UPLOAD_VIDEOS]) * max_scenes_per_video
        st.caption(
            f"ℹ️ 예상 AI 호출 수: 최대 약 {est_calls}번 (영상 {len(uploaded_videos[:MAX_UPLOAD_VIDEOS])}개 "
            f"× 영상당 최대 {max_scenes_per_video}장면). Gemini 무료 한도는 모델/시점에 따라 하루 "
            "20~수백 회로 차이가 커서, 한도에 걸리면 장면 수를 줄이거나 나중에 다시 시도해주세요."
        )

    if uploaded_videos and len(uploaded_videos) > MAX_UPLOAD_VIDEOS:
        st.warning(f"{MAX_UPLOAD_VIDEOS}개까지만 처리합니다. 나머지는 무시돼요.")
        uploaded_videos = uploaded_videos[:MAX_UPLOAD_VIDEOS]

    analyze_scenes_clicked = st.button(
        "🎬 장면 분석 시작", type="primary", disabled=not uploaded_videos or not gemini_key
    )

    if analyze_scenes_clicked:
        if st.session_state.scene_temp_dir is None:
            st.session_state.scene_temp_dir = tempfile.mkdtemp(prefix="scene_matcher_")

        new_videos = [
            v for v in uploaded_videos if v.name not in st.session_state.scene_processed_names
        ]

        if not new_videos:
            st.info("이미 처리한 영상들이에요. 새 영상을 올려주세요.")
        else:
            progress = st.progress(0.0, text="시작 중...")
            total_steps = len(new_videos) * 2  # 장면분할 1 + 설명생성 1 (개략적 진행률용)
            done_steps = 0

            for video_file in new_videos:
                video_path = os.path.join(st.session_state.scene_temp_dir, video_file.name)
                with open(video_path, "wb") as f:
                    f.write(video_file.getbuffer())

                progress.progress(done_steps / total_steps, text=f"{video_file.name} 장면 분할 중...")
                try:
                    scenes = build_scene_library(
                        video_path, video_file.name,
                        threshold=float(scene_sensitivity),
                    )
                    scenes = scenes[:max_scenes_per_video]
                except Exception as e:
                    st.error(f"{video_file.name} 장면 분할 실패: {e}")
                    continue
                done_steps += 1

                quota_hit = False
                for i, scene in enumerate(scenes):
                    if quota_hit:
                        scene["description"] = None
                        continue

                    progress.progress(
                        done_steps / total_steps,
                        text=f"{video_file.name} - 장면 설명 생성 중 ({i+1}/{len(scenes)})",
                    )
                    if scene["thumbnail"]:
                        try:
                            scene["description"] = call_with_fallback(
                                parse_keys(gemini_key), describe_scene, scene["thumbnail"],
                                quota_errors=(SceneQuotaError,),
                            )
                        except SceneQuotaError:
                            scene["description"] = None
                            quota_hit = True
                            st.warning(
                                "⚠️ Gemini 무료 API 일일 한도를 초과했어요. 남은 장면들은 설명 없이 "
                                "저장됩니다. 잠시 후 다시 시도하거나, `video_analyzer.py` / "
                                "`scene_matcher.py`의 DEFAULT_MODEL을 다른 모델로 바꿔보세요."
                            )
                        except SceneModelError as e:
                            scene["description"] = None
                            quota_hit = True  # 이름은 quota지만 '더 이상 진행 못 함' 신호로 재사용
                            st.error(
                                f"⚠️ 현재 설정된 AI 모델을 더 이상 쓸 수 없어요. "
                                f"`scene_matcher.py`의 DEFAULT_MODEL 값을 아래 안내된 모델로 바꿔주세요.\n\n{e}"
                            )
                        except Exception as e:
                            scene["description"] = None
                            st.caption(f"⚠️ 장면 설명 실패({scene['scene_id']}): {e}")
                done_steps += 1

                st.session_state.scene_library.extend(scenes)
                st.session_state.scene_processed_names.add(video_file.name)

            progress.progress(1.0, text="완료!")

    if st.session_state.scene_library:
        st.markdown(f"**장면 라이브러리 ({len(st.session_state.scene_library)}개)**")
        lib_cols = st.columns(6)
        for i, scene in enumerate(st.session_state.scene_library):
            with lib_cols[i % 6]:
                if scene["thumbnail"]:
                    st.image(scene["thumbnail"], width="stretch")
                st.caption(
                    f"{scene['video_name']} · {scene['start_sec']:.1f}~{scene['end_sec']:.1f}초\n\n"
                    f"{scene['description'] or '(설명 없음)'}"
                )

        st.divider()

        if "script_text_area" not in st.session_state:
            st.session_state.script_text_area = ""

        with st.expander("✍️ 대본 자동 생성 (선택 - 대본이 이미 있으면 건너뛰어도 돼요)", expanded=not st.session_state.script_text_area):
            product_info = st.text_area(
                "소개할 제품 설명",
                placeholder="예: 방문에 붙이는 실리콘 문 끼임 방지 패드, 강아지/고양이 발 보호, 부착식",
                height=80,
            )
            style_choice = st.selectbox("대본 스타일", list(SCRIPT_STYLES.keys()))
            st.caption(SCRIPT_STYLES[style_choice])
            target_len = st.slider("목표 길이(초)", min_value=15, max_value=60, value=30, step=5)

            if st.button("✨ 대본 생성하기", disabled=not gemini_key):
                if not product_info.strip():
                    st.warning("제품 설명을 먼저 입력해주세요.")
                else:
                    with st.spinner("대본 작성 중..."):
                        try:
                            st.session_state.script_text_area = call_with_fallback(
                                parse_keys(gemini_key), generate_script,
                                product_info, style_choice, target_len,
                                quota_errors=(AnalyzerQuotaError,),
                            )
                        except AnalyzerQuotaError:
                            st.warning("⚠️ Gemini 무료 API 일일 한도를 초과했어요. 잠시 후 다시 시도해주세요.")
                        except AnalyzerModelError as e:
                            st.error(
                                f"⚠️ 현재 설정된 AI 모델을 더 이상 쓸 수 없어요. "
                                f"`video_analyzer.py`의 DEFAULT_MODEL 값을 아래 안내된 모델로 바꿔주세요.\n\n{e}"
                            )
                        except Exception as e:
                            st.error(f"대본 생성 중 오류: {e}")

        st.markdown("**대본** (한 줄에 한 문장씩 - 위에서 자동 생성했다면 이미 채워져 있어요)")
        script_text = st.text_area(
            "대본",
            placeholder="악마들을 막아준 미국 천재의 발명품\n이 조그만게 컴퓨터야\n레트로 컴퓨터 디자인에 픽셀 아트를 넣었거든",
            height=140,
            label_visibility="collapsed",
            key="script_text_area",
        )

        if st.button("🎯 대본에 맞는 장면 매칭하기", type="primary", disabled=not gemini_key):
            script_lines = [line.strip() for line in script_text.split("\n") if line.strip()]
            if not script_lines:
                st.warning("대본을 먼저 입력해주세요.")
            else:
                with st.spinner("장면 매칭 중..."):
                    try:
                        matches = call_with_fallback(
                            parse_keys(gemini_key), match_script_to_scenes,
                            script_lines, st.session_state.scene_library,
                            quota_errors=(SceneQuotaError,),
                        )
                        st.session_state.script_matching = {"lines": script_lines, "matches": matches}
                    except SceneQuotaError:
                        st.warning("⚠️ Gemini 무료 API 일일 한도를 초과했어요. 잠시 후 다시 시도해주세요.")
                    except SceneModelError as e:
                        st.error(
                            f"⚠️ 현재 설정된 AI 모델을 더 이상 쓸 수 없어요. "
                            f"`scene_matcher.py`의 DEFAULT_MODEL 값을 아래 안내된 모델로 바꿔주세요.\n\n{e}"
                        )
                    except Exception as e:
                        st.error(f"매칭 중 오류: {e}")

        if st.session_state.script_matching:
            lines = st.session_state.script_matching["lines"]
            matches = st.session_state.script_matching["matches"]
            scene_by_id = {s["scene_id"]: s for s in st.session_state.scene_library}
            scene_ids_with_desc = [s["scene_id"] for s in st.session_state.scene_library if s["description"]]

            st.markdown("**매칭 결과** (마음에 안 들면 드롭다운에서 다른 장면으로 바꿀 수 있어요)")

            for idx, line in enumerate(lines):
                m_col1, m_col2 = st.columns([1, 3])
                match = matches.get(idx)
                current_scene_id = match["scene_id"] if match else None

                with m_col1:
                    if current_scene_id and scene_by_id.get(current_scene_id, {}).get("thumbnail"):
                        st.image(scene_by_id[current_scene_id]["thumbnail"], width="stretch")
                    else:
                        st.caption("매칭된 장면 없음")

                with m_col2:
                    st.markdown(f"**{idx+1}. {line}**")
                    options = ["(매칭 안 함)"] + scene_ids_with_desc
                    default_idx = options.index(current_scene_id) if current_scene_id in options else 0
                    chosen = st.selectbox(
                        "장면 선택", options, index=default_idx,
                        key=f"scene_choice_{idx}", label_visibility="collapsed",
                    )
                    if chosen != "(매칭 안 함)":
                        st.session_state.script_matching["matches"][idx] = {"scene_id": chosen, "이유": match.get("이유", "") if match else ""}
                        chosen_scene = scene_by_id[chosen]
                        st.caption(f"{chosen_scene['description']} · {chosen_scene['start_sec']:.1f}~{chosen_scene['end_sec']:.1f}초")
                st.divider()

            if st.button("📦 선택한 장면들 클립으로 내보내기 (zip)"):
                if not ffmpeg_available():
                    st.error(
                        "ffmpeg를 찾을 수 없어 클립을 만들 수 없어요. "
                        "Streamlit Cloud에 배포했다면 저장소에 packages.txt 파일을 만들고 "
                        "그 안에 'ffmpeg' 한 줄을 추가한 뒤 다시 배포해주세요."
                    )
                else:
                    export_dir = tempfile.mkdtemp(prefix="scene_export_")
                    zip_path = os.path.join(export_dir, "matched_clips.zip")
                    with st.spinner("클립 추출 중..."):
                        with zipfile.ZipFile(zip_path, "w") as zf:
                            for idx, line in enumerate(lines):
                                match = st.session_state.script_matching["matches"].get(idx)
                                if not match:
                                    continue
                                scene = scene_by_id.get(match["scene_id"])
                                if not scene:
                                    continue
                                clip_path = os.path.join(export_dir, f"{idx+1:02d}_{scene['scene_id'].replace('#', '_')}.mp4")
                                try:
                                    export_scene_clip(scene["video_path"], scene["start_sec"], scene["end_sec"], clip_path)
                                    zf.write(clip_path, os.path.basename(clip_path))
                                except Exception as e:
                                    st.error(f"{idx+1}번 문장 클립 추출 실패: {e}")

                    with open(zip_path, "rb") as f:
                        st.download_button(
                            "📥 클립 zip 다운로드", f, file_name="matched_clips.zip", mime="application/zip"
                        )
    else:
        st.info("영상을 올리고 '장면 분석 시작'을 눌러보세요.")
