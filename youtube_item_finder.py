#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
신박살림 아이템 발굴 도구 (Golden Finder / TubeLens 스타일 미니 버전)

키워드로 유튜브 영상을 검색해서, 채널 규모 대비 조회수가 얼마나
잘 터졌는지("영상 점수")를 계산하고, 점수 높은 순으로 정리해서
CSV + HTML 리포트로 저장합니다.

필요한 것:
  1. 유튜브 데이터 API 키 (무료) - README.md 참고
  2. pip install -r requirements.txt

사용 예:
  python youtube_item_finder.py --keywords "신박한 아이템" "살림 꿀템" "혼자 사는 살림템"
  python youtube_item_finder.py --keywords "캠핑 신박템" --days 14 --shorts-only
"""

import argparse
import csv
import html
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("google-api-python-client가 설치되어 있지 않습니다.")
    print("먼저 실행하세요: pip install -r requirements.txt")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 1. 영상 점수 계산 (API 호출과 분리된 순수 함수 - 테스트하기 쉽게)
# ---------------------------------------------------------------------------

def parse_iso8601_duration(duration_str):
    """'PT1M30S' 같은 ISO8601 길이를 초 단위 정수로 변환."""
    match = re.match(
        r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        duration_str,
    )
    if not match:
        return 0
    parts = match.groupdict()
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    seconds = int(parts["seconds"] or 0)
    return hours * 3600 + minutes * 60 + seconds


def calculate_video_score(view_count, subscriber_count, like_count, comment_count):
    """
    '영상 점수' = 채널 규모 대비 조회수(70%) + 참여율(30%)

    - view_to_sub_ratio: 조회수가 구독자 수의 몇 배인지 (클수록 '터진' 영상)
    - engagement_rate: (좋아요+댓글) / 조회수 (클수록 시청자 반응이 좋았던 영상)

    subscriber_count가 0이거나 비공개(None)인 채널은 view_to_sub_ratio를
    계산할 수 없으므로 None을 반환한다 (호출부에서 별도 처리).

    가중치(0.7 / 0.3)와 참여율 배수(x100)는 튜닝 가능한 값이라
    필요에 맞게 조정해서 쓰면 된다.
    """
    if view_count is None or view_count <= 0:
        return None, None, None

    engagement_rate = (like_count + comment_count) / view_count

    if subscriber_count is None or subscriber_count <= 0:
        view_to_sub_ratio = None
        score = None
    else:
        view_to_sub_ratio = view_count / subscriber_count
        score = round(view_to_sub_ratio * 0.7 + engagement_rate * 100 * 0.3, 2)

    return score, view_to_sub_ratio, round(engagement_rate * 100, 2)


# ---------------------------------------------------------------------------
# 2. 유튜브 API 호출
# ---------------------------------------------------------------------------

def search_video_ids(youtube, keyword, max_results, published_after):
    """키워드로 영상을 검색해서 video_id 리스트를 반환 (최대 50개/호출)."""
    video_ids = []
    next_page_token = None

    while len(video_ids) < max_results:
        request = youtube.search().list(
            part="id",
            q=keyword,
            type="video",
            order="viewCount",
            maxResults=min(50, max_results - len(video_ids)),
            publishedAfter=published_after,
            regionCode="KR",
            relevanceLanguage="ko",
            pageToken=next_page_token,
        )
        response = request.execute()
        video_ids.extend(item["id"]["videoId"] for item in response.get("items", []))
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    return video_ids[:max_results]


def get_video_details(youtube, video_ids):
    """video_id 리스트로 통계·메타데이터를 가져온다 (최대 50개씩 배치)."""
    details = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
        ).execute()
        details.extend(response.get("items", []))
    return details


def get_channel_subscribers(youtube, channel_ids):
    """channel_id -> 구독자 수 매핑 (구독자 비공개 채널은 None)."""
    result = {}
    unique_ids = list(dict.fromkeys(channel_ids))
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        response = youtube.channels().list(
            part="statistics",
            id=",".join(batch),
        ).execute()
        for item in response.get("items", []):
            stats = item["statistics"]
            if stats.get("hiddenSubscriberCount"):
                result[item["id"]] = None
            else:
                result[item["id"]] = int(stats.get("subscriberCount", 0))
    return result


# ---------------------------------------------------------------------------
# 3. 메인 파이프라인
# ---------------------------------------------------------------------------

def find_items(api_key, keywords, max_results, days, shorts_only, shorts_max_seconds,
                sort_by="score", top_n=None):
    """
    sort_by: "score" (영상 점수 기준, 기본값) 또는 "views" (조회수 기준)
    top_n: 정렬 후 상위 N개만 남기고 자르기 (None이면 전체 반환)
    """
    youtube = build("youtube", "v3", developerKey=api_key)

    published_after = None
    if days:
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_rows = []

    for keyword in keywords:
        print(f"[검색 중] '{keyword}' ...")
        try:
            video_ids = search_video_ids(youtube, keyword, max_results, published_after)
            if not video_ids:
                print(f"  -> 결과 없음")
                continue

            details = get_video_details(youtube, video_ids)
            channel_ids = [v["snippet"]["channelId"] for v in details]
            sub_map = get_channel_subscribers(youtube, channel_ids)

            for v in details:
                duration_sec = parse_iso8601_duration(v["contentDetails"]["duration"])
                if shorts_only and duration_sec > shorts_max_seconds:
                    continue

                stats = v.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                like_count = int(stats.get("likeCount", 0))
                comment_count = int(stats.get("commentCount", 0))
                channel_id = v["snippet"]["channelId"]
                subscriber_count = sub_map.get(channel_id)

                score, ratio, engagement = calculate_video_score(
                    view_count, subscriber_count, like_count, comment_count
                )

                thumbnails = v["snippet"].get("thumbnails", {})
                thumbnail_url = (
                    thumbnails.get("medium", {}).get("url")
                    or thumbnails.get("high", {}).get("url")
                    or thumbnails.get("default", {}).get("url")
                    or ""
                )

                all_rows.append({
                    "검색키워드": keyword,
                    "썸네일": thumbnail_url,
                    "제목": v["snippet"]["title"],
                    "채널명": v["snippet"]["channelTitle"],
                    "조회수": view_count,
                    "구독자수": subscriber_count if subscriber_count is not None else "비공개",
                    "영상점수": score if score is not None else "-",
                    "조회수/구독자배수": round(ratio, 1) if ratio is not None else "-",
                    "참여율(%)": engagement if engagement is not None else "-",
                    "영상길이(초)": duration_sec,
                    "업로드일": v["snippet"]["publishedAt"][:10],
                    "URL": f"https://www.youtube.com/watch?v={v['id']}",
                })

            print(f"  -> {len(details)}개 수집")

        except HttpError as e:
            print(f"  [오류] '{keyword}' 검색 중 API 오류 발생: {e}")
            continue

    # 같은 영상이 여러 키워드 검색에 중복으로 잡힐 수 있어 URL 기준으로 중복 제거
    # (중복된 경우 어떤 키워드들에 걸렸는지 한 줄로 모아서 보여줌)
    deduped = {}
    for row in all_rows:
        url = row["URL"]
        if url in deduped:
            existing_keywords = deduped[url]["검색키워드"].split(", ")
            if row["검색키워드"] not in existing_keywords:
                deduped[url]["검색키워드"] = ", ".join(existing_keywords + [row["검색키워드"]])
        else:
            deduped[url] = row
    all_rows = list(deduped.values())

    # 정렬 기준 적용: "score"(영상점수) 또는 "views"(조회수)
    if sort_by == "views":
        all_rows.sort(key=lambda r: r["조회수"], reverse=True)
    else:
        # 영상 점수 기준 내림차순 정렬 (점수 없는 항목은 맨 뒤로)
        all_rows.sort(key=lambda r: r["영상점수"] if isinstance(r["영상점수"], (int, float)) else -1, reverse=True)

    if top_n:
        all_rows = all_rows[:top_n]

    return all_rows


# ---------------------------------------------------------------------------
# 3-1. 프리셋: "어제 인기 쇼핑쇼츠" 모드
# ---------------------------------------------------------------------------

DEFAULT_SHOPPING_KEYWORDS = [
    "쇼핑 꿀템", "쿠팡 추천템", "다이소 꿀템", "가성비템 추천",
    "신박한 아이템", "살림 꿀템", "1인 가구 꿀템", "리빙 꿀템",
]


def find_yesterday_top_shopping(api_key, keywords=None, top_n=100,
                                 per_keyword_results=50, days_buffer=2,
                                 shorts_max_seconds=180):
    """
    '어제 조회수가 높았던 쇼핑쇼츠 top_n개'를 찾는 전용 함수.
    - keywords가 없으면 DEFAULT_SHOPPING_KEYWORDS를 사용
    - days_buffer: 유튜브 API 인덱싱 지연을 감안해 실제로는 최근 N일을 검색
      (기본 2일 = '어제'를 안전하게 포함하기 위한 여유값)
    - 쇼츠 전용, 조회수 기준 정렬 후 상위 top_n개만 반환
    """
    if not keywords:
        keywords = DEFAULT_SHOPPING_KEYWORDS

    return find_items(
        api_key=api_key,
        keywords=keywords,
        max_results=per_keyword_results,
        days=days_buffer,
        shorts_only=True,
        shorts_max_seconds=shorts_max_seconds,
        sort_by="views",
        top_n=top_n,
    )


# ---------------------------------------------------------------------------
# 4. 결과 저장 (CSV + HTML 리포트)
# ---------------------------------------------------------------------------

def save_csv(rows, path):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_html(rows, path, keywords):
    def esc(v):
        return html.escape(str(v))

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    table_rows = ""
    for r in rows:
        score_display = r["영상점수"]
        highlight = ""
        if isinstance(score_display, (int, float)) and score_display >= 5:
            highlight = ' style="background:#fff2e6;"'
        table_rows += f"""
        <tr{highlight}>
          <td>{esc(r['검색키워드'])}</td>
          <td><a href="{esc(r['URL'])}" target="_blank">{esc(r['제목'])}</a></td>
          <td>{esc(r['채널명'])}</td>
          <td>{esc(r['조회수'])}</td>
          <td>{esc(r['구독자수'])}</td>
          <td><b>{esc(r['영상점수'])}</b></td>
          <td>{esc(r['조회수/구독자배수'])}</td>
          <td>{esc(r['참여율(%)'])}</td>
          <td>{esc(r['영상길이(초)'])}</td>
          <td>{esc(r['업로드일'])}</td>
        </tr>"""

    html_doc = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>신박살림 아이템 발굴 리포트 - {today}</title>
<style>
  body {{ font-family: -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
         background:#faf7f2; margin:0; padding:24px; color:#2b2420; }}
  h1 {{ font-size:20px; margin-bottom:4px; }}
  .meta {{ color:#8a7f74; font-size:13px; margin-bottom:20px; }}
  table {{ border-collapse:collapse; width:100%; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,0.08); }}
  th, td {{ padding:10px 12px; border-bottom:1px solid #eee0d4; font-size:13px; text-align:left; white-space:nowrap; }}
  th {{ background:#c85f3d; color:#fff; position:sticky; top:0; }}
  tr:hover {{ background:#fdf3ea; }}
  a {{ color:#c85f3d; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .badge {{ display:inline-block; background:#c85f3d; color:#fff; border-radius:6px; padding:2px 8px; font-size:12px; margin-right:6px; }}
</style>
</head>
<body>
  <h1>🔎 신박살림 아이템 발굴 리포트</h1>
  <div class="meta">
    생성 시각: {today} &nbsp;|&nbsp; 검색 키워드:
    {' '.join(f'<span class="badge">{esc(k)}</span>' for k in keywords)}
    &nbsp;|&nbsp; 총 {len(rows)}건 (영상점수 5점 이상은 주황색 배경)
  </div>
  <table>
    <thead>
      <tr>
        <th>키워드</th><th>제목</th><th>채널명</th><th>조회수</th><th>구독자수</th>
        <th>영상점수</th><th>조회수/구독자배수</th><th>참여율(%)</th><th>길이(초)</th><th>업로드일</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html_doc)


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="유튜브 아이템 발굴 도구")
    parser.add_argument("--keywords", nargs="+", default=None, help="검색 키워드 (여러 개 가능)")
    parser.add_argument("--preset", choices=["shopping-yesterday"], default=None,
                         help="shopping-yesterday: 어제 조회수 높은 쇼핑쇼츠 100개 (키워드 프리셋 사용, --top-n 기본 100)")
    parser.add_argument("--max-results", type=int, default=30, help="키워드당 최대 수집 개수 (기본 30)")
    parser.add_argument("--days", type=int, default=30, help="최근 N일 이내 영상만 (기본 30일)")
    parser.add_argument("--shorts-only", action="store_true", help="쇼츠(짧은 영상)만 필터링")
    parser.add_argument("--shorts-max-seconds", type=int, default=180, help="쇼츠 판단 기준 길이(초), 기본 180초")
    parser.add_argument("--sort-by", choices=["score", "views"], default="score", help="정렬 기준 (기본 score)")
    parser.add_argument("--top-n", type=int, default=None, help="정렬 후 상위 N개만 남기기")
    parser.add_argument("--api-key", default=None, help="유튜브 API 키 (없으면 환경변수 YOUTUBE_API_KEY 사용)")
    parser.add_argument("--out-dir", default="reports", help="결과 저장 폴더")
    args = parser.parse_args()

    if not args.preset and not args.keywords:
        print("--keywords 를 입력하거나 --preset shopping-yesterday 를 사용하세요.")
        sys.exit(1)

    api_key = args.api_key or os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("API 키가 없습니다. --api-key 옵션을 쓰거나 환경변수 YOUTUBE_API_KEY를 설정하세요.")
        print("API 키 발급 방법은 README.md를 참고하세요.")
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)

    if args.preset == "shopping-yesterday":
        rows = find_yesterday_top_shopping(
            api_key=api_key,
            keywords=args.keywords,  # None이면 함수 내부 기본 키워드 사용
            top_n=args.top_n or 100,
            per_keyword_results=args.max_results if args.max_results != 30 else 50,
            days_buffer=args.days if args.days != 30 else 2,
            shorts_max_seconds=args.shorts_max_seconds,
        )
        display_keywords = args.keywords or DEFAULT_SHOPPING_KEYWORDS
    else:
        rows = find_items(
            api_key=api_key,
            keywords=args.keywords,
            max_results=args.max_results,
            days=args.days,
            shorts_only=args.shorts_only,
            shorts_max_seconds=args.shorts_max_seconds,
            sort_by=args.sort_by,
            top_n=args.top_n,
        )
        display_keywords = args.keywords

    if not rows:
        print("수집된 영상이 없습니다.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = os.path.join(args.out_dir, f"report_{timestamp}.csv")
    html_path = os.path.join(args.out_dir, f"report_{timestamp}.html")

    save_csv(rows, csv_path)
    save_html(rows, html_path, display_keywords)

    print(f"\n완료! 총 {len(rows)}건 수집")
    print(f"CSV: {csv_path}")
    print(f"HTML 리포트: {html_path}  <- 이 파일을 더블클릭해서 브라우저로 열어보세요")


if __name__ == "__main__":
    main()
