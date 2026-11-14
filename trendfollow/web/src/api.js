async function api(path, body) {
  const r = await fetch(
    path,
    body
      ? {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-UI-Token": boot.csrf,
          },
          body: JSON.stringify(body),
        }
      : {},
  );
  if (r.status === 404) {
    const route = path.split("?")[0];
    throw new Error(
      `현재 접속한 UI 서버에서 ${route} 요청을 처리하지 못했습니다. 실행 중인 서버 주소·포트를 확인하고, 해당 서버를 최신 코드로 다시 시작한 뒤 이 주소의 화면을 새로고침하세요.`,
    );
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "요청 실패");
  return j;
}
