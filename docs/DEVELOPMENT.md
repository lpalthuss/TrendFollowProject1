# 개발과 검증

프로젝트 루트에서 Python 3.11 이상과 Node.js 22 이상을 사용한다. 운용 UI 자체는 Node 없이 실행한다.

```powershell
python -m pip install -e ".[test,dev]"
npm ci
python -m trendfollow.checks
```

통합 명령은 UI 소스·번들 일치, Python 테스트, JavaScript 구문 검사와 필터 테스트를 모두 확인한다. 테스트는 임시 디렉터리와 모의 응답을 사용하며 API 키나 증권사 접속이 필요 없다. GitHub Actions도 Windows·Linux에서 같은 명령을 실행한다.

## UI 수정

`trendfollow/web/src/`의 해당 기능과 HTML·CSS를 수정한다. `app.js`는 직접 수정하지 않는다.

```powershell
npm run format
python -m trendfollow.assets
python -m trendfollow.checks
```

서식만 확인할 때는 `npm run format:check`, Python 서식은 `python -m black trendfollow`를 사용한다. `package-lock.json`은 개발 도구 버전을 고정한다. npm 라이브러리를 브라우저에서 가져오지 않는다.

## 호환성 검증

기존 `python -m trendfollow ...`와 `trendfollow.ui_service.Operations`, `trendfollow.strategy.Rules` 경로를 유지한다. 공통 지표 변경은 후보 선별과 백테스트 양쪽을 확인한다. 실제 저장 계좌에 테스트 실행을 하지 않는다. 가격·산업·설정 입력을 바꾸면 정정 검토나 별도 비교 계좌가 필요한 기존 규칙을 유지한다.
