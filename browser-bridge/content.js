const ALLOWED_MODULES = new Set([
  "消息",
  "数据总览",
  "商品数据",
  "商品发布",
  "商品管理",
  "订单管理",
  "退款管理",
  "评价管理",
  "退货地址",
]);
const RISK_PATTERNS = [
  "验证码",
  "滑块",
  "安全验证",
  "异常操作",
  "账号风险",
  "请完成验证",
  "访问受限",
];
const FORBIDDEN_ACTIONS = [
  "确认发布",
  "立即发布",
  "确认发货",
  "提交退款",
  "取消订单",
  "删除商品",
  "确认下架",
  "支付",
  "充值",
];
const MODULE_GROUPS = {
  "数据总览": "数据",
  "商品数据": "数据",
  "商品发布": "商品",
  "商品管理": "商品",
  "订单管理": "交易",
  "退款管理": "交易",
  "评价管理": "交易",
  "退货地址": "交易",
};
const MODULE_HASH_FALLBACKS = {
  "消息": "#/im",
  "商品数据": "#/seller-data/commodity",
  "商品发布": "#/seller-item/publish",
};
const LOGIN_PATTERNS = ["请登录", "登录后继续", "扫码登录", "短信登录"];
const PUBLISH_BUTTON_TEXTS = new Set(["立即发布", "确认发布", "发布商品", "发布"]);
let preparedPublication = null;
let preparedReply = null;
let activeConversationCapture = null;

function isVisible(element) {
  if (!(element instanceof HTMLElement)) return false;
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
}

function pageState() {
  return {
    current_url: location.href,
    page_title: document.title,
  };
}

function detectRisk() {
  const text = document.body?.innerText || "";
  return RISK_PATTERNS.find((pattern) => text.includes(pattern)) || null;
}

function detectLoginRequired() {
  const text = document.body?.innerText || "";
  return LOGIN_PATTERNS.find((pattern) => text.includes(pattern)) || null;
}

function findVisibleText(text) {
  const candidates = [...document.querySelectorAll("body *")];
  return candidates.find((element) => {
    if (!isVisible(element) || element.children.length > 0) return false;
    return (element.textContent || "").trim() === text;
  });
}

function isHitTarget(element) {
  if (!element || !isVisible(element)) return false;
  const rect = element.getBoundingClientRect();
  const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
  return hit === element || element.contains(hit);
}

function dispatchClick(element) {
  element.dispatchEvent(new MouseEvent("click", {
    bubbles: true,
    cancelable: true,
    view: window,
  }));
}

async function openModule(module) {
  if (!ALLOWED_MODULES.has(module)) throw new Error("拒绝打开未列入允许名单的模块");
  if (FORBIDDEN_ACTIONS.includes(module)) throw new Error("拒绝执行最终提交动作");
  const risk = detectRisk();
  if (risk) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module,
      risk_detected: true,
      manual_reason: `页面出现“${risk}”，代理已停止`,
    };
  }
  const fallbackHash = MODULE_HASH_FALLBACKS[module];
  let target = findVisibleText(module);
  if (!isHitTarget(target)) {
    const group = findVisibleText(MODULE_GROUPS[module]);
    if (group && isHitTarget(group)) {
      dispatchClick(group);
      await new Promise((resolve) => setTimeout(resolve, 450));
      target = findVisibleText(module);
    }
  }
  if ((!target || !isHitTarget(target)) && fallbackHash) {
    // Seller workbench menu hit-testing can be occluded by an adjacent menu
    // layer even though the target text is visible. The allowlisted same-origin
    // hash is deterministic and performs navigation only.
    location.hash = fallbackHash;
    await new Promise((resolve) => setTimeout(resolve, 1800));
    const navigationRisk = detectRisk();
    if (navigationRisk) {
      return {
        ...pageState(),
        status: "MANUAL_REQUIRED",
        module,
        risk_detected: true,
        manual_reason: `页面出现“${navigationRisk}”，代理已停止`,
      };
    }
    return {
      ...pageState(),
      status: "SUCCEEDED",
      module,
      risk_detected: false,
    };
  }
  if (!target || !isHitTarget(target)) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module,
      risk_detected: false,
      manual_reason: `未找到“${module}”菜单，请人工打开对应页面`,
    };
  }
  const beforeUrl = location.href;
  dispatchClick(target);
  await new Promise((resolve) => setTimeout(resolve, 900));
  if (fallbackHash && location.href === beforeUrl) {
    // The current seller workbench ignores extension-dispatched synthetic menu
    // clicks. A same-origin hash route is safe for navigation-only modules and
    // does not submit, publish, delete, pay or mutate any seller data.
    location.hash = fallbackHash;
    await new Promise((resolve) => setTimeout(resolve, 1400));
  }
  return {
    ...pageState(),
    status: "SUCCEEDED",
    module,
    risk_detected: false,
  };
}

function fieldContext(element) {
  const own = [
    element.getAttribute("placeholder"),
    element.getAttribute("data-placeholder"),
    element.getAttribute("aria-label"),
    element.getAttribute("name"),
    element.getAttribute("id"),
  ].filter(Boolean).join(" ");
  const labels = element.labels ? [...element.labels].map((label) => label.textContent || "").join(" ") : "";
  const parent = element.closest("label, [class*='form'], [class*='field'], [class*='item']");
  return `${own} ${labels} ${parent?.textContent || ""}`.trim().slice(0, 500).toLowerCase();
}

function candidateFields() {
  return [...document.querySelectorAll("input, textarea, [contenteditable='true']")]
    .filter((element) => isVisible(element) && !element.disabled && element.type !== "file");
}

function findField(keywords, excluded = []) {
  let best = null;
  let bestScore = 0;
  for (const element of candidateFields()) {
    const context = fieldContext(element);
    if (excluded.some((word) => context.includes(word))) continue;
    const score = keywords.reduce((total, word) => total + (context.includes(word) ? 1 : 0), 0);
    if (score > bestScore) {
      best = element;
      bestScore = score;
    }
  }
  return bestScore > 0 ? best : null;
}

function findLabeledField(labels) {
  const label = [...document.querySelectorAll("label")].find((element) => {
    if (!isVisible(element)) return false;
    const text = normalizedText(element).replace(/[＊*：:]$/g, "");
    return labels.includes(text);
  });
  const formItem = label?.closest(".ant-form-item, [class*='form-item'], [class*='formItem']");
  if (!formItem) return null;
  return [...formItem.querySelectorAll("input, textarea, [contenteditable='true']")]
    .find((element) => isVisible(element) && !element.disabled && element.type !== "file") || null;
}

function setFieldValue(element, value) {
  if (element.getAttribute("contenteditable") === "true") {
    element.focus();
    element.textContent = String(value);
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: String(value) }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return;
  }
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(element, String(value));
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

async function waitForFields(timeoutMs = 10000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (candidateFields().length > 0) return;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

function fieldValue(element) {
  if (!element) return "";
  if (element.getAttribute("contenteditable") === "true") return (element.textContent || "").trim();
  return String(element.value || "").trim();
}

function normalizedText(element) {
  return (element?.textContent || "").replace(/\s+/g, "").trim();
}

function findPublishButton() {
  return [...document.querySelectorAll("button, [role='button']")].find((element) => {
    if (!isVisible(element) || !isHitTarget(element)) return false;
    if (!PUBLISH_BUTTON_TEXTS.has(normalizedText(element))) return false;
    return !element.disabled && element.getAttribute("aria-disabled") !== "true";
  }) || null;
}

function visibleFormError() {
  const selectors = [
    ".ant-form-item-explain-error",
    ".ant-message-error",
    ".ant-notification-notice-error",
    "[role='alert']",
  ];
  for (const element of document.querySelectorAll(selectors.join(","))) {
    if (!isVisible(element)) continue;
    const message = (element.textContent || "").replace(/\s+/g, " ").trim();
    if (message) return message.slice(0, 240);
  }
  return null;
}

async function waitForPublishButton(timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const risk = detectRisk();
    if (risk) return null;
    const button = findPublishButton();
    if (button) return button;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return null;
}

function base64File(image) {
  const binary = atob(image.base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new File([bytes], image.filename, { type: image.mime_type || "image/png" });
}

async function uploadImages(images) {
  if (!Array.isArray(images) || images.length < 1 || images.length > 9) return null;
  const inputs = [...document.querySelectorAll("input[type='file']")]
    .filter((element) => !element.disabled && (!element.accept || element.accept.includes("image")));
  const input = inputs.find((element) => element.multiple) || inputs[0];
  if (!input) return null;
  const container = input.closest("[class*='container']") || input.parentElement?.parentElement;
  const previewCount = () => container
    ? container.querySelectorAll("[class*='imgList'] img").length
    : 0;
  const beforePreviews = previewCount();
  const transfer = new DataTransfer();
  images.forEach((image) => transfer.items.add(base64File(image)));
  input.files = transfer.files;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  const started = Date.now();
  while (Date.now() - started < 30000) {
    const uploaded = previewCount() - beforePreviews;
    if (uploaded >= images.length) return images.length;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return null;
}

function categorySearchTerms(category) {
  const leaf = String(category || "")
    .split(/[/>]/)
    .map((value) => value.trim())
    .filter(Boolean)
    .pop() || "";
  const primary = leaf.split(/[、，,]/).map((value) => value.trim()).find(Boolean) || leaf;
  return [...new Set([primary, leaf].filter((value) => value.length >= 2))];
}

function visibleCategoryOption(term) {
  const normalizedTerm = term.replace(/\s+/g, "");
  return [...document.querySelectorAll(
    "[role='option'], li, [class*='option'], [class*='cascader-menu-item'], [class*='dropdown'] button",
  )]
    .filter((element) => isVisible(element) && isHitTarget(element))
    .map((element) => ({ element, text: normalizedText(element) }))
    .filter(({ text }) => text.length >= 2
      && (text.includes(normalizedTerm) || normalizedTerm.includes(text)))
    .sort((left, right) => left.text.length - right.text.length)[0]?.element || null;
}

function selectedCategoryText(categoryField) {
  if (!categoryField) return "";
  const selectRoot = categoryField.closest(".ant-select") || categoryField.parentElement;
  const selected = selectRoot?.querySelector(
    ".ant-select-selection-item, [class*='selection-item'], [class*='selected-value']",
  );
  if (selected && isVisible(selected)) return normalizedText(selected);
  const selector = selectRoot?.querySelector(".ant-select-selector, [class*='select-selector']");
  return selector && isVisible(selector) ? normalizedText(selector) : "";
}

function selectedCategoryMatches(categoryField, terms) {
  const selected = selectedCategoryText(categoryField).replace(/\s+/g, "");
  if (!selected || /请选择|选择类目|商品类目|商品分类/.test(selected)) return false;
  return terms.some((term) => {
    const normalizedTerm = term.replace(/\s+/g, "");
    return selected.includes(normalizedTerm) || normalizedTerm.includes(selected);
  });
}

async function trySelectCategory(category) {
  if (!category) return false;
  const terms = categorySearchTerms(category);
  if (terms.length === 0) return false;
  const categoryField = findLabeledField(["分类", "类目", "商品分类"])
    || findField(["类目", "分类", "商品分类"], ["搜索商品"]);
  if (categoryField) {
    if (selectedCategoryMatches(categoryField, terms)) return true;
    for (const term of terms) {
      setFieldValue(categoryField, term);
      await new Promise((resolve) => setTimeout(resolve, 1200));
      const option = visibleCategoryOption(term);
      if (option) {
        dispatchClick(option);
        await new Promise((resolve) => setTimeout(resolve, 800));
        if (selectedCategoryMatches(categoryField, terms)) return true;
      }
    }
  }
  const trigger = [...document.querySelectorAll("button, [role='button'], input")].find((element) => {
    const context = fieldContext(element);
    return isVisible(element) && isHitTarget(element) && ["选择类目", "商品类目", "选择分类"]
      .some((word) => context.includes(word));
  });
  if (!trigger) return false;
  dispatchClick(trigger);
  await new Promise((resolve) => setTimeout(resolve, 700));
  for (const term of terms) {
    const option = visibleCategoryOption(term);
    if (option) {
      dispatchClick(option);
      await new Promise((resolve) => setTimeout(resolve, 800));
      if (selectedCategoryMatches(categoryField, terms)) return true;
    }
  }
  return false;
}

async function fingerprint(payload, images) {
  const source = JSON.stringify({
    draft_id: payload.draft_id,
    draft_input_hash: payload.draft_input_hash,
    title: payload.title,
    description: payload.description,
    price: String(payload.price),
    category: payload.category || null,
    images: images.map((item) => item.sha256),
  });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function preparePublish(payload, images) {
  preparedPublication = null;
  const login = detectLoginRequired();
  const risk = detectRisk();
  if (login || risk) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品发布",
      risk_detected: Boolean(risk),
      manual_reason: login ? `闲鱼登录状态失效（${login}）` : `页面出现“${risk}”，代理已停止`,
    };
  }
  await waitForFields(12000);
  const filledFields = [];
  const missingFields = [];
  const titleField = findField(["商品标题", "宝贝标题", "标题"], ["描述", "搜索"]);
  const descriptionField = findField(
    ["描述", "商品描述", "宝贝描述", "详情", "货品来源"],
    ["搜索"],
  );
  const combinedDescription = [payload.title, payload.description]
    .filter((value) => typeof value === "string" && value.trim())
    .map((value) => value.trim())
    .join("\n");
  if (titleField && titleField !== descriptionField) {
    setFieldValue(titleField, payload.title);
    filledFields.push("title");
    if (descriptionField) {
      setFieldValue(descriptionField, payload.description);
      filledFields.push("description");
    } else {
      missingFields.push("description");
    }
  } else if (descriptionField) {
    setFieldValue(descriptionField, combinedDescription);
    filledFields.push("title", "description");
  } else {
    missingFields.push("title", "description");
  }

  const priceField = candidateFields().find((element) =>
    element instanceof HTMLInputElement
    && (element.getAttribute("placeholder") === "0.00"
      || fieldContext(element).includes("价格")),
  );
  if (priceField) {
    setFieldValue(priceField, payload.price);
    filledFields.push("price");
  } else {
    missingFields.push("price");
  }

  const uploadedImages = await uploadImages(images);
  if (uploadedImages === images.length) filledFields.push("images");
  else missingFields.push("images");

  const categorySelected = await trySelectCategory(payload.category);
  if (categorySelected) filledFields.push("category");
  else if (payload.category) missingFields.push("category");
  await new Promise((resolve) => setTimeout(resolve, 1200));

  const readbackDescription = fieldValue(descriptionField);
  const readbackTitle = titleField && titleField !== descriptionField ? fieldValue(titleField) : readbackDescription;
  const readbackPrice = fieldValue(priceField).replace(/,/g, "");
  if (!readbackTitle.includes(String(payload.title || "").trim())) missingFields.push("title");
  if (!readbackDescription.includes(String(payload.description || "").trim())) missingFields.push("description");
  if (Number(readbackPrice) !== Number(payload.price)) missingFields.push("price");

  const button = await waitForPublishButton();
  const uniqueMissing = [...new Set(missingFields)];
  if (uniqueMissing.length > 0 || !button) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品发布",
      filled_fields: [...new Set(filledFields)],
      missing_fields: uniqueMissing,
      uploaded_images: uploadedImages || 0,
      risk_detected: false,
      manual_reason: !button
        ? "发布表单未达到可提交状态；请检查类目或平台新增的必填项"
        : `以下字段未通过提交前回读校验：${uniqueMissing.join("、")}`,
    };
  }
  const formFingerprint = await fingerprint(payload, images);
  preparedPublication = {
    formFingerprint,
    payload,
    images,
    descriptionField,
    titleField,
    priceField,
    uploadedImages,
  };
  return {
    ...pageState(),
    status: "READY_TO_SUBMIT",
    module: "商品发布",
    filled_fields: [...new Set(filledFields)],
    missing_fields: [],
    uploaded_images: uploadedImages,
    form_fingerprint: formFingerprint,
    risk_detected: false,
  };
}

function productIdFromUrl(value) {
  try {
    const parsed = new URL(value);
    const queryId = parsed.searchParams.get("id") || parsed.searchParams.get("itemId");
    if (/^[0-9]{6,20}$/.test(queryId || "")) return queryId;
    const pathId = parsed.pathname.match(/(?:item|goods)\/(\d{6,20})/i)?.[1];
    return pathId || null;
  } catch {
    return null;
  }
}

async function prepareTrustedCommit(expectedFingerprint) {
  if (!preparedPublication || preparedPublication.formFingerprint !== expectedFingerprint) {
    return {
      ...pageState(),
      status: "FAILED",
      module: "商品发布",
      error_code: "FORM_STATE_LOST",
      manual_reason: "提交前表单状态已失效",
    };
  }
  const risk = detectRisk();
  const login = detectLoginRequired();
  if (risk || login) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品发布",
      risk_detected: Boolean(risk),
      form_fingerprint: expectedFingerprint,
      manual_reason: login ? `闲鱼登录状态失效（${login}）` : `页面出现“${risk}”，代理已停止`,
    };
  }
  const { payload, descriptionField, titleField, priceField, uploadedImages } = preparedPublication;
  const description = fieldValue(descriptionField);
  const title = titleField && titleField !== descriptionField ? fieldValue(titleField) : description;
  const price = fieldValue(priceField).replace(/,/g, "");
  if (!title.includes(String(payload.title).trim())
    || !description.includes(String(payload.description).trim())
    || Number(price) !== Number(payload.price)) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品发布",
      form_fingerprint: expectedFingerprint,
      uploaded_images: uploadedImages,
      manual_reason: "最终提交前字段内容发生变化，代理已停止",
    };
  }
  const button = findPublishButton();
  if (!button) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品发布",
      form_fingerprint: expectedFingerprint,
      uploaded_images: uploadedImages,
      manual_reason: "最终发布按钮不存在或不可点击",
    };
  }
  const rect = button.getBoundingClientRect();
  return {
    ...pageState(),
    status: "READY_FOR_TRUSTED_CLICK",
    module: "商品发布",
    uploaded_images: uploadedImages,
    form_fingerprint: expectedFingerprint,
    risk_detected: false,
    click_target: {
      button_text: normalizedText(button),
      x: rect.x + rect.width / 2,
      y: rect.y + rect.height / 2,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
    },
  };
}

async function observePublishResult(expectedFingerprint) {
  if (!preparedPublication || preparedPublication.formFingerprint !== expectedFingerprint) {
    return {
      ...pageState(),
      status: "FAILED",
      module: "商品发布",
      error_code: "FORM_STATE_LOST",
      manual_reason: "发布结果观察器启动前表单状态已失效",
    };
  }
  const { uploadedImages } = preparedPublication;
  preparedPublication = null;
  const started = Date.now();
  while (Date.now() - started < 30000) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const nextRisk = detectRisk();
    if (nextRisk) {
      return {
        ...pageState(),
        status: "MANUAL_REQUIRED",
        module: "商品发布",
        submission_attempted: true,
        uploaded_images: uploadedImages,
        form_fingerprint: expectedFingerprint,
        risk_detected: true,
        error_code: "RISK_AFTER_SUBMIT",
        manual_reason: `提交后出现“${nextRisk}”，请人工核对是否已发布`,
      };
    }
    const formError = visibleFormError();
    if (formError) {
      return {
        ...pageState(),
        status: "MANUAL_REQUIRED",
        module: "商品发布",
        submission_attempted: true,
        uploaded_images: uploadedImages,
        form_fingerprint: expectedFingerprint,
        risk_detected: false,
        error_code: "FORM_VALIDATION_AFTER_SUBMIT",
        manual_reason: `闲鱼未接受发布表单：${formError}`,
      };
    }
    const externalProductId = productIdFromUrl(location.href);
    if (externalProductId) {
      return {
        ...pageState(),
        status: "SUCCEEDED",
        module: "商品发布",
        submission_attempted: true,
        uploaded_images: uploadedImages,
        form_fingerprint: expectedFingerprint,
        external_product_id: externalProductId,
        published_url: location.href,
        success_evidence: "ITEM_URL",
        risk_detected: false,
      };
    }
    if (findVisibleText("发布成功") || findVisibleText("上架成功")) {
      return {
        ...pageState(),
        status: "SUCCEEDED",
        module: "商品发布",
        submission_attempted: true,
        uploaded_images: uploadedImages,
        form_fingerprint: expectedFingerprint,
        success_evidence: "SUCCESS_TEXT",
        risk_detected: false,
      };
    }
  }
  return {
    ...pageState(),
    status: "MANUAL_REQUIRED",
    module: "商品发布",
    submission_attempted: true,
    uploaded_images: uploadedImages,
    form_fingerprint: expectedFingerprint,
    error_code: "PUBLISH_RESULT_AMBIGUOUS",
    risk_detected: false,
    manual_reason: "已点击发布，但 30 秒内未获得明确成功证据；为防止重复上架已停止",
  };
}

async function prefillProduct(payload) {
  const risk = detectRisk();
  if (risk) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品发布",
      risk_detected: true,
      manual_reason: `页面出现“${risk}”，未填写任何字段`,
    };
  }
  await waitForFields();
  const filledFields = [];
  const missingFields = [];
  const descriptionField = findField(
    ["描述", "商品描述", "宝贝描述", "详情", "货品来源"],
    ["搜索"],
  );
  const descriptionParts = [payload.title, payload.description]
    .filter((value) => typeof value === "string" && value.trim())
    .map((value) => value.trim());
  if (descriptionField && descriptionParts.length > 0) {
    setFieldValue(descriptionField, descriptionParts.join("\n"));
    if (payload.title) filledFields.push("title");
    if (payload.description) filledFields.push("description");
  } else {
    if (payload.title) missingFields.push("title");
    if (payload.description) missingFields.push("description");
  }
  const priceField = candidateFields().find((element) =>
    element instanceof HTMLInputElement
    && element.type === "text"
    && element.getAttribute("placeholder") === "0.00"
  );
  if (priceField && payload.price !== null && payload.price !== undefined) {
    setFieldValue(priceField, payload.price);
    filledFields.push("price");
  } else if (payload.price !== null && payload.price !== undefined) {
    missingFields.push("price");
  }
  missingFields.push("category", "images");
  return {
    ...pageState(),
    status: "MANUAL_REQUIRED",
    module: "商品发布",
    filled_fields: filledFields,
    missing_fields: [...new Set(missingFields)],
    risk_detected: false,
    manual_reason: filledFields.length > 0
      ? "草稿已预填；请人工核对类目、图片、价格和描述后点击发布"
      : "未识别到发布表单字段，请人工完成本次发布",
  };
}

function captureOverview() {
  const risk = detectRisk();
  const modules = [...ALLOWED_MODULES].filter((module) => Boolean(findVisibleText(module)));
  if (risk) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      modules,
      risk_detected: true,
      manual_reason: `页面出现“${risk}”，只记录了模块可用性`,
    };
  }
  return {
    ...pageState(),
    status: "SUCCEEDED",
    modules,
    risk_detected: false,
  };
}

async function sha256Text(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(value)));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function chatConversationRows() {
  return [...document.querySelectorAll("[class*='conversation-item--']")]
    .filter((element) => isVisible(element));
}

function chatRowName(row) {
  const nameNode = [...(row?.querySelectorAll("div") || [])].find((element) => {
    if (!isVisible(element) || element.children.length > 0) return false;
    const text = (element.textContent || "").trim();
    const style = getComputedStyle(element);
    return Boolean(text)
      && Number.parseFloat(style.fontSize) >= 14
      && Number.parseInt(style.fontWeight, 10) >= 500;
  });
  return (nameNode?.textContent || row?.innerText || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .find(Boolean) || "买家";
}

function maskedBuyerName(value) {
  const text = String(value || "买家").trim().slice(0, 160);
  if (text.length <= 1) return `${text || "买"}***`;
  return `${text[0]}***${text.at(-1)}`;
}

function activeBuyerUserId() {
  const link = [...document.querySelectorAll("a[href*='www.goofish.com/personal?userId=']")]
    .find((element) => isVisible(element));
  if (!link) return null;
  try {
    const value = new URL(link.href).searchParams.get("userId");
    return /^[0-9]{4,30}$/.test(value || "") ? value : null;
  } catch {
    return null;
  }
}

function activeChatProductId() {
  const link = [...document.querySelectorAll("a[href*='www.goofish.com/item?id=']")]
    .find((element) => isVisible(element));
  return link ? productIdFromUrl(link.href) : null;
}

async function activeConversationIdentity() {
  const userId = activeBuyerUserId();
  if (!userId) return null;
  const productId = activeChatProductId() || "none";
  return {
    external_conversation_id: `browser-conv-${await sha256Text(`user:${userId}|item:${productId}`)}`,
    customer_id_masked: `buyer-${(await sha256Text(userId)).slice(0, 24)}`,
    external_product_id: productId === "none" ? null : productId,
  };
}

function activeChatName() {
  const topbar = [...document.querySelectorAll("[class*='message-topbar--']")]
    .find((element) => isVisible(element));
  return (topbar?.innerText || "").split(/\r?\n/).map((value) => value.trim()).find(Boolean) || null;
}

async function waitForActiveConversation(expectedName, timeoutMs = 3000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const identity = await activeConversationIdentity();
    if (identity && activeChatName() === expectedName) {
      const container = document.querySelector("#msg-list-container");
      if (container && isVisible(container)) return identity;
    }
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
  return null;
}

function chatTimestampNodes(container) {
  const pattern = /^(?:\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}|\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}|昨天\s+\d{1,2}:\d{2}|\d{1,2}:\d{2})$/;
  return [...container.querySelectorAll("div")].filter((element) => (
    isVisible(element)
    && element.children.length === 0
    && pattern.test((element.textContent || "").replace(/\s+/g, " ").trim())
  ));
}

function explicitChatTime(value, capturedAt) {
  const full = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$/);
  const short = value.match(/^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$/);
  const yesterday = value.match(/^昨天\s+(\d{1,2}):(\d{2})$/);
  let parsed = null;
  if (full) parsed = new Date(Number(full[1]), Number(full[2]) - 1, Number(full[3]), Number(full[4]), Number(full[5]));
  if (short) {
    parsed = new Date(capturedAt.getFullYear(), Number(short[1]) - 1, Number(short[2]), Number(short[3]), Number(short[4]));
    if (parsed.getTime() > capturedAt.getTime() + 24 * 60 * 60 * 1000) parsed.setFullYear(parsed.getFullYear() - 1);
  }
  if (yesterday) {
    parsed = new Date(capturedAt.getFullYear(), capturedAt.getMonth(), capturedAt.getDate() - 1, Number(yesterday[1]), Number(yesterday[2]));
  }
  return parsed && Number.isFinite(parsed.getTime()) ? parsed : null;
}

function parsedChatTime(label, labelIndex, timestampNodes, capturedAt) {
  if (!label) return null;
  const value = (label.textContent || "").replace(/\s+/g, " ").trim();
  const explicit = explicitChatTime(value, capturedAt);
  if (explicit) return explicit;
  const time = value.match(/^(\d{1,2}):(\d{2})$/);
  if (!time) return null;
  const nextExplicit = timestampNodes.slice(labelIndex + 1)
    .map((node) => explicitChatTime((node.textContent || "").replace(/\s+/g, " ").trim(), capturedAt))
    .find(Boolean);
  if (nextExplicit) {
    const candidate = new Date(nextExplicit.getFullYear(), nextExplicit.getMonth(), nextExplicit.getDate(), Number(time[1]), Number(time[2]));
    if (candidate.getTime() > nextExplicit.getTime()) candidate.setDate(candidate.getDate() - 1);
    return candidate;
  }
  const candidate = new Date(capturedAt.getFullYear(), capturedAt.getMonth(), capturedAt.getDate(), Number(time[1]), Number(time[2]));
  if (candidate.getTime() > capturedAt.getTime() + 5 * 60 * 1000) candidate.setDate(candidate.getDate() - 1);
  return candidate;
}

async function activeChatMessages(externalConversationId, capturedAt) {
  const container = document.querySelector("#msg-list-container");
  if (!container) return [];
  const bubbles = [...container.querySelectorAll("[class*='message-content--']")]
    .filter((element) => isVisible(element))
    .slice(-100);
  const timestampNodes = chatTimestampNodes(container);
  const occurrences = new Map();
  const messages = [];
  for (const bubble of bubbles) {
    const inbound = bubble.querySelector("[class*='message-text-left']");
    const outbound = bubble.querySelector("[class*='message-text-right']");
    const textNode = inbound || outbound;
    const content = (textNode?.innerText || textNode?.textContent || "").replace(/\s+/g, " ").trim();
    if (!content) continue;
    const direction = inbound ? "INBOUND" : "OUTBOUND";
    const key = `${direction}|${content}`;
    const occurrence = (occurrences.get(key) || 0) + 1;
    occurrences.set(key, occurrence);
    const digest = await sha256Text(`${externalConversationId}|${key}|${occurrence}`);
    const precedingLabels = timestampNodes.filter((node) => (
      node.compareDocumentPosition(bubble) & Node.DOCUMENT_POSITION_FOLLOWING
    ));
    const label = precedingLabels.at(-1) || null;
    const labelIndex = label ? timestampNodes.indexOf(label) : -1;
    const platformTime = parsedChatTime(label, labelIndex, timestampNodes, capturedAt);
    messages.push({
      external_message_id: `browser-msg-${digest}`,
      direction,
      role: inbound ? "BUYER" : "SELLER",
      content: content.slice(0, 10000),
      sent_at: (platformTime || capturedAt).toISOString(),
      timestamp_source: platformTime ? "PLATFORM_VISIBLE" : "CAPTURE_OBSERVED",
    });
  }
  return messages;
}

async function captureActiveConversation(rowName, capturedAt) {
  const identity = await activeConversationIdentity();
  if (!identity) return null;
  const messages = await activeChatMessages(identity.external_conversation_id, capturedAt);
  if (messages.length === 0) return null;
  return {
    ...identity,
    customer_name_masked: maskedBuyerName(rowName),
    last_message_at: messages.at(-1).sent_at,
    messages,
  };
}

async function captureConversations(payload = {}) {
  preparedReply = null;
  const risk = detectRisk();
  const login = detectLoginRequired();
  if (risk || login) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: Boolean(risk),
      manual_reason: login ? `闲鱼登录状态失效（${login}）` : `页面出现“${risk}”，代理已停止`,
    };
  }
  const capturedAt = new Date();
  const rowNames = [...new Set(chatConversationRows()
    .slice(0, Math.min(Math.max(Number(payload.max_conversations || 20), 1), 50))
    .map(chatRowName))];
  if (rowNames.length === 0) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "消息页没有可读取的会话，请确认已登录且会话列表已加载",
    };
  }
  const conversations = [];
  for (const rowName of rowNames) {
    const row = chatConversationRows().find((candidate) => chatRowName(candidate) === rowName);
    if (!row) continue;
    dispatchClick(row.querySelector(".ant-dropdown-trigger") || row);
    const identity = await waitForActiveConversation(rowName);
    if (!identity) continue;
    await new Promise((resolve) => setTimeout(resolve, 450));
    const captured = await captureActiveConversation(rowName, capturedAt);
    if (captured) conversations.push(captured);
  }
  if (conversations.length === 0) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "未能核验会话身份或读取文本消息，已停止采集",
    };
  }
  return {
    ...pageState(),
    status: "SUCCEEDED",
    module: "消息",
    captured_at: capturedAt.toISOString(),
    conversations,
    risk_detected: false,
  };
}

async function findConversationById(expectedId) {
  const rowNames = [...new Set(chatConversationRows().map(chatRowName))];
  for (const rowName of rowNames) {
    const row = chatConversationRows().find((candidate) => chatRowName(candidate) === rowName);
    if (!row) continue;
    dispatchClick(row.querySelector(".ant-dropdown-trigger") || row);
    const identity = await waitForActiveConversation(rowName);
    if (identity?.external_conversation_id === expectedId) return identity;
  }
  return null;
}

function findSendButton() {
  return [...document.querySelectorAll("button, [role='button']")].find((element) => (
    isVisible(element)
    && normalizedText(element) === "发送"
    && !element.disabled
    && element.getAttribute("aria-disabled") !== "true"
  )) || null;
}

async function prepareReply(payload) {
  preparedReply = null;
  const risk = detectRisk();
  const login = detectLoginRequired();
  if (risk || login) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: Boolean(risk),
      manual_reason: login ? `闲鱼登录状态失效（${login}）` : `页面出现“${risk}”，代理已停止`,
    };
  }
  const identity = await findConversationById(payload.external_conversation_id);
  if (!identity) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "当前可见会话中没有找到已同步的目标买家",
    };
  }
  const capturedAt = new Date();
  const messages = await activeChatMessages(identity.external_conversation_id, capturedAt);
  const latest = messages.at(-1);
  if (!latest || latest.direction !== "INBOUND" || latest.external_message_id !== payload.source_message_id) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "买家最新消息已变化，请重新同步并生成回复建议",
    };
  }
  const input = [...document.querySelectorAll("textarea, input")].find((element) => (
    isVisible(element)
    && String(element.getAttribute("placeholder") || "").includes("请输入消息")
  ));
  if (!input) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "未识别到闲鱼消息输入框",
    };
  }
  setFieldValue(input, payload.reply);
  await new Promise((resolve) => setTimeout(resolve, 450));
  if (fieldValue(input) !== String(payload.reply).trim()) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "回复文本回读不一致，代理已停止",
    };
  }
  const button = findSendButton();
  if (!button) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      risk_detected: false,
      manual_reason: "发送按钮不存在或不可点击",
    };
  }
  const formFingerprint = await sha256Text(JSON.stringify({
    external_conversation_id: payload.external_conversation_id,
    source_message_id: payload.source_message_id,
    reply: payload.reply,
  }));
  preparedReply = {
    formFingerprint,
    payload,
    input,
    outboundIds: new Set(messages.filter((item) => item.direction === "OUTBOUND").map((item) => item.external_message_id)),
  };
  return {
    ...pageState(),
    status: "READY_TO_SUBMIT",
    module: "消息",
    form_fingerprint: formFingerprint,
    external_conversation_id: payload.external_conversation_id,
    source_message_id: payload.source_message_id,
    risk_detected: false,
  };
}

async function prepareTrustedReplyCommit(expectedFingerprint) {
  if (!preparedReply || preparedReply.formFingerprint !== expectedFingerprint) {
    return {
      ...pageState(),
      status: "FAILED",
      module: "消息",
      error_code: "REPLY_STATE_LOST",
      manual_reason: "回复提交前状态已失效",
    };
  }
  const { payload, input } = preparedReply;
  const identity = await activeConversationIdentity();
  if (identity?.external_conversation_id !== payload.external_conversation_id
    || fieldValue(input) !== String(payload.reply).trim()) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      form_fingerprint: expectedFingerprint,
      external_conversation_id: payload.external_conversation_id,
      source_message_id: payload.source_message_id,
      manual_reason: "最终发送前会话或回复文本发生变化，代理已停止",
    };
  }
  const button = findSendButton();
  if (!button) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "消息",
      form_fingerprint: expectedFingerprint,
      external_conversation_id: payload.external_conversation_id,
      source_message_id: payload.source_message_id,
      manual_reason: "最终发送按钮不存在或不可点击",
    };
  }
  const rect = button.getBoundingClientRect();
  return {
    ...pageState(),
    status: "READY_FOR_TRUSTED_CLICK",
    module: "消息",
    form_fingerprint: expectedFingerprint,
    external_conversation_id: payload.external_conversation_id,
    source_message_id: payload.source_message_id,
    risk_detected: false,
    click_target: {
      button_text: normalizedText(button),
      x: rect.x + rect.width / 2,
      y: rect.y + rect.height / 2,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
    },
  };
}

async function observeReplyResult(expectedFingerprint) {
  if (!preparedReply || preparedReply.formFingerprint !== expectedFingerprint) {
    return {
      ...pageState(),
      status: "FAILED",
      module: "消息",
      error_code: "REPLY_STATE_LOST",
      manual_reason: "回复结果观察器启动前状态已失效",
    };
  }
  const { payload, outboundIds } = preparedReply;
  preparedReply = null;
  const started = Date.now();
  while (Date.now() - started < 15000) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const identity = await activeConversationIdentity();
    if (identity?.external_conversation_id !== payload.external_conversation_id) break;
    const messages = await activeChatMessages(identity.external_conversation_id, new Date());
    const sent = [...messages].reverse().find((item) => (
      item.direction === "OUTBOUND"
      && item.content === String(payload.reply).trim()
      && !outboundIds.has(item.external_message_id)
    ));
    if (sent) {
      return {
        ...pageState(),
        status: "SUCCEEDED",
        module: "消息",
        submission_attempted: true,
        reply_sent: true,
        form_fingerprint: expectedFingerprint,
        external_conversation_id: payload.external_conversation_id,
        source_message_id: payload.source_message_id,
        sent_message_id: sent.external_message_id,
        success_evidence: "MESSAGE_APPEARED",
        risk_detected: false,
      };
    }
  }
  return {
    ...pageState(),
    status: "MANUAL_REQUIRED",
    module: "消息",
    submission_attempted: true,
    form_fingerprint: expectedFingerprint,
    external_conversation_id: payload.external_conversation_id,
    source_message_id: payload.source_message_id,
    error_code: "REPLY_RESULT_AMBIGUOUS",
    risk_detected: false,
    manual_reason: "已点击发送，但未获得新消息气泡证据；为防止重复回复已停止",
  };
}

function metricNumber(value, field, integer = true) {
  const normalized = String(value ?? "").replace(/[¥￥,%\s]/g, "").trim();
  if (!normalized || normalized === "-" || normalized === "--") return null;
  const number = Number(normalized);
  if (!Number.isFinite(number) || number < 0 || (integer && !Number.isInteger(number))) {
    throw new Error(`商品数据字段“${field}”格式发生变化`);
  }
  return number;
}

function metricRowsOnPage() {
  const rows = [...document.querySelectorAll("table tbody tr")]
    .filter((row) => {
      const cells = row.querySelectorAll("td");
      return cells.length > 0 && /商品ID\s*[0-9]{6,20}/.test(cells[0].innerText || "");
    });
  return rows.map((row) => {
    const cells = [...row.querySelectorAll("td")];
    if (cells.length !== 16) throw new Error("商品数据表列数发生变化，已停止采集");
    const productText = (cells[0].innerText || "").replace(/\s+/g, " ").trim();
    const productId = productText.match(/商品ID\s*([0-9]{6,20})/)?.[1];
    const price = productText.match(/[¥￥]\s*([0-9,.]+)/)?.[1];
    const title = productText.split(/商品ID\s*[0-9]{6,20}/)[0]?.trim();
    if (!productId || !title || price === undefined) {
      throw new Error("商品信息字段发生变化，已停止采集");
    }
    const values = cells.slice(1).map((cell) => (cell.innerText || "").trim());
    const conversion = metricNumber(values[8], "浏览支付转化率", false);
    return {
      external_product_id: productId,
      listing_title: title.slice(0, 500),
      listing_price: metricNumber(price, "商品价格", false),
      exposures: metricNumber(values[0], "商品曝光次数"),
      exposed_users: metricNumber(values[1], "商品曝光人数"),
      views: metricNumber(values[2], "商品浏览次数"),
      viewers: metricNumber(values[3], "商品浏览人数"),
      inquiries: metricNumber(values[4], "询单人数"),
      paid_users: metricNumber(values[5], "支付人数"),
      paid_orders: metricNumber(values[6], "支付订单数"),
      paid_amount: metricNumber(values[7], "支付金额", false),
      browse_pay_conversion: conversion === null ? null : conversion / 100,
      refund_requested_users: metricNumber(values[9], "发起退款人数"),
      refund_requested_orders: metricNumber(values[10], "发起退款订单数"),
      refund_requested_amount: metricNumber(values[11], "发起退款金额", false),
      refund_success_users: metricNumber(values[12], "成功退款人数"),
      refund_success_orders: metricNumber(values[13], "成功退款订单数"),
      refund_success_amount: metricNumber(values[14], "成功退款金额", false),
    };
  });
}

function paginationButton(direction) {
  const icon = [...document.querySelectorAll(`[aria-label='${direction}'], img`)]
    .find((element) => (
      element.getAttribute("aria-label") === direction
      || element.getAttribute("alt") === direction
    ) && isVisible(element));
  return icon?.closest("button") || null;
}

function paginationDisabled(button) {
  return !button
    || button.disabled
    || button.getAttribute("aria-disabled") === "true"
    || button.closest("li")?.classList.contains("ant-pagination-disabled");
}

function metricPageFingerprint(rows) {
  return rows.map((row) => row.external_product_id).join("|");
}

function metricRowFingerprint(row) {
  return JSON.stringify(row);
}

async function maximizeMetricPageSize() {
  const sizeChanger = [...document.querySelectorAll(
    ".ant-pagination-options-size-changer, .ant-pagination-options .ant-select",
  )].find((element) => isVisible(element));
  if (!sizeChanger) return null;

  const currentSize = Number(normalizedText(sizeChanger).match(/(\d+)\s*条\s*\/\s*页/)?.[1] || 0);
  dispatchClick(
    sizeChanger.querySelector(".ant-select-selector, [role='combobox']") || sizeChanger,
  );
  const started = Date.now();
  let options = [];
  while (Date.now() - started < 2500) {
    options = [...document.querySelectorAll(
      "[role='option'], .ant-select-item-option",
    )].filter((element) => (
      isVisible(element)
      && /^\d+\s*条\s*\/\s*页$/.test(normalizedText(element))
    ));
    if (options.length > 0) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (options.length === 0) return currentSize || null;

  const candidates = options
    .map((element) => ({
      element,
      size: Number(normalizedText(element).match(/^(\d+)/)?.[1] || 0),
    }))
    .filter(({ size }) => size > 0 && size <= 500)
    .sort((left, right) => right.size - left.size);
  const target = candidates[0];
  if (!target || target.size <= currentSize) {
    dispatchClick(document.body);
    return currentSize || target?.size || null;
  }

  dispatchClick(target.element);
  await new Promise((resolve) => setTimeout(resolve, 900));
  await waitForMetricRows();
  return target.size;
}

async function waitForMetricRows(previousFingerprint = null, timeoutMs = 10000) {
  const started = Date.now();
  let stableFingerprint = null;
  let stableSince = 0;
  while (Date.now() - started < timeoutMs) {
    const risk = detectRisk();
    if (risk) throw new Error(`页面出现“${risk}”，代理已停止`);
    const rows = metricRowsOnPage();
    const fingerprint = metricPageFingerprint(rows);
    if (rows.length > 0 && (!previousFingerprint || fingerprint !== previousFingerprint)) {
      if (fingerprint !== stableFingerprint) {
        stableFingerprint = fingerprint;
        stableSince = Date.now();
      } else if (Date.now() - stableSince >= 500) {
        return rows;
      }
    } else {
      stableFingerprint = null;
      stableSince = 0;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("商品数据表加载超时");
}

async function captureProductMetrics(expectedDate) {
  const risk = detectRisk();
  const login = detectLoginRequired();
  if (risk || login) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品数据",
      risk_detected: Boolean(risk),
      manual_reason: risk ? `页面出现“${risk}”，代理已停止` : `页面出现“${login}”，请重新登录`,
    };
  }

  const nearOneDay = [...document.querySelectorAll("input[type='radio'], [role='radio']")]
    .find((element) => {
      const context = element.closest("label") || element.parentElement;
      return normalizedText(context).includes("近1天");
    });
  if (nearOneDay && !nearOneDay.checked && nearOneDay.getAttribute("aria-checked") !== "true") {
    dispatchClick(nearOneDay.closest("label") || nearOneDay);
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }

  const dateInput = document.querySelector("input[placeholder='结束日期']");
  const metricDate = fieldValue(dateInput);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(metricDate) || metricDate !== expectedDate) {
    return {
      ...pageState(),
      status: "MANUAL_REQUIRED",
      module: "商品数据",
      risk_detected: false,
      manual_reason: `经营数据日期为 ${metricDate || "未知"}，与任务日期 ${expectedDate} 不一致`,
    };
  }

  await maximizeMetricPageSize();

  for (let page = 0; page < 10; page += 1) {
    const previous = paginationButton("left");
    if (paginationDisabled(previous)) break;
    const fingerprint = metricPageFingerprint(metricRowsOnPage());
    dispatchClick(previous);
    await waitForMetricRows(fingerprint);
  }

  const metrics = [];
  const seen = new Map();
  let duplicateRowsSkipped = 0;
  let pagesCaptured = 0;
  for (let page = 0; page < 25; page += 1) {
    const pageRows = await waitForMetricRows();
    pagesCaptured += 1;
    for (const row of pageRows) {
      const existing = seen.get(row.external_product_id);
      if (existing) {
        if (metricRowFingerprint(existing) !== metricRowFingerprint(row)) {
          throw new Error("分页重复商品的指标不一致，已停止采集");
        }
        duplicateRowsSkipped += 1;
        continue;
      }
      seen.set(row.external_product_id, row);
      metrics.push(row);
    }
    if (metrics.length > 500) throw new Error("商品数据超过 500 行限制");
    const next = paginationButton("right");
    if (paginationDisabled(next)) break;
    const fingerprint = metricPageFingerprint(pageRows);
    dispatchClick(next);
    await waitForMetricRows(fingerprint);
  }
  if (metrics.length === 0) throw new Error("商品数据表没有可读取的商品行");
  return {
    ...pageState(),
    status: "SUCCEEDED",
    module: "商品数据",
    metric_date: metricDate,
    metrics,
    metrics_duplicate_rows_skipped: duplicateRowsSkipped,
    metrics_pages: pagesCaptured,
    risk_detected: false,
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const run = async () => {
    if (location.origin !== "https://seller.goofish.com") {
      throw new Error("浏览器代理只允许在闲鱼商家工作台运行");
    }
    if (message?.kind === "OPEN_MODULE") return openModule(message.module);
    if (message?.kind === "CAPTURE_OVERVIEW") return captureOverview();
    if (message?.kind === "CAPTURE_PRODUCT_METRICS") {
      return captureProductMetrics(message.metric_date);
    }
    if (message?.kind === "CAPTURE_CONVERSATIONS") {
      if (!activeConversationCapture) {
        activeConversationCapture = captureConversations(message.payload || {})
          .finally(() => {
            activeConversationCapture = null;
          });
      }
      return activeConversationCapture;
    }
    if (message?.kind === "PREFILL_PRODUCT") return prefillProduct(message.payload || {});
    if (message?.kind === "PREPARE_PUBLISH") {
      return preparePublish(message.payload || {}, message.images || []);
    }
    if (message?.kind === "PREPARE_TRUSTED_COMMIT") {
      return prepareTrustedCommit(message.form_fingerprint);
    }
    if (message?.kind === "OBSERVE_PUBLISH_RESULT") {
      return observePublishResult(message.form_fingerprint);
    }
    if (message?.kind === "PREPARE_REPLY") return prepareReply(message.payload || {});
    if (message?.kind === "PREPARE_TRUSTED_REPLY_COMMIT") {
      return prepareTrustedReplyCommit(message.form_fingerprint);
    }
    if (message?.kind === "OBSERVE_REPLY_RESULT") {
      return observeReplyResult(message.form_fingerprint);
    }
    throw new Error("拒绝未知浏览器任务");
  };
  void run()
    .then(sendResponse)
    .catch((error) => sendResponse({
      ...pageState(),
      status: "FAILED",
      risk_detected: false,
      error_code: "CONTENT_SCRIPT_ERROR",
      manual_reason: error instanceof Error ? error.message : "页面任务执行失败",
    }));
  return true;
});
