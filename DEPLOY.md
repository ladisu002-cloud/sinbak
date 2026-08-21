# 웹에 올려서 아무 때나 접속하기 (GitHub + Streamlit Community Cloud)

완전 무료입니다. 아래 순서대로만 따라 하면 됩니다. 전체 30분 안쪽으로
끝납니다.

## 1단계. GitHub 계정 만들기

이미 있으면 건너뛰세요. 없으면 https://github.com 에서 이메일로 무료
가입합니다.

## 2단계. 저장소(Repository) 만들기

1. GitHub 로그인 후 우측 상단 `+` → `New repository`
2. 저장소 이름 입력 (예: `sinbak-salrim-finder`)
3. **Public**과 **Private** 중 선택
   - Private으로 해도 됩니다 (무료 요금제에서 비공개 앱은 1개까지 배포 가능).
   - API 키는 코드가 아니라 별도 Secrets 공간에 저장할 거라, Public으로
     해도 키가 새어나갈 위험은 없습니다. 다만 코드/전략 자체를 남에게
     보이고 싶지 않으면 Private을 추천합니다.
4. `Create repository` 클릭

## 3단계. 파일 업로드

방금 만든 저장소 페이지에서 `Add file` → `Upload files` 클릭 후, 아래
파일을 전부 끌어다 놓고 `Commit changes`:

- `streamlit_app.py`
- `youtube_item_finder.py`
- `video_analyzer.py`
- `scene_matcher.py`
- `requirements.txt`
- `packages.txt` ⚠️ **장면 매칭(영상 자르기) 기능을 쓰려면 필수** — 이
  파일이 있어야 Streamlit Cloud 서버에 ffmpeg가 자동으로 설치됩니다.
  없으면 클립 추출이 안 됩니다.

(README.md도 같이 올려두면 나중에 참고하기 좋습니다.)

## 4단계. Streamlit Community Cloud 가입 & 배포

1. https://share.streamlit.io 접속 → **GitHub 계정으로 로그인** → 권한
   승인
2. `New app` (또는 `Create app`) 클릭
3. 방금 만든 저장소 선택, Branch는 `main`, **Main file path에
   `streamlit_app.py` 입력**
4. 화면 아래 `Advanced settings` 펼치기 → **Secrets** 입력란에 아래처럼
   입력 (따옴표 꼭 포함):

   ```
   YOUTUBE_API_KEY = "여기에_실제_API_키_붙여넣기"
   GEMINI_API_KEY = "AI 영상 분석 쓸 거면 여기도 붙여넣기 (선택)"
   ```

   Gemini 키가 아직 없으면 `GEMINI_API_KEY` 줄은 생략해도 됩니다.
   나중에 필요할 때 앱 설정의 Secrets에서 추가하면 됩니다.

5. `Deploy` 클릭 → 1~3분 정도 빌드되면 `https://무언가.streamlit.app`
   형태의 내 전용 주소가 생깁니다. 이 링크를 북마크해두면 스마트폰,
   다른 컴퓨터 어디서든 접속해서 바로 쓸 수 있습니다.

## 5단계. 확인

발급된 주소로 접속했을 때 사이드바에 "API 키가 설정되어 있어요 ✅"가
뜨면 정상입니다. 키워드를 입력하고 "아이템 발굴 시작"을 눌러 결과가
표로 나오는지 확인하세요.

## 나중에 코드를 수정하고 싶다면?

GitHub 저장소에서 해당 파일을 다시 열어 수정하고 저장(commit)하면,
Streamlit이 자동으로 감지해서 몇 초~몇 분 안에 앱을 다시 배포합니다.
따로 재배포 버튼을 누를 필요는 없습니다.

## 알아두면 좋은 무료 요금제 제한사항 (2026년 기준)

- 메모리 한도가 약 1GB라, 키워드를 아주 많이 넣거나 `키워드당 최대
  수집 개수`를 극단적으로 높이면 느려지거나 오류가 날 수 있습니다.
  나눠서 검색하는 걸 권장합니다.
- **12시간 동안 아무도 접속하지 않으면 앱이 잠듭니다.** 그 다음 다시
  접속하면 "앱을 깨우는 중"이라는 화면이 잠깐(보통 1분 이내) 뜨고
  자동으로 다시 켜집니다. 오류가 아니라 정상 동작이니 당황하지
  않으셔도 됩니다.
- 비공개(Private) 앱은 무료 요금제에서 1개까지만 가능합니다. 이 프로젝트
  외에 다른 비공개 앱을 또 만들고 싶다면 이 앱을 Public으로 바꾸거나
  유료 요금제가 필요합니다.
