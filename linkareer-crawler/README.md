# 링커리어 식품·건강 공고 수집기

링커리어의 **공모전**과 **대외활동** 목록에서 식품, 영양, 건강 관련 공고만 골라 Google 스프레드시트에 저장합니다. 엑셀 파일로도 함께 저장합니다.
매주 월요일과 목요일 오전에 GitHub Actions로 자동 실행됩니다.

## 동작 방식

1. `/list/contest`, `/list/activity`의 최신순 목록을 앞에서부터 N페이지(`config.yaml`의 `pages_per_list`) 읽습니다.
2. 처음 보는 공고만 상세 페이지를 열어 주최사, 마감일, 본문을 보강합니다.
3. 점수를 매깁니다 (`config.yaml`에서 조정).
   | 조건 | 점수 |
   |---|---|
   | 주최사가 `organizations` 목록의 기업·기관 | +4 |
   | 주최사 이름에 식품, 제약, 헬스 같은 단어 포함 | +3 |
   | 제목이나 분야에 식품·건강 키워드 포함 | 키워드당 +2 (최대 +4) |
   | 본문에 키워드 포함 | 키워드당 +1 (최대 +3) |
   | 제외 키워드 포함 ("금융 건강" 등) | −3 |
4. **4점 이상은 추천, 2~3점은 검토**로 분류해 시트에 추가합니다. 나머지와 마감된 공고는 `_seen` 탭에 ID만 기록해서 다음 실행 때 다시 읽지 않습니다.
5. 이미 있는 행은 건드리지 않고 새 공고만 아래에 추가합니다. `상태`나 `메모` 열에 직접 적은 내용은 그대로 남습니다.

## Google 스프레드시트 연동 (처음 한 번만)

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만들고 **Google Sheets API**를 사용 설정합니다.
2. IAM 및 관리자 → 서비스 계정 → 계정 생성 → 키 → **JSON 키 추가**를 눌러 파일을 받습니다.
3. 스프레드시트를 하나 만들고, 서비스 계정 이메일(`xxx@xxx.iam.gserviceaccount.com`)에 **편집자로 공유**합니다.
4. 스프레드시트 주소 `https://docs.google.com/spreadsheets/d/<이 부분>/edit`에서 `<이 부분>`이 `SPREADSHEET_ID`입니다.

### GitHub Actions로 자동 실행 (추천: PC를 켜 둘 필요 없음)

저장소 Settings → Secrets and variables → Actions에 아래 두 개를 등록합니다.
- `SPREADSHEET_ID`: 위 4번에서 확인한 값
- `GOOGLE_SERVICE_ACCOUNT_JSON`: JSON 키 파일 내용 전체

일정은 `.github/workflows/linkareer-crawler.yml`에 정의되어 있습니다 (월·목 08:47 KST).
Actions 탭에서 **Run workflow**를 누르면 바로 실행해 볼 수 있습니다. 실행마다 새로 추가된 공고가 엑셀 파일로 Artifacts에 올라갑니다.

### 로컬에서 실행

```bash
cd linkareer-crawler
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py --excel-only            # 엑셀만 저장 (output/linkareer.xlsx, 누적)
# 구글시트 연동: JSON 키를 service_account.json 으로 이 폴더에 두고
SPREADSHEET_ID=스프레드시트ID python main.py
```

로컬에서 월·목 자동 실행을 하려면:
- macOS/Linux: `crontab -e`에 `47 8 * * 1,4 cd /경로/linkareer-crawler && .venv/bin/python main.py`를 추가합니다.
- Windows: 작업 스케줄러에서 매주 월·목 트리거로 `.venv\Scripts\python.exe main.py`를 등록합니다. 시작 위치는 이 폴더로 지정합니다.

## 옵션

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 저장하지 않고 매칭 결과만 출력합니다 |
| `--pages N` | 목록을 N페이지까지 읽습니다 |
| `--no-detail` | 상세 페이지를 열지 않습니다 (빠르지만 정확도가 떨어짐) |
| `--dump` | 받은 HTML을 `debug/`에 저장합니다 (사이트 구조를 확인할 때) |

## 한계와 주의사항

- **사이트 구조를 아직 실제로 확인하지 못했습니다.** 파서는 Next.js 페이지에 들어 있는 `__NEXT_DATA__` JSON에서 `__typename: "Activity"` 객체를 찾습니다. 못 찾으면 `/activity/{id}` 링크를 수집합니다.
  - 처음 실행할 때 `python main.py --dry-run --pages 1 --dump`로 결과를 확인하세요.
  - 제목이나 주최가 비어 있거나 목록이 0건이면 `debug/`의 HTML을 보고 필드명을 맞춰야 합니다.
- 목록이 브라우저에서 GraphQL로 따로 불러오는 방식(클라이언트 렌더링)이라면 HTML에 데이터가 없습니다. 그 경우 GraphQL 요청 방식으로 바꿔야 합니다.
- GitHub Actions 서버 IP가 사이트에서 차단되면 목록이 0건이 되고 실행이 실패로 표시됩니다. 그러면 로컬 실행으로 전환하세요.
- 키워드 매칭이라 오탐과 누락이 있습니다. 시트의 `매칭근거` 열을 보고 `config.yaml`의 목록을 계속 다듬는 것을 전제로 합니다.
- 요청 사이에 1.5초씩 쉬고 실행은 주 2회라 서버 부담은 작습니다. 그래도 링커리어 이용약관은 직접 확인하시고, 개인 용도로만 쓰세요.
