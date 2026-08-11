<div align="center">

# ▦ xlmeta

### 엑셀 수식에 갇힌 **업무 규칙**을, LLM 없이 꺼내 읽는 도구
<sub>Business logic is buried in spreadsheet formulas. xlmeta extracts it — deterministically, no LLM.</sub>

<br/>

[![live demo](https://img.shields.io/badge/live%20demo-online-2ea44f?style=flat-square)](https://xlmeta.weaveapp.duckdns.org)
[![no LLM](https://img.shields.io/badge/LLM-not%20used-8250df?style=flat-square)](#원칙)
[![python](https://img.shields.io/badge/python-3.12-3776AB?style=flat-square)](#빠른-시작)
[![deploy](https://img.shields.io/badge/deploy-Docker%20%2B%20EC2-2496ED?style=flat-square)](DEPLOY.md)
[![license](https://img.shields.io/badge/license-Apache--2.0-lightgrey?style=flat-square)](LICENSE)

[**▶ 라이브 데모 열기**](https://xlmeta.weaveapp.duckdns.org) &nbsp;·&nbsp; [빠른 시작](#빠른-시작) &nbsp;·&nbsp; [무엇을 뽑나](#무엇을-뽑나) &nbsp;·&nbsp; [설계](#설계)

</div>

---

회사의 실제 업무 규칙은 규정집이 아니라 **담당자의 엑셀 수식 안**에 있습니다.

```excel
=SUMIFS(실적!G:G, 실적!C:C, A6, 실적!F:F, "승인")
```

*"승인된 건만 원가로 인정한다"* — 이 규칙은 어디에도 문서화되지 않은 채 셀에 박제되어 있습니다.
수식은 이미 **형식 언어**이므로 추론할 필요가 없습니다. **파싱하면 됩니다.**

xlmeta는 복잡한 실무 엑셀을 다른 시스템(DB·문서·AI)으로 옮기기 전에, 그 안의 **구조와 규칙을
결정론적으로 꺼내** 사람과 AI가 함께 읽을 수 있는 형태로 만듭니다.

```mermaid
flowchart LR
    XL["엑셀 .xlsx"] --> LY["layout.py<br/>표·머리글·행식별"]
    XL --> FO["formula.py<br/>수식 파싱"]
    LY --> ME["metric.py<br/>지표 · 업무규칙"]
    FO --> ME
    ME --> OK["OKF 번들<br/>(마크다운)"]
    ME --> SU["summary.py<br/>결정론적 요약"]
    SU --> SH["공개 링크<br/>/s/&lt;id&gt;"]
    SH --> AI["ChatGPT · Claude<br/>프리필로 전달"]
```

<br/>

## 라이브 데모

> ### [https://xlmeta.weaveapp.duckdns.org](https://xlmeta.weaveapp.duckdns.org)

엑셀을 올리면 xlmeta가 읽어낸 **구조를 원본과 나란히** 보여줍니다. 파일 없이 **"샘플로 바로 보기"**
버튼으로 즉시 둘러볼 수 있습니다.

- **왼쪽 — 있는 그대로의 엑셀** · 셀을 빈칸·숫자·텍스트·수식 유형으로 색칠한 실제 격자
- **오른쪽 — 읽어낸 구조** · 표 영역·다단 머리글·행 식별 열·소계행·시트 참조. 클릭하면 원본에서 해당 범위가 하이라이트
- **셀 하나를 클릭** · 그 칸의 값·계산식·쓰인 함수·Python 번역·업무 규칙까지 한눈에

## 특징

|  |  |
|---|---|
| **수식 → 업무 규칙** | `SUMIFS` 계열 조건을 *"결재상태 = 승인"* 같은 규칙으로 |
| **수기 개입 탐지** | 수식이 있어야 할 자리의 상수 → 자동화하면 안 되는 지점 |
| **지표 의존 그래프** | 셀 단위가 아니라 지표 단위로 "무엇이 무엇을 쓰는지" |
| **AI에게 넘기기** | 엑셀을 AI가 바로 읽는 요약 링크로 만들어 원클릭 전달 |
| **소계/합계 제외** | 집계 행을 지표로 오인하지 않음 |

## 빠른 시작

```bash
pip install -r requirements.txt   # 의존성은 openpyxl 하나

python make_sample.py             # 데모용 엑셀 생성 → sample_epc_cost.xlsx
python -m xlmeta sample_epc_cost.xlsx -o demo
```

`demo/`에 마크다운 지식 번들이 생성됩니다. 자기 파일로 돌리려면:

```bash
python -m xlmeta report.xlsx -o okf_bundle
```

> 샘플 엑셀은 리포지터리에 바이너리로 넣지 않고 [`make_sample.py`](make_sample.py)로 재현합니다.
> 누구나 같은 입력으로 같은 결과를 다시 만들 수 있습니다.

**웹 화면으로 보기:**

```bash
pip install -r webapp/requirements.txt
python webapp/app.py            # → http://127.0.0.1:5000
```

## 무엇을 뽑나

`발생원가` 지표 하나의 수식 `=SUMIFS(실적!G:G,실적!C:C,A6,실적!F:F,"승인")` 에서 뽑아낸 것:

| 항목 | 뽑아낸 것 |
|---|---|
| **업무 규칙** | 결재상태 `=` 승인 |
| **매칭 키** | 프로젝트코드 = `A6` |
| **원천** | `실적!G:G`(금액) · `실적!C:C`(프로젝트코드) · `실적!F:F`(결재상태) |
| **수기 개입** | `원가현황!D9` 에 사람이 박은 값 `1,875,000,000` |
| **이 지표를 쓰는 곳** | 집행률 · 총투입예상 |

전체 생성물은 [`demo/`](demo/) 에서 볼 수 있습니다 — [`발생원가-M001.md`](demo/metrics/발생원가-M001.md) 부터 보세요.

## AI에게 넘기기

엑셀을 올리면 구조·업무규칙에 **실제 데이터까지** 결정론적으로 정리해 공개 페이지(`/s/<id>`)로 만들고,
그 링크를 **ChatGPT·Claude 프리필**에 담아 넘깁니다. 요약이 URL이 아니라 링크가 가리키는 페이지에 있어
주소가 길어지지 않고, AI가 **구조와 데이터를 함께 읽어 바로 분석**할 수 있습니다.
(데이터가 담기므로 링크가 공개되면 데이터도 보입니다 — 민감한 파일은 주의.)

> AI는 이 링크를 열어 *"이 시트는 프로젝트코드로 행을 구분하는 표이고, 발생원가·초과율 등 5개
> 값을 실적 시트에서 SUMIFS로 집계한다…"* 같은 요약을 읽고 답합니다.

## 설계

```text
xlmeta/
├── xlmeta/                파이썬 패키지 (의존성: openpyxl)
│   ├── layout.py          표 구조 해석 — 수식을 모름
│   ├── formula.py         수식 파싱 — 표 구조를 모름
│   ├── metric.py          둘을 결합해 지표 구성 — 유일하게 둘 다 앎
│   ├── explain.py         사람·AI용 해석 (한글 설명·함수 문법·Python 변환)
│   ├── summary.py         시트별 결정론적 요약 (AI 프리필 본문)
│   ├── evaluate.py        수식 계산값 산출 (SUMIFS 계열·사칙연산)
│   ├── emit_okf.py        OKF v0.1 번들 출력
│   └── __main__.py        CLI 진입점 (python -m xlmeta)
├── webapp/                업로드→분석→공개 요약 Flask 앱
├── make_sample.py         데모용 엑셀 생성기 (재현용)
├── demo/                  예시 출력 번들
├── Dockerfile · docker-compose.yml · DEPLOY.md   배포 (Docker + AWS EC2)
└── docs/                  주석 달린 예시 문서
```

**레이아웃이 먼저입니다.** 셀을 값이 아니라 유형(`. n t f`)으로 바꾼 지도에서 시작하므로, 내용을
몰라도 모양이 보입니다. 표 영역·다단 머리글·소계행을 확정한 뒤에야 수식을 해석합니다.

`layout.py`는 수식 내용을 전혀 모르고, `formula.py`는 표 구조를 전혀 모릅니다. 둘을 아는 모듈은
`metric.py` 하나뿐 — 이 관심사 분리 덕분에 한쪽을 고쳐도 다른 쪽이 깨지지 않습니다.

### 원칙

- **추론하지 않는다.** 같은 입력이면 같은 출력. 재실행으로 검증 가능.
- **침묵 누락 0.** 이름을 확정하지 못하면 추측 대신 제외하고 `index.md`에 명시합니다.
- **오프라인.** 핵심 도구의 의존성은 `openpyxl` 하나. 폐쇄망에서 돌고 데이터가 나가지 않습니다.

## 배포

Docker 이미지로 패키징되어 있고, 실제로 **AWS EC2에 라이브로 떠 있습니다**
([https://xlmeta.weaveapp.duckdns.org](https://xlmeta.weaveapp.duckdns.org)).

```bash
docker compose up -d --build     # → http://localhost (EC2에선 공개 주소)
```

`ProxyFix`가 걸려 있어 리버스 프록시(Caddy·ALB) 뒤에서 `https://도메인` 링크가 그대로 나옵니다.
자세한 EC2 절차는 [`DEPLOY.md`](DEPLOY.md) 참고.

## 한계

- 피벗 테이블, 배열 수식, 외부 파일 링크 미지원 (감지 후 보고)
- 행 방향 표(좌측 항목 / 상단 기간)·계층형 행머리글 미지원
- 수식 계산값은 엑셀이 저장한 캐시에만 존재 — openpyxl로 만든 파일은 캐시가 없어 비어 있을 수 있음(규칙 추출과는 무관)

## 라이선스

Apache-2.0

<div align="center"><sub>고객 엑셀 구조 파악 → 시스템 이관이라는 실무의 핵심 장면을 담았습니다.</sub></div>
