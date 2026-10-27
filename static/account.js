"use strict";

const $ = (id) => document.getElementById(id);

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
  }
  if (res.status === 401) {
    location.href = "/teacher.html"; // 로그인이 풀렸으면 다시 로그인한다.
    throw new Error("로그인이 필요합니다.");
  }
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch {
    // 본문이 JSON 이 아니면 아래 오류 문구를 쓴다.
  }
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "입력을 확인해 주세요.");
  return data;
}

$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("password-error").textContent = "";
  $("password-done").textContent = "";
  try {
    if ($("password-new").value !== $("password-new2").value) {
      throw new Error("새 비밀번호와 새 비밀번호 확인이 서로 달라요.");
    }
    await api("/api/teacher/password", {
      method: "POST",
      body: { current_password: $("password-current").value, new_password: $("password-new").value },
    });
    for (const id of ["password-current", "password-new", "password-new2"]) $(id).value = "";
    $("password-done").textContent = "비밀번호를 바꿨어요. 다음부터는 새 비밀번호로 로그인하세요.";
  } catch (err) {
    $("password-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  await fetch("/api/teacher/logout", { method: "POST" });
  location.href = "/teacher.html";
});

(async function boot() {
  try {
    const me = await api("/api/teacher/me");
    $("who").textContent = `${me.name} 선생님`;
    $("account-name").textContent = me.name;
    $("account-username").textContent = me.username;
    $("password-username").value = me.username;
  } catch (err) {
    $("page-error").textContent = err.message;
  }
})();
