"use strict";

const $ = (id) => document.getElementById(id);

const CLASSES_PAGE = "/classes.html";

// 서버가 꺼져 있으면 fetch 는 영어 오류(Failed to fetch)로 실패한다. 무엇을 해야 하는지 알려 준다.
async function post(url, body) {
  try {
    return await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
  }
}

// 서버가 문장으로 알려 준 오류(401, 409)는 그대로 쓰고, 입력 형식 오류(422)는 일반 문구로 안내한다.
async function errorMessage(res, fallback) {
  try {
    const detail = (await res.json()).detail;
    return typeof detail === "string" ? detail : fallback;
  } catch {
    return fallback;
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const res = await post("/api/teacher/login", {
      username: $("login-username").value,
      password: $("login-password").value,
    });
    if (!res.ok) throw new Error(await errorMessage(res, "아이디와 비밀번호를 확인해 주세요."));
    location.href = CLASSES_PAGE;
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("signup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("signup-error").textContent = "";
  try {
    if ($("signup-password").value !== $("signup-password2").value) {
      throw new Error("비밀번호와 비밀번호 확인이 서로 달라요.");
    }
    const res = await post("/api/teacher/signup", {
      name: $("signup-name").value,
      username: $("signup-username").value,
      password: $("signup-password").value,
      invite_code: $("signup-invite").value,
    });
    if (!res.ok) {
      throw new Error(
        await errorMessage(res, "아이디는 영문·숫자·밑줄 4~20자, 비밀번호는 8자 이상으로 입력해 주세요.")
      );
    }
    location.href = CLASSES_PAGE;
  } catch (err) {
    $("signup-error").textContent = err.message;
  }
});

// 서버가 초대 코드를 요구하면 그 칸을 보여 준다.
fetch("/api/teacher/signup-info")
  .then((res) => (res.ok ? res.json() : null))
  .then((info) => {
    if (!info?.invite_required) return;
    $("invite-field").hidden = false;
    $("signup-invite").required = true;
  })
  .catch(() => {});

// 이미 로그인되어 있으면 바로 내 교실로 간다.
fetch("/api/teacher/me")
  .then((res) => {
    if (res.ok) location.replace(CLASSES_PAGE);
  })
  .catch(() => {});
