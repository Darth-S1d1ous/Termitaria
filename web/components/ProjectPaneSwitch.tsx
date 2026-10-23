"use client";

import type { ReactNode } from "react";
import { ArtifactIcon, ChatsIcon, NetworkIcon } from "./icons";

export type ProjectPane = "chats" | "knowledge" | "artifacts";

export function ProjectPaneSwitch({
  pane,
  onChange,
}: {
  pane: ProjectPane;
  onChange: (pane: ProjectPane) => void;
}) {
  return (
    <div className="pane-switch" role="tablist" aria-label="Project views">
      <PaneTab id="chats" label="Chats" selected={pane === "chats"} onChange={onChange}>
        <ChatsIcon className="pane-ico" />
      </PaneTab>
      <PaneTab id="knowledge" label="Knowledge" selected={pane === "knowledge"} onChange={onChange}>
        <NetworkIcon className="pane-ico" />
      </PaneTab>
      <PaneTab id="artifacts" label="Artifacts" selected={pane === "artifacts"} onChange={onChange}>
        <ArtifactIcon className="pane-ico" />
      </PaneTab>
    </div>
  );
}

function PaneTab({
  id,
  label,
  selected,
  onChange,
  children,
}: {
  id: ProjectPane;
  label: string;
  selected: boolean;
  onChange: (pane: ProjectPane) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      aria-label={label}
      title={label}
      onClick={() => onChange(id)}
    >
      {children}
      <span className="pane-label">{label}</span>
    </button>
  );
}
