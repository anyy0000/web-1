// 건축물대장정보 서비스 - 전유부공용면적(호실별 전용면적) 테스트 스크립트
//
// 실행 전 준비:
//   1. .env.example 을 참고해 .env 파일에 BUILDING_REGISTRY_API_KEY / BUILDING_REGISTRY_API_ENDPOINT 값을 채워 넣으세요.
//      (공공데이터포털 마이페이지에서 받은 "Decoding" 인증키를 넣으면 됩니다)
//   2. 실행:
//      node --env-file=.env test-building-registry.mjs <시군구코드> <법정동코드> <번> <지> [동이름] [호이름]
//
//   예) node --env-file=.env test-building-registry.mjs 11680 10300 100 0 101동 302호

const [sigunguCd, bjdongCd, bun, ji, dongNm, hoNm] = process.argv.slice(2);

const apiKey = process.env.BUILDING_REGISTRY_API_KEY;
const endpoint = process.env.BUILDING_REGISTRY_API_ENDPOINT;

if (!apiKey || !endpoint) {
  console.error(
    "환경변수가 비어 있습니다. .env 파일에 BUILDING_REGISTRY_API_KEY / BUILDING_REGISTRY_API_ENDPOINT 를 채운 뒤,\n" +
      "`node --env-file=.env test-building-registry.mjs ...` 형태로 실행하세요."
  );
  process.exit(1);
}

if (!sigunguCd || !bjdongCd) {
  console.error(
    "사용법: node --env-file=.env test-building-registry.mjs <시군구코드> <법정동코드> [번] [지] [동이름] [호이름]"
  );
  process.exit(1);
}

const params = new URLSearchParams({
  serviceKey: apiKey,
  sigunguCd,
  bjdongCd,
  bun: bun ?? "",
  ji: ji ?? "",
  numOfRows: "100",
  pageNo: "1",
  _type: "json",
});
if (dongNm) params.set("dongNm", dongNm);
if (hoNm) params.set("hoNm", hoNm);

const url = `${endpoint}?${params.toString()}`;

console.log("요청 URL(키 마스킹):", url.replace(apiKey, "****"));

const res = await fetch(url);
const text = await res.text();

let parsed;
try {
  parsed = JSON.parse(text);
} catch {
  console.log("\n[JSON 파싱 실패 - 응답 원문(XML일 가능성) 출력]\n");
  console.log(text);
  process.exit(0);
}

const header = parsed?.response?.header;
const items = parsed?.response?.body?.items?.item;

console.log("\n[응답 헤더]", header);

if (!items) {
  console.log("[조회 결과 없음] body:", parsed?.response?.body);
  process.exit(0);
}

const list = Array.isArray(items) ? items : [items];
console.log(`\n[조회된 레코드 수: ${list.length}]\n`);

for (const item of list) {
  console.log(
    `동: ${item.dongNm ?? "-"} / 호: ${item.hoNm ?? "-"} / 구분: ${
      item.exposPubuseGbCdNm ?? "-"
    } / 면적: ${item.area ?? "-"}㎡`
  );
}
