# 배포 — Docker + AWS EC2

xlmeta 웹 데모를 컨테이너로 EC2에 올려 운영하는 법. AI에게 넘길
요약 링크(`/s/<id>`)가 **공개 주소**여야 ChatGPT·Claude가 읽을 수 있으므로,
공개 IP(또는 도메인)로 접근되게 하는 것이 목적입니다.

## 로컬에서 먼저 확인

```bash
docker compose up -d --build
# → http://localhost  에서 동작 확인
docker compose logs -f        # 로그
docker compose down           # 내리기
```

## EC2에 올리기

### 1) 인스턴스

- AMI: **Amazon Linux 2023** (또는 Ubuntu 22.04), 타입 **t3.small** 이상 권장
- **보안 그룹(인바운드)**: `22`(SSH, 내 IP만), `80`(HTTP, 0.0.0.0/0). HTTPS까지 하면 `443`.

### 2) Docker 설치 (Amazon Linux 2023)

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user      # 재로그인 후 sudo 없이 docker 사용
# compose 플러그인
sudo mkdir -p /usr/libexec/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/libexec/docker/cli-plugins/docker-compose
sudo chmod +x /usr/libexec/docker/cli-plugins/docker-compose
```

### 3) 코드 받고 실행

```bash
git clone <이 저장소 URL> xlmeta && cd xlmeta
docker compose up -d --build
```

→ 브라우저에서 **`http://<EC2-퍼블릭-IP>`** 접속. 엑셀을 올리고
**AI에게 넘기기**의 버튼을 누르면, 프리필에 담긴 링크가 이 공개 주소를 가리킵니다.

### 4) 운영

```bash
docker compose ps               # 상태
docker compose logs -f xlmeta   # 로그
docker compose up -d --build    # 코드 갱신 후 재배포 (git pull 다음에)
docker compose restart          # 재시작
docker compose down             # 정지
```

- **요약 영속화**: `/s/<id>`는 named volume `xlmeta_data`(`/data`)에 저장돼
  컨테이너를 다시 올려도 살아남습니다. 완전 초기화는 `docker compose down -v`.

## HTTPS (권장, 선택)

ChatGPT·Claude는 http 링크도 대개 읽지만, 프로덕션은 https가 안전합니다.
앱은 이미 `ProxyFix`가 걸려 있어 **리버스 프록시 뒤에서 `X-Forwarded-Proto/Host`를
읽어** 링크를 `https://도메인`으로 만들어 줍니다. 두 가지 방법:

- **Caddy** (도메인만 있으면 인증서 자동): 앱 앞에 Caddy 컨테이너를 두고
  `your.domain { reverse_proxy xlmeta:8000 }` 한 줄이면 Let's Encrypt 자동 발급.
- **AWS ALB + ACM**: ALB에 ACM 인증서를 붙이고 대상 그룹을 EC2:80으로.
  ALB가 `X-Forwarded-*`를 넣어 주므로 앱 수정 없이 https 링크가 나옵니다.

> 참고: `ProxyFix`는 신뢰할 수 있는 프록시(위 Caddy/ALB) 뒤에서만 쓰세요.
> 프록시 없이 공개 IP로 직접 노출할 때는 `X-Forwarded-Host` 스푸핑 여지가
> 있으니, 정식 운영은 프록시+도메인을 두는 것을 권합니다.

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PORT` | `8000` | 컨테이너 내부 포트 (compose가 80→8000 매핑) |
| `XLMETA_DATA_DIR` | `/data/summaries` | 요약 저장 경로 (볼륨) |
| `OPENAI_API_KEY` | (없음) | Q&A·그래프 챗봇 켜는 키. 없으면 둘 다 비활성으로 정상 동작 |
| `OPENAI_MODEL` | `gpt-4o-mini` | Q&A·챗봇에 쓰는 모델 |
| `NEO4J_URI` | (없음) | 그래프 챗봇용. Neo4j Aura 콘솔에서 받은 연결 문자열(`neo4j+s://...`) |
| `NEO4J_USERNAME` | `neo4j` | Aura 기본값 그대로 두면 됨 |
| `NEO4J_PASSWORD` | (없음) | Aura 인스턴스 생성 시 한 번만 보여주는 비밀번호. 그때 저장해둘 것 |

배포 전 서버에서 `.env` 파일에 `OPENAI_API_KEY=...`, `NEO4J_URI=...`, `NEO4J_PASSWORD=...`를
넣어둘 것(compose가 같은 디렉토리의 `.env`를 자동으로 읽는다). 셋 중 하나라도 없으면
그래프 챗봇 탭만 비활성으로 정상 동작한다(`graph_qa.available()`가 False).

## 그래프 챗봇 — Neo4j Aura를 쓰는 이유, 보안 메모

- **Neo4j는 이 EC2에 직접 안 띄운다.** 2026-09-07에 로컬 Neo4j 컨테이너를 얹었다가
  인스턴스 메모리(909MB, 프리티어)가 부족해 반복 OOM으로 SSH 접속까지 막히는 장애를
  겪었다. Weave·xlmeta·Caddy만으로 이미 메모리가 빠듯해서, Neo4j를 아무리 작게 잡아도
  이 인스턴스엔 여유가 없다. → **Neo4j Aura Free**(Neo4j 공식 무료 클라우드, 이 서버
  밖에서 도는 별도 인스턴스)를 쓴다. `NEO4J_URI`만 Aura 주소로 바뀔 뿐, 코드는 로컬
  Neo4j를 쓸 때와 동일하다(`neo4j` 파이썬 드라이버가 `neo4j+s://`도 그대로 처리함).
- 챗봇이 실행하는 Cypher는 `routing_=READ`로 돌아서(`webapp/graph_qa.py`), Neo4j 서버가
  쓰기 쿼리 자체를 거부한다 — 프롬프트 인젝션으로 삭제/수정을 유도해도 서버 단에서 막힘.
- 나중에 데이터 규모가 커져 Aura 무료 티어를 넘으면, 그때 가서 별도의(이 앱과 무관한)
  전용 인스턴스에 Neo4j를 올리는 걸 고려할 것 — 이 EC2엔 다시 올리지 말 것.
