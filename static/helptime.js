"use strict";

// 교사 화면(내 교실, 대시보드)이 함께 쓰는 도움 시간 조절기.
// 친구 도와주러 가기와 채팅은 여기서 연 시간 동안만 된다. 짝에게 서로의 이름을 가릴지도 여기서 고른다.

function formatCountdown(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// classId 교실의 조절기를 만든다. node 를 화면에 붙이고, 서버에서 새 상태를 받을 때마다
// update(help_time) 과 setHideNames(hide_names) 를 부른다.
function createHelpTimeControl(classId) {
  const classUrl = `/api/teacher/classes/${classId}`;
  const url = `${classUrl}/help-time`;
  let deadline = null; // 끝나는 시각 (이 브라우저의 시계 기준, ms). 닫혀 있으면 null.

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function button(className, text) {
    const node = make("button", `btn btn-sm ${className}`, text);
    node.type = "button";
    return node;
  }

  const badge = make("span", "state");
  const clock = make("span", "help-time-clock");
  const status = make("div", "help-time-status");
  status.append(badge, clock);
  status.setAttribute("role", "timer");

  // 닫혀 있을 때: 몇 분 동안 열지 고른다.
  const minutesId = `help-minutes-${classId}`;
  const minutesLabel = make("label", "sr-only", "도움 시간 (분)");
  minutesLabel.htmlFor = minutesId;
  const minutes = make("input", "help-time-minutes");
  Object.assign(minutes, { id: minutesId, type: "number", min: "1", max: "180", step: "1", value: "5", required: true });
  const openForm = make("form", "help-time-actions");
  const openButton = make("button", "btn btn-sm btn-amber", "도움 시간 열기");
  openButton.type = "submit";
  openForm.append(minutesLabel, minutes, make("span", "muted small", "분 동안"), openButton);

  // 열려 있을 때: 늘리거나 바로 끝낸다.
  const plus1 = button("btn-secondary", "+1분");
  const plus5 = button("btn-secondary", "+5분");
  const endNow = button("btn-secondary help-time-end", "즉시 종료");
  const runningActions = make("div", "help-time-actions");
  runningActions.append(plus1, plus5, endNow);

  // 짝이 된 친구의 이름을 가릴지. 켜면 학생 화면에는 이름 대신 "익명"이 보인다.
  const hideNames = make("input");
  hideNames.type = "checkbox";
  const hideLabel = make("label", "help-time-option");
  hideLabel.append(hideNames, make("span", null, "짝이 된 친구의 이름 가리기"));

  const error = make("p", "error");
  error.setAttribute("role", "alert");
  const node = make("div", "help-time");
  node.append(status, openForm, runningActions, hideLabel, error);

  function paint() {
    const remaining = deadline === null ? 0 : (deadline - Date.now()) / 1000;
    const active = remaining > 0;
    badge.className = `state ${active ? "helping" : "idle"}`;
    badge.textContent = active ? "🤝 도움 시간 진행 중" : "🔒 도움 시간 닫힘";
    clock.textContent = active ? `${formatCountdown(remaining)} 남음` : "";
    openForm.hidden = active;
    runningActions.hidden = !active;
    if (!active) deadline = null;
  }

  function update(helpTime) {
    deadline = helpTime && helpTime.active ? Date.now() + helpTime.remaining_seconds * 1000 : null;
    paint();
  }

  // 요청을 보내고 응답 본문을 돌려준다. 로그인이 풀렸으면 로그인 화면으로 보내고 null 을 돌려준다.
  async function request(target, method, body, failMessage) {
    error.textContent = "";
    for (const b of node.querySelectorAll("button, input")) b.disabled = true;
    try {
      let res;
      try {
        res = await fetch(target, {
          method,
          headers: body ? { "Content-Type": "application/json" } : undefined,
          body: body ? JSON.stringify(body) : undefined,
        });
      } catch {
        throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요. (start.bat)");
      }
      if (res.status === 401) {
        location.href = "/teacher.html";
        return null;
      }
      if (!res.ok) throw new Error(res.status === 422 ? "1분에서 180분 사이로 입력해 주세요." : failMessage);
      return await res.json();
    } catch (err) {
      error.textContent = err.message;
      return null;
    } finally {
      for (const b of node.querySelectorAll("button, input")) b.disabled = false;
    }
  }

  async function send(method, body) {
    const helpTime = await request(url, method, body, "도움 시간을 바꾸지 못했어요. 다시 시도해 주세요.");
    if (helpTime) update(helpTime);
  }

  function setHideNames(hidden) {
    hideNames.checked = Boolean(hidden);
  }

  hideNames.addEventListener("change", async () => {
    const wanted = hideNames.checked;
    const cls = await request(classUrl, "PATCH", { hide_names: wanted }, "설정을 바꾸지 못했어요. 다시 시도해 주세요.");
    setHideNames(cls ? cls.hide_names : !wanted); // 실패했으면 원래대로 돌려놓는다.
  });

  openForm.addEventListener("submit", (event) => {
    event.preventDefault();
    send("POST", { minutes: Number(minutes.value) });
  });
  plus1.addEventListener("click", () => send("POST", { minutes: 1 }));
  plus5.addEventListener("click", () => send("POST", { minutes: 5 }));
  endNow.addEventListener("click", () => send("DELETE"));

  setInterval(() => {
    if (deadline !== null) paint();
  }, 500);
  paint();
  return { node, update, setHideNames };
}
