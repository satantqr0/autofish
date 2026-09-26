import { collectMarketTask } from "./market.js";
const BRIDGE_VERSION = "0.6.0";
const DEFAULT_API_BASE = "http://192.168.199.180:18181/api/v1";
const CAPABILITIES = [
  "OPEN_MODULE",
  "CAPTURE_OVERVIEW",
  "CAPTURE_MARKET_PRICES",
  "CAPTURE_PRODUCT_METRICS",
  "CAPTURE_CONVERSATIONS",
  "PREFILL_PRODUCT",
  "PUBLISH_PRODUCT",
  "SEND_REPLY",
];
const SELLER_URL = "https://seller.goofish.com/";
const SELLER_CHAT_URL = "https://seller.goofish.com/?site=COMMONPRO#/im";
let polling = false;

function defaultBridgeId() {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return `chrome-${[...bytes].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

async function getConfig() {
  const stored = await chrome.storage.local.get([
    "apiBase",
    "bridgeToken",
    "bridgeId",
    "bridgeName",
  ]);
  if (!stored.bridgeId) {
    stored.bridgeId = defaultBridgeId();
    await chrome.storage.local.set({ bridgeId: stored.bridgeId });
  }
  return {
    apiBase: (stored.apiBase || DEFAULT_API_BASE).replace(/\/$/, ""),
    bridgeToken: stored.bridgeToken || "",
    bridgeId: stored.bridgeId,
    bridgeName: stored.bridgeName || "本机 Chrome",
  };
}

async function sellerTabs() {
  return chrome.tabs.query({ url: "https://seller.goofish.com/*" });
}

async function currentSellerUrl() {
  const tabs = await sellerTabs();
  return tabs[0]?.url?.startsWith(SELLER_URL) ? tabs[0].url : null;
}

async function apiRequest(path, { method = "POST", body } = {}) {
  const config = await getConfig();
  if (!config.bridgeToken) throw new Error("尚未配置浏览器桥接令牌");
  const response = await fetch(`${config.apiBase}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-AutoFish-Bridge-Token": config.bridgeToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch {
      // Keep the status fallback for non-JSON errors.
    }
    throw new Error(detail);
  }
  return response.json();
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function downloadTaskAssets(task) {
  const config = await getConfig();
  const descriptors = Array.isArray(task.payload.images) ? task.payload.images : [];
  if (descriptors.length < 1 || descriptors.length > 9) {
    throw new Error("发布任务的精品图数量必须为 1–9 张");
  }
  const totalBytes = descriptors.reduce((sum, item) => sum + Number(item.bytes || 0), 0);
  if (totalBytes > 40 * 1024 * 1024) throw new Error("发布图片总大小超过 40MB");
  const images = [];
  for (const descriptor of descriptors) {
    const response = await fetch(
      `${config.apiBase}/xianyu/browser-bridge/agent/tasks/${task.id}/assets/${descriptor.ordinal}`,
      {
        method: "GET",
        headers: {
          "X-AutoFish-Bridge-Token": config.bridgeToken,
          "X-AutoFish-Bridge-Id": config.bridgeId,
        },
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error(`精品图下载失败 (${response.status})`);
    const buffer = await response.arrayBuffer();
    const digest = await sha256Hex(buffer);
    const expected = String(descriptor.sha256 || "");
    const serverDigest = response.headers.get("X-AutoFish-Asset-SHA256") || "";
    if (digest !== expected || serverDigest !== expected) {
      throw new Error(`精品图哈希校验失败：${descriptor.filename}`);
    }
    images.push({
      filename: descriptor.filename,
      mime_type: descriptor.mime_type,
      sha256: digest,
      base64: bytesToBase64(new Uint8Array(buffer)),
    });
  }
  return images;
}

async function reportProgress(task, phase, details = {}) {
  const config = await getConfig();
  return apiRequest(`/xianyu/browser-bridge/agent/tasks/${task.id}/progress`, {
    body: {
      bridge_id: config.bridgeId,
      phase,
      current_url: await currentSellerUrl(),
      form_fingerprint: details.form_fingerprint || null,
    },
  });
}

async function agentState(lastError = null) {
  const config = await getConfig();
  return {
    bridge_id: config.bridgeId,
    name: config.bridgeName,
    version: BRIDGE_VERSION,
    capabilities: CAPABILITIES,
    current_url: await currentSellerUrl(),
    last_error: lastError,
  };
}

async function heartbeat(lastError = null) {
  return apiRequest("/xianyu/browser-bridge/agent/heartbeat", {
    body: await agentState(lastError),
  });
}

async function findOrOpenSellerTab(activate, preferChat = false) {
  const tabs = await sellerTabs();
  const ranked = [...tabs].sort((left, right) => {
    const activeDifference = Number(Boolean(right.active)) - Number(Boolean(left.active));
    if (activeDifference !== 0) return activeDifference;
    return Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0);
  });
  const existing = preferChat
    ? ranked.find((tab) => tab.url?.includes("#/im")) || ranked[0]
    : ranked[0];
  if (existing) {
    if (activate) await chrome.tabs.update(existing.id, { active: true });
    return existing;
  }
  return chrome.tabs.create({ url: SELLER_URL, active: activate });
}

async function waitForTabReady(tabId, timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error("闲鱼商家工作台加载超时");
}

async function openSellerChat(tabId, activate) {
  const current = await chrome.tabs.get(tabId);
  if (!current.url?.includes("#/im")) {
    await chrome.tabs.update(tabId, { url: SELLER_CHAT_URL, active: activate });
  } else if (activate) {
    await chrome.tabs.update(tabId, { active: true });
  }
  const started = Date.now();
  while (Date.now() - started < 15000) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete" && tab.url?.includes("#/im")) {
      await new Promise((resolve) => setTimeout(resolve, 2200));
      return tab;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("闲鱼消息页加载超时");
}

async function sendToContent(tabId, message) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await Promise.race([
        chrome.tabs.sendMessage(tabId, message),
        new Promise((_, reject) => setTimeout(
          () => reject(new Error("闲鱼页面任务超过 90 秒未返回，已停止等待")),
          90000,
        )),
      ]);
    } catch (error) {
      const missingReceiver = String(error).includes("Receiving end does not exist");
      if (attempt === 0 && missingReceiver) {
        const tab = await chrome.tabs.get(tabId);
        if (!tab.url?.startsWith(SELLER_URL)) {
          throw new Error("拒绝向闲鱼卖家工作台之外的页面注入脚本");
        }
        await chrome.scripting.executeScript({
          target: { tabId },
          files: ["content.js"],
        });
        await new Promise((resolve) => setTimeout(resolve, 700));
        continue;
      }
      // A timeout or page-side failure may leave the original async listener
      // running. Retrying would start a duplicate publish/capture/reply action
      // and let the two executions interfere with each other.
      throw error;
    }
  }
  throw new Error("无法连接闲鱼页面脚本");
}

async function dispatchTrustedPublishClick(tabId, clickTarget) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.url?.startsWith(SELLER_URL)) {
    throw new Error("拒绝在闲鱼卖家工作台之外发送发布点击");
  }
  const x = Number(clickTarget?.x);
  const y = Number(clickTarget?.y);
  const viewportWidth = Number(clickTarget?.viewport_width);
  const viewportHeight = Number(clickTarget?.viewport_height);
  if (clickTarget?.button_text !== "发布"
    || !Number.isFinite(x)
    || !Number.isFinite(y)
    || !Number.isFinite(viewportWidth)
    || !Number.isFinite(viewportHeight)
    || x <= 0
    || y <= 0
    || x >= viewportWidth
    || y >= viewportHeight) {
    throw new Error("发布按钮坐标未通过允许列表校验");
  }

  const target = { tabId };
  let attached = false;
  let pressed = false;
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x,
      y,
      button: "none",
      buttons: 0,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mousePressed",
      x,
      y,
      button: "left",
      buttons: 1,
      clickCount: 1,
    });
    pressed = true;
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x,
      y,
      button: "left",
      buttons: 0,
      clickCount: 1,
    });
  } catch (error) {
    const wrapped = new Error(error instanceof Error ? error.message : "可信发布点击失败");
    wrapped.submissionAttempted = pressed;
    throw wrapped;
  } finally {
    if (attached) {
      try {
        await chrome.debugger.detach(target);
      } catch {
        // The target may already have navigated or closed after submission.
      }
    }
  }
}

async function dispatchTrustedReplyClick(tabId, clickTarget) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.url?.startsWith(SELLER_URL) || !tab.url.includes("#/im")) {
    throw new Error("拒绝在闲鱼消息页之外发送回复点击");
  }
  const x = Number(clickTarget?.x);
  const y = Number(clickTarget?.y);
  const viewportWidth = Number(clickTarget?.viewport_width);
  const viewportHeight = Number(clickTarget?.viewport_height);
  if (clickTarget?.button_text !== "发送"
    || !Number.isFinite(x)
    || !Number.isFinite(y)
    || !Number.isFinite(viewportWidth)
    || !Number.isFinite(viewportHeight)
    || x <= 0
    || y <= 0
    || x >= viewportWidth
    || y >= viewportHeight) {
    throw new Error("回复发送按钮坐标未通过允许列表校验");
  }

  const target = { tabId };
  let attached = false;
  let pressed = false;
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved", x, y, button: "none", buttons: 0,
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1,
    });
    pressed = true;
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1,
    });
  } catch (error) {
    const wrapped = new Error(error instanceof Error ? error.message : "可信回复点击失败");
    wrapped.submissionAttempted = pressed;
    throw wrapped;
  } finally {
    if (attached) {
      try {
        await chrome.debugger.detach(target);
      } catch {
        // The chat tab may have navigated or closed after submission.
      }
    }
  }
}

function publicProductFromUrl(value) {
  try {
    const parsed = new URL(value);
    if (parsed.hostname !== "www.goofish.com") return null;
    const productId = parsed.searchParams.get("id")
      || parsed.pathname.match(/(?:item|goods)\/(\d{6,20})/i)?.[1];
    if (!/^[0-9]{6,20}$/.test(productId || "")) return null;
    return { productId, publishedUrl: parsed.href };
  } catch {
    return null;
  }
}

async function executeTask(task) {
  const activate = ![
    "CAPTURE_OVERVIEW",
    "CAPTURE_PRODUCT_METRICS",
  ].includes(task.operation);
  const tab = await findOrOpenSellerTab(
    activate,
    ["CAPTURE_CONVERSATIONS", "SEND_REPLY"].includes(task.operation),
  );
  await waitForTabReady(tab.id);

  if (task.operation === "OPEN_MODULE") {
    return sendToContent(tab.id, {
      kind: "OPEN_MODULE",
      module: task.payload.module,
    });
  }
  if (task.operation === "CAPTURE_OVERVIEW") {
    return sendToContent(tab.id, { kind: "CAPTURE_OVERVIEW" });
  }
  if (task.operation === "CAPTURE_PRODUCT_METRICS") {
    const navigation = await sendToContent(tab.id, {
      kind: "OPEN_MODULE",
      module: "商品数据",
    });
    if (navigation.risk_detected || navigation.status === "MANUAL_REQUIRED") return navigation;
    await new Promise((resolve) => setTimeout(resolve, 2200));
    return sendToContent(tab.id, {
      kind: "CAPTURE_PRODUCT_METRICS",
      metric_date: task.payload.metric_date,
    });
  }
  if (task.operation === "CAPTURE_CONVERSATIONS") {
    await openSellerChat(tab.id, false);
    return sendToContent(tab.id, {
      kind: "CAPTURE_CONVERSATIONS",
      payload: task.payload,
    });
  }
  if (task.operation === "PREFILL_PRODUCT") {
    const navigation = await sendToContent(tab.id, {
      kind: "OPEN_MODULE",
      module: "商品发布",
    });
    if (navigation.risk_detected) return navigation;
    await new Promise((resolve) => setTimeout(resolve, 2200));
    return sendToContent(tab.id, {
      kind: "PREFILL_PRODUCT",
      payload: task.payload,
    });
  }
  if (task.operation === "PUBLISH_PRODUCT") {
    await reportProgress(task, "PREPARING");
    const navigation = await sendToContent(tab.id, {
      kind: "OPEN_MODULE",
      module: "商品发布",
    });
    if (navigation.risk_detected || navigation.status === "MANUAL_REQUIRED") return navigation;
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const images = await downloadTaskAssets(task);
    const prepared = await sendToContent(tab.id, {
      kind: "PREPARE_PUBLISH",
      payload: task.payload,
      images,
    });
    if (prepared.status !== "READY_TO_SUBMIT") return prepared;
    await reportProgress(task, "READY_TO_SUBMIT", prepared);
    const commitReady = await sendToContent(tab.id, {
      kind: "PREPARE_TRUSTED_COMMIT",
      form_fingerprint: prepared.form_fingerprint,
    });
    if (commitReady.status !== "READY_FOR_TRUSTED_CLICK") return commitReady;
    await reportProgress(task, "SUBMITTING", prepared);
    const observation = sendToContent(tab.id, {
      kind: "OBSERVE_PUBLISH_RESULT",
      form_fingerprint: prepared.form_fingerprint,
    });
    await new Promise((resolve) => setTimeout(resolve, 120));
    try {
      await dispatchTrustedPublishClick(tab.id, commitReady.click_target);
    } catch (error) {
      void observation.catch(() => null);
      throw error;
    }
    try {
      return await observation;
    } catch (error) {
      const currentTab = await chrome.tabs.get(tab.id).catch(() => null);
      const published = publicProductFromUrl(currentTab?.url || "");
      if (published) {
        return {
          status: "SUCCEEDED",
          current_url: currentTab.url,
          page_title: currentTab.title || null,
          module: "商品发布",
          submission_attempted: true,
          uploaded_images: prepared.uploaded_images || 0,
          form_fingerprint: prepared.form_fingerprint,
          external_product_id: published.productId,
          published_url: published.publishedUrl,
          success_evidence: "ITEM_URL_AFTER_NAVIGATION",
          risk_detected: false,
        };
      }
      const wrapped = new Error(error instanceof Error ? error.message : "发布提交后页面连接中断");
      wrapped.submissionAttempted = true;
      throw wrapped;
    }
  }
  if (task.operation === "SEND_REPLY") {
    await reportProgress(task, "PREPARING");
    await openSellerChat(tab.id, true);
    const prepared = await sendToContent(tab.id, {
      kind: "PREPARE_REPLY",
      payload: task.payload,
    });
    if (prepared.status !== "READY_TO_SUBMIT") return prepared;
    await reportProgress(task, "READY_TO_SUBMIT", prepared);
    const commitReady = await sendToContent(tab.id, {
      kind: "PREPARE_TRUSTED_REPLY_COMMIT",
      form_fingerprint: prepared.form_fingerprint,
    });
    if (commitReady.status !== "READY_FOR_TRUSTED_CLICK") return commitReady;
    await reportProgress(task, "SUBMITTING", prepared);
    const observation = sendToContent(tab.id, {
      kind: "OBSERVE_REPLY_RESULT",
      form_fingerprint: prepared.form_fingerprint,
    });
    await new Promise((resolve) => setTimeout(resolve, 120));
    try {
      await dispatchTrustedReplyClick(tab.id, commitReady.click_target);
    } catch (error) {
      void observation.catch(() => null);
      throw error;
    }
    return observation;
  }
  throw new Error("服务端下发了不受支持的操作");
}

async function completeTask(task, result) {
  const config = await getConfig();
  const normalized = {
    bridge_id: config.bridgeId,
    status: result.status,
    current_url: result.current_url || null,
    page_title: result.page_title || null,
    module: result.module || null,
    modules: result.modules || [],
    filled_fields: result.filled_fields || [],
    missing_fields: result.missing_fields || [],
    risk_detected: Boolean(result.risk_detected),
    submission_attempted: Boolean(result.submission_attempted),
    uploaded_images: Number(result.uploaded_images || 0),
    form_fingerprint: result.form_fingerprint || null,
    external_product_id: result.external_product_id || null,
    published_url: result.published_url || null,
    success_evidence: result.success_evidence || null,
    metric_date: result.metric_date || null,
    metrics: result.metrics || [],
    metrics_duplicate_rows_skipped: Number(result.metrics_duplicate_rows_skipped || 0),
    metrics_pages: Number(result.metrics_pages || 0),
    captured_at: result.captured_at || null,
    conversations: result.conversations || [],
    external_conversation_id: result.external_conversation_id || null,
    source_message_id: result.source_message_id || null,
    sent_message_id: result.sent_message_id || null,
    reply_sent: Boolean(result.reply_sent),
    manual_reason: result.manual_reason || null,
    error_code: result.error_code || null,
  };
  return apiRequest(`/xianyu/browser-bridge/agent/tasks/${task.id}/result`, {
    body: normalized,
  });
}

async function pollOnce() {
  if (polling) return;
  polling = true;
  let status = { ok: true, message: "浏览器代理在线", at: new Date().toISOString() };
  try {
    const config = await getConfig();
    if (!config.bridgeToken) {
      status = { ok: false, message: "请先配置浏览器桥接令牌", at: new Date().toISOString() };
      return;
    }
    const response = await apiRequest("/xianyu/browser-bridge/agent/claim", {
      body: await agentState(),
    });
    if (!response.task) {
      await heartbeat();
      const market = await apiRequest("/market-prices/agent/claim", { body: {bridge_id: config.bridgeId} });
      if (market.task) {
        const result = await collectMarketTask(market.task, config.bridgeId);
        await apiRequest("/market-prices/agent/result", {body:result});
        status = {ok:result.status === "OK",message:`行情 ${result.query}：${result.status}（${result.items.length} 件）`,at:new Date().toISOString()};
      }
      return;
    }
    try {
      const result = await executeTask(response.task);
      await completeTask(response.task, result);
      status = {
        ok: result.status !== "FAILED",
        message: `任务 #${response.task.id}：${result.manual_reason || result.status}`,
        at: new Date().toISOString(),
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : "浏览器任务执行失败";
      const ambiguousCode = response.task.operation === "SEND_REPLY"
        ? "REPLY_RESULT_AMBIGUOUS"
        : "PUBLISH_RESULT_AMBIGUOUS";
      await completeTask(response.task, {
        status: error?.submissionAttempted ? "MANUAL_REQUIRED" : "FAILED",
        current_url: await currentSellerUrl(),
        submission_attempted: Boolean(error?.submissionAttempted),
        error_code: error?.submissionAttempted ? ambiguousCode : "BRIDGE_EXECUTION_ERROR",
        manual_reason: message,
      });
      status = { ok: false, message, at: new Date().toISOString() };
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "浏览器代理同步失败";
    status = { ok: false, message, at: new Date().toISOString() };
    try {
      await heartbeat(message);
    } catch {
      // The popup will expose the original connection error.
    }
  } finally {
    polling = false;
    await chrome.storage.local.set({ lastStatus: status });
    await chrome.action.setBadgeText({ text: status.ok ? "" : "!" });
    if (!status.ok) await chrome.action.setBadgeBackgroundColor({ color: "#b45309" });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("autofish-browser-bridge", { periodInMinutes: 0.5 });
  void pollOnce();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("autofish-browser-bridge", { periodInMinutes: 0.5 });
  void pollOnce();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "autofish-browser-bridge") void pollOnce();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.kind !== "POLL_NOW") return false;
  void pollOnce().then(() => sendResponse({ ok: true }));
  return true;
});
