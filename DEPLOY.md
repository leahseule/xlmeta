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
