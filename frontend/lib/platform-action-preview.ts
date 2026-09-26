import type { PlatformActionItem } from "@/lib/types";

export type PlatformActionFormValue = {
  actionType: string;
  price: string;
  targetId: string;
};

export type BoundPlatformActionPreview = {
  action: PlatformActionItem;
  fingerprint: string;
};

export function platformActionFingerprint({
  actionType,
  price,
  targetId,
}: PlatformActionFormValue) {
  return JSON.stringify([actionType, targetId, price.trim()]);
}

export function isPlatformActionPreviewCurrent(
  preview: BoundPlatformActionPreview | null,
  form: PlatformActionFormValue,
) {
  return Boolean(
    preview
    && preview.fingerprint === platformActionFingerprint(form)
    && preview.action.action_type === form.actionType
    && String(preview.action.target_id) === form.targetId
    && preview.action.status === "PREVIEWED"
    && preview.action.preview.executable,
  );
}
