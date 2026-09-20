"use strict";

const $ = (id) => document.getElementById(id);

// 방금 만든 학급의 코드와 교사 키. 화면에 보여 주는 값을 그대로 로그인에 쓴다.
let created = null;

// 서버가 꺼져 있으면 fetch 는 영어 오류(Failed to fetch)로 실패한다. 무엇을 해야 하는지 알려 준다.
async function api(url, options) {
  try {
    return await fetch(url, options);
  } catch {
    throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
  }
}

async function teacherLogin(joinCode, teacherKey) {
  const res = await api("/api/teacher/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ join_code: joinCode, teacher_key: teacherKey }),
  });
  if (res.ok) {
    location.href = "/dashboard.html";
    return;
  }
  // 401 은 서버가 알려 주는 문구를 그대로 쓰고, 그 밖의 오류(입력 형식 등)는 일반 문구로 안내한다.
  const detail = res.status === 401 ? (await res.json()).detail : null;
  throw new Error(detail || "학급 코드와 교사 키를 확인해 주세요.");
}

$("class-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("class-error").textContent = "";
  try {
    const res = await api("/api/classes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("class-name").value }),
    });
    if (!res.ok) {
      throw new Error("학급 이름을 확인해 주세요.");
    }
    created = await res.json();
    $("created-name").textContent = created.name;
    $("created-code").textContent = created.join_code;
    $("created-key").textContent = created.teacher_key;
    $("create-view").hidden = true;
    $("created-view").hidden = false;
  } catch (err) {
    $("class-error").textContent = err.message;
  }
});

$("open-dashboard").addEventListener("click", async () => {
  $("open-error").textContent = "";
  try {
    await teacherLogin(created.join_code, created.teacher_key);
  } catch (err) {
    $("open-error").textContent = err.message;
  }
});

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await teacherLogin($("login-code").value, $("login-key").value);
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

// 이미 로그인되어 있으면 키를 다시 넣지 않고 바로 들어갈 수 있게 한다.
fetch("/api/teacher/me")
  .then((res) => (res.ok ? res.json() : null))
  .then((me) => {
    if (!me) return;
    $("resume-link").textContent = `${me.class_name} 대시보드 바로 열기 →`;
    $("resume-link").hidden = false;
  })
  .catch(() => {});
