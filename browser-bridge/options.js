const DEFAULT_API_BASE = "http://192.168.199.180:18181/api/v1";
const fields = {
  apiBase: document.querySelector("#apiBase"),
  bridgeName: document.querySelector("#bridgeName"),
  bridgeId: document.querySelector("#bridgeId"),
  bridgeToken: document.querySelector("#bridgeToken"),
};
const status = document.querySelector("#status");

async function load() {
  const stored = await chrome.storage.local.get(Object.keys(fields));
  fields.apiBase.value = stored.apiBase || DEFAULT_API_BASE;
  fields.bridgeName.value = stored.bridgeName || "本机 Chrome";
  fields.bridgeId.value = stored.bridgeId || "";
  fields.bridgeToken.value = stored.bridgeToken || "";
}

function validate(values) {
  const parsed = new URL(values.apiBase);
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("API 地址必须使用 HTTP 或 HTTPS");
  if (!/^[A-Za-z0-9._-]{3,80}$/.test(values.bridgeId)) throw new Error("代理 ID 格式无效");
  if (values.bridgeToken.length < 32) throw new Error("桥接令牌至少需要 32 个字符");
}

document.querySelector("#save").addEventListener("click", async () => {
  status.textContent = "正在保存并测试…";
  try {
    const values = Object.fromEntries(
      Object.entries(fields).map(([key, element]) => [key, element.value.trim()]),
    );
    values.apiBase = values.apiBase.replace(/\/$/, "");
    validate(values);
    await chrome.storage.local.set(values);
    await chrome.runtime.sendMessage({ kind: "POLL_NOW" });
    const latest = await chrome.storage.local.get("lastStatus");
    status.textContent = latest.lastStatus?.message || "配置已保存";
  } catch (error) {
    status.textContent = error instanceof Error ? error.message : "保存失败";
  }
});

void load();
