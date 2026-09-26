const status = document.querySelector("#status");

async function refresh() {
  const stored = await chrome.storage.local.get("lastStatus");
  status.textContent = stored.lastStatus?.message || "尚未连接 AutoFish";
}

document.querySelector("#sync").addEventListener("click", async () => {
  status.textContent = "正在领取任务…";
  await chrome.runtime.sendMessage({ kind: "POLL_NOW" });
  await refresh();
});

document.querySelector("#openSeller").addEventListener("click", async () => {
  await chrome.tabs.create({ url: "https://seller.goofish.com/" });
});

document.querySelector("#settings").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});

void refresh();
