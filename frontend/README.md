# Outlook 폴더 설정 UI

React + Vite로 구현한 더미 Outlook 폴더 설정 화면입니다. 실제 Exchange나 DB에는 연결하지 않습니다.

## 실행

```bash
npm install
npm run dev
```

Vite가 출력하는 로컬 URL을 브라우저에서 엽니다.

## 검증

```bash
npm test
npm run lint
npm run build
```

## 실제 백엔드 연결 시 교체 지점

`src/services/mailSourceService.ts`의 `DummyMailSourceService`를 FastAPI 클라이언트로 교체합니다.

- `connect(userId, password)`: 폴더 목록 API 호출
- `saveSelection(userId, folderIds)`: 사용자별 선택 폴더 DB 저장 API 호출

현재 더미 구현은 비밀번호를 저장하지 않으며, `localStorage`에는 사용자 ID, 선택한 폴더 ID, 저장 시각만 기록합니다.
