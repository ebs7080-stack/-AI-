"use strict";

const $ = (id) => document.getElementById(id);

const MAX_STEPS = 6;
const NEW_STEP_LABELS = ["관찰", "의심 지점", "힌트", "추가 힌트"];

// 서버에서 온 값과 교사가 쓴 문구는 항상 textContent / value 로 넣는다. innerHTML 은 쓰지 않는다.
function el(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(opts)) {
    if (value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

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
  let data = null;
  try {
    data = await res.json();
  } catch {
    // 본문이 JSON 이 아니면 아래 오류 문구를 쓴다.
  }
  if (!res.ok) {
    throw new Error(
      typeof data?.detail === "string" ? data.detail : "단계 이름은 20자, 문구는 500자까지 쓸 수 있어요. 빈 칸이 없는지도 확인해 주세요."
    );
  }
  return data;
}

function sameSteps(a, b) {
  return a.length === b.length && a.every((s, i) => s.label === b[i].label && s.text === b[i].text);
}

function trimmed(steps) {
  return steps.map((s) => ({ label: s.label.trim(), text: s.text.trim() }));
}

/* ---------- 오류 하나의 편집기 ---------- */

function caseCard(initial) {
  let saved = initial; // 서버에 저장된 상태
  let draft = initial.steps.map((s) => ({ ...s })); // 화면에서 고치는 중인 문구

  const badge = el("span", { class: "badge" });
  const summary = el("summary", {}, el("span", { class: "hint-case-title", text: saved.label }), badge);
  const stepsBox = el("div", { class: "hint-steps" });
  const addButton = el("button", { class: "btn btn-secondary btn-sm", type: "button", text: "+ 단계 추가" });
  const defaultButton = el("button", { class: "btn btn-secondary btn-sm", type: "button", text: "기본 문구 불러오기" });
  const saveButton = el("button", { class: "btn btn-primary btn-sm", type: "submit", text: "저장" });
  const status = el("p", { class: "hint-status muted small", role: "status" });
  const error = el("p", { class: "error", role: "alert" });

  const places = saved.placeholders.length
    ? el(
        "p",
        { class: "muted small hint-places" },
        "쓸 수 있는 자리: ",
        ...saved.placeholders.flatMap((p, i) => [i ? " · " : "", el("code", { text: `{${p.name}}` }), ` ${p.meaning}`])
      )
    : "";

  const form = el(
    "form",
    { class: "hint-form" },
    places,
    stepsBox,
    el("div", { class: "hint-actions" }, addButton, defaultButton, saveButton),
    status,
    error
  );
  const card = el("details", { class: "card hint-case", "data-case-id": saved.id }, summary, form);

  function paintState() {
    const dirty = !sameSteps(trimmed(draft), saved.steps);
    badge.className = saved.customized ? "badge amber" : "badge";
    badge.textContent = saved.customized ? "고쳐 씀" : "기본 문구";
    saveButton.disabled = !dirty;
    defaultButton.disabled = sameSteps(trimmed(draft), saved.default_steps);
    addButton.disabled = draft.length >= MAX_STEPS;
    if (dirty) status.textContent = "저장하지 않은 변경이 있어요.";
    else if (status.textContent === "저장하지 않은 변경이 있어요.") status.textContent = "";
  }

  function paintSteps() {
    stepsBox.replaceChildren(
      ...draft.map((step, i) => {
        const labelId = `hint-${saved.id}-${i}-label`;
        const textId = `hint-${saved.id}-${i}-text`;
        const label = el("input", { id: labelId, type: "text", maxlength: "20", required: "" });
        label.value = step.label;
        label.addEventListener("input", () => {
          step.label = label.value;
          paintState();
        });
        const text = el("textarea", { id: textId, class: "hint-text", maxlength: "500", rows: "3", required: "" });
        text.value = step.text;
        text.addEventListener("input", () => {
          step.text = text.value;
          paintState();
        });
        const remove = el("button", {
          class: "btn btn-secondary btn-sm",
          type: "button",
          text: "삭제",
          "aria-label": `${i + 1}단계 삭제`,
          disabled: draft.length === 1 ? "" : null, // 단계는 하나 이상 있어야 한다.
        });
        remove.addEventListener("click", () => {
          draft.splice(i, 1);
          paintSteps();
        });
        return el(
          "div",
          { class: "hint-step" },
          el("span", { class: "step-num", "aria-hidden": "true", text: String(i + 1) }),
          el(
            "div",
            { class: "hint-step-fields" },
            el("label", { for: labelId, text: `${i + 1}단계 이름` }),
            label,
            el("label", { for: textId, text: "학생에게 보여 줄 문구" }),
            text
          ),
          remove
        );
      })
    );
    paintState();
  }

  addButton.addEventListener("click", () => {
    draft.push({ label: NEW_STEP_LABELS[Math.min(draft.length, NEW_STEP_LABELS.length - 1)], text: "" });
    paintSteps();
    stepsBox.querySelector(".hint-step:last-child textarea").focus();
  });

  // 기본 문구는 편집 칸에만 불러온다. 저장을 눌러야 적용되므로 실수로 눌러도 고친 문구가 사라지지 않는다.
  defaultButton.addEventListener("click", () => {
    draft = saved.default_steps.map((s) => ({ ...s }));
    paintSteps();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    const steps = trimmed(draft);
    saveButton.disabled = true;
    try {
      // 기본 문구와 똑같이 저장하려는 것이면 고친 기록을 지워 기본값으로 되돌린다.
      saved = sameSteps(steps, saved.default_steps)
        ? await api(`/api/teacher/hints/${saved.id}`, { method: "DELETE" })
        : await api(`/api/teacher/hints/${saved.id}`, { method: "PUT", body: { steps } });
      draft = saved.steps.map((s) => ({ ...s }));
      paintSteps();
      status.textContent = saved.customized
        ? "저장했어요. 다음 제출부터 이 문구가 쓰여요."
        : "기본 문구로 되돌렸어요.";
    } catch (err) {
      error.textContent = err.message;
      paintState();
    }
  });

  paintSteps();
  return card;
}

/* ---------- 불러오기 ---------- */

function render(cases) {
  const groups = new Map(); // 분류 -> 오류들 (서버가 준 순서 그대로)
  for (const c of cases) {
    if (!groups.has(c.category)) groups.set(c.category, []);
    groups.get(c.category).push(c);
  }
  $("hint-list").replaceChildren(
    ...[...groups].map(([category, items]) =>
      el("section", { class: "hint-group" }, el("h2", { text: category }), ...items.map(caseCard))
    )
  );
}

$("logout").addEventListener("click", async () => {
  await fetch("/api/teacher/logout", { method: "POST" });
  location.href = "/teacher.html";
});

(async function boot() {
  try {
    const me = await api("/api/teacher/me");
    $("who").textContent = `${me.name} 선생님`;
    render(await api("/api/teacher/hints"));
  } catch (err) {
    $("page-error").textContent = err.message;
  }
})();
