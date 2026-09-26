export type DashboardData = {
  data_mode: string;
  metrics_mode: "DEMO_FIXTURE" | "LIVE_FACTS";
  date: string;
  automation: { running: boolean; controls: Record<string, boolean> };
  business: {
    gmv: string;
    orders: number;
    gross_profit: string;
    expected_net_profit: string;
    actual_net_profit: string;
    refunds: number;
  };
  traffic: Record<"exposures" | "views" | "consultations" | "favorites" | "wants", number>;
  ai_service: {
    messages: number;
    auto_replies: number;
    auto_rate: string;
    manual_takeovers: number;
    average_response_seconds: string;
  };
  products: {
    total: number;
    active: number;
    testing: number;
    winner: number;
    low_stock: number;
    pending_removal: number;
  };
  operation_summary: {
    products: number;
    pending_drafts: number;
    today_orders: number;
    manual_tasks: number;
  };
  risks: Record<"price" | "stock" | "aftersales" | "other", number>;
  chart: Array<{ date: string; revenue: number; profit: number }>;
  recent_activity: Array<{
    id: number;
    time: string;
    type: string;
    action: string;
    entity_id: string;
    actor: string;
  }>;
  recent_tasks: Array<{
    id: number;
    type: string;
    status: string;
    updated_at: string;
  }>;
  manual_items: Array<{
    id: number;
    title: string;
    reason: string;
    priority: number;
    created_at: string;
  }>;
};

export type RuntimeReadiness = {
  status: "READY" | "BLOCKED";
  listing_ready: boolean;
  summary: string;
  blockers: string[];
  stages: Array<{
    key: "selection" | "content" | "publish" | "fulfillment";
    label: string;
    ready: boolean;
    detail: string;
    route: string;
  }>;
  ai: {
    enabled: boolean;
    primary_provider: string | null;
    routes: Array<{
      task: string;
      label: string;
      provider: string | null;
      ready: boolean;
      message: string;
    }>;
  };
  browser: {
    online: boolean;
    bridge_id: string | null;
    version: string | null;
    capabilities: string[];
    last_seen_at: string | null;
  };
  candidate_pool: {
    discovered: number;
    eligible: number;
  };
  checked_at: string;
};

export type OperationsFacts = {
  agents: {
    total_decisions: number;
    by_agent: Array<{ agent: string; count: number }>;
    recent: Array<{
      id: number;
      agent: string;
      confidence: string;
      reason_summary: string;
      provider: string;
      model: string;
      created_at: string;
    }>;
  };
  risk: {
    open_manual_tasks: Array<{
      id: number;
      type: string;
      title: string;
      reason: string;
      priority: number;
      entity_type: string | null;
      entity_id: string | null;
      created_at: string;
    }>;
    failed_jobs: Array<{
      id: number;
      type: string;
      status: string;
      error_code: string | null;
      error_message: string | null;
      attempts: number;
      created_at: string;
    }>;
    controls: Array<{
      scope: string;
      enabled: boolean;
      mode: string;
      consecutive_failures: number;
      cooldown_until: string | null;
      stopped_reason: string | null;
    }>;
    after_sales: Array<{
      id: number;
      order_id: number;
      type: string;
      status: string;
      amount: string | null;
      reason: string | null;
      created_at: string;
    }>;
    recent_audit: Array<{
      id: number;
      action: string;
      entity_type: string;
      entity_id: string | null;
      result: string;
      actor_type: string;
      created_at: string;
    }>;
  };
  purchases: {
    items: Array<{
      id: number;
      order_id: number;
      supplier: string;
      status: string;
      cost: string;
      shipping_cost: string;
      external_order_recorded: boolean;
      created_at: string;
    }>;
  };
  logistics: {
    items: Array<{
      id: number;
      supplier_order_id: number;
      carrier: string | null;
      tracking_recorded: boolean;
      status: string;
      shipped_at: string | null;
      delivered_at: string | null;
      last_event_at: string | null;
      created_at: string;
    }>;
  };
};

export type OperationsWorkflow = {
  id: string;
  order: number;
  name: string;
  stage: string;
  route: string;
  route_label: string;
  automation: string;
  system_status: string;
  goal: string;
  trigger: string;
  prerequisites: string[];
  steps: string[];
  evidence: string[];
  stop_conditions: string[];
  acceptance: string;
};

export type ModelProviderSnapshot = {
  id: string;
  provider: string;
  model: string;
  currency: "USD" | "CNY";
  input_per_million: number;
  output_per_million: number;
  cached_input_per_million: number | null;
  offpeak_input_per_million?: number;
  offpeak_output_per_million?: number;
  context_tokens: number;
  structured_output: boolean;
  tool_calling: boolean;
  vision_input: boolean;
  image_generation: string;
  availability: string;
  fit_score: number;
  cost_score: number;
  recommendation: string;
  notes: string;
  source_url: string;
  pricing_url: string;
};

export type ModelPlan = {
  name: string;
  primary: string;
  image: string;
  why: string;
  offpeak_text?: string;
};

export type OperationsGuide = {
  version: string;
  price_checked_at: string;
  workflow_count: number;
  workflows: OperationsWorkflow[];
  model_providers: ModelProviderSnapshot[];
  model_strategy: {
    decision: string;
    single_provider: ModelPlan;
    lowest_cost: ModelPlan;
    domestic: ModelPlan;
    routing: Array<{ task: string; route: string; fallback: string }>;
    cost_controls: string[];
    production_gate: string[];
  };
  default_scenario: {
    name: string;
    input_million_tokens: number;
    output_million_tokens: number;
    usd_to_cny: number;
    assumptions: string[];
  };
  guardrails: string[];
};

export type AIProviderSettings = {
  provider: "openai" | "deepseek" | "qwen";
  display_name: string;
  base_url: string;
  text_model: string;
  vision_model: string | null;
  image_model: string | null;
  api_key_configured: boolean;
  api_key_hint: string | null;
  last_test_status: "NOT_TESTED" | "SUCCESS" | "FAILED";
  last_test_message: string | null;
  last_tested_at: string | null;
  capabilities: Record<string, boolean>;
  updated_at: string;
};

export type AIRuntimeSettings = {
  enabled: boolean;
  primary_provider: "openai" | "deepseek" | "qwen";
  fallback_provider: "openai" | "deepseek" | "qwen" | null;
  monthly_budget_cny: string;
  temperature: string;
  max_output_tokens: number;
  request_timeout_seconds: number;
  task_routing: Record<string, "openai" | "deepseek" | "qwen" | "human">;
  updated_at: string;
};

export type AISettingsResponse = {
  providers: AIProviderSettings[];
  runtime: AIRuntimeSettings;
  security: {
    api_keys_encrypted: boolean;
    api_keys_returned: boolean;
    platform_automation_affected: boolean;
    activation_requires_test: boolean;
  };
};

export type CommercializationOverview = {
  version: string;
  status: "INTERNAL" | "PAID_PILOT" | "PRIVATE_RELEASE_READY";
  status_label: string;
  release_ready: boolean;
  readiness_score: number;
  growth_level: number;
  growth_task: string;
  deployment: {
    id: number;
    edition: string;
    installation_name: string;
    customer_name: string | null;
    customer_type: "INDIVIDUAL_OPERATOR" | "SOLE_PROPRIETOR" | "COMPANY";
    plan: "PILOT" | "STANDARD" | "PRO";
    deployment_mode: "CUSTOMER_OWNED_SINGLE_TENANT";
    acceptance: {
      account_owned_by_customer: boolean;
      data_stays_customer_controlled: boolean;
      no_credential_custody: boolean;
      no_revenue_guarantee_acknowledged: boolean;
      prohibited_automation_acknowledged: boolean;
      regulatory_obligations_acknowledged: boolean;
      terms_version: string | null;
      privacy_version: string | null;
      compliance_version: string | null;
      accepted_at: string | null;
    };
  };
  stages: Array<{ key: string; label: string; ready: boolean; detail: string }>;
  blockers: string[];
  boundaries: string[];
  offers: Array<{
    code: "PILOT" | "STANDARD" | "PRO";
    name: string;
    price_cny: number;
    billing: string;
    purpose: string;
    includes: string[];
    not_included: string[];
  }>;
  evidence_summary: {
    qualified_leads: number;
    product_demos: number;
    paid_customers: number;
    refunded_customers: number;
    revenue_cny: number;
    delivery_hours_per_paid_customer: number | null;
    support_hours_per_active_customer: number | null;
    latest_retention_rate: number | null;
  };
  periods: Array<{
    id: number;
    period_start: string;
    qualified_leads: number;
    product_demos: number;
    paid_new_customers: number;
    active_customers: number;
    retained_30d_customers: number;
    refunded_customers: number;
    revenue_cny: number;
    delivery_hours: number;
    support_hours: number;
    notes: string | null;
  }>;
  versions: { terms: string; privacy: string; compliance: string };
};

export type ProductItem = {
  id: number;
  internal_code: string;
  title: string;
  category: string;
  description: string | null;
  lifecycle: string;
  xianyu_status: string;
  created_at: string;
  supplier: {
    name: string;
    code: string;
    masked_external_id: string;
    url: string | null;
  };
  sku: {
    id: number;
    sku_code: string;
    supplier_cost: string;
    supplier_shipping: string;
    platform_fee: string;
    after_sales_reserve: string;
    minimum_profit: string;
    target_profit: string;
    final_cost: string;
    minimum_sale_price: string;
    target_sale_price: string;
    recommended_price: string;
    expected_profit: string;
    stock: number;
    score: string;
    last_checked_at: string | null;
  };
  score_breakdown: Record<string, string>;
};

export type ProductResponse = {
  items: ProductItem[];
  total: number;
  page: number;
  page_size: number;
};

export type IntegrationHealth = {
  name: string;
  configured_provider: string;
  status: string;
  available: boolean;
  authenticated: boolean;
  read_only: boolean;
  write_enabled: boolean;
  source: string;
  source_version: string;
  capabilities: Record<string, unknown>;
  error_code: string | null;
  message: string | null;
};

export type IntegrationStatus = {
  global_enabled: boolean;
  write_enabled: boolean;
  data_mode: string;
  supplier: IntegrationHealth;
  xianyu: IntegrationHealth;
};

export type SourcingCandidate = {
  id: number;
  external_product_id: string;
  adapter_name: string;
  adapter_version: string;
  title: string;
  url: string | null;
  image_url: string | null;
  category: string | null;
  supplier_name: string | null;
  minimum_price: string | null;
  maximum_price: string | null;
  sku_count: number;
  stock: number | null;
  score: string | null;
  status: string;
  last_fetched_at: string;
  import_ready: boolean;
  normalized_data: {
    external_supplier_id?: string;
    stats?: Record<string, unknown>;
    skus?: Array<{
      external_sku_id: string;
      spec?: Record<string, unknown>;
      price: string | { amount: string; currency?: string };
      shipping?: string | { amount: string; currency?: string };
      stock?: number;
    }>;
    manual_verification?: {
      method: string;
      captured_at: string;
      source_url: string;
      evidence_note: string;
    };
  };
};

export type XianyuOverview = {
  accounts: Array<{
    id: number;
    nickname: string;
    external_account_id: string;
    status: string;
    is_enabled: boolean;
    last_synced_at: string | null;
    source: string;
  }>;
  products: Array<{
    id: number;
    external_product_id: string;
    title: string | null;
    price: string | null;
    status: string;
    last_synced_at: string | null;
    source_url: string | null;
    source: string;
  }>;
  conversations: Array<{
    id: number;
    external_conversation_id: string;
    customer_name: string;
    status: string;
    manual_mode: boolean;
    last_message_at: string | null;
    message_count: number;
  }>;
  orders: Array<{
    id: number;
    external_order_id: string;
    status: string;
    revenue: string;
    expected_profit: string;
    actual_profit: string | null;
    paid_at: string | null;
    last_synced_at: string | null;
  }>;
};

export type XianyuTrafficOverview = {
  latest_date: string | null;
  period_start: string | null;
  period_end: string | null;
  data_days: number;
  summary: {
    products: number;
    managed_products?: number;
    awaiting_t1?: number;
    exposures: number;
    views: number;
    ctr: string;
    managed_exposures?: number;
    managed_views?: number;
    managed_ctr?: string;
    inquiries: number;
    paid_orders: number;
    paid_amount: string;
  };
  daily: Array<{
    date: string;
    products: number;
    exposures: number;
    views: number;
    ctr: string;
    inquiries: number;
    paid_orders: number;
  }>;
  products: Array<{
    external_product_id: string;
    xianyu_product_id: number | null;
    managed: boolean;
    title: string;
    price: string;
    listing_days: number;
    data_days: number;
    exposures: number;
    views: number;
    ctr: string;
    inquiries: number;
    inquiry_rate: string;
    paid_orders: number;
    paid_amount: string;
    diagnosis: {
      code: "AWAITING_T1" | "COLLECTING_DATA" | "NO_EXPOSURE" | "LOW_CLICK" | "LOW_INTENT" | "INQUIRY_NOT_PAID" | "HIGH_REFUND" | "HEALTHY" | "OBSERVE";
      label: string;
      severity: "info" | "medium" | "high" | "critical" | "success";
      actions: string[];
    };
  }>;
  strategy: {
    observation_days: number;
    minimum_exposures: number;
    price_change_days: number;
    note: string;
  };
};

export type BrowserBridgeAgent = {
  bridge_id: string;
  name: string;
  version: string;
  status: "ONLINE" | "OFFLINE";
  capabilities: Array<"OPEN_MODULE" | "CAPTURE_OVERVIEW" | "CAPTURE_PRODUCT_METRICS" | "CAPTURE_CONVERSATIONS" | "PREFILL_PRODUCT" | "PUBLISH_PRODUCT" | "SEND_REPLY">;
  current_url: string | null;
  last_error: string | null;
  last_seen_at: string;
};

export type BrowserBridgeTask = {
  id: number;
  operation: "OPEN_MODULE" | "CAPTURE_OVERVIEW" | "CAPTURE_PRODUCT_METRICS" | "CAPTURE_CONVERSATIONS" | "PREFILL_PRODUCT" | "PUBLISH_PRODUCT" | "SEND_REPLY";
  action_type: string;
  target_type: string;
  target_id: number;
  status: string;
  preview: {
    executable: boolean;
    blockers: string[];
    warnings: string[];
    expected_changes: Record<string, unknown>;
    scope: string;
    mode: string;
  };
  execution_result: {
    module?: string | null;
    modules?: string[];
    filled_fields?: string[];
    missing_fields?: string[];
    manual_reason?: string | null;
    risk_detected?: boolean;
    phase?: "PREPARING" | "READY_TO_SUBMIT" | "SUBMITTING" | "SUBMITTED";
    submission_attempted?: boolean;
    uploaded_images?: number;
    external_product_id?: string | null;
    external_conversation_id?: string | null;
    source_message_id?: string | null;
    sent_message_id?: string | null;
    reply_sent?: boolean;
    conversation_import?: {
      conversations_seen: number;
      conversations_verified: number;
      conversations_unverified: number;
      conversations_created: number;
      messages_seen: number;
      messages_created: number;
    };
    success_evidence?: "SUCCESS_TEXT" | "ITEM_URL" | "ITEM_URL_AFTER_NAVIGATION" | "MANAGEMENT_ROW" | "MESSAGE_APPEARED" | null;
  };
  attempts: number;
  requires_confirmation: boolean;
  error_code: string | null;
  error_message: string | null;
  lease_owner: string | null;
  lease_expires_at: string | null;
  created_at: string;
  updated_at: string;
};

export type BrowserBridgeOverview = {
  configured: boolean;
  agents: BrowserBridgeAgent[];
  tasks: BrowserBridgeTask[];
  safety: {
    external_submission: boolean;
    publish_submission_enabled: boolean;
    customer_service_submission_enabled: boolean;
    arbitrary_scripts: boolean;
    cookies_exported: boolean;
    human_confirmation_required: boolean;
    automatic_publish_ready: boolean;
    automatic_publish_blockers: string[];
    lease_seconds: number;
    max_attempts: number;
  };
};

export type ClawHubCapability = {
  id: number;
  key: string;
  name: string;
  status: "ACTIVE" | "PARTIAL" | "SAFE_PREVIEW" | "MANUAL_REQUIRED";
};

export type ClawHubOverview = {
  capabilities: ClawHubCapability[];
  counts: Record<string, number>;
  safety: {
    external_writes_enabled: boolean;
    mode: string;
  };
};

export type SupplierCandidateItem = {
  id: number;
  external_supplier_id: string;
  name: string;
  source: string;
  source_version: string;
  region: string | null;
  industry: string | null;
  factory_tags: string[];
  source_product_count: number;
  suitability_score: string;
  response_speed_score: string | null;
  delivery_score: string | null;
  status: string;
  last_verified_at: string | null;
};

export type ClawHubList<T> = { items: T[]; total: number };

export type ProductEvaluationItem = {
  id: number;
  product_id: number | null;
  sourcing_candidate_id: number | null;
  stage: string;
  grade: string;
  total_score: string;
  dimensions: Record<string, string>;
  evidence: Record<string, unknown>;
  created_at: string;
};

export type ProductDiagnosisItem = {
  id: number;
  diagnosis: string;
  severity: string;
  evidence: Record<string, unknown>;
  recommended_actions: string[];
  status: string;
  created_at: string;
};

export type XianyuDraftItem = {
  id: number;
  product_id: number;
  version: number;
  title: string;
  description: string;
  price: string;
  category: string | null;
  validation: { passed: boolean; blockers: string[]; warnings: string[] };
  status: string;
  created_at: string;
};

export type SupplierInquiryItem = {
  id: number;
  supplier_candidate_id: number | null;
  sourcing_candidate_id: number | null;
  topic: string;
  questions: string[];
  status: string;
  result: Record<string, unknown>;
  created_at: string;
};

export type PlatformActionItem = {
  id: number;
  action_type: string;
  target_type: string;
  target_id: number;
  preview: {
    executable: boolean;
    blockers: string[];
    warnings: string[];
    expected_changes: Record<string, unknown>;
    scope: string;
    mode: string;
  };
  status: string;
  requires_confirmation: boolean;
  automatic: boolean;
  provider: string | null;
  attempts: number;
  execution_result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  correlation_id: string | null;
  approved_at: string | null;
  executed_at: string | null;
  created_at: string;
};

export type AutomationControl = {
  scope: string;
  enabled: boolean;
  mode: "REVIEW" | "AUTOMATIC";
  daily_limit: number;
  min_interval_seconds: number;
  failure_threshold: number;
  consecutive_failures: number;
  cooldown_until: string | null;
  last_executed_at: string | null;
  stopped_reason: string | null;
  updated_at: string;
};

export type AutonomousAsset = {
  filename: string;
  relative_path: string;
  kind: "source" | "generated" | "rejected";
  sha256: string;
  bytes: number;
  mime_type: string;
  width: number;
  height: number;
  provider?: string;
  model?: string;
  prompt?: string;
  qa?: {
    passed: boolean;
    confidence: string;
    summary: string;
    blockers: string[];
  };
};

export type AutonomousLaunch = {
  id: number;
  automation_job_id: number | null;
  sourcing_candidate_id: number | null;
  product_id: number | null;
  draft_id: number | null;
  status: string;
  current_stage: string;
  idempotency_key: string;
  selection: {
    total_score?: string;
    dimensions?: Record<string, string>;
    warnings?: string[];
    considered?: Array<{
      candidate_id: number;
      total_score: string;
      eligible: boolean;
      blockers: string[];
    }>;
  };
  ai_copy: {
    title?: string;
    description?: string;
    image_prompts?: string[];
  };
  asset_manifest: AutonomousAsset[];
  vision_qa: {
    passed?: boolean;
    passed_count?: number;
    required_count?: number;
  };
  stages: Array<{
    name: string;
    status: string;
    at?: string;
    detail: Record<string, unknown>;
  }>;
  blockers: string[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  handoff: {
    required_actor: string;
    scope: string;
    external_submission_performed: boolean;
  };
};
