# iBalance Frontend

Next.js (App Router) 기반 프론트엔드. Vercel 배포용. 기능은 없고 backend
health check와 연동되는 상태 확인 페이지만 제공합니다.

## 구조

- `app/page.tsx` — Frontend/Backend 연동 상태를 보여주는 페이지
- `app/api/health/route.ts` — 서버 사이드에서 backend `/health`를 호출하는 프록시
  (Vercel(HTTPS) → backend(HTTP) mixed-content 문제를 피하기 위해 서버에서 호출)

## 로컬 실행

```bash
npm install
cp .env.example .env.local   # BACKEND_URL 확인/수정
npm run dev
```

http://localhost:3000 에서 연동 상태를 확인합니다.

## 환경 변수

- `BACKEND_URL`: backend 주소. 기본값 `http://localhost:8000`.

## Vercel 배포

1. Vercel에서 이 저장소(`team-i-balance/frontend`)를 import
2. Framework Preset: **Next.js** (자동 감지)
3. Environment Variables 에 `BACKEND_URL` 추가
   예: `http://13.193.207.69:8501`
4. Deploy

배포 후 루트 페이지에서 Backend가 "연결됨"으로 표시되면 연동 성공입니다.
