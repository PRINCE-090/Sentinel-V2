import type { ReactNode } from "react";

/**
 * Keeps optional product features out of the DOM until they are enabled.
 * Add a flag in App.tsx and wrap the next feature with this component.
 */
export default function FeatureGate({
  enabled,
  children,
}: {
  enabled: boolean;
  children: ReactNode;
}) {
  return enabled ? <>{children}</> : null;
}
