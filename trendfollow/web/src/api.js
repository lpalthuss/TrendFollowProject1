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
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "요청 실패");
  return j;
}
