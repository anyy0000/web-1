# 건축물대장정보 API 테스트 (전유부/호실별 전용면적)

공공데이터포털의 국토교통부 "건축물대장정보 서비스" 중 **전유부공용면적 조회**
(`getBrExposPubuseAreaInfo`) 오퍼레이션을 호출해, 집합건물(아파트/오피스텔/다세대 등)의
호실별 전용면적을 확인하는 테스트 스크립트입니다.

## 준비

1. `.env.example`을 참고해서 `.env` 파일에 아래 두 값을 채웁니다. (`.env`는 git에 커밋되지 않습니다)
   - `BUILDING_REGISTRY_API_KEY`: 공공데이터포털에서 발급받은 인증키 (Decoding 키 권장)
   - `BUILDING_REGISTRY_API_ENDPOINT`: 오퍼레이션 엔드포인트
     (예: `https://apis.data.go.kr/1613000/BldRgstService_v2/getBrExposPubuseAreaInfo`)

## 실행

```bash
cd building-registry-api
node --env-file=.env test-building-registry.mjs <시군구코드> <법정동코드> <번> <지> [동이름] [호이름]
```

예시:

```bash
node --env-file=.env test-building-registry.mjs 11680 10300 100 0 101동 302호
```

- 시군구코드/법정동코드/번/지는 조회할 건물의 지번 주소를 코드화한 값입니다.
- 동이름/호이름은 선택값이며, 비워두면 해당 건물의 모든 호실이 조회됩니다.

## 결과 확인

`exposPubuseGbCdNm`이 "전유"인 레코드의 `area` 값이 그 호실의 **전용면적**입니다.
같은 호실에 대해 "공용" 레코드도 함께 내려옵니다.

## 주의

- `.env` 파일에는 실제 인증키가 들어가므로 절대 커밋하거나 공유하지 마세요.
- 이 폴더의 `.gitignore`에 `.env`가 등록되어 있어 기본적으로 커밋 대상에서 제외됩니다.
